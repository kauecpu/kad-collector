from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .models import StrictModel

DesktopDocumentType = Literal["auto", "exam", "answer_key"]
DesktopQuestionStatus = Literal["pending", "approved", "rejected", "exception", "exported"]
ClassifierProviderName = Literal["local", "openai"]


class DesktopImportMetadata(StrictModel):
    provider: str | None = None
    source_url: str | None = None
    canonical_url: str | None = None
    external_id: str | None = None
    document_title: str | None = None
    variant: str | None = None
    document_type: DesktopDocumentType = "auto"
    concurso: str | None = None
    board: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)
    role: str | None = None
    stage: str | None = None
    turn: str | None = None
    organization: str | None = None
    level: Literal["Fundamental", "Médio", "Superior"] | None = None
    discipline: str | None = None
    subject: str | None = None
    topic: str | None = None
    difficulty: Literal["Fácil", "Média", "Difícil"] | None = None

    @field_validator(
        "provider",
        "source_url",
        "canonical_url",
        "external_id",
        "document_title",
        "variant",
        "concurso",
        "board",
        "role",
        "stage",
        "turn",
        "organization",
        "discipline",
        "subject",
        "topic",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("source_url", "canonical_url")
    @classmethod
    def require_https_when_present(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("a URL de origem deve usar HTTPS")
        return value


class DesktopFilterSet(StrictModel):
    source_files: list[str] = Field(default_factory=list)
    concursos: list[str] = Field(default_factory=list)
    boards: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    variants: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    disciplines: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    difficulties: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    search: str = ""
    min_confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator(
        "source_files",
        "concursos",
        "boards",
        "roles",
        "variants",
        "levels",
        "disciplines",
        "subjects",
        "topics",
        "difficulties",
        "statuses",
        "quality_flags",
    )
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(value.split())
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                normalized.append(item)
        return normalized


class ClassificationValue(StrictModel):
    value: str | int | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: str | None = None
    source: str | None = None
    reason: str | None = None
    provenance: list[str] = Field(default_factory=list)


class QuestionClassification(StrictModel):
    concurso: ClassificationValue = Field(default_factory=ClassificationValue)
    board: ClassificationValue = Field(default_factory=ClassificationValue)
    year: ClassificationValue = Field(default_factory=ClassificationValue)
    role: ClassificationValue = Field(default_factory=ClassificationValue)
    organization: ClassificationValue = Field(default_factory=ClassificationValue)
    level: ClassificationValue = Field(default_factory=ClassificationValue)
    discipline: ClassificationValue = Field(default_factory=ClassificationValue)
    subject: ClassificationValue = Field(default_factory=ClassificationValue)
    topic: ClassificationValue = Field(default_factory=ClassificationValue)
    difficulty: ClassificationValue = Field(default_factory=ClassificationValue)


class ClassificationRequest(StrictModel):
    question_number: int = Field(ge=1)
    statement: str
    alternatives: list[str]
    section_title: str | None = None
    block_id: str | None = None
    context: str | None = None


class ClassificationResponseItem(StrictModel):
    question_number: int = Field(ge=1)
    classification: QuestionClassification


class ClassificationResponse(StrictModel):
    items: list[ClassificationResponseItem]


class TaxonomyChoiceResult(StrictModel):
    question_number: int = Field(ge=1)
    option_id: str
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)


class TaxonomyChoiceResponse(StrictModel):
    items: list[TaxonomyChoiceResult]
