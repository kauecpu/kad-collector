from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .json_utils import read_json, write_json
from .models import (
    LocalQuestionDecision,
    LocalReviewSession,
    QuestionBatch,
    QuestionRecord,
    ReviewDecisionStatus,
    ReviewState,
)
from .review import approve_batch_model
from .validation import batch_content_sha256, validate_questions


def question_content_sha256(question: QuestionRecord) -> str:
    canonical = json.dumps(
        question.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_review_session(
    batch: QuestionBatch, *, now: datetime | None = None
) -> LocalReviewSession:
    if batch.review.status == "approved":
        raise ValueError("use um lote pendente para iniciar a revisao local")
    created_at = now or datetime.now(UTC)
    pending_batch = batch.model_copy(deep=True)
    pending_batch.review = ReviewState()
    return LocalReviewSession(
        source_content_sha256=batch_content_sha256(batch),
        created_at=created_at,
        updated_at=created_at,
        batch=pending_batch,
        decisions=[
            LocalQuestionDecision(question_number=question.number)
            for question in pending_batch.questions
        ],
    )


def load_or_create_review_session(
    batch_path: Path,
    session_path: Path | None = None,
) -> tuple[LocalReviewSession, Path]:
    source_batch = QuestionBatch.model_validate(read_json(batch_path))
    if session_path is None:
        session_path = Path("data/reviews") / f"{source_batch.batch_id}.json"
    if session_path.exists():
        session = LocalReviewSession.model_validate(read_json(session_path))
        if session.source_content_sha256 != batch_content_sha256(source_batch):
            raise ValueError("o lote de origem mudou depois que a sessao de revisao foi criada")
        verify_review_session(session)
        return session, session_path
    session = create_review_session(source_batch)
    save_review_session(session, session_path)
    return session, session_path


def save_review_session(session: LocalReviewSession, path: Path) -> None:
    verify_review_session(session)
    write_json(path, session.model_dump(mode="json"))


def review_summary(session: LocalReviewSession) -> dict[ReviewDecisionStatus, int]:
    summary: dict[ReviewDecisionStatus, int] = {
        "pending": 0,
        "approved": 0,
        "rejected": 0,
    }
    for decision in session.decisions:
        summary[decision.status] += 1
    return summary


def update_review_question(
    session: LocalReviewSession,
    question_number: int,
    question: QuestionRecord,
    *,
    now: datetime | None = None,
) -> LocalReviewSession:
    if question.number != question_number:
        raise ValueError("o numero da questao nao pode ser alterado durante a revisao")
    updated = session.model_copy(deep=True)
    question_index = _question_index(updated, question_number)
    decision_index = _decision_index(updated, question_number)
    updated.batch.questions[question_index] = question
    previous_notes = updated.decisions[decision_index].notes
    updated.decisions[decision_index] = LocalQuestionDecision(
        question_number=question_number,
        notes=previous_notes,
    )
    updated.batch.review = ReviewState()
    updated.batch.validation = validate_questions(updated.batch.questions)
    updated.updated_at = now or datetime.now(UTC)
    return LocalReviewSession.model_validate(updated.model_dump(mode="python"))


def decide_review_question(
    session: LocalReviewSession,
    question_number: int,
    status: ReviewDecisionStatus,
    reviewer: str,
    *,
    notes: str | None = None,
    now: datetime | None = None,
) -> LocalReviewSession:
    if status == "pending":
        raise ValueError("use apenas approved ou rejected para concluir uma decisao")
    reviewer = reviewer.strip()
    if len(reviewer) < 2:
        raise ValueError("informe o nome ou identificador do revisor")
    normalized_notes = (notes or "").strip() or None
    updated = session.model_copy(deep=True)
    question = updated.batch.questions[_question_index(updated, question_number)]
    if status == "approved":
        validation = validate_questions(
            [question], require_answers=True, require_editorial=True
        )
        if not validation.valid:
            raise ValueError("a questao nao pode ser aprovada: " + "; ".join(validation.errors))
    elif normalized_notes is None:
        raise ValueError("informe a justificativa para rejeitar a questao")
    decision_index = _decision_index(updated, question_number)
    updated.decisions[decision_index] = LocalQuestionDecision(
        question_number=question_number,
        status=status,
        reviewed_by=reviewer,
        reviewed_at=now or datetime.now(UTC),
        content_sha256=question_content_sha256(question),
        notes=normalized_notes,
    )
    updated.updated_at = now or datetime.now(UTC)
    return LocalReviewSession.model_validate(updated.model_dump(mode="python"))


def export_review_session(
    session: LocalReviewSession,
    reviewer: str,
    *,
    notes: str | None = None,
    output_path: Path | None = None,
) -> tuple[QuestionBatch, Path]:
    verify_review_session(session)
    summary = review_summary(session)
    if summary["pending"]:
        raise ValueError(f"a revisao ainda possui {summary['pending']} questoes pendentes")
    approved_numbers = {
        decision.question_number
        for decision in session.decisions
        if decision.status == "approved"
    }
    if not approved_numbers:
        raise ValueError("nenhuma questao foi aprovada para exportacao")
    approved_batch = session.batch.model_copy(deep=True)
    approved_batch.questions = [
        question
        for question in approved_batch.questions
        if question.number in approved_numbers
    ]
    if summary["rejected"]:
        approved_batch.processing_warnings.append(
            f"revisao local rejeitou {summary['rejected']} questoes; consulte a sessao de revisao"
        )
    normalized_notes = (notes or "").strip() or None
    approved_batch = approve_batch_model(approved_batch, reviewer, notes=normalized_notes)
    if output_path is None:
        output_path = Path("data/approved") / f"{approved_batch.batch_id}.json"
    write_json(output_path, approved_batch.model_dump(mode="json"))
    return approved_batch, output_path


def verify_review_session(session: LocalReviewSession) -> None:
    questions = {question.number: question for question in session.batch.questions}
    for decision in session.decisions:
        if decision.status == "pending":
            continue
        expected = question_content_sha256(questions[decision.question_number])
        if decision.content_sha256 != expected:
            raise ValueError(
                f"questao {decision.question_number}: conteudo mudou depois da decisao editorial"
            )


def _question_index(session: LocalReviewSession, question_number: int) -> int:
    for index, question in enumerate(session.batch.questions):
        if question.number == question_number:
            return index
    raise ValueError(f"questao {question_number} nao encontrada na sessao")


def _decision_index(session: LocalReviewSession, question_number: int) -> int:
    for index, decision in enumerate(session.decisions):
        if decision.question_number == question_number:
            return index
    raise ValueError(f"decisao da questao {question_number} nao encontrada")
