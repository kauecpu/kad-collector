from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kad_collector.desktop_automation import DesktopAutomationManager
from kad_collector.desktop_store import DesktopStore


class _Ollama:
    def __init__(self) -> None:
        self.state = "idle"
        self.processed = 0
        self.target = 0
        self.remaining = 0
        self.run_id: str | None = None
        self.paused: list[str] = []

    def status(self):
        return {
            "state": self.state,
            "processed": self.processed,
            "target": self.target,
            "remaining": self.remaining,
            "runId": self.run_id,
        }

    def start_automatic(self, _scope, *, limit: int):
        assert limit == 250
        self.state = "running"
        self.target = 250
        self.run_id = "qwen-run"

    def pause(self, run_id: str):
        self.paused.append(run_id)
        self.state = "pause_requested"


class _UnavailableOllama(_Ollama):
    def start_automatic(self, _scope, *, limit: int):
        assert limit == 250
        raise RuntimeError("GPU indisponível")


def test_empty_database_stays_idle_and_persists_status() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())

        result = manager.run_once()

        assert result["status"] == "idle"
        assert result["phase"] == "idle"
        assert "novas questões" in result["message"]
        assert manager.status()["report"]["rawQuestions"] == 0


def test_collection_pauses_automation_without_writing_questions() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama(), collection_active=lambda: True)

        result = manager.run_once()

        assert result["status"] == "idle"
        assert result["phase"] == "collection"
        assert "coleta" in result["message"]


def test_running_qwen_does_not_repeat_database_preparation() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        ollama = _Ollama()
        manager = DesktopAutomationManager(store, ollama)
        deterministic = SimpleNamespace(ai_candidates=1, as_dict=lambda: {"aiCandidates": 1})

        with (
            patch.object(
                store,
                "operational_presentation_summary",
                return_value={"rawQuestions": 1},
            ),
            patch(
                "kad_collector.desktop_automation.apply_desktop_preparation",
                return_value={"prepared": 1, "qwenEligible": 1},
            ) as preparation,
            patch(
                "kad_collector.desktop_automation.run_canonical_classification",
                return_value=deterministic,
            ),
            patch.object(manager, "_approve_ready_questions", return_value=0),
        ):
            first = manager.run_once()
            second = manager.run_once()

        assert first["status"] == "qwen_processing"
        assert second["status"] == "qwen_processing"
        assert preparation.call_count == 1


def test_new_questions_mark_automation_as_pending_without_starting_it() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())

        manager.mark_pending()

        status = manager.status()
        assert status["status"] == "waiting"
        assert status["phase"] == "pending"
        assert "Iniciar processamento" in status["message"]


def test_qwen_failure_persists_backoff_without_repeating_preparation() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _UnavailableOllama())
        deterministic = SimpleNamespace(ai_candidates=1, as_dict=lambda: {"aiCandidates": 1})

        with (
            patch.object(
                store,
                "operational_presentation_summary",
                return_value={"rawQuestions": 1},
            ),
            patch(
                "kad_collector.desktop_automation.apply_desktop_preparation",
                return_value={"prepared": 1, "qwenEligible": 1},
            ) as preparation,
            patch(
                "kad_collector.desktop_automation.run_canonical_classification",
                return_value=deterministic,
            ),
            patch.object(manager, "_approve_ready_questions", return_value=0),
        ):
            first = manager.run_once()
            second = manager.run_once()

        assert first["status"] == "retry"
        assert first["retryAttempt"] == 1
        assert first["nextRetryAt"]
        assert second["status"] == "retry"
        assert preparation.call_count == 1


def test_qwen_does_not_start_when_eligible_units_are_already_complete() -> None:
    """Eligibility is broader than the queue that actually needs AI."""
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        ollama = _Ollama()
        manager = DesktopAutomationManager(store, ollama)
        deterministic = SimpleNamespace(ai_candidates=0, as_dict=lambda: {"aiCandidates": 0})

        with (
            patch.object(
                store,
                "operational_presentation_summary",
                return_value={"rawQuestions": 1},
            ),
            patch(
                "kad_collector.desktop_automation.apply_desktop_preparation",
                return_value={"qwenEligible": 1, "qwenEligibleQuestions": 1},
            ),
            patch(
                "kad_collector.desktop_automation.run_canonical_classification",
                return_value=deterministic,
            ),
            patch.object(manager, "_approve_ready_questions", return_value=0),
        ):
            result = manager.run_once()

        assert result["status"] == "ready"
        assert result["progress"]["percent"] == 100
        assert result["report"]["noWorkReason"] == "Nenhuma questão precisa do Qwen"
        assert ollama.state == "idle"


def test_qwen_progress_is_cumulative_across_four_batches() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        ollama = _Ollama()
        manager = DesktopAutomationManager(store, ollama)
        progress = {
            "runId": "automation-776",
            "initialTotal": 776,
            "completedTotal": 0,
        }
        percentages: list[int] = []

        for completed_before, current_batch, target in (
            (0, 250, 250),
            (250, 250, 250),
            (500, 250, 250),
            (750, 26, 26),
        ):
            progress["completedTotal"] = completed_before
            manager._write(
                "qwen_processing",
                "qwen_processing",
                "Classificando",
                {"automationProgress": dict(progress)},
            )
            ollama.state = "running"
            ollama.processed = current_batch
            ollama.target = target
            status = manager.status()
            percentages.append(status["progress"]["percent"])
            assert status["progress"]["completed"] == min(
                completed_before + current_batch, 776
            )
            assert status["progress"]["runId"] == "automation-776"

        assert percentages == sorted(percentages)
        assert percentages[-1] == 99
        manager._write(
            "ready",
            "ready",
            "Concluído",
            {"automationProgress": {**progress, "completedTotal": 776}},
            finished=True,
        )
        assert manager.status()["progress"]["percent"] == 100


def test_auto_approval_processes_more_than_one_thousand_questions() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())
        first = [
            {"id": f"q-{index}", "importable": True, "reviewer": None, "review_notes": None}
            for index in range(1_000)
        ]
        second = [
            {"id": "q-1000", "importable": True, "reviewer": None, "review_notes": None}
        ]
        with (
            patch.object(
                store,
                "query",
                side_effect=[{"questions": first}, {"questions": second}, {"questions": []}],
            ),
            patch.object(
                store,
                "approve_questions",
                side_effect=lambda ids, **_kwargs: len(ids),
            ) as approve,
        ):
            total = manager._approve_ready_questions()

        assert total == 1_001
        assert [len(call.args[0]) for call in approve.call_args_list] == [1_000, 1]


def test_auto_approval_failure_does_not_hide_remaining_batch() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())
        batches = [
            [
                {
                    "id": f"q-{offset + index}",
                    "importable": True,
                    "reviewer": None,
                    "review_notes": None,
                }
                for index in range(size)
            ]
            for offset, size in ((0, 1_000), (1_000, 1))
        ]
        with (
            patch.object(
                store,
                "query",
                side_effect=[{"questions": batch} for batch in batches],
            ),
            patch.object(
                store,
                "approve_questions",
                side_effect=[1_000, RuntimeError("falha transacional")],
            ),
            pytest.raises(RuntimeError, match="falha transacional"),
        ):
            manager._approve_ready_questions()


def test_shutdown_keeps_lease_when_worker_does_not_stop() -> None:
    class _StuckThread:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float) -> None:
            assert timeout == 10

    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())
        manager._thread = _StuckThread()  # type: ignore[assignment]
        with patch.object(manager, "_release_lease") as release:
            manager.shutdown()

        release.assert_not_called()
        status = manager.status()
        assert status["status"] == "error"
        assert "mantido" in status["error"]


def test_stop_requests_qwen_pause_and_exposes_stopping_state() -> None:
    class _LiveThread:
        def is_alive(self) -> bool:
            return True

    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        ollama = _Ollama()
        ollama.state = "running"
        ollama.run_id = "qwen-active"
        manager = DesktopAutomationManager(store, ollama)
        manager._thread = _LiveThread()  # type: ignore[assignment]

        status = manager.stop()

        assert status["status"] == "stopping"
        assert ollama.paused == ["qwen-active"]
        assert manager._wait_for_qwen_stop


def test_cancellation_after_preparation_skips_classification() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())

        def prepare(*_args, **_kwargs):
            manager._stop.set()
            return {"prepared": 1}

        with (
            patch.object(
                store, "operational_presentation_summary", return_value={"rawQuestions": 1}
            ),
            patch("kad_collector.desktop_automation.apply_desktop_preparation", prepare),
            patch(
                "kad_collector.desktop_automation.run_canonical_classification"
            ) as classify,
        ):
            status = manager.run_once()

        assert status["status"] == "stopped"
        classify.assert_not_called()


def test_cancellation_after_rules_skips_finalization() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())
        deterministic = SimpleNamespace(ai_candidates=0, as_dict=lambda: {})

        def classify(*_args, **_kwargs):
            manager._stop.set()
            return deterministic

        with (
            patch.object(
                store, "operational_presentation_summary", return_value={"rawQuestions": 1}
            ),
            patch(
                "kad_collector.desktop_automation.apply_desktop_preparation",
                return_value={"prepared": 1},
            ),
            patch(
                "kad_collector.desktop_automation.run_canonical_classification",
                side_effect=classify,
            ),
            patch.object(manager, "_approve_ready_questions") as approve,
        ):
            status = manager.run_once()

        assert status["status"] == "stopped"
        approve.assert_not_called()


def test_cancellation_during_finalization_does_not_report_ready() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama())
        deterministic = SimpleNamespace(ai_candidates=0, as_dict=lambda: {})

        def finalize() -> int:
            manager._stop.set()
            return 0

        with (
            patch.object(
                store, "operational_presentation_summary", return_value={"rawQuestions": 1}
            ),
            patch(
                "kad_collector.desktop_automation.apply_desktop_preparation",
                return_value={"prepared": 1},
            ),
            patch(
                "kad_collector.desktop_automation.run_canonical_classification",
                return_value=deterministic,
            ),
            patch.object(manager, "_approve_ready_questions", side_effect=finalize),
        ):
            status = manager.run_once()

        assert status["status"] == "stopped"


def test_two_automation_instances_cannot_claim_the_same_database() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        first = DesktopAutomationManager(store, _Ollama())
        second = DesktopAutomationManager(store, _Ollama())

        first._acquire_lease()
        try:
            with pytest.raises(RuntimeError, match="outra instância"):
                second._acquire_lease()
        finally:
            first._release_lease()
