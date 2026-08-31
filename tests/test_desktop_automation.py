from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from kad_collector.desktop_automation import DesktopAutomationManager
from kad_collector.desktop_store import DesktopStore


class _Ollama:
    def status(self):
        return {"state": "idle"}


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
