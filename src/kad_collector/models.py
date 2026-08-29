from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_FilterValue = TypeVar("_FilterValue", int, str)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectorSettings(StrictModel):
    data_dir: str = "data"
    user_agent: str = "KADCollector/0.1"
    capacity_profile: Literal["conservative", "balanced", "high_performance", "custom"] = "balanced"
    request_interval_seconds: float = Field(default=3.0, ge=0.0)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    connect_timeout_seconds: float = Field(default=10.0, ge=1.0, le=120.0)
    max_files_per_source: int | None = Field(default=20, ge=1, le=10_000)
    max_html_bytes: int = Field(default=5_000_000, ge=1_024)
    max_pdf_bytes: int = Field(default=50_000_000, ge=1_024)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    max_retries: int = Field(default=4, ge=0, le=10)
    retry_max_delay_seconds: float = Field(default=120.0, ge=0.1, le=3_600.0)
    conditional_cache: bool = True
    development_cache: bool = False
    resume_downloads: bool = True
    disk_quota_bytes: int | None = Field(default=5_000_000_000, ge=1_000_000)
    cloudflare_bypass_enabled: bool = True


DiscoveryStrategy = Literal["html", "sitemap", "feed", "json", "browser"]


def _default_discovery_strategies() -> list[DiscoveryStrategy]:
    return ["html"]


class JsonDiscoveryEndpoint(StrictModel):
    url: str
    items_path: str = "items"
    url_field: str = "url"
    title_field: str = "title"
    type_field: str | None = None
    next_page_field: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


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
    collection_url_patterns: list[str] = Field(default_factory=list)
    pagination_patterns: list[str] = Field(default_factory=list)
    max_pages_per_run: int | None = Field(default=20, ge=1, le=10_000)
    discovery_strategies: list[DiscoveryStrategy] = Field(
        default_factory=_default_discovery_strategies
    )
    sitemap_urls: list[str] = Field(default_factory=list)
    feed_urls: list[str] = Field(default_factory=list)
    json_endpoints: list[JsonDiscoveryEndpoint] = Field(default_factory=list)
    browser_enabled: bool = False
    page_transport: Literal["http", "scrapling"] = "http"
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    request_interval_seconds: float | None = Field(default=None, ge=0.0)
    robots_policy: Literal["enforce", "observe", "ignore"] = "enforce"
    crawl_delay_policy: Literal["enforce", "observe", "ignore"] = "enforce"
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
        if (
            self.pagination_patterns
            and self.max_pages_per_run is not None
            and self.max_pages_per_run < len(self.start_urls)
        ):
            raise ValueError("max_pages_per_run nao pode ser menor que start_urls")
        if not self.discovery_strategies:
            raise ValueError("discovery_strategies exige pelo menos uma estrategia")
        if "browser" in self.discovery_strategies and not self.browser_enabled:
            raise ValueError("a estrategia browser exige browser_enabled=true")
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


class CollectionFailure(StrictModel):
    source_id: str
    url: str
    stage: Literal["robots", "discovery", "download"]
    message: str
    retryable: bool = False


class CollectionTelemetryEvent(StrictModel):
    occurred_at: datetime
    source_id: str
    url: str
    strategy: str
    outcome: str
    status_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    bytes_received: int = Field(default=0, ge=0)
    attempt: int = Field(default=1, ge=1)
    wait_seconds: float = Field(default=0.0, ge=0.0)
    cache_status: Literal["disabled", "miss", "hit", "revalidated"] = "disabled"
    detail: str | None = None


class DownloadManifest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime
    documents: list[DocumentRecord]
    references: list[DiscoveryRecord] = Field(default_factory=list)
    filters: CollectionFilters = Field(default_factory=CollectionFilters)
    filtered_out_documents: int = Field(default=0, ge=0)
    duplicate_documents: int = Field(default=0, ge=0)
    failures: list[CollectionFailure] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    telemetry: list[CollectionTelemetryEvent] = Field(default_factory=list)
    collection_policy: dict[str, Any] = Field(default_factory=dict)


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
    discipline: str | None = None
    concurso: str | None = None
    level: Literal["Fundamental", "Médio", "Superior"] | None = None
    difficulty: Literal["Fácil", "Média", "Difícil"] | None = None
    explanation: str | None = None
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
    answer_key_document: DocumentRecord | None = None
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


ReviewDecisionStatus = Literal["pending", "approved", "rejected"]


class LocalQuestionDecision(StrictModel):
    question_number: int = Field(ge=1)
    status: ReviewDecisionStatus = "pending"
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    notes: str | None = None

    @model_validator(mode="after")
    def decided_item_requires_audit_fields(self) -> LocalQuestionDecision:
        audit_fields = (self.reviewed_by, self.reviewed_at, self.content_sha256)
        if self.status == "pending" and any(value is not None for value in audit_fields):
            raise ValueError("uma decisao pendente nao pode conter dados de aprovacao")
        if self.status != "pending" and any(value is None for value in audit_fields):
            raise ValueError("uma decisao concluida exige revisor, data e hash")
        if self.status == "rejected" and not (self.notes or "").strip():
            raise ValueError("uma questao rejeitada exige justificativa")
        return self


class LocalReviewSession(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime
    batch: QuestionBatch
    decisions: list[LocalQuestionDecision]

    @model_validator(mode="after")
    def decisions_must_match_questions(self) -> LocalReviewSession:
        question_numbers = [question.number for question in self.batch.questions]
        decision_numbers = [decision.question_number for decision in self.decisions]
        if len(set(question_numbers)) != len(question_numbers):
            raise ValueError("a sessao de revisao exige numeros de questao unicos")
        if len(set(decision_numbers)) != len(decision_numbers):
            raise ValueError("a sessao possui decisoes duplicadas")
        if sorted(question_numbers) != sorted(decision_numbers):
            raise ValueError("cada questao da sessao exige exatamente uma decisao")
        return self


class ReviewQueueItem(StrictModel):
    batch_id: str
    source_id: str
    source_title: str
    batch_path: str
    session_path: str
    answer_key_paths: list[str] = Field(default_factory=list)
    status: Literal["ready", "exception"]
    question_count: int = Field(ge=0)
    matched_answers: int = Field(ge=0)
    missing_answers: int = Field(ge=0)
    issues: list[str] = Field(default_factory=list)


class ReviewQueue(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime
    extraction_manifest: str
    items: list[ReviewQueueItem] = Field(default_factory=list)


class QuestionOrigin(StrictModel):
    source_id: str
    source_name: str
    document_title: str
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pages: list[int] = Field(default_factory=list)


class OrganizedQuestion(StrictModel):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    question: QuestionRecord
    origins: list[QuestionOrigin]
    issues: list[str] = Field(default_factory=list)


class RunMetrics(StrictModel):
    requested_links: int = Field(default=0, ge=0)
    collected_documents: int = Field(default=0, ge=0)
    duplicate_documents: int = Field(default=0, ge=0)
    filtered_out_documents: int = Field(default=0, ge=0)
    documents_needing_ocr: int = Field(default=0, ge=0)
    extracted_questions: int = Field(default=0, ge=0)
    filtered_out_questions: int = Field(default=0, ge=0)
    duplicate_questions: int = Field(default=0, ge=0)
    ready_questions: int = Field(default=0, ge=0)
    exception_questions: int = Field(default=0, ge=0)


class ReportArtifacts(StrictModel):
    download_manifest: str
    extraction_manifest: str
    question_batches: list[str] = Field(default_factory=list)
    review_queue: str | None = None


class QuestionReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: datetime
    requested_urls: list[str]
    filters: CollectionFilters = Field(default_factory=CollectionFilters)
    questions: list[OrganizedQuestion] = Field(default_factory=list)
    exceptions: list[OrganizedQuestion] = Field(default_factory=list)
    metrics: RunMetrics
    collection_failures: list[CollectionFailure] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifacts: ReportArtifacts


class RetryRecord(StrictModel):
    source_id: str
    url: str
    stage: Literal["discovery", "download"]
    attempts: int = Field(ge=1)
    last_error: str
    last_attempt_at: datetime
    next_attempt_at: datetime | None = None
    exhausted: bool = False


class AutomationState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    updated_at: datetime | None = None
    processed_documents: dict[str, datetime] = Field(default_factory=dict)
    known_references: dict[str, datetime] = Field(default_factory=dict)
    source_snapshots: dict[str, list[str]] = Field(default_factory=dict)
    retries: list[RetryRecord] = Field(default_factory=list)
    answer_key_manifests: list[str] = Field(default_factory=list)
    pending_review_batches: dict[str, str] = Field(default_factory=dict)


class AutomationMetrics(StrictModel):
    new_documents: int = Field(default=0, ge=0)
    known_documents: int = Field(default=0, ge=0)
    new_references: int = Field(default=0, ge=0)
    known_references: int = Field(default=0, ge=0)
    changed_sources: int = Field(default=0, ge=0)
    pending_retries: int = Field(default=0, ge=0)
    exhausted_retries: int = Field(default=0, ge=0)
    review_ready: int = Field(default=0, ge=0)
    review_exceptions: int = Field(default=0, ge=0)


class AutomationReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime
    state_path: str
    full_download_manifest: str
    automatic_metrics: AutomationMetrics
    changed_sources: list[str] = Field(default_factory=list)
    retry_queue: list[RetryRecord] = Field(default_factory=list)
    review_queue_path: str | None = None
    result: QuestionReport


class PromotionPackage(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    target: Literal["kad"] = "kad"
    package_id: str
    created_at: datetime
    batches: list[QuestionBatch] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
