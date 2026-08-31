from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from kad_collector import desktop_answer_suggestions as module
from kad_collector.desktop_answer_suggestions import DesktopAnswerSuggestionManager


class _Store:
    def __init__(self, path: Path, answer_status: str = "missing") -> None:
        self.path = path
        self.answer_status = answer_status

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def question(self, question_id: str):
        return {
            "id": question_id,
            "question": {
                "number": 1,
                "statement": "Qual alternativa está correta?",
                "matter": None,
                "subject": None,
                "board": None,
                "organization": None,
                "role": None,
                "year": None,
                "source_pages": [1],
                "alternatives": [
                    {"letter": "A", "text": "Primeira alternativa"},
                    {"letter": "B", "text": "Segunda alternativa"},
                ],
                "answer_status": self.answer_status,
                "correct_answer": "B" if self.answer_status == "matched" else None,
            },
            "question_equivalence": {"isRepresentative": True},
        }


class _Admin:
    base_url = "http://127.0.0.1:11434"

    def tags(self):
        return [
            {
                "name": "qwen3:8b",
                "digest": module.DESKTOP_OLLAMA_DIGEST,
                "details": {"quantization_level": "Q4_K_M"},
            }
        ]

    def running_models(self):
        return []

    def version(self):
        return "0.0.0"

    def chat(self, payload):
        return {
            "done": True,
            "message": {
                "content": json.dumps(
                    {
                        "answer": "B",
                        "explanation": (
                            "A segunda alternativa é a mais adequada porque é a única "
                            "compatível com o enunciado."
                        ),
                        "confidence": 0.81,
                    }
                )
            },
        }

    def close(self):
        return None


def _manager(path: Path, answer_status: str = "missing") -> DesktopAnswerSuggestionManager:
    store = _Store(path, answer_status)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE questions (id TEXT PRIMARY KEY)")
        connection.commit()
    return DesktopAnswerSuggestionManager(store, admin_factory=_Admin)


def test_suggestion_is_stored_as_non_official_and_can_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "inspect_qwen8b_desktop", lambda admin: {})
    monkeypatch.setattr(module, "warmup_and_require_full_gpu", lambda admin: {})
    with TemporaryDirectory() as directory:
        manager = _manager(Path(directory) / "suggestions.sqlite3")

        suggestion = manager.suggest("question-1")

        assert suggestion["status"] == "pending"
        assert suggestion["isOfficial"] is False
        confirmed = manager.decide("question-1", decision="confirm", actor="operador")
        assert confirmed["status"] == "confirmed"
        assert confirmed["confirmedAnswer"] == "B"


def test_suggestion_cannot_be_generated_when_official_answer_exists() -> None:
    with TemporaryDirectory() as directory:
        manager = _manager(Path(directory) / "suggestions.sqlite3", answer_status="matched")

        with pytest.raises(ValueError, match="somente sem gabarito"):
            manager.suggest("question-1")


def test_operator_can_correct_or_reject_without_creating_official_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "inspect_qwen8b_desktop", lambda admin: {})
    monkeypatch.setattr(module, "warmup_and_require_full_gpu", lambda admin: {})
    with TemporaryDirectory() as directory:
        manager = _manager(Path(directory) / "suggestions.sqlite3")
        manager.suggest("question-1")

        corrected = manager.decide(
            "question-1", decision="confirm", actor="operador", corrected_answer="A"
        )
        assert corrected["status"] == "confirmed"
        assert corrected["confirmedAnswer"] == "A"

        with pytest.raises(ValueError, match="não existe sugestão pendente"):
            manager.decide("question-1", decision="reject", actor="operador")

        manager.suggest("question-2")
        rejected = manager.decide("question-2", decision="reject", actor="operador")
        assert rejected["status"] == "rejected"
        assert rejected["confirmedAnswer"] is None
