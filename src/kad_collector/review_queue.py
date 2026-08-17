from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
from .validation import batch_content_sha256, validate_questions

_VARIANT_PATTERN = re.compile(r"\bV[1-9]\d*\b", re.IGNORECASE)
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_TOKENS = {
    "arquivo",
    "caderno",
    "definitivo",
    "esperada",
    "esperadas",
    "fase",
    "files",
    "gabarito",
    "gabaritos",
    "pdf",
    "prova",
    "provas",
    "resposta",
    "respostas",
    "uploads",
    "wp",
    "content",
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def _document_tokens(document: DocumentRecord) -> set[str]:
    path = urlsplit(document.resolved_url).path
    tokens = set(_TOKEN_PATTERN.findall(_normalize(f"{document.title} {path}")))
    return {token for token in tokens if token not in _STOP_TOKENS and len(token) > 1}


def _variant(document: DocumentRecord) -> str | None:
    candidate = _normalize(f"{document.title} {document.resolved_url}")
    match = _VARIANT_PATTERN.search(candidate)
    return match.group(0).upper() if match else None


def _pair_score(exam: DocumentRecord, answer_key: DocumentRecord) -> int:
    common = _document_tokens(exam) & _document_tokens(answer_key)
    score = 0
    for token in common:
        if re.fullmatch(r"(?:[a-z]+)?20\d{2}", token):
            score += 4
        elif re.fullmatch(r"(?:v|p|d|cd)\d+", token):
            score += 3
        else:
            score += 1
    return score


def _select_answer_key(
    exam: DocumentRecord, candidates: list[ExtractedDocument]
) -> tuple[ExtractedDocument | None, list[str]]:
    same_source = [
        candidate
        for candidate in candidates
        if candidate.document.source_id == exam.source_id and not candidate.needs_ocr
    ]
    if not same_source:
        return None, ["nenhum gabarito textual encontrado para a fonte"]
    ranked = sorted(
        ((_pair_score(exam, candidate.document), candidate) for candidate in same_source),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best = ranked[0]
    if len(ranked) > 1 and best_score == 0:
        return None, ["gabaritos encontrados, mas nenhum corresponde claramente a prova"]
    tied = [candidate for score, candidate in ranked if score == best_score]
    if len(tied) > 1:
        titles = ", ".join(item.document.title for item in tied[:3])
        return None, [f"associacao de gabarito ambigua: {titles}"]
    return best, []


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
        answer_key, pairing_issues = _select_answer_key(batch.source_document, answer_keys)
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
