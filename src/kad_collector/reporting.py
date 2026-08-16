from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    DownloadManifest,
    ExtractionManifest,
    OrganizedQuestion,
    QuestionBatch,
    QuestionOrigin,
    QuestionRecord,
    QuestionReport,
    ReportArtifacts,
    RunMetrics,
)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())


def question_fingerprint(question: QuestionRecord) -> str:
    canonical = json.dumps(
        {
            "statement": _normalize(question.statement),
            "alternatives": [
                {"letter": item.letter, "text": _normalize(item.text)}
                for item in question.alternatives
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _origin(batch: QuestionBatch, question: QuestionRecord) -> QuestionOrigin:
    document = batch.source_document
    return QuestionOrigin(
        source_id=document.source_id,
        source_name=document.source_name,
        document_title=document.title,
        url=document.resolved_url,
        sha256=document.sha256,
        pages=question.source_pages,
    )


def _question_issues(
    question: QuestionRecord, *, require_answers: bool = False
) -> list[str]:
    issues = list(question.review_notes)
    if require_answers and question.answer_status == "missing":
        issues.append("gabarito ausente")
    required_metadata = (
        ("materia nao identificada", question.matter),
        ("banca nao identificada", question.board),
        ("concurso/orgao nao identificado", question.organization),
        ("cargo nao identificado", question.role),
        ("ano nao identificado", question.year),
    )
    for issue, value in required_metadata:
        if value is None or value == "":
            issues.append(issue)
    if not question.source_pages:
        issues.append("pagina de origem nao identificada")
    return list(dict.fromkeys(issues))


def _merge_duplicate(existing: OrganizedQuestion, candidate: QuestionRecord) -> None:
    if existing.question.number != candidate.number:
        existing.issues.append(
            f"numeracao divergente entre fontes: {existing.question.number} e {candidate.number}"
        )

    fields = ("matter", "subject", "board", "organization", "role", "year")
    labels = {
        "matter": "materia",
        "subject": "assunto",
        "board": "banca",
        "organization": "concurso/orgao",
        "role": "cargo",
        "year": "ano",
    }
    for field in fields:
        current = getattr(existing.question, field)
        incoming = getattr(candidate, field)
        if current in {None, ""} and incoming not in {None, ""}:
            setattr(existing.question, field, incoming)
        elif (
            current not in {None, ""}
            and incoming not in {None, ""}
            and _normalize(str(current)) != _normalize(str(incoming))
        ):
            existing.issues.append(f"{labels[field]} divergente entre fontes")

    existing.issues.extend(candidate.review_notes)
    existing.issues = list(dict.fromkeys(existing.issues))


def _sort_key(item: OrganizedQuestion) -> tuple[object, ...]:
    question = item.question

    def text_key(value: str | None) -> tuple[bool, str]:
        return (value is None, _normalize(value or ""))

    return (
        text_key(question.organization),
        text_key(question.role),
        text_key(question.board),
        (question.year is None, question.year or 9999),
        question.number,
    )


def _extraction_warnings(manifest: ExtractionManifest) -> list[str]:
    warnings: list[str] = []
    for document in manifest.documents:
        warnings.extend(f"{document.document.title}: {warning}" for warning in document.warnings)
    return warnings


def build_question_report(
    *,
    requested_urls: list[str],
    download_manifest: DownloadManifest,
    download_path: Path,
    extraction_manifest: ExtractionManifest,
    extraction_path: Path,
    batches: list[QuestionBatch],
    batch_paths: list[Path],
    review_queue_path: Path | None = None,
    require_answers: bool = False,
) -> QuestionReport:
    organized: dict[str, OrganizedQuestion] = {}
    total_candidates = 0
    filtered_out_questions = 0
    processing_warnings: list[str] = []

    for batch in batches:
        filtered_out_questions += batch.filtered_out_questions
        processing_warnings.extend(
            f"{batch.source_document.title}: {warning}" for warning in batch.processing_warnings
        )
        processing_warnings.extend(
            f"{batch.source_document.title}: validacao: {message}"
            for message in batch.validation.errors + batch.validation.warnings
        )
        for question in batch.questions:
            total_candidates += 1
            fingerprint = question_fingerprint(question)
            existing = organized.get(fingerprint)
            if existing is None:
                copied = question.model_copy(deep=True)
                organized[fingerprint] = OrganizedQuestion(
                    fingerprint=fingerprint,
                    question=copied,
                    origins=[_origin(batch, copied)],
                    issues=list(copied.review_notes),
                )
                continue
            _merge_duplicate(existing, question)
            origin = _origin(batch, question)
            if origin not in existing.origins:
                existing.origins.append(origin)

    questions: list[OrganizedQuestion] = []
    exceptions: list[OrganizedQuestion] = []
    for item in organized.values():
        item.issues = list(
            dict.fromkeys(
                item.issues
                + _question_issues(item.question, require_answers=require_answers)
            )
        )
        (exceptions if item.issues else questions).append(item)
    questions.sort(key=_sort_key)
    exceptions.sort(key=_sort_key)

    warnings = list(
        dict.fromkeys(
            download_manifest.warnings
            + _extraction_warnings(extraction_manifest)
            + processing_warnings
        )
    )
    duplicate_questions = total_candidates - len(organized)
    metrics = RunMetrics(
        requested_links=len(requested_urls),
        collected_documents=len(download_manifest.documents),
        duplicate_documents=download_manifest.duplicate_documents,
        filtered_out_documents=download_manifest.filtered_out_documents,
        documents_needing_ocr=sum(document.needs_ocr for document in extraction_manifest.documents),
        extracted_questions=total_candidates + filtered_out_questions,
        filtered_out_questions=filtered_out_questions,
        duplicate_questions=duplicate_questions,
        ready_questions=len(questions),
        exception_questions=len(exceptions),
    )
    return QuestionReport(
        run_id=str(uuid.uuid4()),
        created_at=datetime.now(UTC),
        requested_urls=list(dict.fromkeys(requested_urls)),
        filters=download_manifest.filters,
        questions=questions,
        exceptions=exceptions,
        metrics=metrics,
        collection_failures=download_manifest.failures,
        warnings=warnings,
        artifacts=ReportArtifacts(
            download_manifest=str(download_path),
            extraction_manifest=str(extraction_path),
            question_batches=[str(path) for path in batch_paths],
            review_queue=str(review_queue_path) if review_queue_path else None,
        ),
    )
