from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Literal, cast
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import Field

from .answer_key import adapt_true_false_entries, parse_answer_key
from .desktop_parser import cebraspe_question_contexts, parse_question_document
from .fgv_parser import BankParsingContext
from .json_utils import read_json, write_json
from .models import (
    DocumentRecord,
    DownloadManifest,
    ExtractedDocument,
    ExtractedPage,
    ExtractionManifest,
    StrictModel,
)
from .pdf_extractor import extract_manifest

STRUCTURED_PACKAGE_VERSION = "1.0"
STRUCTURED_PARSER_VERSION = "cebraspe-true-false-2.0"
DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_QWEN_MODEL = "qwen3:8b"

QuestionFormat = Literal["true_false", "multiple_choice"]
ValidationStatus = Literal["accepted", "quarantined", "rejected"]
AlternativeLetter = Literal["A", "B", "C", "D", "E", "F", "G", "H"]
ExtractionMethod = Literal["text", "ocr", "mixed", "unreadable"]
OriginalAnswer = Literal["A", "B", "C", "D", "E", "F", "G", "H", "X"]


class PageExtractionTrace(StrictModel):
    page_number: int = Field(ge=1)
    method: Literal["text", "ocr", "unreadable"]
    character_count: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    duration_ms: int = Field(ge=0)
    ocr_reason: str | None = None


class AnswerAssociationTrace(StrictModel):
    answer_key_id: str
    original_answer: OriginalAnswer | None
    internal_answer: AlternativeLetter | None
    confidence: Literal["high"] = "high"
    reason: str


class StructuredQuestion(StrictModel):
    stable_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    board: str
    organization: str
    contest: str
    year: int = Field(ge=1900, le=2100)
    role: str
    area: str | None = None
    block: str | None = None
    original_number: int = Field(ge=1)
    statement: str = Field(min_length=1)
    alternatives: dict[AlternativeLetter, str]
    question_format: QuestionFormat
    correct_answer: AlternativeLetter | None
    correct_answer_label: str | None
    supporting_text: str | None
    exam_url: str
    answer_key_url: str
    exam_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer_key_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_pages: list[int]
    extraction_method: ExtractionMethod
    parser_version: str
    validation_status: ValidationStatus
    validation_reasons: list[str] = Field(default_factory=list)
    answer_association: AnswerAssociationTrace


class PackageError(StrictModel):
    stage: Literal["manifest", "pairing", "extraction", "parsing", "validation"]
    document_id: str | None = None
    message: str
    isolated: bool = True


class QwenDecisionTrace(StrictModel):
    reason: str
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    response: dict[str, object]
    accepted: bool
    duration_ms: int = Field(ge=0)
    model: str


class QwenRuntimeTrace(StrictModel):
    endpoint: str
    model: str
    available: bool
    calls: list[QwenDecisionTrace] = Field(default_factory=list)


class QwenRecoveredQuestion(StrictModel):
    number: int = Field(ge=1)
    statement: str = Field(min_length=10)
    alternatives: dict[AlternativeLetter, str] = Field(default_factory=dict)
    supporting_text: str | None = None
    block: str | None = None
    source_pages: list[int] = Field(min_length=1)


class ExamProcessingMetrics(StrictModel):
    exam_id: str
    title: str
    expected_questions: int = Field(ge=0)
    detected_questions: int = Field(ge=0)
    accepted_questions: int = Field(ge=0)
    quarantined_questions: int = Field(ge=0)
    rejected_questions: int = Field(ge=0)
    segmentation_precision: float = Field(ge=0.0, le=1.0)
    segmentation_coverage: float = Field(ge=0.0, le=1.0)
    associated_answers: int = Field(ge=0)
    missing_answers: int = Field(ge=0)
    incorrect_associations: int = Field(ge=0)
    duplicate_questions: int = Field(ge=0)
    ocr_pages: int = Field(ge=0)
    qwen_calls: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    intervention_free: bool


class StructuredPackageMetrics(StrictModel):
    documents_processed: int = Field(ge=0)
    exams_processed: int = Field(ge=0)
    answer_keys_processed: int = Field(ge=0)
    expected_questions: int = Field(ge=0)
    detected_questions: int = Field(ge=0)
    accepted_questions: int = Field(ge=0)
    quarantined_questions: int = Field(ge=0)
    rejected_questions: int = Field(ge=0)
    segmentation_precision: float = Field(ge=0.0, le=1.0)
    segmentation_coverage: float = Field(ge=0.0, le=1.0)
    associated_answers: int = Field(ge=0)
    missing_answers: int = Field(ge=0)
    duplicate_questions: int = Field(ge=0)
    ocr_pages: int = Field(ge=0)
    qwen_calls: int = Field(ge=0)
    intervention_free_percent: float = Field(ge=0.0, le=1.0)
    duration_ms: int = Field(ge=0)


class StructuredQuestionPackage(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    parser_version: str
    input_manifest_sha256s: list[str]
    accepted: list[StructuredQuestion]
    quarantined: list[StructuredQuestion]
    rejected: list[StructuredQuestion]
    errors: list[PackageError]
    page_extraction: dict[str, list[PageExtractionTrace]]
    qwen: QwenRuntimeTrace
    exams: list[ExamProcessingMetrics]
    metrics: StructuredPackageMetrics
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", plain).casefold().split())


def _metadata_value(document: DocumentRecord, *keys: str) -> str | None:
    for key in keys:
        value = document.metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _contest_slug(document: DocumentRecord) -> str:
    declared = _metadata_value(document, "contest", "concurso", "event", "evento")
    if declared is not None:
        return _normalize(declared).replace(" ", "_")

    candidates = [
        _metadata_value(document, "canonical_url"),
        document.resolved_url,
        document.original_url,
    ]
    for candidate in filter(None, candidates):
        parsed = urlparse(candidate)
        match = re.search(r"(?i)/concursos?/([a-z0-9_-]+)/?", parsed.path)
        if match is not None:
            return match.group(1).casefold()
        file_value = parse_qs(parsed.query).get("file", [""])[0]
        file_path = unquote(file_value).replace("\\", "/")
        match = re.search(r"(?i)(?:^|/)([a-z]{2,}[a-z0-9_-]*\d{2,})(?:/|$)", file_path)
        if match is not None:
            return match.group(1).casefold()
    raise ValueError("URL oficial não contém o identificador do concurso")


def _year(document: DocumentRecord) -> int:
    declared = _metadata_value(document, "year", "ano", "ano_publicacao")
    if declared is not None and re.fullmatch(r"(?:19|20)\d{2}", declared):
        return int(declared)
    slug = _contest_slug(document)
    match = re.search(r"(?:^|_)(\d{2})(?:_|$)", slug)
    if match is None:
        raise ValueError("ano do concurso não identificado")
    suffix = int(match.group(1))
    return 2000 + suffix if suffix < 80 else 1900 + suffix


def _pair_key(document: DocumentRecord) -> str:
    title = _normalize(document.title)
    cargo = re.search(r"\bcargo\s+(\d{1,3})\b", title)
    if "conhecimentos basicos" in title:
        block = re.search(r"\bbloco\s+([ivx]+)\b", title)
        if block is not None:
            return f"conhecimentos_basicos_bloco_{block.group(1)}"
        if "perito" in title:
            return "conhecimentos_basicos_peritos"
        cargo_range = re.search(
            r"\bcargos?\s+(?:de\s+)?(\d{1,3})\s+a\s+(\d{1,3})\b", title
        )
        if cargo_range is not None:
            return f"conhecimentos_basicos_cargos_{cargo_range.group(1)}_{cargo_range.group(2)}"
        if cargo is not None:
            return f"conhecimentos_basicos_cargo_{int(cargo.group(1))}"
        return "conhecimentos_basicos"
    if cargo is not None:
        return f"cargo_{int(cargo.group(1))}"
    generic = re.sub(
        r"^(?:gabarito(?: oficial)?(?: preliminar| definitivo)?|prova objetiva)\s+",
        "",
        title,
    )
    generic = re.sub(r"\s+gabarito\s+\d{1,3}$", "", generic)
    generic = re.sub(r"\s+tipo\s+\d{1,3}$", "", generic)
    generic = re.sub(r"^prova\s+(?!(?:[a-z])\b)", "", generic)
    generic = generic.strip()
    if len(generic) >= 4:
        return generic.replace(" ", "_")
    raise ValueError(f"identidade de prova/gabarito não reconhecida: {document.title}")


def _variant(document: DocumentRecord) -> str | None:
    declared = _metadata_value(document, "variant", "versao", "tipo", "booklet_type")
    if declared is not None:
        match = re.search(r"[1-9]\d*", declared)
        if match is not None:
            return f"Tipo {int(match.group(0))}"
    title = _normalize(document.title)
    match = re.search(r"(?:gabarito|tipo)\s+([1-9]\d*)$", title)
    return f"Tipo {int(match.group(1))}" if match is not None else None


def _content_document_type(
    document: ExtractedDocument,
) -> Literal["exam", "answer_key"]:
    """Classify by PDF structure when a portal's labels are misleading.

    Public archives sometimes call the booklet variant a "gabarito" and place
    the consolidated answer grid under a path named "provas". Ten or more
    unambiguous numbered answers are strong enough to treat the file as a key;
    otherwise a declared key with substantial question text is treated as an
    exam. The original title, URL and provenance remain untouched.
    """
    entries = parse_answer_key(
        document.text,
        variant=_variant(document.document),
        role=_role(document.document),
    )
    answer_density = len(document.text) / max(1, len(entries))
    answer_grid_headings = len(
        re.findall(r"(?im)^\s*GABARITO\s+[1-9]\d*\s*$", document.text)
    )
    explicit_question = re.search(
        r"(?im)^\s*QUEST(?:ÃO|AO|\.)\s*\d{1,3}\b", document.text
    )
    alternative_lines = len(
        re.findall(r"(?im)^\s*(?:\(?[A-E]\)|[A-E][.:-])\s+\S", document.text)
    )
    if document.document.document_type == "answer_key" and len(document.text) < 5_000:
        # Gabaritos agregados de algumas bancas usam "QUESTÃO" como cabeçalho de
        # coluna. Só revertemos a declaração do portal quando também existe a
        # estrutura inequívoca de um enunciado com alternativas.
        return (
            "exam"
            if explicit_question is not None and alternative_lines >= 2
            else "answer_key"
        )
    if len(entries) >= 10 and (answer_grid_headings >= 2 or answer_density < 150):
        return "answer_key"
    if document.document.document_type == "answer_key":
        if len(document.text) >= 10_000 or explicit_question is not None:
            return "exam"
        return "answer_key"
    return "exam"


def _with_content_document_type(document: ExtractedDocument) -> ExtractedDocument:
    if document.document.document_type not in {"exam", "answer_key"}:
        return document
    detected = _content_document_type(document)
    if detected == document.document.document_type:
        return document
    metadata = dict(document.document.metadata)
    metadata["declared_document_type"] = document.document.document_type
    metadata["document_type_evidence"] = "pdf_structure"
    record = document.document.model_copy(
        update={"document_type": detected, "metadata": metadata}
    )
    return document.model_copy(update={"document": record})


def _role(document: DocumentRecord) -> str:
    title = re.sub(
        r"(?i)^\s*(?:PROVA\s+OBJETIVA|GABARITO(?:\s+OFICIAL)?(?:\s+DEFINITIVO)?)\s*[-–—:]\s*",
        "",
        document.title,
    ).strip()
    return title or document.title


def _area(role: str) -> str | None:
    match = re.search(r"(?i)\bÁREA\s+\d+\s*[:–—-]?\s*(.+)$", role)
    return match.group(1).strip() if match is not None else None


def _identity(document: DocumentRecord) -> tuple[str, int, str]:
    return _contest_slug(document), _year(document), _pair_key(document)


def _answer_key_version(
    document: DocumentRecord,
) -> Literal["definitive", "preliminary", "unknown"]:
    value = _normalize(
        f"{document.title} {document.original_url} {document.resolved_url}"
    )
    if "definitiv" in value:
        return "definitive"
    if "preliminar" in value:
        return "preliminary"
    return "unknown"


def _single_preferred_key(
    candidates: list[tuple[tuple[str, int, str], ExtractedDocument]],
) -> tuple[tuple[str, int, str], ExtractedDocument] | None:
    if len(candidates) == 1:
        return candidates[0]
    definitive = [
        item
        for item in candidates
        if _answer_key_version(item[1].document) == "definitive"
    ]
    return definitive[0] if len(definitive) == 1 else None


def _pair_documents(
    documents: list[ExtractedDocument], errors: list[PackageError]
) -> list[tuple[ExtractedDocument, ExtractedDocument]]:
    exams: dict[tuple[str, int, str], list[ExtractedDocument]] = {}
    keys: dict[tuple[str, int, str], list[ExtractedDocument]] = {}
    for original in documents:
        document = _with_content_document_type(original)
        try:
            identity = _identity(document.document)
        except ValueError as exc:
            errors.append(
                PackageError(
                    stage="pairing",
                    document_id=document.document.sha256,
                    message=str(exc),
                )
            )
            continue
        target = keys if document.document.document_type == "answer_key" else exams
        if document.document.document_type not in {"exam", "answer_key"}:
            continue
        target.setdefault(identity, []).append(document)

    pairs: list[tuple[ExtractedDocument, ExtractedDocument]] = []
    unmatched_exams: list[tuple[tuple[str, int, str], ExtractedDocument]] = []
    unmatched_keys: list[tuple[tuple[str, int, str], ExtractedDocument]] = []
    for identity in sorted(set(exams) | set(keys)):
        matching_exams = exams.get(identity, [])
        matching_keys = keys.get(identity, [])
        if len(matching_exams) == 1 and len(matching_keys) == 1:
            pairs.append((matching_exams[0], matching_keys[0]))
            continue
        unmatched_exams.extend((identity, item) for item in matching_exams)
        unmatched_keys.extend((identity, item) for item in matching_keys)

    remaining_keys = list(unmatched_keys)
    for exam_identity, exam in unmatched_exams:
        contest, year, pair_key = exam_identity
        same_contest = [
            (identity, key)
            for identity, key in remaining_keys
            if identity[:2] == (contest, year)
        ]
        exact = [
            (identity, key)
            for identity, key in same_contest
            if (
                identity[2] == pair_key
                or (
                    pair_key.startswith("conhecimentos_basicos")
                    and identity[2].startswith("conhecimentos_basicos")
                )
            )
        ]
        selected = _single_preferred_key(exact) or _single_preferred_key(same_contest)
        if selected is not None:
            _key_identity, key = selected
            pairs.append((exam, key))
            continue
        errors.append(
            PackageError(
                stage="pairing",
                document_id=exam.document.sha256,
                message=(
                    f"{exam_identity}: gabarito único não identificado; "
                    f"{len(same_contest)} candidatos compatíveis"
                ),
            )
        )
    paired_key_ids = {key.document.sha256 for _exam, key in pairs}
    paired_definitive_contests = {
        identity[:2]
        for identity, key in remaining_keys
        if key.document.sha256 in paired_key_ids
        and _answer_key_version(key.document) == "definitive"
    }
    for key_identity, key in remaining_keys:
        if key.document.sha256 in paired_key_ids:
            continue
        if (
            key_identity[:2] in paired_definitive_contests
            and _answer_key_version(key.document) == "preliminary"
        ):
            continue
        errors.append(
            PackageError(
                stage="pairing",
                document_id=key.document.sha256,
                message=f"{key_identity}: prova correspondente não identificada",
            )
        )
    return pairs


def _page_traces(document: ExtractedDocument) -> list[PageExtractionTrace]:
    return [
        PageExtractionTrace(
            page_number=page.number,
            method=page.extraction_method,
            character_count=page.character_count,
            confidence=page.confidence,
            duration_ms=page.duration_ms,
            ocr_reason=page.ocr_reason,
        )
        for page in document.pages
    ]


def _extraction_method(pages: list[ExtractedPage], source_pages: list[int]) -> str:
    methods = {
        page.extraction_method for page in pages if page.number in set(source_pages)
    }
    if "unreadable" in methods:
        return "unreadable"
    if methods == {"ocr"}:
        return "ocr"
    if "ocr" in methods:
        return "mixed"
    return "text"


def _visual_dependency(statement: str, supporting_text: str | None) -> bool:
    normalized = _normalize(" ".join((statement, supporting_text or "")))
    markers = (
        "grafico precedente",
        "figura precedente",
        "imagem precedente",
        "planilha a seguir",
        "tabela a seguir",
    )
    return any(marker in normalized for marker in markers)


def _stable_question_id(
    exam: DocumentRecord, number: int, statement: str, parser_version: str
) -> str:
    return _canonical_sha256(
        {
            "exam_sha256": exam.sha256,
            "number": number,
            "statement": " ".join(statement.split()),
            "parser_version": parser_version,
        }
    )


def _ollama_available(endpoint: str, model: str, enabled: bool) -> bool:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("o endpoint do Ollama deve usar loopback")
    if not enabled:
        return False
    try:
        response = httpx.get(f"{endpoint.rstrip('/')}/api/tags", timeout=2.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return any(
        isinstance(item, dict) and str(item.get("name", "")).split("@", 1)[0] == model
        for item in models
    )


def _qwen_recover_missing(
    pages: list[dict[str, object]],
    missing_numbers: set[int],
    *,
    question_format: QuestionFormat,
    endpoint: str,
    model: str,
    available: bool,
) -> tuple[list[QwenRecoveredQuestion], QwenDecisionTrace | None]:
    if not available or not missing_numbers:
        return [], None
    marker = re.compile(
        rf"(?im)^\s*(?:QUEST(?:ÃO|AO)\s+)?"
        rf"(?:{'|'.join(str(number) for number in sorted(missing_numbers))})\s+\S"
    )
    candidates = [
        {
            "page_number": int(str(page["page_number"])),
            "text": str(page["text"])[:12_000],
        }
        for page in pages
        if marker.search(str(page["text"]))
    ][:8]
    if not candidates:
        return [], None
    request_payload = {
        "missing_numbers": sorted(missing_numbers),
        "question_format": question_format,
        "pages": candidates,
    }
    input_sha256 = _canonical_sha256(request_payload)
    started = time.monotonic()
    try:
        response = httpx.post(
            f"{endpoint.rstrip('/')}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "number": {"type": "integer"},
                                    "statement": {"type": "string"},
                                    "alternatives": {"type": "object"},
                                    "supporting_text": {"type": ["string", "null"]},
                                    "block": {"type": ["string", "null"]},
                                    "source_pages": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                    },
                                },
                                "required": ["number", "statement", "source_pages"],
                            },
                        }
                    },
                    "required": ["questions"],
                },
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Extraia apenas questões literalmente presentes no trecho. "
                            "Não complete, reescreva nem invente conteúdo. Retorne JSON."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(request_payload, ensure_ascii=False),
                    },
                ],
                "options": {"temperature": 0},
            },
            timeout=90.0,
        )
        response.raise_for_status()
        envelope = response.json()
        raw_content = envelope["message"]["content"]
        decoded = json.loads(raw_content)
        proposed = [
            QwenRecoveredQuestion.model_validate(item)
            for item in decoded.get("questions", [])
            if isinstance(item, dict) and item.get("number") in missing_numbers
        ]
        candidate_page_numbers = {int(str(item["page_number"])) for item in candidates}
        candidate_text = _normalize(" ".join(str(item["text"]) for item in candidates))
        recovered = [
            item
            for item in proposed
            if set(item.source_pages) <= candidate_page_numbers
            and _normalize(item.statement) in candidate_text
            and all(
                _normalize(value) in candidate_text
                for value in item.alternatives.values()
            )
            and (
                item.supporting_text is None
                or _normalize(item.supporting_text) in candidate_text
            )
        ]
        unique = {item.number: item for item in recovered}
        recovered = [unique[number] for number in sorted(unique)]
        decision = QwenDecisionTrace(
            reason="números esperados ausentes após parsing determinístico",
            input_sha256=input_sha256,
            response={"recovered_numbers": [item.number for item in recovered]},
            accepted=bool(recovered),
            duration_ms=round((time.monotonic() - started) * 1000),
            model=model,
        )
        return recovered, decision
    except (
        httpx.HTTPError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        return [], QwenDecisionTrace(
            reason="números esperados ausentes após parsing determinístico",
            input_sha256=input_sha256,
            response={"error": type(exc).__name__},
            accepted=False,
            duration_ms=round((time.monotonic() - started) * 1000),
            model=model,
        )


def _load_extracted_documents(
    manifest_paths: list[Path], extraction_dir: Path, errors: list[PackageError]
) -> tuple[list[ExtractedDocument], list[str]]:
    documents: list[ExtractedDocument] = []
    manifest_hashes: list[str] = []
    seen_documents: set[str] = set()
    for manifest_path in manifest_paths:
        try:
            DownloadManifest.model_validate(read_json(manifest_path))
            manifest_hash = _file_sha256(manifest_path)
            extraction_path = extraction_dir / f"{manifest_hash}-extracted.json"
            if extraction_path.is_file():
                extraction = ExtractionManifest.model_validate(
                    read_json(extraction_path)
                )
            else:
                extraction, _path = extract_manifest(manifest_path, extraction_path)
        except Exception as exc:  # noqa: BLE001 - isolate one input manifest
            errors.append(
                PackageError(
                    stage="manifest",
                    document_id=str(manifest_path),
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        manifest_hashes.append(manifest_hash)
        for document in extraction.documents:
            if document.document.sha256 in seen_documents:
                continue
            seen_documents.add(document.document.sha256)
            documents.append(document)
    return documents, sorted(manifest_hashes)


def _structured_question(
    *,
    exam: ExtractedDocument,
    answer_key: ExtractedDocument,
    question_number: int,
    statement: str,
    alternatives: dict[str, str],
    source_pages: list[int],
    supporting_text: str | None,
    block: str | None,
    internal_answer: str | None,
    original_answer: str | None,
    annulled: bool,
    parser_version: str,
) -> StructuredQuestion:
    exam_record = exam.document
    key_record = answer_key.document
    role = _role(exam_record)
    reasons: list[str] = []
    if annulled:
        reasons.append("item anulado no gabarito oficial")
    if internal_answer is None and not annulled:
        reasons.append("resposta oficial ausente")
    if len(statement.strip()) < 10:
        reasons.append("enunciado truncado")
    if not source_pages:
        reasons.append("página de origem ausente")
    if _visual_dependency(statement, supporting_text):
        reasons.append("elemento visual exige revisão")
    if "texto" in _normalize(statement) and not supporting_text:
        reasons.append("texto de apoio não associado")
    status: ValidationStatus = "accepted" if not reasons else "quarantined"
    correct_label = alternatives.get(internal_answer or "")
    original = "X" if annulled else original_answer
    return StructuredQuestion(
        stable_id=_stable_question_id(
            exam_record, question_number, statement, parser_version
        ),
        board=_metadata_value(exam_record, "board", "banca") or "Cebraspe",
        organization=(
            _metadata_value(exam_record, "organization", "orgao") or "Polícia Federal"
        ),
        contest=(
            _metadata_value(exam_record, "contest", "concurso")
            or _contest_slug(exam_record)
        ),
        year=_year(exam_record),
        role=role,
        area=_area(role),
        block=block,
        original_number=question_number,
        statement=statement,
        alternatives=cast(dict[AlternativeLetter, str], alternatives),
        question_format=(
            "true_false"
            if alternatives == {"A": "Certo", "B": "Errado"}
            else "multiple_choice"
        ),
        correct_answer=cast(AlternativeLetter | None, internal_answer),
        correct_answer_label=correct_label,
        supporting_text=supporting_text,
        exam_url=exam_record.resolved_url or exam_record.original_url,
        answer_key_url=key_record.resolved_url or key_record.original_url,
        exam_sha256=exam_record.sha256,
        answer_key_sha256=key_record.sha256,
        source_pages=source_pages,
        extraction_method=cast(
            ExtractionMethod, _extraction_method(exam.pages, source_pages)
        ),
        parser_version=parser_version,
        validation_status=status,
        validation_reasons=reasons,
        answer_association=AnswerAssociationTrace(
            answer_key_id=key_record.sha256,
            original_answer=cast(OriginalAnswer | None, original),
            internal_answer=cast(AlternativeLetter | None, internal_answer),
            reason=(
                "item anulado no gabarito oficial"
                if annulled
                else "número, concurso, cargo/área e bloco compatíveis"
            ),
        ),
    )


def _process_pair(
    exam: ExtractedDocument,
    answer_key: ExtractedDocument,
    errors: list[PackageError],
    *,
    qwen_endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
    qwen_model: str = DEFAULT_QWEN_MODEL,
    qwen_available: bool = False,
    qwen_decisions: list[QwenDecisionTrace] | None = None,
) -> tuple[list[StructuredQuestion], ExamProcessingMetrics]:
    started = time.monotonic()
    entries = parse_answer_key(
        answer_key.text,
        variant=_variant(exam.document),
        role=_role(exam.document),
        turn=_metadata_value(exam.document, "turn", "turno", "shift"),
    )
    if not entries:
        raise ValueError("gabarito sem respostas reconhecíveis")
    expected_numbers = tuple(sorted(entries))
    answer_values = {
        entry.answer for entry in entries.values() if entry.answer is not None
    }
    true_false = bool(answer_values) and answer_values <= {"C", "E"}
    pages: list[dict[str, object]] = [
        {"page_number": page.number, "text": page.text} for page in exam.pages
    ]
    context = BankParsingContext(
        document_id=exam.document.sha256,
        board=_metadata_value(exam.document, "board", "banca") or "Cebraspe",
        provider=exam.document.source_id,
        contest=_metadata_value(exam.document, "contest", "concurso"),
        role=_role(exam.document),
        expected_numbers=expected_numbers,
        question_format="true_false" if true_false else "multiple_choice",
    )
    parsing = parse_question_document(pages, context)
    question_contexts = cebraspe_question_contexts(
        pages, expected_numbers=expected_numbers
    )
    adapted_entries = adapt_true_false_entries(entries) if true_false else entries
    output: list[StructuredQuestion] = []
    for question in parsing.objective_questions:
        entry = entries.get(question.number)
        adapted = adapted_entries.get(question.number)
        item_context = question_contexts.get(question.number)
        output.append(
            _structured_question(
                exam=exam,
                answer_key=answer_key,
                question_number=question.number,
                statement=question.statement,
                alternatives={item.letter: item.text for item in question.alternatives},
                source_pages=question.source_pages,
                supporting_text=(
                    item_context.supporting_text if item_context is not None else None
                ),
                block=item_context.block if item_context is not None else None,
                internal_answer=(
                    adapted.answer
                    if adapted is not None and not adapted.annulled
                    else None
                ),
                original_answer=entry.answer if entry is not None else None,
                annulled=bool(entry and entry.annulled),
                parser_version=f"{parsing.adapter_id}-{parsing.adapter_version}",
            )
        )
    deterministic_numbers = {item.original_number for item in output}
    recovered, decision = _qwen_recover_missing(
        pages,
        set(expected_numbers) - deterministic_numbers,
        question_format="true_false" if true_false else "multiple_choice",
        endpoint=qwen_endpoint,
        model=qwen_model,
        available=qwen_available,
    )
    if decision is not None and qwen_decisions is not None:
        qwen_decisions.append(decision)
    for recovered_question in recovered:
        entry = entries.get(recovered_question.number)
        adapted = adapted_entries.get(recovered_question.number)
        alternatives: dict[str, str] = (
            {"A": "Certo", "B": "Errado"}
            if true_false
            else {
                str(letter): text
                for letter, text in recovered_question.alternatives.items()
            }
        )
        output.append(
            _structured_question(
                exam=exam,
                answer_key=answer_key,
                question_number=recovered_question.number,
                statement=recovered_question.statement,
                alternatives=alternatives,
                source_pages=recovered_question.source_pages,
                supporting_text=recovered_question.supporting_text,
                block=recovered_question.block,
                internal_answer=(
                    adapted.answer
                    if adapted is not None and not adapted.annulled
                    else None
                ),
                original_answer=entry.answer if entry is not None else None,
                annulled=bool(entry and entry.annulled),
                parser_version=f"qwen-fallback-{STRUCTURED_PACKAGE_VERSION}",
            )
        )
    found_numbers = {item.original_number for item in output}
    expected_set = set(expected_numbers)
    correct_numbers = found_numbers & expected_set
    unexpected = found_numbers - expected_set
    if parsing.warnings:
        errors.append(
            PackageError(
                stage="parsing",
                document_id=exam.document.sha256,
                message="; ".join(parsing.warning_messages()),
            )
        )
    accepted = sum(item.validation_status == "accepted" for item in output)
    quarantined = sum(item.validation_status == "quarantined" for item in output)
    rejected = sum(item.validation_status == "rejected" for item in output)
    associated = sum(
        item.answer_association.original_answer is not None for item in output
    )
    ocr_pages = sum(page.extraction_method == "ocr" for page in exam.pages)
    metrics = ExamProcessingMetrics(
        exam_id=exam.document.sha256,
        title=exam.document.title,
        expected_questions=len(expected_numbers),
        detected_questions=len(output),
        accepted_questions=accepted,
        quarantined_questions=quarantined,
        rejected_questions=rejected,
        segmentation_precision=(
            len(correct_numbers) / len(found_numbers) if found_numbers else 0.0
        ),
        segmentation_coverage=(
            len(correct_numbers) / len(expected_set) if expected_set else 0.0
        ),
        associated_answers=associated,
        missing_answers=max(0, len(expected_set) - associated),
        incorrect_associations=len(unexpected),
        duplicate_questions=len(output) - len(found_numbers),
        ocr_pages=ocr_pages,
        qwen_calls=1 if decision is not None else 0,
        duration_ms=round((time.monotonic() - started) * 1000),
        intervention_free=True,
    )
    return output, metrics


def build_structured_question_package(
    manifest_paths: list[Path],
    output_path: Path,
    *,
    extraction_dir: Path | None = None,
    ollama_endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
    qwen_model: str = DEFAULT_QWEN_MODEL,
    enable_ollama: bool = True,
) -> StructuredQuestionPackage:
    """Build a deterministic, review-first package from collector manifests."""
    if not manifest_paths:
        raise ValueError("informe ao menos um manifesto de coleta")
    started = time.monotonic()
    errors: list[PackageError] = []
    extraction_dir = extraction_dir or output_path.parent / "extracted"
    documents, manifest_hashes = _load_extracted_documents(
        manifest_paths, extraction_dir, errors
    )
    page_extraction = {
        document.document.sha256: _page_traces(document) for document in documents
    }
    pairs = _pair_documents(documents, errors)
    qwen_available = _ollama_available(ollama_endpoint, qwen_model, enable_ollama)
    qwen_decisions: list[QwenDecisionTrace] = []
    questions: list[StructuredQuestion] = []
    exam_metrics: list[ExamProcessingMetrics] = []
    for exam, answer_key in pairs:
        try:
            items, metrics = _process_pair(
                exam,
                answer_key,
                errors,
                qwen_endpoint=ollama_endpoint,
                qwen_model=qwen_model,
                qwen_available=qwen_available,
                qwen_decisions=qwen_decisions,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one exam/key pair
            errors.append(
                PackageError(
                    stage="parsing",
                    document_id=exam.document.sha256,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        questions.extend(items)
        exam_metrics.append(metrics)

    by_id: dict[str, StructuredQuestion] = {}
    duplicate_ids: set[str] = set()
    for question in questions:
        if question.stable_id in by_id:
            duplicate_ids.add(question.stable_id)
        else:
            by_id[question.stable_id] = question
    if duplicate_ids:
        questions = [
            item.model_copy(
                update={
                    "validation_status": "quarantined",
                    "validation_reasons": [
                        *item.validation_reasons,
                        "identificador estável duplicado",
                    ],
                }
            )
            if item.stable_id in duplicate_ids
            else item
            for item in questions
        ]

    accepted = sorted(
        (item for item in questions if item.validation_status == "accepted"),
        key=lambda item: (item.year, item.role, item.original_number, item.stable_id),
    )
    quarantined = sorted(
        (item for item in questions if item.validation_status == "quarantined"),
        key=lambda item: (item.year, item.role, item.original_number, item.stable_id),
    )
    rejected = sorted(
        (item for item in questions if item.validation_status == "rejected"),
        key=lambda item: (item.year, item.role, item.original_number, item.stable_id),
    )
    expected_total = sum(item.expected_questions for item in exam_metrics)
    detected_total = sum(item.detected_questions for item in exam_metrics)
    correct_total = sum(
        round(item.segmentation_precision * item.detected_questions)
        for item in exam_metrics
    )
    intervention_free = sum(item.intervention_free for item in exam_metrics)
    qwen = QwenRuntimeTrace(
        endpoint=ollama_endpoint,
        model=qwen_model,
        available=qwen_available,
        calls=qwen_decisions,
    )
    package_metrics = StructuredPackageMetrics(
        documents_processed=len(documents),
        exams_processed=len(exam_metrics),
        answer_keys_processed=len({key.document.sha256 for _exam, key in pairs}),
        expected_questions=expected_total,
        detected_questions=detected_total,
        accepted_questions=len(accepted),
        quarantined_questions=len(quarantined),
        rejected_questions=len(rejected),
        segmentation_precision=(
            correct_total / detected_total if detected_total else 0.0
        ),
        segmentation_coverage=(
            correct_total / expected_total if expected_total else 0.0
        ),
        associated_answers=sum(item.associated_answers for item in exam_metrics),
        missing_answers=sum(item.missing_answers for item in exam_metrics),
        duplicate_questions=(
            sum(item.duplicate_questions for item in exam_metrics) + len(duplicate_ids)
        ),
        ocr_pages=sum(item.ocr_pages for item in exam_metrics),
        qwen_calls=len(qwen_decisions),
        intervention_free_percent=(
            intervention_free / len(exam_metrics) if exam_metrics else 0.0
        ),
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    content = {
        "schema_version": STRUCTURED_PACKAGE_VERSION,
        "parser_version": STRUCTURED_PARSER_VERSION,
        "input_manifest_sha256s": manifest_hashes,
        "accepted": [item.model_dump(mode="json") for item in accepted],
        "quarantined": [item.model_dump(mode="json") for item in quarantined],
        "rejected": [item.model_dump(mode="json") for item in rejected],
        "errors": [item.model_dump(mode="json") for item in errors],
        "page_extraction": {
            key: [item.model_dump(mode="json") for item in value]
            for key, value in sorted(page_extraction.items())
        },
        "qwen": qwen.model_dump(mode="json"),
        "exams": [item.model_dump(mode="json") for item in exam_metrics],
        "metrics": package_metrics.model_dump(mode="json"),
    }
    semantic_content = json.loads(json.dumps(content, ensure_ascii=False))
    # O manifesto inclui horários e telemetria de coleta. Os hashes dos PDFs já
    # fazem parte das questões e dos rastros; o invólucro não altera o conteúdo.
    semantic_content.pop("input_manifest_sha256s", None)
    for traces in semantic_content["page_extraction"].values():
        for trace in traces:
            trace.pop("duration_ms", None)
    semantic_content["qwen"].pop("available", None)
    for decision in semantic_content["qwen"]["calls"]:
        decision.pop("duration_ms", None)
    for exam_metric in semantic_content["exams"]:
        exam_metric.pop("duration_ms", None)
    semantic_content["metrics"].pop("duration_ms", None)
    package = StructuredQuestionPackage.model_validate(
        {
            **content,
            "content_sha256": _canonical_sha256(semantic_content),
        }
    )
    write_json(output_path, package.model_dump(mode="json"))
    return package
