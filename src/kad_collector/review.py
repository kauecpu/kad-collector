from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .json_utils import read_json, write_json
from .models import QuestionBatch, ReviewState
from .validation import batch_content_sha256, validate_questions


def approve_batch_model(
    batch: QuestionBatch,
    reviewer: str,
    *,
    notes: str | None = None,
) -> QuestionBatch:
    reviewer = reviewer.strip()
    if len(reviewer) < 2:
        raise ValueError("informe o nome ou identificador do revisor")
    approved = batch.model_copy(deep=True)
    validation = validate_questions(
        approved.questions, require_answers=True, require_editorial=True
    )
    if not validation.valid:
        raise ValueError("o lote nao pode ser aprovado: " + "; ".join(validation.errors))
    approved.validation = validation
    approved.review = ReviewState(
        status="approved",
        reviewed_by=reviewer,
        reviewed_at=datetime.now(UTC),
        content_sha256=batch_content_sha256(approved),
        notes=notes,
    )
    return approved


def approve_batch(
    input_path: Path,
    reviewer: str,
    *,
    notes: str | None = None,
    output_path: Path | None = None,
) -> tuple[QuestionBatch, Path]:
    batch = QuestionBatch.model_validate(read_json(input_path))
    batch = approve_batch_model(batch, reviewer, notes=notes)
    if output_path is None:
        output_path = Path("data/approved") / f"{batch.batch_id}.json"
    write_json(output_path, batch.model_dump(mode="json"))
    return batch, output_path
