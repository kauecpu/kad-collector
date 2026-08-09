from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    authorization_basis: str = ""
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


class DownloadManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime
    documents: list[DocumentRecord]
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
