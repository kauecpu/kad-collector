from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .json_utils import read_json
from .models import QuestionBatch
from .validation import verify_approved_batch


@dataclass(frozen=True)
class StageResult:
    batch_id: str
    question_count: int
    inserted_count: int
    executed: bool


def stage_batch(path: Path, *, execute: bool = False) -> StageResult:
    batch = QuestionBatch.model_validate(read_json(path))
    verify_approved_batch(batch)
    if not execute:
        return StageResult(
            batch_id=batch.batch_id,
            question_count=len(batch.questions),
            inserted_count=0,
            executed=False,
        )

    database_url = os.environ.get("KAD_DATABASE_URL")
    if not database_url:
        raise RuntimeError("defina KAD_DATABASE_URL para executar a importacao em staging")
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "dependencia de banco ausente; execute pip install -e .[database]"
        ) from exc

    inserted = 0
    with (
        psycopg.connect(database_url, connect_timeout=10) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO collector.import_batches (
                id, source_sha256, source_url, source_title, authorization_basis,
                reviewed_by, reviewed_at, content_sha256, model, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                batch.batch_id,
                batch.source_document.sha256,
                batch.source_document.resolved_url,
                batch.source_document.title,
                batch.source_document.authorization_basis,
                batch.review.reviewed_by,
                batch.review.reviewed_at,
                batch.review.content_sha256,
                batch.model,
                batch.created_at,
            ),
        )
        for question in batch.questions:
            question_id = str(uuid.uuid5(uuid.UUID(batch.batch_id), str(question.number)))
            cursor.execute(
                """
                INSERT INTO collector.question_staging (
                    id, batch_id, question_number, statement, alternatives,
                    correct_answer, answer_status, matter, subject, board,
                    organization, role, year, source_pages, review_notes
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    question_id,
                    batch.batch_id,
                    question.number,
                    question.statement,
                    Jsonb([item.model_dump(mode="json") for item in question.alternatives]),
                    question.correct_answer,
                    question.answer_status,
                    question.matter,
                    question.subject,
                    question.board,
                    question.organization,
                    question.role,
                    question.year,
                    question.source_pages,
                    question.review_notes,
                ),
            )
            inserted += max(cursor.rowcount, 0)
    return StageResult(
        batch_id=batch.batch_id,
        question_count=len(batch.questions),
        inserted_count=inserted,
        executed=True,
    )
