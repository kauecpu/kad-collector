from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .json_utils import read_json, write_json
from .models import QuestionBatch, ReviewState
from .validation import batch_content_sha256, validate_questions


def approve_batch(
    input_path: Path,
    reviewer: str,
    *,
    notes: str | None = None,
    output_path: Path | None = None,
) -> tuple[QuestionBatch, Path]:
    reviewer = reviewer.strip()
    if len(reviewer) < 2:
        raise ValueError("informe o nome ou identificador do revisor")
    batch = QuestionBatch.model_validate(read_json(input_path))
    validation = validate_questions(batch.questions, require_answers=True)
    if not validation.valid:
        raise ValueError("o lote nao pode ser aprovado: " + "; ".join(validation.errors))
    batch.validation = validation
    batch.review = ReviewState(
        status="approved",
        reviewed_by=reviewer,
        reviewed_at=datetime.now(UTC),
        content_sha256=batch_content_sha256(batch),
        notes=notes,
    )
    if output_path is None:
        output_path = Path("data/approved") / f"{batch.batch_id}.json"
    write_json(output_path, batch.model_dump(mode="json"))
    return batch, output_path
