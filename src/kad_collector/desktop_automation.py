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
        self._retry_attempt = 0
        self._instance_id = str(uuid.uuid4())
        self._run_id: str | None = None
        self._wait_for_qwen_stop = False
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
            elif row["status"] in {
                "running",
                "classifying_qwen",
                "starting",
                "validating",
                "preparing",
                "deduplicating",
                "classifying_rules",
                "qwen_pending",
                "qwen_processing",
                "finalizing",
                "stopping",
            }:
                connection.execute(
                    "UPDATE desktop_automation_state SET status='idle',phase='resume',"
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
        started: bool = False,
        retry_attempt: int | None = None,
        next_retry_at: str | None = None,
    ) -> None:
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_automation_state SET status=?,phase=?,message=?,"
                "report_json=?,error=?,updated_at=?,finished_at=?,"
                "retry_attempt=?,next_retry_at=?,started_at=CASE WHEN ? THEN ? "
                "ELSE started_at END WHERE id='singleton'",
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
                    int(started),
                    _now(),
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
        status = str(row["status"])
        if status in {"classifying_qwen", "qwen_processing"}:
            total = max(int(progress.get("initialTotal", 0) or 0), 0)
            base_completed = max(int(progress.get("completedTotal", 0) or 0), 0)
            batch_completed = max(int(qwen.get("processed", 0) or 0), 0)
            completed = min(base_completed + batch_completed, total) if total else 0
            progress = {
                "stage": "qwen_processing",
                "runId": progress.get("runId"),
                "completed": completed,
                "total": total,
                "percent": min(round(completed / total * 100), 99) if total else 0,
                "initialTotal": total,
                "completedTotal": completed,
                "currentBatchCompleted": batch_completed,
                "currentBatchTotal": int(qwen.get("target", 0) or 0),
                "remainingTotal": max(total - completed, 0),
            }
        elif status in {"completed", "ready"}:
            total = max(int(progress.get("initialTotal", 0) or 0), 0)
            progress = {
                **progress,
                "stage": "ready",
                "completed": total,
                "total": total,
                "completedTotal": total,
                "remainingTotal": 0,
                "percent": 100,
            }
        elif status == "idle" and not progress:
            progress = {
                "stage": "idle",
                "completed": 0,
                "total": 0,
                "remainingTotal": 0,
                "percent": 100,
            }
        try:
            stale = (
                status
                in {
                    "running",
                    "classifying_qwen",
                    "starting",
                    "validating",
                    "preparing",
                    "deduplicating",
                    "classifying_rules",
                    "qwen_pending",
                    "qwen_processing",
                    "finalizing",
                    "stopping",
                }
                and datetime.fromisoformat(str(row["updated_at"]))
                < datetime.now(UTC) - timedelta(seconds=90)
            )
        except (TypeError, ValueError):
            stale = False
        public_status = "stale" if stale else status
        return {
            "status": public_status,
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
            self._run_id = str(uuid.uuid4())
            self._thread = threading.Thread(
                target=self._run_loop,
                name="kad-desktop-automation",
                daemon=True,
            )
            self._write(
                "starting",
                "starting",
                "Iniciando automação local",
                {
                    "automationProgress": {
                        "runId": self._run_id,
                        "stage": "starting",
                        "completed": 0,
                        "total": 0,
                        "percent": 0,
                    }
                },
                started=True,
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
        if self._stop.is_set():
            self._write("stopped", "stopped", "Processamento interrompido", finished=True)
            return self.status()
        if self.collection_active():
            self._write("idle", "collection", "Aguardando a coleta terminar")
            return self.status()
        persisted = self.status()
        persisted_report = persisted.get("report")
        if not isinstance(persisted_report, dict):
            persisted_report = {}

        def execution_report(values: dict[str, Any]) -> dict[str, Any]:
            """Keep the stable run identity while intermediate stages are persisted."""
            return {**persisted_report, **values}

        qwen_status = self.ollama.status()
        qwen_active = qwen_status.get("state") in {
            "starting",
            "running",
            "pause_requested",
        }
        if persisted["status"] in {"classifying_qwen", "qwen_processing"} and qwen_active:
            self._write(
                "qwen_processing",
                "qwen_processing",
                "Classificando novas questões com Qwen",
                persisted.get("report") or {},
                retry_attempt=self._retry_attempt,
            )
            return self.status()
        if persisted["status"] in {"classifying_qwen", "qwen_processing"} and qwen_status.get(
            "state"
        ) not in {"completed"}:
            detail = str(qwen_status.get("pauseReason") or qwen_status.get("state") or "")
            self._write(
                "error",
                "qwen_processing",
                "O Qwen não concluiu a classificação",
                persisted.get("report") or {},
                error=detail or "estado final do Qwen não reconhecido",
                finished=True,
            )
            return self.status()
        if persisted["status"] in {"waiting_qwen", "retry"}:
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
            self._write("idle", "idle", "Aguardando novas questões", summary, finished=True)
            return self.status()
        try:
            self._write(
                "validating",
                "validating",
                "Validando o banco local",
                execution_report(summary),
            )
            self._write(
                "preparing", "preparing", "Preparando as provas", execution_report(summary)
            )
            self._write(
                "deduplicating",
                "deduplicating",
                "Agrupando cópias e definindo questões principais",
                execution_report(summary),
            )
            with closing(self.store._connect()) as connection:
                preparation = apply_desktop_preparation(
                    connection,
                    run_id=f"automatic-{uuid.uuid4().hex}",
                )
            if self._stop.is_set():
                self._write(
                    "stopped", "stopped", "Processamento interrompido", finished=True
                )
                return self.status()
            self._write(
                "classifying_rules",
                "classifying_rules",
                "Aplicando regras locais de classificação",
                execution_report({**summary, "preparation": preparation}),
            )
            with closing(self.store._connect()) as connection:
                deterministic = run_canonical_classification(
                    connection,
                    apply=True,
                    enable_ai=False,
                    pending_only=True,
                    queue_ineligible=False,
                    eligibility_scope="answered",
                    checkpoint_interval=5,
                    should_pause=self._stop.is_set,
                )
            if self._stop.is_set():
                self._write("stopped", "stopped", "Processamento interrompido", finished=True)
                return self.status()
            self._write(
                "finalizing",
                "finalizing",
                "Finalizando validações e aprovações seguras",
                execution_report({**summary, "preparation": preparation}),
            )
            approved = self._approve_ready_questions()
            if self._stop.is_set():
                self._write(
                    "stopped", "stopped", "Processamento interrompido", finished=True
                )
                return self.status()
            latest = self.store.operational_presentation_summary()
            pending_qwen = max(int(getattr(deterministic, "ai_candidates", 0) or 0), 0)
            previous_progress = persisted.get("report", {}).get("automationProgress", {})
            if not isinstance(previous_progress, dict):
                previous_progress = {}
            initial_total = max(
                int(previous_progress.get("initialTotal", 0) or 0), pending_qwen
            )
            completed_total = max(initial_total - pending_qwen, 0)
            run_id = str(
                previous_progress.get("runId") or self._run_id or uuid.uuid4()
            )
            self._run_id = run_id
            previous_qwen = persisted.get("report", {}).get("qwenTotals", {})
            if not isinstance(previous_qwen, dict):
                previous_qwen = {}
            finished_qwen = (
                qwen_status
                if persisted["status"] in {"classifying_qwen", "qwen_processing"}
                and qwen_status.get("state") == "completed"
                else {}
            )
            qwen_totals = {
                "processed": int(previous_qwen.get("processed", 0) or 0)
                + int(finished_qwen.get("processed", 0) or 0),
                "aiCalls": int(previous_qwen.get("aiCalls", 0) or 0)
                + int(finished_qwen.get("aiCalls", 0) or 0),
                "accepted": int(previous_qwen.get("accepted", 0) or 0)
                + int(finished_qwen.get("acceptedSuggestions", 0) or 0),
                "reviewRequired": int(previous_qwen.get("reviewRequired", 0) or 0)
                + int(finished_qwen.get("reviewRequired", 0) or 0),
                "failures": int(previous_qwen.get("failures", 0) or 0)
                + int(finished_qwen.get("failures", 0) or 0),
            }
            deterministic_report = deterministic.as_dict()
            previous_rules = persisted.get("report", {}).get("ruleTotals", {})
            if not isinstance(previous_rules, dict):
                previous_rules = {}
            rule_totals = {
                "alreadyComplete": int(
                    previous_rules.get(
                        "alreadyComplete",
                        deterministic_report.get("alreadyComplete", 0),
                    )
                    or 0
                ),
                "resolved": int(previous_rules.get("resolved", 0) or 0)
                + int(deterministic_report.get("deterministicQuestions", 0) or 0),
                "needsReview": int(deterministic_report.get("reviewRequired", 0) or 0),
                "blocked": int(latest.get("blocked", 0) or 0),
            }
            report = {
                **latest,
                "preparation": preparation,
                "deterministic": deterministic_report,
                "autoApproved": approved,
                "qwenTotals": qwen_totals,
                "ruleTotals": rule_totals,
                "automationProgress": {
                    "runId": run_id,
                    "stage": "qwen_pending" if pending_qwen else "ready",
                    "completed": completed_total,
                    "total": initial_total,
                    "initialTotal": initial_total,
                    "completedTotal": completed_total,
                    "currentBatchCompleted": 0,
                    "currentBatchTotal": min(250, pending_qwen),
                    "remainingTotal": pending_qwen,
                    "percent": (
                        min(round(completed_total / initial_total * 100), 99)
                        if initial_total
                        else 100
                    ),
                },
            }
            qwen_status = self.ollama.status()
            if qwen_status.get("state") in {"starting", "running", "pause_requested"}:
                self._write(
                    "qwen_processing",
                    "qwen_processing",
                    "Classificando novas questões com Qwen",
                    report,
                )
                return self.status()
            # qwenEligible is the complete canonical population, including
            # units already complete or solved by rules.  ai_candidates is
            # measured after those rules and is the actual remaining queue.
            qwen_needed = pending_qwen > 0
            if qwen_needed:
                return self._start_qwen(report)
            report["noWorkReason"] = "Nenhuma questão precisa do Qwen"
            self._write(
                "ready",
                "ready",
                "Nenhuma questão precisa do Qwen; banco pronto para exportação",
                report,
                finished=True,
            )
            return self.status()
        except Exception as exc:
            if self._stop.is_set():
                self._write(
                    "stopped", "stopped", "Processamento interrompido", finished=True
                )
                return self.status()
            self._write(
                "retry",
                "retry",
                "A automação aguardará uma nova tentativa",
                execution_report(summary),
                error=str(exc),
            )
            return self.status()

    def _start_qwen(self, report: dict[str, Any]) -> dict[str, Any]:
        self._write(
            "qwen_pending",
            "qwen_pending",
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
                "qwen_processing",
                "qwen_processing",
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
                "retry",
                "retry",
                "Qwen indisponível; a automação tentará novamente",
                report,
                error=str(exc),
                retry_attempt=self._retry_attempt,
                next_retry_at=next_retry,
            )
            return self.status()

    def _approve_ready_questions(self) -> int:
        approved = 0
        while not self._stop.is_set():
            views = self.store.query(DesktopFilterSet(statuses=["pending"]))["questions"]
            ids = [
                str(view["id"])
                for view in views
                if view.get("importable")
                and not view.get("reviewer")
                and not view.get("review_notes")
            ][:1_000]
            if not ids:
                break
            approved += self.store.approve_questions(
                ids,
                actor="automacao_local",
                notes=(
                    "Aprovada automaticamente após validações locais e "
                    "classificação canônica."
                ),
            )
        return approved

    def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self._renew_lease()
                result = self.run_once()
                status = result.get("status")
                if status in {"completed", "ready", "idle", "stopped", "stale", "error"}:
                    return
                if status in {"waiting_qwen", "retry"}:
                    delay = min(60.0, 15.0 * (2 ** max(self._retry_attempt - 1, 0)))
                else:
                    delay = self.interval_seconds
                self._wake.wait(delay)
                self._wake.clear()
        finally:
            while self._wait_for_qwen_stop:
                try:
                    state = self.ollama.status().get("state")
                except Exception:
                    state = "unavailable"
                if state not in {"starting", "running", "pause_requested", "unavailable"}:
                    self._wait_for_qwen_stop = False
                    break
                self._renew_lease()
                time.sleep(0.5)
            if self._stop.is_set() and not self._wait_for_qwen_stop:
                self._write(
                    "stopped", "stopped", "Processamento interrompido", finished=True
                )
            self._release_lease()

    def shutdown(self) -> None:
        self.stop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
        if thread is not None and thread.is_alive():
            self._write(
                "error",
                "stopping",
                "Não foi possível parar a automação com segurança",
                error="a tarefa permaneceu ativa; o bloqueio do banco foi mantido",
            )
            return
        self._release_lease()

    def stop(self) -> dict[str, Any]:
        """Request cooperative cancellation without releasing a live lease."""
        self._stop.set()
        self._wake.set()
        try:
            qwen = self.ollama.status()
            run_id = qwen.get("runId")
            if run_id and qwen.get("state") in {"starting", "running", "pause_requested"}:
                self._wait_for_qwen_stop = True
                self.ollama.pause(str(run_id))
        except Exception:
            pass
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._write("stopped", "stopped", "Processamento interrompido", finished=True)
        else:
            self._write("stopping", "stopping", "Parando o processamento com segurança")
        return self.status()
