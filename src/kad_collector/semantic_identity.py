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
MAX_SEMANTIC_VALUES = 128
MAX_SEMANTIC_NUMERIC_VALUE = 10_000
MAX_SEMANTIC_VALUE_CHARS = 512
MAX_PUBLIC_SEMANTIC_ITEMS = 32
MAX_PUBLIC_SEMANTIC_CHARS = 160
_OMITTED_PUBLIC_KEYS = frozenset(
    {
        "raw_value",
        "raw_values",
        "normalized_value",
        "canonicalText",
        "canonical_text",
        "origin",
        "original_url",
        "resolved_url",
        "source_page_url",
        "traceback",
    }
)
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


class SemanticValueLimitError(ValueError):
    """A semantic assertion exceeded deterministic extraction limits."""

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
    "organization": ("orgao", "órgão", "organizacao", "organização", "organization"),
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
    return " ".join(value.split()).casefold()


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


def semantic_public_dto(value: object) -> object:
    """Return the bounded semantic read model used by desktop GET and PUT responses."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {
            str(key): semantic_public_dto(item)
            for key, item in value.items()
            if str(key) not in _OMITTED_PUBLIC_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value[:MAX_PUBLIC_SEMANTIC_ITEMS])
        if len(value) > MAX_PUBLIC_SEMANTIC_ITEMS:
            items.append("[itens adicionais omitidos]")
        return [semantic_public_dto(item) for item in items]
    if isinstance(value, str) and len(value) > MAX_PUBLIC_SEMANTIC_CHARS:
        return "[valor omitido: excede limite de exibição]"
    return value


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
    if isinstance(raw_value, (str, int)):
        values: Sequence[str | int] = (raw_value,)
    else:
        if len(raw_value) > MAX_SEMANTIC_VALUES:
            raise SemanticValueLimitError("lista semântica excede limite seguro")
        values = raw_value
    expanded: list[SemanticValue] = []
    for value in values:
        if isinstance(value, int):
            if abs(value) > MAX_SEMANTIC_NUMERIC_VALUE:
                raise SemanticValueLimitError("valor semântico excede limite seguro")
            expanded.append(value)
            continue
        if len(value) > MAX_SEMANTIC_VALUE_CHARS:
            raise SemanticValueLimitError("linha semântica excede limite seguro")
        text = value.strip()
        if field == "year":
            expanded.extend(int(year) for year in _YEAR_PATTERN.findall(text))
        elif field == "variants":
            interval = re.fullmatch(r"\s*(\d+)\s*(?:a|ate|até|[-–])\s*(\d+)\s*", text, re.I)
            if interval is not None:
                start, end = (int(item) for item in interval.groups())
                if start <= end:
                    if (
                        end > MAX_SEMANTIC_NUMERIC_VALUE
                        or end - start + 1 > MAX_SEMANTIC_VALUES
                    ):
                        raise SemanticValueLimitError("intervalo semântico excede limite seguro")
                    expanded.extend(f"tipo {number}" for number in range(start, end + 1))
            else:
                parts = re.split(r"\s*[,;/]\s*", text)
                if len(parts) > MAX_SEMANTIC_VALUES:
                    raise SemanticValueLimitError("lista semântica excede limite seguro")
                expanded.extend(_variant_value(part) for part in parts if part.strip())
        else:
            parts = re.split(r"\s*[,;/]\s*", text)
            if len(parts) > MAX_SEMANTIC_VALUES:
                raise SemanticValueLimitError("lista semântica excede limite seguro")
            expanded.extend(part.strip() for part in parts if part.strip())
        if len(expanded) > MAX_SEMANTIC_VALUES:
            raise SemanticValueLimitError("campo semântico excede limite seguro")
    return tuple(expanded)


def _variant_value(value: str) -> str:
    match = re.fullmatch(r"\s*(?:v|tipo|variant|versao|versão)?\s*(\d+)\s*", value, re.I)
    if match is None:
        return value.strip()
    number = int(match.group(1))
    if number > MAX_SEMANTIC_NUMERIC_VALUE:
        raise SemanticValueLimitError("valor semântico excede limite seguro")
    return f"tipo {number}"


def _labeled_values(pages: Sequence[tuple[int, str]], field: str) -> tuple[tuple[str, str], ...]:
    labels = "|".join(re.escape(label) + "s?" for label in _PDF_LABELS[field])
    pattern = re.compile(rf"(?im)^\s*(?:{labels})\s*:\s*(?P<value>[^\r\n]+)")
    result: list[tuple[str, str]] = []
    for page_number, text in pages:
        for match in pattern.finditer(text):
            value = match.group("value").strip()
            if len(value) > MAX_SEMANTIC_VALUE_CHARS:
                raise SemanticValueLimitError("linha semântica excede limite seguro")
            result.append((f"page:{page_number}", value))
            if len(result) > MAX_SEMANTIC_VALUES:
                raise SemanticValueLimitError("evidências semânticas excedem limite seguro")
    return tuple(result)


def _field_from_sources(
    name: str,
    document: NormalizedDocument,
    pages: Sequence[tuple[int, str]],
    human_overrides: Mapping[str, str | int | Sequence[str]] | None,
) -> SemanticField:
    human_groups: list[tuple[SemanticEvidence, ...]] = []
    strong_groups: list[tuple[SemanticEvidence, ...]] = []
    try:
        if human_overrides is not None and name in human_overrides:
            values = _values(human_overrides[name], name)
            if values:
                human_groups.append(
                    tuple(
                        SemanticEvidence.human_review(f"override:{name}", value)
                        for value in values
                    )
                )
        for alias in METADATA_ALIASES[name]:
            if alias in document.metadata:
                values = _values(document.metadata[alias], name)
                if values:
                    strong_groups.append(
                        tuple(
                            SemanticEvidence.metadata(f"metadata:{alias}", value)
                            for value in values
                        )
                    )
        for locator, raw_value in _labeled_values(pages, name):
            values = _values(raw_value, name)
            if values:
                strong_groups.append(
                    tuple(SemanticEvidence.pdf_text(locator, value) for value in values)
                )
    except SemanticValueLimitError:
        return SemanticField.unknown(f"{name} excede limite semântico seguro")
    if name == "variants" and not strong_groups:
        for page_number, text in pages:
            matches = re.findall(
                r"(?i)(?:prova|tipo|variant|vers[aã]o)\s*V?\s*(\d+)", text
            )
            if len(matches) > MAX_SEMANTIC_VALUES or any(
                int(number) > MAX_SEMANTIC_NUMERIC_VALUE for number in matches
            ):
                return SemanticField.unknown(f"{name} excede limite semântico seguro")
            values = tuple(f"tipo {int(number)}" for number in matches)
            if values:
                strong_groups.append(tuple(
                    SemanticEvidence.pdf_text(f"page:{page_number}:variant", value)
                    for value in values
                ))
    if name == "year":
        years = tuple(
            sorted({int(year) for _, text in pages for year in _YEAR_PATTERN.findall(text)})
        )
        if len(years) > MAX_SEMANTIC_VALUES:
            return SemanticField.unknown(f"{name} excede limite semântico seguro")
        if not strong_groups and len(years) == 1:
            strong_groups.append(
                (SemanticEvidence.pdf_text("document:unique-year", years[0]),)
            )
    weak_groups: list[tuple[SemanticEvidence, ...]] = []
    if not strong_groups and name in {"year", "turns", "variants"}:
        title_values = _title_values(document.title, name)
        if len(title_values) > MAX_SEMANTIC_VALUES:
            return SemanticField.unknown(f"{name} excede limite semântico seguro")
        if title_values:
            weak_groups.append(
                tuple(SemanticEvidence.title("title", value) for value in title_values)
            )
    return _resolve_field(
        name,
        human_groups=human_groups,
        source_groups=strong_groups or weak_groups,
        collection=name in {"roles", "stage", "turns", "variants"},
    )


def _resolve_field(
    name: str,
    *,
    human_groups: Sequence[tuple[SemanticEvidence, ...]],
    source_groups: Sequence[tuple[SemanticEvidence, ...]],
    collection: bool,
) -> SemanticField:
    evidence = tuple(item for group in (*human_groups, *source_groups) for item in group)
    if not evidence:
        return SemanticField.unknown(f"{name} sem evidência")

    human_values = _group_values(human_groups)
    if human_groups:
        if not collection and len(human_values) != 1:
            return _semantic_field(name, evidence, (), "conflict", "human_conflict")
        return _semantic_field(name, evidence, human_values, "known", "human_override")

    sets = _group_sets(source_groups)
    if not sets or (not collection and any(len(values) != 1 for values in sets)):
        return _semantic_field(name, evidence, (), "conflict", "strong_conflict")
    if len(set(sets)) != 1:
        return _semantic_field(name, evidence, (), "conflict", "strong_conflict")
    return _semantic_field(name, evidence, sets[0], "known", "source_evidence")


def _group_values(groups: Sequence[tuple[SemanticEvidence, ...]]) -> tuple[SemanticValue, ...]:
    return tuple(
        sorted(
            {item.normalized_value for group in groups for item in group}, key=_typed_sort_key
        )
    )


def _group_sets(
    groups: Sequence[tuple[SemanticEvidence, ...]]
) -> tuple[tuple[SemanticValue, ...], ...]:
    return tuple(_group_values((group,)) for group in groups)


def _semantic_field(
    name: str,
    evidence: Sequence[SemanticEvidence],
    values: Sequence[SemanticValue],
    status: SemanticStatus,
    method: str,
) -> SemanticField:
    ordered = tuple(sorted(evidence, key=lambda item: (
        _typed_sort_key(item.normalized_value), _typed_sort_key(item.raw_value),
        item.source, item.locator, item.strength,
    )))
    all_values = _group_values((ordered,))
    if status == "conflict":
        return SemanticField(
            status="conflict", raw_values=tuple(item.raw_value for item in ordered),
            normalized_values=all_values, evidence=ordered, method=method,
            reason=f"{name} possui afirmações fortes incompatíveis",
        )
    return SemanticField(
        status="known", raw_values=tuple(item.raw_value for item in ordered),
        normalized_values=tuple(values), evidence=ordered, method=method, confidence=1.0,
        reason=(
            f"{name} definido por revisão humana; evidências anteriores preservadas"
            if method == "human_override" else f"{name} derivado de evidências concordantes"
        ),
    )


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
    content = "\n".join(text for _, text in pages)[:20000]
    sample = f"{document.title}\n{content}".casefold()
    matches = {
        role
        for role, markers in _ROLE_MARKERS.items()
        if any(_has_marker(sample, marker) for marker in markers)
    }
    return cast(DocumentRole, next(iter(matches))) if len(matches) == 1 else "unknown"


def _answer_key_state(
    document: NormalizedDocument, pages: Sequence[tuple[int, str]]
) -> AnswerKeyState:
    content = "\n".join(value for _, value in pages)
    text = f"{document.title}\n{content}".casefold()
    preliminary = any(_has_marker(text, marker) for marker in _PRELIMINARY_MARKERS)
    definitive = any(_has_marker(text, marker) for marker in _DEFINITIVE_MARKERS)
    if preliminary == definitive:
        return "unknown"
    return "preliminary" if preliminary else "definitive"


def _has_marker(text: str, marker: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text) is not None


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
