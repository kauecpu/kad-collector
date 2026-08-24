from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field

from .json_utils import write_json, write_json_lines
from .models import DocumentRecord, QuestionBatch, QuestionRecord, StrictModel
from .validation import validate_editorial_question, verify_approved_batch


class EditorialAlternative(StrictModel):
    id: Literal["A", "B", "C", "D", "E"]
    text: str = Field(min_length=1)


class EditorialCanonicalIdentity(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contest_id: str = Field(alias="contestId", min_length=1)
    contest_key: str = Field(alias="contestKey", min_length=1)
    contest_name: str = Field(alias="contestName", min_length=1)
    application_id: str = Field(alias="applicationId", min_length=1)
    application_key: str = Field(alias="applicationKey", min_length=1)
    application_name: str = Field(alias="applicationName", min_length=1)
    document_id: str = Field(alias="documentId", min_length=1)
    scope_ids: list[str] = Field(alias="scopeIds", min_length=1)
    aliases: list[str] = Field(default_factory=list)


class EditorialQuestionData(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,119}$")
    discipline: str
    subject: str
    topic: str
    board: str
    year: int = Field(ge=1900, le=2100)
    role: str
    institution: str
    concurso: str
    level: Literal["Fundamental", "Médio", "Superior"]
    difficulty: Literal["Fácil", "Média", "Difícil"]
    statement: str
    alternatives: list[EditorialAlternative] = Field(min_length=2, max_length=5)
    correct: Literal["A", "B", "C", "D", "E"]
    explanation: str
    canonical_identity: EditorialCanonicalIdentity | None = Field(
        default=None, alias="canonicalIdentity"
    )
    publication_status: Literal["draft"] = Field(alias="publicationStatus")


class EditorialSource(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    provider: str
    external_id: str = Field(alias="externalId")
    url: str
    collected_at: datetime = Field(alias="collectedAt")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class EditorialImportRecordV1(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    kind: Literal["question"]
    source: EditorialSource
    data: EditorialQuestionData


class EditorialExportException(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_number: int = Field(alias="questionNumber", ge=1)
    stable_id: str | None = Field(default=None, alias="stableId")
    issues: list[str] = Field(min_length=1)
    question: dict[str, Any]


@dataclass(frozen=True)
class EditorialExportResult:
    directory: Path
    questions_path: Path
    exceptions_path: Path
    exported_count: int
    exception_count: int


def _slug(value: str, *, maximum: int) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")
    return (slug or "fonte")[:maximum].rstrip("-")


def stable_question_id(batch: QuestionBatch, question: QuestionRecord) -> str:
    provider = _slug(batch.source_document.source_id, maximum=72)
    proof = batch.source_document.sha256[:12]
    return f"q-{provider}-{proof}-{question.number}"


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_errors(document: DocumentRecord) -> list[str]:
    parsed = urlsplit(document.resolved_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ["source.url deve ser uma URL HTTPS publica sem credenciais"]
    return []


def build_editorial_record(
    batch: QuestionBatch,
    question: QuestionRecord,
    *,
    canonical_identity: dict[str, Any] | EditorialCanonicalIdentity | None = None,
) -> EditorialImportRecordV1:
    issues = [*validate_editorial_question(question), *_source_errors(batch.source_document)]
    if issues:
        raise ValueError("; ".join(issues))

    stable_id = stable_question_id(batch, question)
    canonical_value = (
        EditorialCanonicalIdentity.model_validate(canonical_identity)
        if isinstance(canonical_identity, dict)
        else canonical_identity
    )
    data = EditorialQuestionData(
        id=stable_id,
        discipline=question.discipline or "",
        subject=question.matter or "",
        topic=question.subject or "",
        board=question.board or "",
        year=question.year or 0,
        role=question.role or "",
        institution=question.organization or "",
        concurso=(
            canonical_value.contest_name
            if canonical_value is not None
            else question.concurso or ""
        ),
        level=question.level,  # type: ignore[arg-type]
        difficulty=question.difficulty,  # type: ignore[arg-type]
        statement=question.statement.strip(),
        alternatives=[
            EditorialAlternative(id=item.letter, text=item.text.strip())  # type: ignore[arg-type]
            for item in question.alternatives
        ],
        correct=question.correct_answer,  # type: ignore[arg-type]
        explanation=(question.explanation or "").strip(),
        canonicalIdentity=canonical_value,
        publicationStatus="draft",
    )
    fingerprint_payload = data.model_dump(mode="json", by_alias=True, exclude_none=True)
    fingerprint_payload.pop("id")
    fingerprint = _canonical_sha256(fingerprint_payload)
    provider = _slug(batch.source_document.source_id, maximum=100)
    return EditorialImportRecordV1(
        schemaVersion=1,
        kind="question",
        source=EditorialSource(
            provider=provider,
            externalId=(
                f"{provider}:{batch.source_document.sha256}:question:{question.number}"
            ),
            url=batch.source_document.resolved_url,
            collectedAt=batch.source_document.downloaded_at,
            fingerprint=fingerprint,
        ),
        data=data,
    )


def _exception(
    question: QuestionRecord, issues: list[str], *, stable_id: str | None = None
) -> EditorialExportException:
    return EditorialExportException(
        questionNumber=question.number,
        stableId=stable_id,
        issues=list(dict.fromkeys(issues)),
        question=question.model_dump(mode="json"),
    )


def _verify_and_copy_evidence(document: DocumentRecord, destination: Path) -> Path:
    source = Path(document.local_path)
    if not source.is_file():
        raise ValueError(f"PDF de evidencia nao encontrado: {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != document.sha256 or source.stat().st_size != document.size_bytes:
        raise ValueError(f"PDF de evidencia diverge do manifesto: {source}")
    evidence_path = destination / f"{document.document_type}-{document.sha256[:16]}.pdf"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, evidence_path)
    return evidence_path


def export_admin_package(
    batch: QuestionBatch,
    *,
    output_root: Path = Path("data/exports"),
    additional_exceptions: list[EditorialExportException] | None = None,
    now: datetime | None = None,
) -> EditorialExportResult:
    verify_approved_batch(batch)
    directory = output_root / batch.batch_id
    questions_path = directory / "questoes.jsonl"
    exceptions_path = directory / "excecoes" / "questoes.jsonl"

    records: list[EditorialImportRecordV1] = []
    exceptions = list(additional_exceptions or [])
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for question in batch.questions:
        stable_id = stable_question_id(batch, question)
        issues = [*validate_editorial_question(question), *_source_errors(batch.source_document)]
        if issues:
            exceptions.append(_exception(question, issues, stable_id=stable_id))
            continue
        record = build_editorial_record(batch, question)
        duplicate_issues: list[str] = []
        if record.data.id in seen_ids:
            duplicate_issues.append("ID estavel duplicado no lote")
        if record.source.fingerprint in seen_fingerprints:
            duplicate_issues.append("conteudo duplicado no lote")
        if duplicate_issues:
            exceptions.append(_exception(question, duplicate_issues, stable_id=stable_id))
            continue
        seen_ids.add(record.data.id)
        seen_fingerprints.add(record.source.fingerprint)
        records.append(record)

    record_payloads = [
        item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in records
    ]
    exception_payloads = [
        item.model_dump(mode="json", by_alias=True) for item in exceptions
    ]
    write_json_lines(questions_path, record_payloads)
    write_json_lines(exceptions_path, exception_payloads)

    evidence_documents = [batch.source_document]
    if batch.answer_key_document is not None:
        evidence_documents.append(batch.answer_key_document)
    copied_evidence = [
        _verify_and_copy_evidence(document, directory / "fontes")
        for document in evidence_documents
    ]
    created_at = now or datetime.now(UTC)
    write_json(
        directory / "relatorio.json",
        {
            "schemaVersion": 1,
            "batchId": batch.batch_id,
            "createdAt": created_at.isoformat(),
            "exported": len(records),
            "exceptions": len(exceptions),
            "exceptionItems": exception_payloads,
        },
    )
    write_json(
        directory / "manifesto.json",
        {
            "schemaVersion": 1,
            "batchId": batch.batch_id,
            "createdAt": created_at.isoformat(),
            "importFile": "questoes.jsonl",
            "files": [
                {
                    "path": str(path.relative_to(directory)).replace("\\", "/"),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "sizeBytes": path.stat().st_size,
                }
                for path in [questions_path, exceptions_path, *copied_evidence]
            ],
        },
    )
    return EditorialExportResult(
        directory=directory,
        questions_path=questions_path,
        exceptions_path=exceptions_path,
        exported_count=len(records),
        exception_count=len(exceptions),
    )


def rejected_question_exception(
    question: QuestionRecord, *, reason: str
) -> EditorialExportException:
    return _exception(question, [reason])
