from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_FilterValue = TypeVar("_FilterValue", int, str)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectorSettings(StrictModel):
    data_dir: str = "data"
    user_agent: str = "KADCollector/0.1"
    request_interval_seconds: float = Field(default=3.0, ge=1.0)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    max_files_per_source: int = Field(default=20, ge=1, le=500)
    max_html_bytes: int = Field(default=5_000_000, ge=1_024)
    max_pdf_bytes: int = Field(default=50_000_000, ge=1_024)


class CollectionFilters(StrictModel):
    years: list[int] = Field(default_factory=list)
    boards: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    matters: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)

    @field_validator("years")
    @classmethod
    def normalize_years(cls, years: list[int]) -> list[int]:
        normalized = sorted(set(years))
        if any(year < 1900 or year > 2100 for year in normalized):
            raise ValueError("anos devem estar entre 1900 e 2100")
        return normalized

    @field_validator("boards", "organizations", "roles", "matters", "subjects")
    @classmethod
    def normalize_text_filters(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(value.split())
            key = item.casefold()
            if not item:
                raise ValueError("filtros textuais nao podem ser vazios")
            if key not in seen:
                seen.add(key)
                normalized.append(item)
        return normalized

    def is_empty(self) -> bool:
        return not any(
            (
                self.years,
                self.boards,
                self.organizations,
                self.roles,
                self.matters,
                self.subjects,
            )
        )

    def merged_with(self, other: CollectionFilters) -> CollectionFilters:
        def intersect(
            field: str,
            current: list[_FilterValue],
            refinement: list[_FilterValue],
        ) -> list[_FilterValue]:
            if not current:
                return refinement
            if not refinement:
                return current
            common = [item for item in current if item in refinement]
            if not common:
                raise ValueError(f"filtro de {field} em process contradiz o filtro de collect")
            return common

        return CollectionFilters(
            years=intersect("ano", self.years, other.years),
            boards=intersect("banca", self.boards, other.boards),
            organizations=intersect("orgao", self.organizations, other.organizations),
            roles=intersect("cargo", self.roles, other.roles),
            matters=intersect("materia", self.matters, other.matters),
            subjects=intersect("assunto", self.subjects, other.subjects),
        )


class SourceDefinition(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=2)
    enabled: bool = False
    start_urls: list[str] = Field(min_length=1)
    allowed_hosts: list[str] = Field(min_length=1)
    include_patterns: list[str] = Field(default_factory=lambda: [r"(?i)\.pdf(?:$|\?)"])
    exclude_patterns: list[str] = Field(default_factory=list)
    exam_patterns: list[str] = Field(default_factory=lambda: [r"(?i)prova|caderno"])
    answer_key_patterns: list[str] = Field(default_factory=lambda: [r"(?i)gabarito"])
    access_mode: Literal["content", "reference_only"] = "content"
    authorization_basis: str = ""
    requires_written_authorization: bool = False
    written_authorization_reference: str | None = None
    terms_url: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, hosts: list[str]) -> list[str]:
        normalized = [host.strip().rstrip(".").lower() for host in hosts]
        if any(not host or ":" in host or "/" in host for host in normalized):
            raise ValueError("allowed_hosts deve conter apenas nomes de host")
        return normalized

    @model_validator(mode="after")
    def require_authorization_for_enabled_source(self) -> SourceDefinition:
        if self.enabled and not self.authorization_basis.strip():
            raise ValueError("uma fonte habilitada exige authorization_basis")
        if (
            self.enabled
            and self.requires_written_authorization
            and not (self.written_authorization_reference or "").strip()
        ):
            raise ValueError("a fonte habilitada exige written_authorization_reference")
        return self


class AppConfig(StrictModel):
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    sources: list[SourceDefinition] = Field(default_factory=list)


DocumentType = Literal["exam", "answer_key", "other"]


class DocumentRecord(StrictModel):
    source_id: str
    source_name: str
    document_type: DocumentType
    title: str
    original_url: str
    resolved_url: str
    local_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str
    size_bytes: int = Field(ge=1)
    downloaded_at: datetime
    authorization_basis: str
    terms_url: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class DiscoveryRecord(StrictModel):
    source_id: str
    source_name: str
    title: str
    url: str
    discovered_at: datetime
    authorization_basis: str
    written_authorization_reference: str | None = None
    terms_url: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class DownloadManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime
    documents: list[DocumentRecord]
    references: list[DiscoveryRecord] = Field(default_factory=list)
    filters: CollectionFilters = Field(default_factory=CollectionFilters)
    filtered_out_documents: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class ExtractedPage(StrictModel):
    number: int = Field(ge=1)
    text: str
    character_count: int = Field(ge=0)


class ExtractedDocument(StrictModel):
    document: DocumentRecord
    pages: list[ExtractedPage]
    text: str
    needs_ocr: bool = False
    warnings: list[str] = Field(default_factory=list)


class ExtractionManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime
    documents: list[ExtractedDocument]
    filters: CollectionFilters = Field(default_factory=CollectionFilters)
    filtered_out_documents: int = Field(default=0, ge=0)


class Alternative(StrictModel):
    letter: str = Field(pattern=r"^[A-H]$")
    text: str = Field(min_length=1)


class AIQuestion(StrictModel):
    number: int = Field(ge=1)
    statement: str = Field(min_length=1)
    alternatives: list[Alternative] = Field(min_length=2, max_length=8)
    matter: str | None
    subject: str | None
    board: str | None
    organization: str | None
    role: str | None
    year: int | None = Field(ge=1900, le=2100)
    source_pages: list[int]


class AIChunkResult(StrictModel):
    questions: list[AIQuestion]
    chunk_has_continuation: bool
    warnings: list[str]


class QuestionRecord(AIQuestion):
    correct_answer: str | None = Field(default=None, pattern=r"^[A-H]$")
    answer_status: Literal["missing", "matched", "annulled"] = "missing"
    review_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def answer_state_must_be_consistent(self) -> QuestionRecord:
        if self.answer_status == "matched" and self.correct_answer is None:
            raise ValueError("uma resposta matched exige correct_answer")
        if self.answer_status in {"missing", "annulled"} and self.correct_answer is not None:
            raise ValueError(f"uma resposta {self.answer_status} nao pode ter correct_answer")
        return self


class ReviewState(StrictModel):
    status: Literal["pending", "approved"] = "pending"
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    notes: str | None = None


class ValidationState(StrictModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class QuestionBatch(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    batch_id: str
    created_at: datetime
    model: str
    source_document: DocumentRecord
    questions: list[QuestionRecord]
    filters: CollectionFilters = Field(default_factory=CollectionFilters)
    filtered_out_questions: int = Field(default=0, ge=0)
    processing_warnings: list[str] = Field(default_factory=list)
    review: ReviewState = Field(default_factory=ReviewState)
    validation: ValidationState

    @model_validator(mode="after")
    def approval_must_be_complete(self) -> QuestionBatch:
        if self.review.status == "approved":
            required = (
                self.review.reviewed_by,
                self.review.reviewed_at,
                self.review.content_sha256,
            )
            if any(value is None for value in required):
                raise ValueError("um lote aprovado exige revisor, data e hash do conteudo")
        return self
