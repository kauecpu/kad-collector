from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from .editorial_export import EditorialExportException
from .json_utils import read_json, write_json
from .local_review import load_or_create_review_session
from .models import (
    Alternative,
    DocumentRecord,
    DownloadManifest,
    QuestionBatch,
    QuestionRecord,
    ReviewState,
    StrictModel,
)
from .operator_run import OperatorRunState
from .structured_questions import StructuredQuestion, StructuredQuestionPackage
from .validation import batch_content_sha256, validate_questions

OPERATOR_REVIEW_VERSION = "1.0"


class OperatorReviewBatch(StrictModel):
    batch_id: str
    exam_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    batch_path: str | None
    session_path: str | None
    exceptions_path: str
    accepted: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    rejected: int = Field(ge=0)


class OperatorReviewIndex(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    operator_run_id: str
    operator_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    batches: list[OperatorReviewBatch]
    pending_questions: int = Field(ge=0)
    quarantined_questions: int = Field(ge=0)
    rejected_questions: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _question_record(question: StructuredQuestion, *, state: str) -> QuestionRecord:
    notes = [
        f"Estado estrutural: {state}.",
        f"Identificador estruturado: {question.stable_id}.",
        f"SHA-256 da prova: {question.exam_sha256}.",
        f"SHA-256 do gabarito: {question.answer_key_sha256}.",
        f"Extração: {question.extraction_method}; parser: {question.parser_version}.",
        (
            "Associação de gabarito: "
            f"{question.answer_association.answer_key_id}; "
            f"{question.answer_association.reason}"
        ),
    ]
    if question.correct_answer_label:
        notes.append(f"Rótulo original do gabarito: {question.correct_answer_label}.")
    notes.extend(f"Validação estrutural: {reason}" for reason in question.validation_reasons)
    answer_status: Literal["missing", "matched", "annulled"] = "missing"
    if question.correct_answer is not None:
        answer_status = "matched"
    elif question.answer_association.original_answer == "X":
        answer_status = "annulled"
    statement = question.statement.strip()
    supporting_text = (question.supporting_text or "").strip()
    if supporting_text and supporting_text not in statement:
        statement = f"{supporting_text}\n\n{statement}"
    return QuestionRecord(
        source_stable_id=question.stable_id,
        number=question.original_number,
        statement=statement,
        alternatives=[
            Alternative(letter=letter, text=text)
            for letter, text in sorted(question.alternatives.items())
        ],
        matter=question.area,
        subject=question.block,
        board=question.board,
        organization=question.organization,
        role=question.role,
        year=question.year,
        source_pages=question.source_pages,
        discipline=None,
        concurso=question.contest,
        correct_answer=question.correct_answer,
        answer_status=answer_status,
        review_notes=notes,
        editorial_blocks=(
            list(question.validation_reasons) if state == "quarantined" else []
        ),
    )


def _annotated_document(
    document: DocumentRecord,
    *,
    run: OperatorRunState,
    package: StructuredQuestionPackage,
    document_type: Literal["exam", "answer_key"],
) -> DocumentRecord:
    metadata = {
        **document.metadata,
        "operator_original_document_type": document.document_type,
        "operator_run_id": run.run_id,
        "operator_package_sha256": package.content_sha256,
        "operator_review_bridge": OPERATOR_REVIEW_VERSION,
    }
    return document.model_copy(
        update={"document_type": document_type, "metadata": metadata}
    )


def _exception(question: StructuredQuestion) -> EditorialExportException:
    record = _question_record(question, state="rejected")
    issues = question.validation_reasons or ["Questão rejeitada pela validação estrutural."]
    return EditorialExportException(
        questionNumber=record.number,
        stableId=question.stable_id,
        issues=issues,
        question=record.model_dump(mode="json"),
    )


def _load_inputs(
    output_dir: Path,
) -> tuple[OperatorRunState, DownloadManifest, StructuredQuestionPackage]:
    state = OperatorRunState.model_validate(read_json(output_dir / "run.json"))
    manifest = DownloadManifest.model_validate(read_json(output_dir / "manifest.json"))
    package = StructuredQuestionPackage.model_validate(
        read_json(output_dir / "review-package.json")
    )
    if state.status != "completed":
        raise ValueError("a execução precisa estar concluída antes de abrir a revisão")
    if state.semantic_sha256 != package.content_sha256:
        raise ValueError("o pacote estruturado não corresponde ao estado da execução")
    return state, manifest, package


def build_operator_review_index(output_dir: Path) -> tuple[OperatorReviewIndex, Path]:
    output_dir = output_dir.resolve()
    run, manifest, package = _load_inputs(output_dir)
    documents = {document.sha256: document for document in manifest.documents}
    grouped: dict[str, dict[str, list[StructuredQuestion]]] = defaultdict(
        lambda: {"accepted": [], "quarantined": [], "rejected": []}
    )
    for state, questions in (
        ("accepted", package.accepted),
        ("quarantined", package.quarantined),
        ("rejected", package.rejected),
    ):
        for question in questions:
            grouped[question.exam_sha256][state].append(question)

    review_root = output_dir / "review"
    batch_dir = review_root / "batches"
    session_dir = review_root / "sessions"
    exception_dir = review_root / "exceptions"
    entries: list[OperatorReviewBatch] = []
    pending_total = 0
    quarantined_total = 0
    rejected_total = 0

    for exam_sha256 in sorted(grouped):
        states = grouped[exam_sha256]
        source = documents.get(exam_sha256)
        if source is None:
            raise ValueError(f"prova {exam_sha256} não está presente no manifesto")
        active = [*states["accepted"], *states["quarantined"]]
        numbers = [question.original_number for question in active]
        if len(numbers) != len(set(numbers)):
            raise ValueError(f"prova {exam_sha256} possui números de questão duplicados")

        batch_id = f"operator-{exam_sha256[:24]}"
        rejected = [_exception(question) for question in states["rejected"]]
        exceptions_path = exception_dir / f"{batch_id}.json"
        write_json(
            exceptions_path,
            [item.model_dump(mode="json", by_alias=True) for item in rejected],
        )

        batch_path: Path | None = None
        session_path: Path | None = None
        if active:
            answer_key_sha256s = {question.answer_key_sha256 for question in active}
            if len(answer_key_sha256s) != 1:
                raise ValueError(
                    f"prova {exam_sha256} possui mais de um gabarito associado no mesmo lote"
                )
            answer_key_sha256 = next(iter(answer_key_sha256s))
            answer_key = documents.get(answer_key_sha256)
            if answer_key is None:
                raise ValueError(f"gabarito {answer_key_sha256} não está presente no manifesto")
            review_questions = [
                _question_record(question, state="accepted")
                for question in states["accepted"]
            ] + [
                _question_record(question, state="quarantined")
                for question in states["quarantined"]
            ]
            review_questions.sort(key=lambda item: item.number)
            batch = QuestionBatch(
                batch_id=batch_id,
                created_at=manifest.created_at,
                model=f"structured:{package.parser_version}",
                source_document=_annotated_document(
                    source, run=run, package=package, document_type="exam"
                ),
                answer_key_document=_annotated_document(
                    answer_key,
                    run=run,
                    package=package,
                    document_type="answer_key",
                ),
                questions=review_questions,
                filters=manifest.filters,
                processing_warnings=[
                    f"Execução do operador: {run.run_id}.",
                    f"Pacote estruturado: {package.content_sha256}.",
                    f"{len(states['quarantined'])} questão(ões) exigem atenção estrutural.",
                    f"{len(states['rejected'])} questão(ões) foram mantidas nas exceções.",
                ],
                review=ReviewState(),
                validation=validate_questions(review_questions),
            )
            batch_path = batch_dir / f"{batch_id}.json"
            write_json(batch_path, batch.model_dump(mode="json"))
            version = batch_content_sha256(batch)[:12]
            session_path = session_dir / f"{batch_id}-{version}.json"
            load_or_create_review_session(batch_path, session_path)

        entries.append(
            OperatorReviewBatch(
                batch_id=batch_id,
                exam_sha256=exam_sha256,
                batch_path=str(batch_path) if batch_path is not None else None,
                session_path=str(session_path) if session_path is not None else None,
                exceptions_path=str(exceptions_path),
                accepted=len(states["accepted"]),
                quarantined=len(states["quarantined"]),
                rejected=len(states["rejected"]),
            )
        )
        pending_total += len(active)
        quarantined_total += len(states["quarantined"])
        rejected_total += len(states["rejected"])

    fingerprint_payload = {
        "operator_run_id": run.run_id,
        "operator_package_sha256": package.content_sha256,
        "batches": [
            {
                "batch_id": entry.batch_id,
                "exam_sha256": entry.exam_sha256,
                "accepted": entry.accepted,
                "quarantined": entry.quarantined,
                "rejected": entry.rejected,
            }
            for entry in entries
        ],
    }
    index = OperatorReviewIndex(
        operator_run_id=run.run_id,
        operator_package_sha256=package.content_sha256,
        created_at=run.created_at,
        batches=entries,
        pending_questions=pending_total,
        quarantined_questions=quarantined_total,
        rejected_questions=rejected_total,
        content_sha256=_canonical_sha256(fingerprint_payload),
    )
    index_path = review_root / "index.json"
    write_json(index_path, index.model_dump(mode="json"))
    run.artifacts.update(
        {
            "operator_review_index": str(index_path),
            "operator_review_root": str(review_root),
        }
    )
    write_json(output_dir / "run.json", run.model_dump(mode="json"))
    return index, index_path


def first_reviewable_batch(index: OperatorReviewIndex) -> OperatorReviewBatch | None:
    return next((entry for entry in index.batches if entry.batch_path is not None), None)
