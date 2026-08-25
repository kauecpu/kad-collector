from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from .editorial_taxonomy import EditorialTaxonomy
from .json_utils import read_json

REFERENCE_REVIEW_SCHEMA_VERSION = 2
REFERENCE_REVIEW_KIND = "canonical-ai-reference-review"

ReferenceReviewStatus = Literal[
    "agent_reviewed_reference",
    "ambiguous_reference",
    "structural_only_reference",
    "rejected_reference",
]
REFERENCE_REVIEW_STATUSES = frozenset(
    {
        "agent_reviewed_reference",
        "ambiguous_reference",
        "structural_only_reference",
        "rejected_reference",
    }
)


class ReferenceReviewError(ValueError):
    """The offline reference-review artifact is incomplete or inconsistent."""


@dataclass(frozen=True)
class ReferenceReview:
    source_question_id: str
    content_fingerprint: str
    status: ReferenceReviewStatus
    structural_expected: dict[str, str]
    reviewed_expected: dict[str, str] | None
    reason_code: str


def _classification(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ReferenceReviewError(f"{field} deve ser um objeto")
    required = ("discipline", "matter", "subject", "level")
    parsed = {name: str(value.get(name) or "").strip() for name in required}
    if any(not parsed[name] for name in required):
        raise ReferenceReviewError(f"{field} deve conter os quatro campos editoriais")
    return parsed


def _is_taxonomy_path(expected: Mapping[str, str], taxonomy: EditorialTaxonomy) -> bool:
    if expected["level"] not in {"Fundamental", "Médio", "Superior"}:
        return False
    return any(
        path.discipline == expected["discipline"]
        and path.matter == expected["matter"]
        and path.subject == expected["subject"]
        for path in taxonomy.candidate_paths()
    )


def load_reference_reviews(
    path: Path, *, taxonomy: EditorialTaxonomy
) -> dict[str, ReferenceReview]:
    payload = cast(dict[str, Any], read_json(path))
    if payload.get("schemaVersion") != REFERENCE_REVIEW_SCHEMA_VERSION:
        raise ReferenceReviewError("versão do schema de revisão incompatível")
    if payload.get("kind") != REFERENCE_REVIEW_KIND:
        raise ReferenceReviewError("arquivo não é uma revisão de referências canônicas")
    if payload.get("taxonomyVersion") != taxonomy.version:
        raise ReferenceReviewError("versão da taxonomia da revisão não confere")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ReferenceReviewError("revisão sem lista de registros")

    reviews: dict[str, ReferenceReview] = {}
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            raise ReferenceReviewError("registro de revisão inválido")
        source_id = str(raw.get("sourceQuestionId") or "").strip()
        if not source_id:
            raise ReferenceReviewError("registro sem sourceQuestionId")
        if source_id in reviews:
            raise ReferenceReviewError(f"sourceQuestionId duplicado: {source_id}")
        raw_status = str(raw.get("status") or "")
        if raw_status == "human_review":
            raise ReferenceReviewError("human_review não é um status permitido")
        if raw_status not in REFERENCE_REVIEW_STATUSES:
            raise ReferenceReviewError(f"status de revisão inválido: {raw_status}")
        status = cast(ReferenceReviewStatus, raw_status)
        structural = _classification(raw.get("structuralExpected"), field="structuralExpected")
        reviewed_raw = raw.get("reviewedExpected")
        if status == "agent_reviewed_reference":
            reviewed = _classification(reviewed_raw, field="reviewedExpected")
            if not _is_taxonomy_path(reviewed, taxonomy):
                raise ReferenceReviewError(
                    f"reviewedExpected não corresponde a um caminho taxonômico: {source_id}"
                )
        else:
            if reviewed_raw is not None:
                raise ReferenceReviewError(
                    f"reviewedExpected só é permitido para agent_reviewed_reference: {source_id}"
                )
            reviewed = None
        fingerprint = str(raw.get("contentFingerprint") or "").strip()
        reason_code = str(raw.get("reasonCode") or "").strip()
        if not fingerprint or not reason_code:
            raise ReferenceReviewError(f"revisão sem fingerprint ou reasonCode: {source_id}")
        reviews[source_id] = ReferenceReview(
            source_question_id=source_id,
            content_fingerprint=fingerprint,
            status=status,
            structural_expected=structural,
            reviewed_expected=reviewed,
            reason_code=reason_code,
        )
    return reviews
