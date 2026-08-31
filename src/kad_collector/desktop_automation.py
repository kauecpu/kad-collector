from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from .canonical_classification import run_canonical_classification
from .desktop_models import DesktopFilterSet, DesktopOperationScope
from .desktop_preparation import apply_desktop_preparation
from .desktop_store import DesktopStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DesktopAutomationManager:
    """Runs the safe, resumable local preparation pipeline in the background."""

    def __init__(
        self,
        store: DesktopStore,
        ollama: Any,
        *,
        collection_active: Callable[[], bool] | None = None,
        interval_seconds: float = 3.0,
    ) -> None:
        self.store = store
        self.ollama = ollama
        self.collection_active = collection_active or (lambda: False)
        self.interval_seconds = max(0.5, interval_seconds)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_qwen_attempt = 0.0
        with closing(self.store._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS desktop_automation_state (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    message TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            row = connection.execute(
                "SELECT status FROM desktop_automation_state WHERE id='singleton'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO desktop_automation_state "
                    "(id,status,phase,message,report_json,updated_at) VALUES "
                    "('singleton','idle','waiting','Aguardando o banco local', '{}', ?)" ,
                    (_now(),),
                )
            elif row["status"] in {"running", "classifying_qwen"}:
                connection.execute(
                    "UPDATE desktop_automation_state SET status='waiting',phase='resume',"
                    "message='Retomando a automação após reiniciar o aplicativo',updated_at=? "
                    "WHERE id='singleton'",
                    (_now(),),
                )
            connection.commit()

    def _write(
        self,
        status: str,
        phase: str,
        message: str,
        report: dict[str, Any] | None = None,
        *,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_automation_state SET status=?,phase=?,message=?,"
                "report_json=?,error=?,updated_at=?,finished_at=? WHERE id='singleton'",
                (
                    status,
                    phase,
                    message,
                    json.dumps(report or {}, ensure_ascii=False, sort_keys=True),
                    error,
                    _now(),
                    _now() if finished else None,
                ),
            )
            connection.commit()

    def status(self) -> dict[str, Any]:
        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM desktop_automation_state WHERE id='singleton'"
            ).fetchone()
        if row is None:
            return {"status": "idle", "phase": "waiting", "message": "Aguardando o banco local"}
        try:
            report = json.loads(row["report_json"] or "{}")
        except json.JSONDecodeError:
            report = {}
        return {
            "status": row["status"],
            "phase": row["phase"],
            "message": row["message"],
            "report": report,
            "error": row["error"],
            "updatedAt": row["updated_at"],
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake.set()
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="kad-desktop-automation",
                daemon=True,
            )
            self._thread.start()

    def wake(self) -> None:
        self.start()
        self._wake.set()

    def run_once(self) -> dict[str, Any]:
        if self.collection_active():
            self._write("waiting", "collection", "Aguardando a coleta terminar")
            return self.status()
        summary = self.store.operational_presentation_summary()
        if not summary["rawQuestions"]:
            self._write("idle", "waiting", "Aguardando novas questões", summary, finished=True)
            return self.status()
        try:
            self._write("running", "preparing", "Preparando provas e agrupando duplicatas", summary)
            with closing(self.store._connect()) as connection:
                preparation = apply_desktop_preparation(
                    connection,
                    run_id=f"automatic-{uuid.uuid4().hex}",
                )
                deterministic = run_canonical_classification(
                    connection,
                    apply=True,
                    enable_ai=False,
                    pending_only=True,
                    queue_ineligible=False,
                    eligibility_scope="answered",
                    checkpoint_interval=5,
                )
            approved = self._approve_ready_questions()
            latest = self.store.operational_presentation_summary()
            report = {
                **latest,
                "preparation": preparation,
                "deterministic": deterministic.as_dict(),
                "autoApproved": approved,
            }
            qwen_status = self.ollama.status()
            if qwen_status.get("state") in {"starting", "running", "pause_requested"}:
                self._write(
                    "classifying_qwen", "qwen", "Classificando novas questões com Qwen", report
                )
                return self.status()
            qwen_needed = int(getattr(deterministic, "ai_candidates", 0)) > 0
            if qwen_needed and time.monotonic() - self._last_qwen_attempt >= 30:
                self._last_qwen_attempt = time.monotonic()
                self._write(
                    "classifying_qwen",
                    "qwen",
                    "Iniciando classificação automática com Qwen",
                    report,
                )
                try:
                    self.ollama.start_automatic(
                        DesktopOperationScope(type="all", allowOutOfScope=True),
                        limit=250,
                    )
                    return self.status()
                except Exception as exc:
                    self._write(
                        "waiting_qwen",
                        "qwen",
                        "Qwen indisponível; a automação tentará novamente",
                        report,
                        error=str(exc),
                    )
                    return self.status()
            self._write(
                "completed",
                "ready",
                "Banco preparado; revise o resumo e exporte",
                report,
                finished=True,
            )
            return self.status()
        except Exception as exc:
            self._write(
                "waiting",
                "retry",
                "A automação aguardará uma nova tentativa",
                summary,
                error=str(exc),
            )
            return self.status()

    def _approve_ready_questions(self) -> int:
        views = self.store.query(DesktopFilterSet(statuses=["pending"]))["questions"]
        ids = [
            str(view["id"])
            for view in views
            if view.get("importable")
            and not view.get("reviewer")
            and not view.get("review_notes")
        ]
        if not ids:
            return 0
        return self.store.approve_questions(
            ids[:1_000],
            actor="automacao_local",
            notes="Aprovada automaticamente após validações locais e classificação canônica.",
        )

    def _run_loop(self) -> None:
        self.run_once()
        while not self._stop.is_set():
            self._wake.wait(self.interval_seconds)
            self._wake.clear()
            if not self._stop.is_set():
                self.run_once()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
