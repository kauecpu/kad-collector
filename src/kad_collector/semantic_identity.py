from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

from pydantic import ConfigDict

from .models import StrictModel

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


class FrozenSemanticModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _norm(value: SemanticValue) -> SemanticValue:
    if isinstance(value, int):
        return value
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


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
            normalized_value=_norm(value), strength="medium"
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

    @classmethod
    def unknown(cls, reason: str) -> SemanticField:
        return cls(status="unknown", method="unresolved", reason=reason)

    @classmethod
    def from_evidence(cls, name: str, evidence: tuple[SemanticEvidence, ...]) -> SemanticField:
        ordered = tuple(sorted(
            evidence, key=lambda item: (str(item.normalized_value), item.source, item.locator)
        ))
        values = tuple(sorted({item.normalized_value for item in ordered}, key=str))
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
    payload = "".join(f"\n--- PAGE {number} ---\n{text}" for number, text in normalized)
    return ContentFingerprint(sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                              page_sha256s=page_hashes, page_count=len(normalized),
                              character_count=sum(len(text) for _, text in normalized))
