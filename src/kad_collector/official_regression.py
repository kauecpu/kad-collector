from __future__ import annotations

import hashlib
import json
import re
import tempfile
import tomllib
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pypdf import PdfReader

from .answer_key import AnswerEntry, parse_answer_key
from .desktop_parser import parse_question_document
from .fgv_parser import BankParsingContext
from .models import QuestionRecord

DocumentKind = Literal["exam", "answer_key"]
ContentKind = Literal["objective", "discursive", "answer_key"]
SupportStatus = Literal["supported", "inventory_only"]
AnswerKeyStatus = Literal["preliminary", "definitive", "rectified", "annulled"]
AccessPolicy = Literal["enforce", "observe", "ignore"]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RFB22_HEADER = re.compile(r"(?i)CONCURSO P[ÚU]BLICO DA RECEITA FEDERAL DO BRASIL")
_DISCURSIVE_HEADING = re.compile(r"(?im)^\s*Prova\s+Discursiva\s*$")
_DISCURSIVE_QUESTION = re.compile(r"(?im)^\s*Quest(?:ão|ao)\s+(\d{1,3})\b")
_TYPE_COLORS = {"branca": 1, "verde": 2, "amarela": 3, "azul": 4}


class OfficialRegressionError(ValueError):
    """Invalid official manifest or a failed offline regression assertion."""


class QuestionSectionSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["objective", "discursive"]
    first: int = Field(ge=1)
    last: int = Field(ge=1)
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> QuestionSectionSpec:
        if self.last < self.first:
            raise ValueError("section last must be greater than or equal to first")
        if self.count != self.last - self.first + 1:
            raise ValueError("section count must match its inclusive interval")
        return self


class AnswerScopeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str = Field(min_length=1)
    shift: str = Field(min_length=1)
    booklet_types: tuple[int, ...] = Field(min_length=1)
    first: int = Field(ge=1)
    last: int = Field(ge=1)
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> AnswerScopeSpec:
        if any(booklet_type < 1 for booklet_type in self.booklet_types):
            raise ValueError("answer scope booklet types must be positive")
        if len(self.booklet_types) != len(set(self.booklet_types)):
            raise ValueError("answer scope booklet types must be unique")
        if self.last < self.first:
            raise ValueError("answer scope last must be greater than or equal to first")
        if self.count != self.last - self.first + 1:
            raise ValueError("answer scope count must match its inclusive interval")
        return self


class ApplicationSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    application_date: date
    support_status: SupportStatus
    notes: str = Field(min_length=1)


class OfficialDocumentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    kind: DocumentKind
    support_status: SupportStatus
    path: Path
    source_url: str = Field(pattern=r"^https://")
    source_page_url: str = Field(pattern=r"^https://")
    evidence_urls: tuple[str, ...] = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    page_count: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    title: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    board: str = Field(min_length=1)
    contest_name: str = Field(min_length=1)
    contest_aliases: tuple[str, ...] = Field(min_length=1)
    notice_year: int = Field(ge=1900)
    application_id: str = Field(min_length=1)
    application_date: date
    application_year: int = Field(ge=1900)
    published_on: date
    roles: tuple[str, ...] = Field(min_length=1)
    stage: str = Field(min_length=1)
    shift: str | None = None
    booklet_type: int | None = Field(default=None, ge=1)
    content_kinds: tuple[ContentKind, ...] = Field(min_length=1)
    sections: tuple[QuestionSectionSpec, ...] = ()
    answer_scopes: tuple[AnswerScopeSpec, ...] = ()
    answer_key_id: str | None = None
    answer_key_status: AnswerKeyStatus | None = None
    extraction_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_document_contract(self) -> OfficialDocumentSpec:
        if self.path.is_absolute() or ".." in self.path.parts:
            raise ValueError("document path must stay relative to the manifest")
        if any(not url.startswith("https://") for url in self.evidence_urls):
            raise ValueError("evidence URLs must use HTTPS")
        if self.application_year != self.application_date.year:
            raise ValueError("application_year must match application_date")
        if self.kind == "exam":
            if self.answer_key_id is None:
                raise ValueError("exam document requires answer_key_id")
            if self.answer_key_status is not None or self.answer_scopes:
                raise ValueError("exam document cannot declare answer-key-only fields")
            if self.shift is None or self.booklet_type is None:
                raise ValueError("exam document requires shift and booklet_type")
            if not self.sections:
                raise ValueError("exam document requires question sections")
            if set(self.content_kinds) != {section.kind for section in self.sections}:
                raise ValueError("exam content_kinds must match its sections")
        else:
            if self.answer_key_status is None:
                raise ValueError("answer key requires answer_key_status")
            if self.answer_key_id is not None or self.sections:
                raise ValueError("answer key cannot declare exam-only fields")
            if self.content_kinds != ("answer_key",):
                raise ValueError("answer key content_kinds must contain only answer_key")
            if not self.answer_scopes:
                raise ValueError("answer key requires at least one answer scope")
        return self


class OfficialContestManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1]
    id: str = Field(min_length=1)
    contest_name: str = Field(min_length=1)
    contest_aliases: tuple[str, ...] = Field(min_length=1)
    organization: str = Field(min_length=1)
    board: str = Field(min_length=1)
    notice_year: int = Field(ge=1900)
    source_page_url: str = Field(pattern=r"^https://")
    evidence_urls: tuple[str, ...] = Field(min_length=1)
    robots_policy: AccessPolicy = "enforce"
    crawl_delay_policy: AccessPolicy = "enforce"
    policy_basis: str | None = None
    applications: tuple[ApplicationSpec, ...] = Field(min_length=1)
    documents: tuple[OfficialDocumentSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_relationships(self) -> OfficialContestManifest:
        if (
            self.robots_policy != "enforce" or self.crawl_delay_policy != "enforce"
        ) and not (self.policy_basis or "").strip():
            raise ValueError("non-enforcing access policy requires policy_basis")
        application_ids = [item.id for item in self.applications]
        if len(application_ids) != len(set(application_ids)):
            raise ValueError("application IDs must be unique")
        document_ids = [item.id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("document IDs must be unique")
        paths = [item.path.as_posix().casefold() for item in self.documents]
        if len(paths) != len(set(paths)):
            raise ValueError("document paths must be unique")

        applications = {item.id: item for item in self.applications}
        documents = {item.id: item for item in self.documents}
        for document in self.documents:
            application = applications.get(document.application_id)
            if application is None:
                raise ValueError(f"unknown application: {document.application_id}")
            if document.application_date != application.application_date:
                raise ValueError(f"application date mismatch: {document.id}")
            if document.stage != application.stage:
                raise ValueError(f"application stage mismatch: {document.id}")
            if document.support_status != application.support_status:
                raise ValueError(f"support status mismatch: {document.id}")
            if document.contest_name != self.contest_name:
                raise ValueError(f"contest name mismatch: {document.id}")
            if set(document.contest_aliases) != set(self.contest_aliases):
                raise ValueError(f"contest aliases mismatch: {document.id}")
            if document.organization != self.organization or document.board != self.board:
                raise ValueError(f"organization or board mismatch: {document.id}")
            if document.notice_year != self.notice_year:
                raise ValueError(f"notice year mismatch: {document.id}")
            if document.kind != "exam":
                continue
            answer_key = documents.get(document.answer_key_id or "")
            if answer_key is None or answer_key.kind != "answer_key":
                raise ValueError(f"invalid answer key relation: {document.id}")
            if answer_key.application_id != document.application_id:
                raise ValueError(f"answer key application mismatch: {document.id}")
            if not set(answer_key.roles).intersection(document.roles):
                raise ValueError(f"answer key role mismatch: {document.id}")
            scope = _answer_scope_for(answer_key, document)
            objective = next(
                (section for section in document.sections if section.kind == "objective"),
                None,
            )
            if scope is None or objective is None:
                raise ValueError(f"answer key scope missing: {document.id}")
            if (scope.first, scope.last, scope.count) != (
                objective.first,
                objective.last,
                objective.count,
            ):
                raise ValueError(f"answer key interval mismatch: {document.id}")
        return self


@dataclass(frozen=True)
class LoadedOfficialManifest:
    path: Path
    spec: OfficialContestManifest


@dataclass(frozen=True)
class Rfb22BookletIdentity:
    role: str
    shift: str
    booklet_type: int
    content_kinds: tuple[ContentKind, ...]
    discursive_numbers: tuple[int, ...]


def _answer_scope_for(
    answer_key: OfficialDocumentSpec, exam: OfficialDocumentSpec
) -> AnswerScopeSpec | None:
    if exam.shift is None or exam.booklet_type is None:
        return None
    role = exam.roles[0]
    return next(
        (
            scope
            for scope in answer_key.answer_scopes
            if scope.role == role
            and scope.shift == exam.shift
            and exam.booklet_type in scope.booklet_types
        ),
        None,
    )


def load_official_manifest(path: Path) -> LoadedOfficialManifest:
    resolved = path.resolve()
    try:
        payload = tomllib.loads(resolved.read_text(encoding="utf-8"))
        raw_documents = payload.get("documents")
        raw_applications = payload.get("applications")
        if not isinstance(raw_documents, list) or not isinstance(raw_applications, list):
            raise OfficialRegressionError("official manifest requires documents and applications")
        applications = {
            str(application["id"]): application
            for application in raw_applications
            if isinstance(application, dict) and "id" in application
        }
        common: dict[str, Any] = {
            "source_page_url": payload.get("source_page_url"),
            "evidence_urls": payload.get("evidence_urls"),
            "organization": payload.get("organization"),
            "board": payload.get("board"),
            "contest_name": payload.get("contest_name"),
            "contest_aliases": payload.get("contest_aliases"),
            "notice_year": payload.get("notice_year"),
        }
        for raw_document in raw_documents:
            if not isinstance(raw_document, dict):
                raise OfficialRegressionError("official document entries must be tables")
            document = cast(dict[str, Any], raw_document)
            for field, value in common.items():
                document.setdefault(field, value)
            application = applications.get(str(document.get("application_id", "")))
            if application is None:
                continue
            application_date = application.get("application_date")
            document.setdefault("application_date", application_date)
            document.setdefault("application_year", getattr(application_date, "year", None))
            document.setdefault("stage", application.get("stage"))
            document.setdefault("support_status", application.get("support_status"))
        spec = OfficialContestManifest.model_validate(payload)
    except (OSError, tomllib.TOMLDecodeError, ValidationError, OfficialRegressionError) as exc:
        raise OfficialRegressionError(f"invalid official manifest {path}: {exc}") from exc
    return LoadedOfficialManifest(path=resolved, spec=spec)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_official_fixture(document: OfficialDocumentSpec, root: Path) -> Path:
    path = (root / document.path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise OfficialRegressionError(f"fixture outside manifest root: {document.id}") from exc
    if not path.is_file():
        raise OfficialRegressionError(f"missing official fixture: {document.id} ({path})")
    if path.stat().st_size != document.size_bytes:
        raise OfficialRegressionError(f"official fixture size mismatch: {document.id}")
    digest = _sha256(path)
    if digest != document.sha256:
        raise OfficialRegressionError(
            f"official fixture SHA-256 mismatch: {document.id} ({digest})"
        )
    with path.open("rb") as handle:
        if not handle.read(5).startswith(b"%PDF-"):
            raise OfficialRegressionError(f"invalid PDF signature: {document.id}")
    return path


def _normalize_token(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def inspect_rfb22_booklet(pages: list[dict[str, object]]) -> Rfb22BookletIdentity:
    combined = "\n".join(str(page["text"]) for page in pages)
    if _RFB22_HEADER.search(combined) is None:
        raise OfficialRegressionError("PDF does not contain the official RFB22 header")
    normalized = _normalize_token(combined).casefold()
    header_match = re.search(
        r"(?im)^(?:cns\d+\s*-\s*)?"
        r"(?P<role>auditor-fiscal|analista-tributario)\s+da\s+receita\s+federal\s+"
        r"do\s+brasil[^\n]*?\btipo\s+(?P<color>branca|verde|amarela|azul)\b",
        normalized,
    )
    if header_match is None:
        raise OfficialRegressionError("RFB22 role or booklet color not recognized from header")
    role = (
        "Auditor-Fiscal da Receita Federal do Brasil"
        if header_match.group("role") == "auditor-fiscal"
        else "Analista-Tributário da Receita Federal do Brasil"
    )
    booklet_type = _TYPE_COLORS[header_match.group("color")]

    split = _DISCURSIVE_HEADING.split(combined, maxsplit=1)
    discursive_numbers = (
        tuple(int(value) for value in _DISCURSIVE_QUESTION.findall(split[1]))
        if len(split) == 2
        else ()
    )
    content_kinds: tuple[ContentKind, ...] = (
        ("objective", "discursive") if discursive_numbers else ("objective",)
    )
    return Rfb22BookletIdentity(
        role=role,
        shift="Tarde" if discursive_numbers else "Manhã",
        booklet_type=booklet_type,
        content_kinds=content_kinds,
        discursive_numbers=discursive_numbers,
    )


def _read_pdf_pages(path: Path) -> list[dict[str, object]]:
    reader = PdfReader(path, strict=False)
    return [
        {
            "page_number": number,
            "text": (page.extract_text() or "").replace(chr(0), ""),
        }
        for number, page in enumerate(reader.pages, start=1)
    ]


def _payload_sha256(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _question_digest(questions: list[QuestionRecord]) -> str:
    payload = [
        [
            question.number,
            question.statement,
            [[alternative.letter, alternative.text] for alternative in question.alternatives],
            question.source_pages,
        ]
        for question in questions
    ]
    return _payload_sha256(payload)


def _answer_digest(entries: dict[int, AnswerEntry]) -> str:
    payload = [
        [number, entry.answer, entry.annulled] for number, entry in sorted(entries.items())
    ]
    return _payload_sha256(payload)


def _number_diagnostics(numbers: list[int], first: int, last: int) -> dict[str, object]:
    counts = Counter(numbers)
    expected = set(range(first, last + 1))
    observed = set(numbers)
    return {
        "count": len(numbers),
        "first": min(numbers) if numbers else None,
        "last": max(numbers) if numbers else None,
        "missing": sorted(expected - observed),
        "duplicates": sorted(number for number, count in counts.items() if count > 1),
        "outside": sorted(observed - expected),
    }


def _assert_numbers(
    label: str, numbers: list[int], expected: QuestionSectionSpec | AnswerScopeSpec
) -> dict[str, object]:
    diagnostics = _number_diagnostics(numbers, expected.first, expected.last)
    if diagnostics != {
        "count": expected.count,
        "first": expected.first,
        "last": expected.last,
        "missing": [],
        "duplicates": [],
        "outside": [],
    }:
        raise OfficialRegressionError(f"{label} numbering mismatch: {diagnostics}")
    return diagnostics


def _execute_rfb22_exam(
    exam: OfficialDocumentSpec,
    exam_path: Path,
    answer_key: OfficialDocumentSpec,
    answer_key_text: str,
) -> dict[str, object]:
    pages = _read_pdf_pages(exam_path)
    if len(pages) != exam.page_count:
        raise OfficialRegressionError(
            f"page count mismatch: {exam.id} ({len(pages)} != {exam.page_count})"
        )
    parsing = parse_question_document(
        pages,
        BankParsingContext(
            document_id=exam.id,
            board=exam.board,
            provider="fgv_conhecimento",
            contest=exam.contest_aliases[0],
            application_id=exam.application_id,
            role=exam.roles[0],
            shift=exam.shift,
            booklet_type=exam.booklet_type,
        ),
    )
    identity = parsing.identity
    if identity.role not in exam.roles:
        raise OfficialRegressionError(f"role mismatch: {exam.id} ({identity.role})")
    if identity.shift != exam.shift:
        raise OfficialRegressionError(f"shift mismatch: {exam.id} ({identity.shift})")
    if identity.booklet_type != exam.booklet_type:
        raise OfficialRegressionError(
            f"booklet type mismatch: {exam.id} ({identity.booklet_type})"
        )
    content_kinds: tuple[ContentKind, ...] = (
        ("objective", "discursive")
        if parsing.discursive_numbers
        else ("objective",)
    )
    if content_kinds != exam.content_kinds:
        raise OfficialRegressionError(
            f"content kinds mismatch: {exam.id} ({content_kinds})"
        )
    configured_sections = tuple(
        (section.kind, section.first, section.last, section.count)
        for section in parsing.expected_intervals
    )
    manifest_sections = tuple(
        (section.kind, section.first, section.last, section.count)
        for section in exam.sections
    )
    if configured_sections != manifest_sections:
        raise OfficialRegressionError(
            f"FGV profile differs from official manifest: {exam.id}"
        )
    if parsing.status != "completed":
        reasons = [exception.reason for exception in parsing.exceptions]
        raise OfficialRegressionError(f"{exam.id} incomplete parsing: {reasons}")
    if "discursive" in exam.content_kinds and not any(
        section.kind == "answer_sheet" for section in parsing.sections
    ):
        raise OfficialRegressionError(f"answer sheet section not detected: {exam.id}")

    objective = next(section for section in exam.sections if section.kind == "objective")
    questions = list(parsing.objective_questions)
    objective_result = _assert_numbers(
        f"{exam.id} objective",
        [question.number for question in questions],
        objective,
    )
    if parsing.warnings:
        raise OfficialRegressionError(f"{exam.id} parser warnings: {parsing.warnings}")
    objective_result["digest"] = _question_digest(questions)
    if exam.extraction_sha256 and objective_result["digest"] != exam.extraction_sha256:
        raise OfficialRegressionError(
            f"extraction digest mismatch: {exam.id} ({objective_result['digest']})"
        )

    discursive = next(
        (section for section in exam.sections if section.kind == "discursive"),
        None,
    )
    if discursive is None:
        if parsing.discursive_numbers:
            raise OfficialRegressionError(f"unexpected discursive section: {exam.id}")
        discursive_result: dict[str, object] = {
            "count": 0,
            "first": None,
            "last": None,
            "missing": [],
            "duplicates": [],
            "outside": [],
        }
    else:
        discursive_result = _assert_numbers(
            f"{exam.id} discursive",
            list(parsing.discursive_numbers),
            discursive,
        )

    scope = _answer_scope_for(answer_key, exam)
    if scope is None:
        raise OfficialRegressionError(f"answer scope not found: {exam.id}")
    entries = parse_answer_key(
        answer_key_text,
        variant=f"Tipo {identity.booklet_type}",
        role=identity.role or exam.roles[0],
        turn=identity.shift,
    )
    answer_result = _assert_numbers(f"{exam.id} answer key", list(entries), scope)
    answer_result["annulled"] = sum(entry.annulled for entry in entries.values())
    answer_result["digest"] = _answer_digest(entries)

    return {
        "page_count": len(pages),
        "role": identity.role,
        "shift": identity.shift,
        "booklet_type": identity.booklet_type,
        "content_kinds": list(content_kinds),
        "adapter_id": parsing.adapter_id,
        "adapter_version": parsing.adapter_version,
        "profile_id": parsing.profile_id,
        "parsing_status": parsing.status,
        "sections": [asdict(section) for section in parsing.sections],
        "objective": objective_result,
        "discursive": discursive_result,
        "answer_key_id": answer_key.id,
        "answer_key": answer_result,
    }


def _execute_missing_marker_probe(
    exam: OfficialDocumentSpec, exam_path: Path
) -> dict[str, object]:
    pages = _read_pdf_pages(exam_path)
    objective = next(section for section in exam.sections if section.kind == "objective")
    target = objective.first + 1 if objective.count > 1 else objective.first
    marker = re.compile(rf"^\s*{target}\s*$")
    changed = False
    mutated_pages: list[dict[str, object]] = []
    for page in pages:
        lines: list[str] = []
        for line in str(page["text"]).splitlines():
            if not changed and marker.match(line):
                changed = True
                continue
            lines.append(line)
        mutated_pages.append(
            {
                "page_number": cast(int, page["page_number"]),
                "text": "\n".join(lines),
            }
        )
    if not changed:
        raise OfficialRegressionError(
            f"negative marker probe could not remove question {target}: {exam.id}"
        )
    result = parse_question_document(
        mutated_pages,
        BankParsingContext(
            document_id=f"{exam.id}-missing-{target}",
            board=exam.board,
            provider="fgv_conhecimento",
            contest=exam.contest_aliases[0],
            application_id=exam.application_id,
            role=exam.roles[0],
            shift=exam.shift,
            booklet_type=exam.booklet_type,
        ),
    )
    matching = [
        exception
        for exception in result.exceptions
        if exception.section == "objective"
        and exception.expected_number == target
        and exception.reason == "questão esperada não extraída"
    ]
    if result.status != "incomplete" or not matching:
        raise OfficialRegressionError(
            f"negative marker probe did not block completion: {exam.id}"
        )
    return {
        "document_id": exam.id,
        "removed_question": target,
        "status": result.status,
        "exception": asdict(matching[0]),
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def run_official_regression(manifest_path: Path, report_path: Path) -> dict[str, object]:
    loaded = load_official_manifest(manifest_path)
    root = loaded.path.parent
    documents = {document.id: document for document in loaded.spec.documents}
    supported_documents = [
        document
        for document in loaded.spec.documents
        if document.support_status == "supported"
    ]
    fixture_paths = {
        document.id: validate_official_fixture(document, root)
        for document in supported_documents
    }
    answer_key_texts: dict[str, str] = {}
    for document in supported_documents:
        if document.kind != "answer_key":
            continue
        pages = _read_pdf_pages(fixture_paths[document.id])
        if len(pages) != document.page_count:
            raise OfficialRegressionError(
                f"page count mismatch: {document.id} ({len(pages)} != {document.page_count})"
            )
        answer_key_texts[document.id] = "\n".join(str(page["text"]) for page in pages)

    cases: list[dict[str, object]] = []
    failures: list[str] = []
    for exam in supported_documents:
        if exam.kind != "exam":
            continue
        answer_key = documents[exam.answer_key_id or ""]
        try:
            result = _execute_rfb22_exam(
                exam,
                fixture_paths[exam.id],
                answer_key,
                answer_key_texts[answer_key.id],
            )
        except Exception as exc:
            message = f"{exam.id}: {type(exc).__name__}: {exc}"
            failures.append(message)
            cases.append({"id": exam.id, "status": "failed", "error": message})
        else:
            cases.append({"id": exam.id, "status": "passed", "result": result})

    first_exam = next(
        document for document in supported_documents if document.kind == "exam"
    )
    negative_probe = _execute_missing_marker_probe(
        first_exam, fixture_paths[first_exam.id]
    )

    passed = sum(case["status"] == "passed" for case in cases)
    report: dict[str, object] = {
        "schema_version": loaded.spec.schema_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": str(loaded.path),
        "manifest_sha256": _sha256(loaded.path),
        "offline": True,
        "applications": [
            application.model_dump(mode="json") for application in loaded.spec.applications
        ],
        "fixtures": [
            {
                "id": document.id,
                "path": document.path.as_posix(),
                "sha256": document.sha256,
                "size_bytes": document.size_bytes,
            }
            for document in supported_documents
        ],
        "cases": cases,
        "negative_marker_probe": negative_probe,
        "summary": {
            "supported_documents": len(supported_documents),
            "exam_cases": len(cases),
            "passed": passed,
            "failed": len(failures),
        },
    }
    _write_report(report_path, report)
    if failures:
        raise OfficialRegressionError("official regression failed:\n- " + "\n- ".join(failures))
    return report
