from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
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
        self._retry_attempt = 0
        self._instance_id = str(uuid.uuid4())
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
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(desktop_automation_state)"
                ).fetchall()
            }
            if "retry_attempt" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_automation_state ADD COLUMN "
                    "retry_attempt INTEGER NOT NULL DEFAULT 0"
                )
            if "next_retry_at" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_automation_state ADD COLUMN next_retry_at TEXT"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS desktop_automation_lease ("
                "id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,expires_at TEXT NOT NULL)"
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
            retry_row = connection.execute(
                "SELECT retry_attempt FROM desktop_automation_state WHERE id='singleton'"
            ).fetchone()
            if retry_row is not None:
                self._retry_attempt = int(retry_row["retry_attempt"] or 0)
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
        retry_attempt: int | None = None,
        next_retry_at: str | None = None,
    ) -> None:
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_automation_state SET status=?,phase=?,message=?,"
                "report_json=?,error=?,updated_at=?,finished_at=?,"
                "retry_attempt=?,next_retry_at=? WHERE id='singleton'",
                (
                    status,
                    phase,
                    message,
                    json.dumps(report or {}, ensure_ascii=False, sort_keys=True),
                    error,
                    _now(),
                    _now() if finished else None,
                    self._retry_attempt if retry_attempt is None else retry_attempt,
                    next_retry_at,
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
        progress = report.get("automationProgress")
        if not isinstance(progress, dict):
            progress = {}
        try:
            qwen = self.ollama.status()
        except Exception:
            qwen = {"state": "unavailable"}
        if row["status"] == "classifying_qwen":
            total = max(
                int(qwen.get("target", 0) or 0),
                int(progress.get("total", 0) or 0),
            )
            completed = min(int(qwen.get("processed", 0) or 0), total) if total else 0
            progress = {
                "stage": "qwen_processing",
                "completed": completed,
                "total": total,
                "percent": round(completed / total * 100) if total else 0,
            }
        elif row["status"] in {"completed", "idle"}:
            progress = {"stage": "ready", "completed": 1, "total": 1, "percent": 100}
        try:
            stale = (
                row["status"] in {"running", "classifying_qwen"}
                and datetime.fromisoformat(str(row["updated_at"]))
                < datetime.now(UTC) - timedelta(seconds=90)
            )
        except (TypeError, ValueError):
            stale = False
        return {
            "status": row["status"],
            "phase": row["phase"],
            "message": row["message"],
            "report": report,
            "error": row["error"],
            "updatedAt": row["updated_at"],
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "retryAttempt": int(row["retry_attempt"]),
            "nextRetryAt": row["next_retry_at"],
            "progress": progress,
            "stale": stale,
            "qwen": {
                "state": qwen.get("state", "unavailable"),
                "processed": int(qwen.get("processed", 0) or 0),
                "target": int(qwen.get("target", 0) or 0),
                "remaining": int(qwen.get("remaining", 0) or 0),
            },
        }

    def _acquire_lease(self) -> None:
        now = datetime.now(UTC)
        expires = (now + timedelta(minutes=2)).isoformat()
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_id,expires_at FROM desktop_automation_lease WHERE id='singleton'"
            ).fetchone()
            if row is not None and row["owner_id"] != self._instance_id:
                try:
                    active = datetime.fromisoformat(str(row["expires_at"])) > now
                except ValueError:
                    active = False
                if active:
                    connection.rollback()
                    raise RuntimeError(
                        "outra instância do Collector já está processando este banco"
                    )
            connection.execute(
                "INSERT INTO desktop_automation_lease(id,owner_id,expires_at) VALUES "
                "('singleton',?,?) ON CONFLICT(id) DO UPDATE SET "
                "owner_id=excluded.owner_id,expires_at=excluded.expires_at",
                (self._instance_id, expires),
            )
            connection.commit()

    def _release_lease(self) -> None:
        with closing(self.store._connect()) as connection:
            connection.execute(
                "DELETE FROM desktop_automation_lease WHERE id='singleton' AND owner_id=?",
                (self._instance_id,),
            )
            connection.commit()

    def _renew_lease(self) -> None:
        expires = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_automation_lease SET expires_at=? "
                "WHERE id='singleton' AND owner_id=?",
                (expires, self._instance_id),
            )
            connection.commit()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake.set()
                return
            self._acquire_lease()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="kad-desktop-automation",
                daemon=True,
            )
            self._write(
                "running",
                "starting",
                "Iniciando automação local",
            )
            self._thread.start()

    def wake(self) -> None:
        self.start()
        self._wake.set()

    def mark_pending(self) -> dict[str, Any]:
        """Expose newly imported work without starting the processing loop."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake.set()
                return self.status()
            self._retry_attempt = 0
            self._write(
                "waiting",
                "pending",
                "Novas questões aguardando; clique em Iniciar processamento",
                retry_attempt=0,
                next_retry_at=None,
            )
            return self.status()

    def run_once(self) -> dict[str, Any]:
        if self.collection_active():
            self._write("waiting", "collection", "Aguardando a coleta terminar")
            return self.status()
        persisted = self.status()
        qwen_status = self.ollama.status()
        qwen_active = qwen_status.get("state") in {
            "starting",
            "running",
            "pause_requested",
        }
        if persisted["status"] == "classifying_qwen" and qwen_active:
            self._write(
                "classifying_qwen",
                "qwen",
                "Classificando novas questões com Qwen",
                persisted.get("report") or {},
                retry_attempt=self._retry_attempt,
            )
            return self.status()
        if persisted["status"] == "waiting_qwen":
            next_retry_at = persisted.get("nextRetryAt")
            if next_retry_at:
                try:
                    if datetime.fromisoformat(str(next_retry_at)) > datetime.now(UTC):
                        return persisted
                except ValueError:
                    pass
            return self._start_qwen(persisted.get("report") or {})
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
                "automationProgress": {
                    "stage": "qwen_pending",
                    "completed": 0,
                    "total": max(int(preparation.get("qwenEligible", 0) or 0), 0),
                    "percent": 0,
                },
            }
            qwen_status = self.ollama.status()
            if qwen_status.get("state") in {"starting", "running", "pause_requested"}:
                self._write(
                    "classifying_qwen", "qwen", "Classificando novas questões com Qwen", report
                )
                return self.status()
            # The rules pass deliberately runs with AI disabled, so its
            # ``ai_candidates`` value is always zero.  The preparation report
            # is the source of truth because it counts canonical units that
            # are actually eligible for Qwen.
            qwen_needed = int(preparation.get("qwenEligible", 0) or 0) > 0
            if qwen_needed and time.monotonic() - self._last_qwen_attempt >= 30:
                self._last_qwen_attempt = time.monotonic()
                return self._start_qwen(report)
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

    def _start_qwen(self, report: dict[str, Any]) -> dict[str, Any]:
        self._last_qwen_attempt = time.monotonic()
        self._write(
            "classifying_qwen",
            "qwen",
            "Iniciando classificação automática com Qwen",
            report,
            retry_attempt=self._retry_attempt,
            next_retry_at=None,
        )
        try:
            self.ollama.start_automatic(
                DesktopOperationScope(type="all", allowOutOfScope=True),
                limit=250,
            )
            self._retry_attempt = 0
            self._write(
                "classifying_qwen",
                "qwen",
                "Classificando novas questões com Qwen",
                report,
                retry_attempt=0,
                next_retry_at=None,
            )
            return self.status()
        except Exception as exc:
            self._retry_attempt += 1
            delay = min(300, 15 * (2 ** (self._retry_attempt - 1)))
            next_retry = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            self._write(
                "waiting_qwen",
                "qwen",
                "Qwen indisponível; a automação tentará novamente",
                report,
                error=str(exc),
                retry_attempt=self._retry_attempt,
                next_retry_at=next_retry,
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
        try:
            while not self._stop.is_set():
                self._renew_lease()
                result = self.run_once()
                status = result.get("status")
                if status in {"completed", "idle"}:
                    return
                if status == "waiting_qwen":
                    delay = min(60.0, 15.0 * (2 ** max(self._retry_attempt - 1, 0)))
                else:
                    delay = self.interval_seconds
                self._wake.wait(delay)
                self._wake.clear()
        finally:
            self._release_lease()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
        self._release_lease()
