from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, cast

from .desktop_ollama_classification import (
    DESKTOP_OLLAMA_MODEL,
    inspect_qwen8b_desktop,
    warmup_and_require_full_gpu,
)
from .desktop_store import DesktopStore
from .models import QuestionRecord
from .ollama_preflight import HttpOllamaAdminClient, OllamaAdminClient


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


class DesktopAnswerSuggestionManager:
    """Keeps non-official Qwen answers separate from official answer keys."""

    def __init__(
        self,
        store: DesktopStore,
        *,
        admin_factory: Callable[[], OllamaAdminClient] | None = None,
    ) -> None:
        self.store = store
        self._admin_factory = admin_factory or (lambda: HttpOllamaAdminClient())
        with closing(self.store._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS desktop_answer_suggestions (
                    id TEXT PRIMARY KEY,
                    question_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','confirmed','rejected')),
                    suggested_answer TEXT NOT NULL,
                    confirmed_answer TEXT,
                    explanation TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    is_official INTEGER NOT NULL DEFAULT 0 CHECK(is_official = 0),
                    actor TEXT,
                    decision_notes TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY(question_id) REFERENCES questions(id)
                );
                CREATE INDEX IF NOT EXISTS desktop_answer_suggestions_question_idx
                ON desktop_answer_suggestions(question_id, created_at DESC);
                """
            )
            connection.commit()

    def latest(self, question_id: str) -> dict[str, Any] | None:
        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM desktop_answer_suggestions WHERE question_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (question_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "questionId": row["question_id"],
            "status": row["status"],
            "suggestedAnswer": row["suggested_answer"],
            "confirmedAnswer": row["confirmed_answer"],
            "explanation": row["explanation"],
            "confidence": float(row["confidence"]),
            "model": row["model"],
            "provider": row["provider"],
            "isOfficial": False,
            "actor": row["actor"],
            "decisionNotes": row["decision_notes"],
            "createdAt": row["created_at"],
            "decidedAt": row["decided_at"],
            "warning": "Sugestão do Qwen; não é gabarito oficial.",
        }

    def summary(self) -> dict[str, int]:
        with closing(self.store._connect()) as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) AS total FROM ("
                "SELECT s.status,ROW_NUMBER() OVER (PARTITION BY question_id "
                "ORDER BY created_at DESC) AS rn "
                "FROM desktop_answer_suggestions s) WHERE rn=1 GROUP BY status"
            ).fetchall()
        counts = {cast(str, row["status"]): int(row["total"]) for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "confirmed": counts.get("confirmed", 0),
            "rejected": counts.get("rejected", 0),
        }

    def suggest(self, question_id: str) -> dict[str, Any]:
        view = self.store.question(question_id)
        question = QuestionRecord.model_validate(view["question"])
        if question.answer_status != "missing" or question.correct_answer is not None:
            raise ValueError("a sugestão é permitida somente sem gabarito oficial")
        equivalence = cast(dict[str, Any] | None, view.get("question_equivalence"))
        if equivalence and not equivalence.get("isRepresentative"):
            raise ValueError("use a questão principal do grupo canônico")
        letters = [item.letter for item in question.alternatives]
        if len(letters) < 2:
            raise ValueError("a questão precisa de ao menos duas alternativas")

        admin = self._admin_factory()
        try:
            inspect_qwen8b_desktop(admin)
            warmup_and_require_full_gpu(admin)
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["answer", "explanation", "confidence"],
                "properties": {
                    "answer": {"type": "string", "enum": letters},
                    "explanation": {"type": "string", "minLength": 20},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            }
            alternatives = "\n".join(
                f"{item.letter}) {item.text}" for item in question.alternatives
            )
            response = admin.chat(
                {
                    "model": DESKTOP_OLLAMA_MODEL,
                    "stream": False,
                    "format": schema,
                    "think": False,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Resolva a questão objetiva em português. Retorne apenas JSON "
                                "válido. A resposta é uma sugestão não oficial e deve explicar "
                                "por que a alternativa escolhida é a melhor."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Enunciado:\n{question.statement}\n\n"
                                f"Alternativas:\n{alternatives}"
                            ),
                        },
                    ],
                    "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512, "seed": 0},
                }
            )
        finally:
            _close(admin)
        message = response.get("message") if isinstance(response, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ValueError("o Qwen não retornou uma sugestão válida")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("o Qwen retornou JSON inválido") from exc
        answer = parsed.get("answer")
        explanation = parsed.get("explanation")
        confidence = parsed.get("confidence")
        if (
            answer not in letters
            or not isinstance(explanation, str)
            or len(explanation.strip()) < 20
        ):
            raise ValueError("a sugestão do Qwen não atende ao contrato")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ValueError("a confiança da sugestão é inválida")
        confidence = max(0.0, min(1.0, float(confidence)))
        suggestion_id = str(uuid.uuid4())
        with closing(self.store._connect()) as connection:
            connection.execute(
                "INSERT INTO desktop_answer_suggestions "
                "(id,question_id,status,suggested_answer,explanation,confidence,"
                "model,provider,created_at) "
                "VALUES (?,?,'pending',?,?,?,?, 'ollama',?)",
                (
                    suggestion_id,
                    question_id,
                    answer,
                    explanation.strip(),
                    confidence,
                    DESKTOP_OLLAMA_MODEL,
                    _now(),
                ),
            )
            connection.commit()
        result = self.latest(question_id)
        assert result is not None
        return result

    def decide(
        self,
        question_id: str,
        *,
        decision: str,
        actor: str,
        corrected_answer: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if decision not in {"confirm", "reject"}:
            raise ValueError("decisão de sugestão inválida")
        current = self.latest(question_id)
        if current is None or current["status"] != "pending":
            raise ValueError("não existe sugestão pendente para esta questão")
        view = self.store.question(question_id)
        question = QuestionRecord.model_validate(view["question"])
        if question.answer_status != "missing" or question.correct_answer is not None:
            raise ValueError(
                "um gabarito oficial foi associado; a sugestão não pode ser confirmada"
            )
        answer = corrected_answer or cast(str, current["suggestedAnswer"])
        letters = {item.letter for item in question.alternatives}
        if decision == "confirm" and answer not in letters:
            raise ValueError("a resposta confirmada não corresponde às alternativas")
        status = "confirmed" if decision == "confirm" else "rejected"
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_answer_suggestions SET status=?,confirmed_answer=?,actor=?,"
                "decision_notes=?,decided_at=? WHERE id=? AND status='pending'",
                (
                    status,
                    answer if status == "confirmed" else None,
                    actor.strip(),
                    notes,
                    _now(),
                    current["id"],
                ),
            )
            connection.commit()
        result = self.latest(question_id)
        assert result is not None
        return result
