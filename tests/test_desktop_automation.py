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

    def status(self):
        return {"state": self.state}

    def start_automatic(self, _scope, *, limit: int):
        assert limit == 250
        self.state = "running"


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
        assert result["phase"] == "waiting"
        assert "novas questões" in result["message"]
        assert manager.status()["report"]["rawQuestions"] == 0


def test_collection_pauses_automation_without_writing_questions() -> None:
    with TemporaryDirectory() as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        manager = DesktopAutomationManager(store, _Ollama(), collection_active=lambda: True)

        result = manager.run_once()

        assert result["status"] == "waiting"
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
                return_value={"prepared": 1},
            ) as preparation,
            patch(
                "kad_collector.desktop_automation.run_canonical_classification",
                return_value=deterministic,
            ),
            patch.object(manager, "_approve_ready_questions", return_value=0),
        ):
            first = manager.run_once()
            second = manager.run_once()

        assert first["status"] == "classifying_qwen"
        assert second["status"] == "classifying_qwen"
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
                return_value={"prepared": 1},
            ) as preparation,
            patch(
                "kad_collector.desktop_automation.run_canonical_classification",
                return_value=deterministic,
            ),
            patch.object(manager, "_approve_ready_questions", return_value=0),
        ):
            first = manager.run_once()
            second = manager.run_once()

        assert first["status"] == "waiting_qwen"
        assert first["retryAttempt"] == 1
        assert first["nextRetryAt"]
        assert second["status"] == "waiting_qwen"
        assert preparation.call_count == 1


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
