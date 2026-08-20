from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal, cast

from pydantic import ConfigDict, model_validator

from .models import StrictModel

if TYPE_CHECKING:
    from .document_contract import NormalizedDocument
    from .models import DocumentRecord

SEMANTIC_SCHEMA_VERSION = 1
IDENTITY_ALGORITHM_VERSION = "semantic-identity-v1"
CONTENT_NORMALIZER_VERSION = "pdf-text-nfkc-v1"
SemanticValue = str | int
SemanticStatus = Literal["known", "unknown", "conflict"]
EvidenceSource = Literal["declared_metadata", "pdf_text", "document_title", "human_review"]
EvidenceStrength = Literal["strong", "medium", "weak"]
DocumentRole = Literal["exam", "answer_key", "other", "unknown"]
AnswerKeyState = Literal["preliminary", "definitive", "unknown"]
ResolutionOutcome = Literal[
    "exact_duplicate", "republication", "new_version", "new_identity", "uncertain"
]
AssociationOutcome = Literal[
    "selected", "missing", "conflict", "insufficient_evidence", "ambiguous"
]

METADATA_ALIASES = {
    "board": ("board", "banca"),
    "concurso": ("concurso",),
    "organization": ("organization", "orgao"),
    "year": ("year", "ano"),
    "roles": ("role", "cargo"),
    "stage": ("stage", "etapa", "fase"),
    "turns": ("turn", "turno"),
    "variants": ("variant", "tipo"),
}

_PDF_LABELS = {
    "board": ("banca",),
    "concurso": ("concurso",),
    "organization": ("orgao", "organizacao", "organization"),
    "year": ("ano", "year"),
    "roles": ("cargo", "role"),
    "stage": ("etapa", "fase", "stage"),
    "turns": ("turno", "turn"),
    "variants": ("tipo", "variant", "versao"),
}
_YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_ROLE_MARKERS = {"exam": ("prova", "exam"), "answer_key": ("gabarito", "answer key")}
_PRELIMINARY_MARKERS = ("preliminar", "preliminary")
_DEFINITIVE_MARKERS = ("definitivo", "definitive", "final")


class FrozenSemanticModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _norm(value: SemanticValue) -> SemanticValue:
    if isinstance(value, int):
        return value
    decomposed = unicodedata.normalize("NFD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split()).casefold()


def _typed_sort_key(value: SemanticValue) -> tuple[str, str]:
    return (type(value).__name__, canonical_json(value))


class SemanticEvidence(FrozenSemanticModel):
    source: EvidenceSource
    locator: str
    raw_value: SemanticValue
    normalized_value: SemanticValue
    strength: EvidenceStrength

    @classmethod
    def metadata(cls, locator: str, value: SemanticValue) -> SemanticEvidence:
        return cls(
            source="declared_metadata", locator=locator, raw_value=value,
            normalized_value=_norm(value), strength="strong"
        )

    @classmethod
    def pdf_text(cls, locator: str, value: SemanticValue) -> SemanticEvidence:
        return cls(
            source="pdf_text", locator=locator, raw_value=value,
            normalized_value=_norm(value), strength="strong"
        )

    @classmethod
    def title(cls, locator: str, value: SemanticValue) -> SemanticEvidence:
        return cls(
            source="document_title", locator=locator, raw_value=value,
            normalized_value=_norm(value), strength="weak"
        )

    @classmethod
    def human_review(cls, locator: str, value: SemanticValue) -> SemanticEvidence:
        return cls(
            source="human_review", locator=locator, raw_value=value,
            normalized_value=_norm(value), strength="strong"
        )


class SemanticField(FrozenSemanticModel):
    status: SemanticStatus
    raw_values: tuple[SemanticValue, ...] = ()
    normalized_values: tuple[SemanticValue, ...] = ()
    evidence: tuple[SemanticEvidence, ...] = ()
    method: str
    confidence: float | None = None
    reason: str
    algorithm_version: str = IDENTITY_ALGORITHM_VERSION

    @model_validator(mode="after")
    def validate_state(self) -> SemanticField:
        if self.status == "unknown":
            if (
                self.raw_values or self.normalized_values or self.evidence
                or self.confidence is not None
            ):
                raise ValueError("campo unknown não pode conter valores, evidência ou confiança")
        elif self.status == "known":
            if not self.normalized_values:
                raise ValueError("campo known exige valor normalizado")
        elif self.confidence is not None:
            raise ValueError("campo conflict não pode conter confiança")
        elif len(self.normalized_values) < 2:
            raise ValueError("campo conflict exige ao menos dois valores normalizados")
        return self

    @classmethod
    def unknown(cls, reason: str) -> SemanticField:
        return cls(status="unknown", method="unresolved", reason=reason)

    @classmethod
    def from_evidence(cls, name: str, evidence: tuple[SemanticEvidence, ...]) -> SemanticField:
        ordered = tuple(sorted(evidence, key=lambda item: (
            _typed_sort_key(item.normalized_value), _typed_sort_key(item.raw_value),
            item.source, item.locator, item.strength,
        )))
        values = tuple(sorted(
            {item.normalized_value for item in ordered},
            key=_typed_sort_key,
        ))
        status: SemanticStatus = "known" if len(values) == 1 else "conflict"
        return cls(
            status=status, raw_values=tuple(item.raw_value for item in ordered),
            normalized_values=values, evidence=ordered, method="evidence",
            confidence=1.0 if status == "known" else None,
            reason=f"campo {name} derivado de evidências"
        )


class ExamSemanticIdentity(FrozenSemanticModel):
    board: SemanticField
    concurso: SemanticField
    organization: SemanticField
    year: SemanticField
    roles: SemanticField
    stage: SemanticField
    turns: SemanticField
    variants: SemanticField


class AnswerKeyCoverage(FrozenSemanticModel):
    roles: SemanticField
    stage: SemanticField
    turns: SemanticField
    variants: SemanticField


class ContentFingerprint(FrozenSemanticModel):
    sha256: str
    page_sha256s: tuple[str, ...]
    page_count: int
    character_count: int
    normalizer_version: str = CONTENT_NORMALIZER_VERSION


class DocumentSemanticProfile(FrozenSemanticModel):
    identity: ExamSemanticIdentity
    identity_key: str | None
    document_role: DocumentRole
    answer_key_state: AnswerKeyState = "unknown"
    coverage: AnswerKeyCoverage
    content_fingerprint: ContentFingerprint
    has_conflict: bool
    algorithm_version: str = IDENTITY_ALGORITHM_VERSION


class KnownDocumentVersion(FrozenSemanticModel):
    version_id: str
    identity_key: str
    document_role: DocumentRole
    content_sha256: str
    version_number: int
    predecessor_version_id: str | None = None


class IdentityResolution(FrozenSemanticModel):
    outcome: ResolutionOutcome
    profile: DocumentSemanticProfile | None = None
    document_version_id: str | None = None
    predecessor_version_id: str | None = None
    version_number: int | None = None
    reason: str
    algorithm_version: str = IDENTITY_ALGORITHM_VERSION


class AssociationCandidate(FrozenSemanticModel):
    version_id: str
    profile: DocumentSemanticProfile
    predecessor_version_id: str | None = None


class CandidateAssessment(FrozenSemanticModel):
    version_id: str
    compatible: bool
    score: int
    matched_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class DocumentAssociationDecision(FrozenSemanticModel):
    outcome: AssociationOutcome
    selected_version_id: str | None
    assessments: tuple[CandidateAssessment, ...]
    minimum_score: int
    minimum_margin: int
    achieved_margin: int | None
    reason: str
    algorithm_version: str


def canonical_json(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def semantic_identity_key(identity: ExamSemanticIdentity) -> str | None:
    required = (identity.board, identity.concurso, identity.year)
    if any(field.status != "known" or len(field.normalized_values) != 1 for field in required):
        return None
    payload = {"schema_version": SEMANTIC_SCHEMA_VERSION, "identity": {
        name: getattr(identity, name).normalized_values
        for name in ExamSemanticIdentity.model_fields
    }}
    return stable_sha256(payload)


def _normalize_page(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    result: list[str] = []
    for line in lines:
        if line or (result and result[-1]):
            result.append(line)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


def build_content_fingerprint(pages: Sequence[tuple[int, str]]) -> ContentFingerprint:
    normalized = tuple((number, _normalize_page(text)) for number, text in pages)
    page_hashes = tuple(
        stable_sha256({"page": number, "text": text}) for number, text in normalized
    )
    payload = {"pages": [{"number": number, "text": text} for number, text in normalized]}
    return ContentFingerprint(sha256=stable_sha256(payload),
                              page_sha256s=page_hashes, page_count=len(normalized),
                              character_count=sum(len(text) for _, text in normalized))


def _values(raw_value: str | int | Sequence[str | int], field: str) -> tuple[SemanticValue, ...]:
    values = (raw_value,) if isinstance(raw_value, (str, int)) else tuple(raw_value)
    expanded: list[SemanticValue] = []
    for value in values:
        if isinstance(value, int):
            expanded.append(value)
            continue
        text = value.strip()
        if field == "year":
            expanded.extend(int(year) for year in _YEAR_PATTERN.findall(text))
        elif field == "variants":
            interval = re.fullmatch(r"\s*(\d+)\s*(?:a|ate|até|[-–])\s*(\d+)\s*", text, re.I)
            if interval is not None:
                start, end = (int(item) for item in interval.groups())
                if start <= end:
                    expanded.extend(f"tipo {number}" for number in range(start, end + 1))
            else:
                parts = re.split(r"\s*[,;/]\s*", text)
                expanded.extend(_variant_value(part) for part in parts if part.strip())
        else:
            parts = re.split(r"\s*[,;/]\s*", text)
            expanded.extend(part.strip() for part in parts if part.strip())
    return tuple(expanded)


def _variant_value(value: str) -> str:
    match = re.fullmatch(r"\s*(?:tipo|variant|versao|versão)?\s*(\d+)\s*", value, re.I)
    return f"tipo {match.group(1)}" if match is not None else value.strip()


def _labeled_values(pages: Sequence[tuple[int, str]], field: str) -> tuple[tuple[str, str], ...]:
    labels = "|".join(re.escape(label) + "s?" for label in _PDF_LABELS[field])
    pattern = re.compile(rf"(?im)^\s*(?:{labels})\s*:\s*(?P<value>[^\r\n]+)")
    return tuple(
        (f"page:{page_number}", match.group("value").strip())
        for page_number, text in pages
        for match in pattern.finditer(text)
    )


def _field_from_sources(
    name: str,
    document: NormalizedDocument,
    pages: Sequence[tuple[int, str]],
    human_overrides: Mapping[str, str | int | Sequence[str]] | None,
) -> SemanticField:
    evidence: list[SemanticEvidence] = []
    if human_overrides is not None and name in human_overrides:
        for value in _values(human_overrides[name], name):
            evidence.append(SemanticEvidence.human_review(f"override:{name}", value))
    for alias in METADATA_ALIASES[name]:
        if alias in document.metadata:
            for value in _values(document.metadata[alias], name):
                evidence.append(SemanticEvidence.metadata(f"metadata:{alias}", value))
    for locator, raw_value in _labeled_values(pages, name):
        for value in _values(raw_value, name):
            evidence.append(SemanticEvidence.pdf_text(locator, value))
    if evidence:
        return SemanticField.from_evidence(name, tuple(evidence))
    if name in {"year", "turns", "variants"}:
        title_values = _title_values(document.title, name)
        if title_values:
            return SemanticField.from_evidence(
                name,
                tuple(SemanticEvidence.title("title", value) for value in title_values),
            )
    if name == "year":
        years = tuple(
            sorted({int(year) for _, text in pages for year in _YEAR_PATTERN.findall(text)})
        )
        if len(years) == 1:
            return SemanticField.from_evidence(
                name, (SemanticEvidence.pdf_text("document:unique-year", years[0]),)
            )
    return SemanticField.unknown(f"{name} sem evidência")


def _title_values(title: str, field: str) -> tuple[SemanticValue, ...]:
    if field == "year":
        return tuple(int(year) for year in _YEAR_PATTERN.findall(title))
    if field == "variants":
        matches = re.findall(r"(?:tipo|variant|versao|versão)\s*[-_ ]*(\d+)", title, re.I)
        return tuple(f"tipo {number}" for number in matches)
    if field == "turns":
        matches = re.findall(r"(?:turno|turn)\s*[-_ ]*([\w]+)", title, re.I)
        return tuple(f"turno {value}" for value in matches)
    return ()


def _detect_role(document: NormalizedDocument, pages: Sequence[tuple[int, str]]) -> DocumentRole:
    if document.declared_type != "auto":
        return document.declared_type
    sample = f"{document.title}\n{''.join(text for _, text in pages)[:20000]}".casefold()
    matches = {
        role
        for role, markers in _ROLE_MARKERS.items()
        if any(marker in sample for marker in markers)
    }
    return cast(DocumentRole, next(iter(matches))) if len(matches) == 1 else "unknown"


def _answer_key_state(
    document: NormalizedDocument, pages: Sequence[tuple[int, str]]
) -> AnswerKeyState:
    text = f"{document.title}\n{''.join(value for _, value in pages)}".casefold()
    preliminary = any(marker in text for marker in _PRELIMINARY_MARKERS)
    definitive = any(marker in text for marker in _DEFINITIVE_MARKERS)
    if preliminary == definitive:
        return "unknown"
    return "preliminary" if preliminary else "definitive"


def extract_semantic_profile(
    document: NormalizedDocument,
    pages: Sequence[tuple[int, str]],
    human_overrides: Mapping[str, str | int | Sequence[str]] | None = None,
) -> DocumentSemanticProfile:
    fields = {
        name: _field_from_sources(name, document, pages, human_overrides)
        for name in METADATA_ALIASES
    }
    identity = ExamSemanticIdentity(**fields)
    coverage = AnswerKeyCoverage(
        roles=fields["roles"], stage=fields["stage"], turns=fields["turns"],
        variants=fields["variants"],
    )
    role = _detect_role(document, pages)
    return DocumentSemanticProfile(
        identity=identity, identity_key=semantic_identity_key(identity), document_role=role,
        answer_key_state=_answer_key_state(document, pages) if role == "answer_key" else "unknown",
        coverage=coverage, content_fingerprint=build_content_fingerprint(pages),
        has_conflict=any(field.status == "conflict" for field in fields.values()),
    )


def profile_from_document_record(
    record: DocumentRecord, pages: Sequence[tuple[int, str]]
) -> DocumentSemanticProfile:
    from .document_contract import normalize_collected_document

    return extract_semantic_profile(normalize_collected_document(record), pages)
