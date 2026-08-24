from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .answer_key import apply_answer_entries, parse_answer_key
from .json_utils import read_json, write_json
from .local_review import load_or_create_review_session
from .models import (
    DocumentRecord,
    ExtractedDocument,
    ExtractionManifest,
    QuestionBatch,
    ReviewQueue,
    ReviewQueueItem,
)
from .semantic_identity import (
    AssociationCandidate,
    QuestionInterval,
    profile_from_document_record,
)
from .semantic_resolution import select_answer_key
from .validation import batch_content_sha256, validate_questions

_VARIANT_PATTERN = re.compile(r"\b(?:V[1-9]\d*|TIPO\s*[1-9]\d*)\b", re.IGNORECASE)


def _metadata_value(document: DocumentRecord, *names: str) -> str | None:
    for name in names:
        value = document.metadata.get(name)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _variant(document: DocumentRecord) -> str | None:
    candidate = f"{_metadata_value(document, 'variant', 'tipo') or ''} {document.title}"
    match = _VARIANT_PATTERN.search(candidate)
    return " ".join(match.group(0).split()).title() if match else None


def _select_answer_key(
    exam: DocumentRecord,
    candidates: list[ExtractedDocument],
    exam_content: str = "",
    exam_numbers: list[int] | None = None,
) -> tuple[ExtractedDocument | None, list[str]]:
    textual = [
        candidate
        for candidate in candidates
        if candidate.document.document_type == "answer_key"
        and not candidate.needs_ocr
        and candidate.text.strip()
    ]
    if not textual:
        return None, ["nenhum gabarito oficial textual encontrado"]
    exam_profile = profile_from_document_record(exam, [(1, exam_content)])
    def interval(numbers: list[int]) -> QuestionInterval | None:
        ordered = sorted(set(numbers))
        if not ordered or len(ordered) != ordered[-1] - ordered[0] + 1:
            return None
        return QuestionInterval(first=ordered[0], last=ordered[-1])

    variant = _variant(exam)
    role = _metadata_value(exam, "role", "cargo")
    turn = _metadata_value(exam, "turn", "turno")
    association_candidates = []
    for item in textual:
        entries = parse_answer_key(item.text, variant=variant, role=role, turn=turn)
        association_candidates.append(
            AssociationCandidate(
                version_id=item.document.sha256,
                profile=profile_from_document_record(item.document, [(1, item.text)]),
                question_interval=(interval(list(entries)) if exam_numbers is not None else None),
            )
        )
    decision = select_answer_key(
        exam_profile,
        association_candidates,
        exam_interval=(interval(exam_numbers) if exam_numbers is not None else None),
    )
    if decision.outcome == "ambiguous":
        titles = ", ".join(item.document.title for item in textual[:3])
        return None, [f"associacao de gabarito ambigua: {titles}"]
    if decision.selected_version_id is None and decision.outcome == "insufficient_evidence":
        return None, ["gabaritos encontrados, mas nenhum corresponde claramente a prova"]
    if decision.selected_version_id is None and decision.outcome == "conflict":
        return None, ["gabaritos encontrados, mas nenhum corresponde claramente a prova"]
    if decision.selected_version_id is None and decision.outcome == "incomplete":
        return None, [
            "gabaritos encontrados, mas nenhum corresponde claramente; "
            "associacao incompleta, revise metadados e intervalos"
        ]
    if decision.selected_version_id is not None:
        return next(
            item for item in textual if item.document.sha256 == decision.selected_version_id
        ), []
    titles = ", ".join(item.document.title for item in textual[:3])
    return None, [f"associacao de gabarito ambigua: {titles}"]


def prepare_review_queue(
    *,
    extraction_path: Path,
    batch_paths: list[Path],
    data_dir: Path,
    output_path: Path | None = None,
    now: datetime | None = None,
    answer_key_documents: list[ExtractedDocument] | None = None,
) -> tuple[ReviewQueue, Path]:
    created_at = now or datetime.now(UTC)
    extraction = ExtractionManifest.model_validate(read_json(extraction_path))
    answer_keys = answer_key_documents or [
        document
        for document in extraction.documents
        if document.document.document_type == "answer_key"
    ]
    items: list[ReviewQueueItem] = []
    reviewed_dir = data_dir / "reviewed"
    sessions_dir = data_dir / "reviews"

    for batch_path in batch_paths:
        batch = QuestionBatch.model_validate(read_json(batch_path))
        issues: list[str] = []
        matched_paths: list[str] = []
        exam_content = "\n".join(
            " ".join(
                [question.statement, *[item.text for item in question.alternatives]]
            )
            for question in batch.questions
        )
        answer_key, pairing_issues = _select_answer_key(
            batch.source_document,
            answer_keys,
            exam_content,
            [question.number for question in batch.questions],
        )
        issues.extend(pairing_issues)
        updated = batch
        if answer_key is not None:
            entries = parse_answer_key(
                answer_key.text,
                variant=_variant(batch.source_document),
            )
            matched_paths.append(answer_key.document.local_path)
            if entries:
                updated = apply_answer_entries(batch, entries)
                updated.answer_key_document = answer_key.document
            else:
                issues.append("gabarito associado, mas nenhuma resposta A-H foi reconhecida")

        answer_validation = validate_questions(
            updated.questions, require_answers=True, require_editorial=True
        )
        missing_answers = sum(
            question.answer_status == "missing" for question in updated.questions
        )
        matched_answers = len(updated.questions) - missing_answers
        if missing_answers:
            issues.append(f"{missing_answers} questoes ainda estao sem resposta")
        issues.extend(answer_validation.errors)
        issues.extend(answer_validation.warnings)
        issues = list(dict.fromkeys(issues))

        reviewed_path = reviewed_dir / f"auto-{batch_path.name}"
        write_json(reviewed_path, updated.model_dump(mode="json"))
        session_path = sessions_dir / f"{updated.batch_id}.json"
        try:
            load_or_create_review_session(reviewed_path, session_path)
        except ValueError as exc:
            issues.append(f"nova versao criou outra sessao: {exc}")
            version = batch_content_sha256(updated)[:12]
            session_path = sessions_dir / f"{updated.batch_id}-{version}.json"
            load_or_create_review_session(reviewed_path, session_path)
        status: Literal["ready", "exception"] = (
            "ready" if answer_validation.valid else "exception"
        )
        items.append(
            ReviewQueueItem(
                batch_id=updated.batch_id,
                source_id=updated.source_document.source_id,
                source_title=updated.source_document.title,
                batch_path=str(reviewed_path),
                session_path=str(session_path),
                answer_key_paths=matched_paths,
                status=status,
                question_count=len(updated.questions),
                matched_answers=matched_answers,
                missing_answers=missing_answers,
                issues=issues,
            )
        )

    queue = ReviewQueue(
        created_at=created_at,
        extraction_manifest=str(extraction_path),
        items=items,
    )
    if output_path is None:
        timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
        output_path = sessions_dir / f"queue-{timestamp}.json"
    write_json(output_path, queue.model_dump(mode="json"))
    return queue, output_path
