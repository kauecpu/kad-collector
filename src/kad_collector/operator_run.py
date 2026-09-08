from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .collector import collect_documents
from .config import load_config, load_config_text
from .discovery_intelligence import expand_aliases, normalize_discovery_text
from .json_utils import read_json, write_json
from .models import (
    AppConfig,
    CollectionFilters,
    DocumentRecord,
    DownloadManifest,
    ExtractionManifest,
    SourceDefinition,
    StrictModel,
)
from .pdf_extractor import extract_manifest
from .structured_questions import (
    DEFAULT_OLLAMA_ENDPOINT,
    DEFAULT_QWEN_MODEL,
    StructuredQuestionPackage,
    build_structured_question_package,
)
from .url_utils import canonicalize_url, redact_text_secrets, redact_url_secrets

OPERATOR_RUN_VERSION = "1.0"

OperatorStage = Literal["discovery", "download", "extraction", "structuring"]
RunStatus = Literal["running", "interrupted", "failed", "completed"]


class CollectorCallable(Protocol):
    def __call__(
        self,
        config: AppConfig,
        filters: CollectionFilters | None = None,
        *,
        run_id: str | None = None,
    ) -> tuple[DownloadManifest, Path]: ...


class ExtractorCallable(Protocol):
    def __call__(
        self,
        manifest_path: Path,
        output_path: Path | None = None,
    ) -> tuple[ExtractionManifest, Path]: ...


class StructurerCallable(Protocol):
    def __call__(
        self,
        manifest_paths: list[Path],
        output_path: Path,
        *,
        extraction_dir: Path | None = None,
        ollama_endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
        qwen_model: str = DEFAULT_QWEN_MODEL,
        enable_ollama: bool = True,
    ) -> StructuredQuestionPackage: ...


class OperatorParameters(StrictModel):
    organization: str = Field(min_length=1)
    board: str = Field(min_length=1)
    start_year: int = Field(ge=1900, le=2100)
    end_year: int = Field(ge=1900, le=2100)

    @model_validator(mode="after")
    def validate_period(self) -> OperatorParameters:
        if self.start_year > self.end_year:
            raise ValueError("o ano inicial não pode ser maior que o ano final")
        return self

    def filters(self) -> CollectionFilters:
        return CollectionFilters(
            years=list(range(self.start_year, self.end_year + 1)),
            boards=[self.board],
            organizations=[self.organization],
        )


class OperatorFailure(StrictModel):
    stage: str
    source_id: str | None = None
    document_id: str | None = None
    message: str
    continuing: bool = True
    retryable: bool = False


class OperatorChanges(StrictModel):
    new_documents: int = Field(default=0, ge=0)
    removed_documents: int = Field(default=0, ge=0)
    changed_documents: int = Field(default=0, ge=0)


class OperatorMetrics(StrictModel):
    sources_consulted: int = Field(default=0, ge=0)
    documents_found: int = Field(default=0, ge=0)
    exams_accepted: int = Field(default=0, ge=0)
    answer_keys_accepted: int = Field(default=0, ge=0)
    questions_found: int = Field(default=0, ge=0)
    questions_ready: int = Field(default=0, ge=0)
    questions_quarantined: int = Field(default=0, ge=0)
    questions_rejected: int = Field(default=0, ge=0)
    rejected_files: int = Field(default=0, ge=0)
    duplicate_documents: int = Field(default=0, ge=0)
    duplicate_questions: int = Field(default=0, ge=0)
    handled_failures: int = Field(default=0, ge=0)
    ocr_pages: int = Field(default=0, ge=0)
    qwen_calls: int = Field(default=0, ge=0)


class OperatorRunState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    parameters: OperatorParameters
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    completed_stages: list[OperatorStage] = Field(default_factory=list)
    resumed_count: int = Field(default=0, ge=0)
    repeated_count: int = Field(default=0, ge=0)
    selected_sources: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    semantic_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    changes: OperatorChanges = Field(default_factory=OperatorChanges)
    metrics: OperatorMetrics = Field(default_factory=OperatorMetrics)
    last_error: str | None = None


class OperatorRunResult(StrictModel):
    state: OperatorRunState
    package: StructuredQuestionPackage
    failures: list[OperatorFailure]
    report_path: str


def _now() -> datetime:
    return datetime.now(UTC)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-") or "coleta"


def default_operator_output(parameters: OperatorParameters) -> Path:
    return Path("data/runs") / (
        f"{_safe_slug(parameters.organization)}-{_safe_slug(parameters.board)}-"
        f"{parameters.start_year}-{parameters.end_year}"
    )


def _matches_requested_value(configured: str, requested: str) -> bool:
    configured_aliases = expand_aliases(configured)
    requested_aliases = expand_aliases(requested)
    if configured_aliases.intersection(requested_aliases):
        return True
    configured_text = normalize_discovery_text(configured)
    requested_text = normalize_discovery_text(requested)
    return bool(
        configured_text
        and requested_text
        and (configured_text in requested_text or requested_text in configured_text)
    )


def _year_from_location(value: str) -> int | None:
    full_year = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value)
    if full_year is not None:
        return int(full_year.group(1))
    contest = re.search(r"(?i)/(?:concursos?|concurso)/([^/?#]+)", value)
    if contest is None:
        return None
    short_year = re.search(r"(?:^|[_-])(\d{2})(?:[_-]|$)", contest.group(1))
    if short_year is None:
        return None
    suffix = int(short_year.group(1))
    return 2000 + suffix if suffix < 80 else 1900 + suffix


def _source_for_period(
    source: SourceDefinition, parameters: OperatorParameters
) -> SourceDefinition | None:
    selected_urls = [
        url
        for url in source.start_urls
        if (year := _year_from_location(url)) is None
        or parameters.start_year <= year <= parameters.end_year
    ]
    if not selected_urls:
        return None
    return source.model_copy(update={"start_urls": selected_urls})


def select_operator_config(
    config: AppConfig,
    parameters: OperatorParameters,
    output_dir: Path,
    *,
    enable_ollama: bool,
) -> AppConfig:
    matching = [
        narrowed
        for source in config.sources
        if source.enabled
        and source.access_mode == "content"
        and _matches_requested_value(source.metadata.get("orgao", ""), parameters.organization)
        and _matches_requested_value(source.metadata.get("banca", ""), parameters.board)
        if (narrowed := _source_for_period(source, parameters)) is not None
    ]
    if not matching:
        raise ValueError(
            "nenhuma fonte de conteúdo cadastrada corresponde ao órgão e à banca informados"
        )
    requested_budget = min(
        1_000,
        max(
            config.collector.max_files_per_source or 0,
            50 * (parameters.end_year - parameters.start_year + 1),
        ),
    )
    settings = config.collector.model_copy(
        update={
            "data_dir": str((output_dir / "work").resolve()),
            "ai_discovery_enabled": config.collector.ai_discovery_enabled and enable_ollama,
            "max_files_per_source": requested_budget,
        }
    )
    return AppConfig(collector=settings, sources=matching)


def load_operator_source_config(config_path: Path) -> AppConfig:
    if config_path.is_file():
        return load_config(config_path)
    if config_path == Path("config/sources.official.toml"):
        packaged = resources.files("kad_collector").joinpath("sources.official.toml")
        return load_config_text(
            packaged.read_text(encoding="utf-8"),
            source_name="kad_collector/sources.official.toml",
        )
    return load_config(config_path)


def _deduplicate_manifest(manifest: DownloadManifest) -> DownloadManifest:
    selected: list[DocumentRecord] = []
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    duplicates = manifest.duplicate_documents
    for document in manifest.documents:
        try:
            identity = canonicalize_url(document.resolved_url or document.original_url)
        except ValueError:
            identity = redact_url_secrets(document.resolved_url or document.original_url)
        if identity in seen_urls or document.sha256 in seen_hashes:
            duplicates += 1
            continue
        seen_urls.add(identity)
        seen_hashes.add(document.sha256)
        selected.append(document)
    return manifest.model_copy(
        update={"documents": selected, "duplicate_documents": duplicates}
    )


def _filter_manifest_period(
    manifest: DownloadManifest, parameters: OperatorParameters
) -> DownloadManifest:
    selected: list[DocumentRecord] = []
    removed = manifest.filtered_out_documents
    for document in manifest.documents:
        declared = (
            document.metadata.get("ano")
            or document.metadata.get("year")
            or document.metadata.get("ano_publicacao")
        )
        year = int(declared) if declared and declared.isdigit() else None
        if year is None:
            year = _year_from_location(
                f"{document.original_url}\n{document.resolved_url}\n{document.title}"
            )
        if year is not None and not parameters.start_year <= year <= parameters.end_year:
            removed += 1
            continue
        selected.append(document)
    return manifest.model_copy(
        update={"documents": selected, "filtered_out_documents": removed}
    )


def _preserve_previous_documents_on_total_failure(
    current: DownloadManifest, previous: DownloadManifest | None
) -> DownloadManifest:
    if (
        current.documents
        or not current.failures
        or previous is None
        or not previous.documents
    ):
        return current
    return current.model_copy(
        update={
            "documents": previous.documents,
            "warnings": [
                *current.warnings,
                "A tentativa atual não encontrou documentos e registrou falhas; "
                "o último manifesto verificado foi preservado.",
            ],
        }
    )


def _manifest_is_reusable(manifest_path: Path) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        manifest = DownloadManifest.model_validate(read_json(manifest_path))
    except (OSError, ValueError):
        return False
    return bool(manifest.documents) and all(
        Path(document.local_path).is_file()
        and _file_sha256(Path(document.local_path)) == document.sha256
        for document in manifest.documents
    )


def _document_map(manifest: DownloadManifest | None) -> dict[str, str]:
    if manifest is None:
        return {}
    result: dict[str, str] = {}
    for document in manifest.documents:
        try:
            key = canonicalize_url(document.resolved_url or document.original_url)
        except ValueError:
            key = redact_url_secrets(document.resolved_url or document.original_url)
        result[key] = document.sha256
    return result


def _changes(
    previous: DownloadManifest | None, current: DownloadManifest
) -> OperatorChanges:
    old = _document_map(previous)
    new = _document_map(current)
    shared = set(old).intersection(new)
    return OperatorChanges(
        new_documents=len(set(new) - set(old)),
        removed_documents=len(set(old) - set(new)),
        changed_documents=sum(old[key] != new[key] for key in shared),
    )


def _failures(
    manifest: DownloadManifest | None,
    package: StructuredQuestionPackage | None,
) -> list[OperatorFailure]:
    failures: list[OperatorFailure] = []
    if manifest is not None:
        failures.extend(
            OperatorFailure(
                stage=item.stage,
                source_id=item.source_id,
                document_id=redact_url_secrets(item.url),
                message=redact_text_secrets(item.message),
                retryable=item.retryable,
            )
            for item in manifest.failures
        )
    if package is not None:
        failures.extend(
            OperatorFailure(
                stage=item.stage,
                document_id=item.document_id,
                message=redact_text_secrets(item.message),
            )
            for item in package.errors
        )
    return failures


def _metrics(
    selected_sources: int,
    manifest: DownloadManifest,
    package: StructuredQuestionPackage,
    failures: list[OperatorFailure],
) -> OperatorMetrics:
    package_metrics = package.metrics
    return OperatorMetrics(
        sources_consulted=selected_sources,
        documents_found=len(manifest.documents),
        exams_accepted=sum(item.document_type == "exam" for item in manifest.documents),
        answer_keys_accepted=sum(
            item.document_type == "answer_key" for item in manifest.documents
        ),
        questions_found=package_metrics.detected_questions,
        questions_ready=package_metrics.accepted_questions,
        questions_quarantined=package_metrics.quarantined_questions,
        questions_rejected=package_metrics.rejected_questions,
        rejected_files=(
            manifest.filtered_out_documents
            + sum(item.document_type == "other" for item in manifest.documents)
        ),
        duplicate_documents=manifest.duplicate_documents,
        duplicate_questions=package_metrics.duplicate_questions,
        handled_failures=len(failures),
        ocr_pages=package_metrics.ocr_pages,
        qwen_calls=package_metrics.qwen_calls,
    )


def _report_text(state: OperatorRunState, failures: list[OperatorFailure]) -> str:
    metrics = state.metrics
    parameters = state.parameters
    lines = [
        "# Relatório da operação do KAD Collector",
        "",
        f"- Execução: `{state.run_id}`",
        f"- Estado: `{state.status}`",
        f"- Órgão: {parameters.organization}",
        f"- Banca: {parameters.board}",
        f"- Período: {parameters.start_year}–{parameters.end_year}",
        f"- Fontes: {', '.join(state.selected_sources)}",
        f"- Retomadas: {state.resumed_count}",
        f"- Repetições: {state.repeated_count}",
        "- Publicação externa: não executada",
        "",
        "## Resultado",
        "",
        "| Métrica | Valor |",
        "|---|---:|",
        f"| Fontes consultadas | {metrics.sources_consulted} |",
        f"| Documentos encontrados | {metrics.documents_found} |",
        f"| Provas aceitas | {metrics.exams_accepted} |",
        f"| Gabaritos aceitos | {metrics.answer_keys_accepted} |",
        f"| Questões encontradas | {metrics.questions_found} |",
        f"| Questões prontas para revisão | {metrics.questions_ready} |",
        f"| Questões em quarentena | {metrics.questions_quarantined} |",
        f"| Questões rejeitadas | {metrics.questions_rejected} |",
        f"| Duplicatas de documentos | {metrics.duplicate_documents} |",
        f"| Duplicatas de questões | {metrics.duplicate_questions} |",
        f"| Páginas processadas por OCR | {metrics.ocr_pages} |",
        f"| Chamadas ao Qwen | {metrics.qwen_calls} |",
        f"| Falhas tratadas | {metrics.handled_failures} |",
        "",
        "## Mudanças desde a execução anterior",
        "",
        f"- Documentos novos: {state.changes.new_documents}",
        f"- Documentos removidos: {state.changes.removed_documents}",
        f"- Documentos alterados: {state.changes.changed_documents}",
        f"- Hash semântico: `{state.semantic_sha256 or 'indisponível'}`",
        "",
        "## Falhas tratadas",
        "",
    ]
    if not failures:
        lines.append("Nenhuma falha foi registrada.")
    else:
        lines.extend(
            f"- `{item.stage}`: {item.message} (continuação: "
            f"{'sim' if item.continuing else 'não'})"
            for item in failures
        )
    lines.extend(
        [
            "",
            "## Artefatos",
            "",
            *[f"- {key}: `{value}`" for key, value in sorted(state.artifacts.items())],
            "",
        ]
    )
    return "\n".join(lines)


def _write_report(path: Path, state: OperatorRunState, failures: list[OperatorFailure]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_report_text(state, failures), encoding="utf-8", newline="\n")


def _write_state(path: Path, state: OperatorRunState) -> None:
    write_json(path, state.model_dump(mode="json"))


def _load_previous_manifest(path: Path) -> DownloadManifest | None:
    if not path.is_file():
        return None
    try:
        return DownloadManifest.model_validate(read_json(path))
    except (OSError, ValueError):
        return None


def run_operator(
    *,
    config_path: Path,
    output_dir: Path,
    parameters: OperatorParameters | None = None,
    resume: bool = False,
    ollama_endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
    qwen_model: str = DEFAULT_QWEN_MODEL,
    enable_ollama: bool = True,
    collector: CollectorCallable = collect_documents,
    extractor: ExtractorCallable = extract_manifest,
    structurer: StructurerCallable = build_structured_question_package,
    emit: Callable[[str], None] = print,
) -> OperatorRunResult:
    output_dir = output_dir.resolve()
    state_path = output_dir / "run.json"
    manifest_path = output_dir / "manifest.json"
    package_path = output_dir / "review-package.json"
    failures_path = output_dir / "failures.json"
    report_path = output_dir / "report.md"

    existing_state: OperatorRunState | None = None
    if state_path.is_file():
        existing_state = OperatorRunState.model_validate(read_json(state_path))
    elif output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(
            "o diretório de saída contém arquivos sem run.json; escolha outro diretório"
        )

    if resume:
        if existing_state is None:
            raise ValueError("não existe run.json no diretório informado para retomada")
        if parameters is not None and parameters != existing_state.parameters:
            raise ValueError("os parâmetros informados diferem da execução que será retomada")
        parameters = existing_state.parameters
    elif parameters is None:
        raise ValueError("informe órgão, banca, ano inicial e ano final")

    assert parameters is not None
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_manifest = _load_previous_manifest(manifest_path)
    timestamp = _now()
    if existing_state is None:
        state = OperatorRunState(
            run_id=str(uuid.uuid4()),
            parameters=parameters,
            status="running",
            created_at=timestamp,
            updated_at=timestamp,
        )
    else:
        if not resume and existing_state.status != "completed":
            raise ValueError(
                "a execução anterior não terminou; use --resume para continuar"
            )
        state = existing_state.model_copy(
            update={
                "status": "running",
                "updated_at": timestamp,
                "last_error": None,
                "resumed_count": existing_state.resumed_count + (1 if resume else 0),
                "repeated_count": existing_state.repeated_count + (0 if resume else 1),
                "completed_stages": (
                    list(existing_state.completed_stages) if resume else []
                ),
            }
        )

    config = select_operator_config(
        load_operator_source_config(config_path),
        parameters,
        output_dir,
        enable_ollama=enable_ollama,
    )
    state = state.model_copy(
        update={
            "selected_sources": [source.id for source in config.sources],
            "artifacts": {
                "falhas": str(failures_path),
                "manifesto": str(manifest_path),
                "pacote_revisao": str(package_path),
                "relatorio": str(report_path),
            },
        }
    )
    _write_state(state_path, state)
    active_stage = "discovery"
    manifest: DownloadManifest | None = None
    package: StructuredQuestionPackage | None = None
    failures: list[OperatorFailure] = []

    try:
        reusable_manifest = (
            resume
            and "download" in state.completed_stages
            and _manifest_is_reusable(manifest_path)
        )
        if reusable_manifest:
            manifest = DownloadManifest.model_validate(read_json(manifest_path))
            emit("[1/6] Localizando documentos (checkpoint reutilizado)")
            emit("[2/6] Baixando e validando PDFs (arquivos válidos reutilizados)")
        else:
            state.completed_stages = [
                stage
                for stage in state.completed_stages
                if stage not in {"discovery", "download", "extraction", "structuring"}
            ]
            emit("[1/6] Localizando documentos")
            emit("[2/6] Baixando e validando PDFs")
            manifest, _generated_path = collector(
                config, parameters.filters(), run_id=state.run_id
            )
            manifest = _preserve_previous_documents_on_total_failure(
                _filter_manifest_period(_deduplicate_manifest(manifest), parameters),
                previous_manifest,
            )
            write_json(manifest_path, manifest.model_dump(mode="json"))
            state.completed_stages.extend(["discovery", "download"])
            state.updated_at = _now()
            _write_state(state_path, state)

        assert manifest is not None
        extraction_path = (
            output_dir
            / "work"
            / "extracted"
            / f"{_file_sha256(manifest_path)}-extracted.json"
        )
        active_stage = "extraction"
        if resume and "extraction" in state.completed_stages and extraction_path.is_file():
            ExtractionManifest.model_validate(read_json(extraction_path))
            emit("[3/6] Extraindo conteúdo (checkpoint reutilizado)")
        else:
            state.completed_stages = [
                stage for stage in state.completed_stages if stage != "structuring"
            ]
            emit("[3/6] Extraindo conteúdo")
            extractor(manifest_path, extraction_path)
            if "extraction" not in state.completed_stages:
                state.completed_stages.append("extraction")
            state.updated_at = _now()
            _write_state(state_path, state)

        active_stage = "structuring"
        if resume and "structuring" in state.completed_stages and package_path.is_file():
            emit("[4/6] Associando gabaritos (checkpoint reutilizado)")
            emit("[5/6] Estruturando questões (checkpoint reutilizado)")
            package = StructuredQuestionPackage.model_validate(read_json(package_path))
        else:
            emit("[4/6] Associando gabaritos")
            emit("[5/6] Estruturando questões")
            package = structurer(
                [manifest_path],
                package_path,
                extraction_dir=output_dir / "work" / "extracted",
                ollama_endpoint=ollama_endpoint,
                qwen_model=qwen_model,
                enable_ollama=enable_ollama,
            )
            if "structuring" not in state.completed_stages:
                state.completed_stages.append("structuring")
            state.updated_at = _now()
            _write_state(state_path, state)

        emit("[6/6] Gerando pacote de revisão")
        failures = _failures(manifest, package)
        write_json(failures_path, [item.model_dump(mode="json") for item in failures])
        state.metrics = _metrics(len(config.sources), manifest, package, failures)
        total_source_failure = not manifest.documents and bool(manifest.failures)
        state.status = "failed" if total_source_failure else "completed"
        state.updated_at = _now()
        state.semantic_sha256 = package.content_sha256
        state.changes = _changes(previous_manifest, manifest)
        state.last_error = (
            "nenhuma fonte entregou documentos nesta tentativa"
            if total_source_failure
            else None
        )
        _write_state(state_path, state)
        _write_report(report_path, state, failures)
    except BaseException as exc:
        interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
        failures = _failures(manifest, package)
        failures.append(
            OperatorFailure(
                stage=active_stage,
                message=redact_text_secrets(f"{type(exc).__name__}: {exc}"),
                continuing=False,
                retryable=interrupted,
            )
        )
        write_json(failures_path, [item.model_dump(mode="json") for item in failures])
        state.status = "interrupted" if interrupted else "failed"
        state.updated_at = _now()
        state.last_error = failures[-1].message
        _write_state(state_path, state)
        _write_report(report_path, state, failures)
        raise

    metrics = state.metrics
    emit("")
    emit("Execução concluída" if state.status == "completed" else "Execução sem resultado")
    emit("")
    emit(f"Fontes consultadas: {metrics.sources_consulted}")
    emit(f"Documentos encontrados: {metrics.documents_found}")
    emit(f"Provas aceitas: {metrics.exams_accepted}")
    emit(f"Gabaritos aceitos: {metrics.answer_keys_accepted}")
    emit(f"Questões encontradas: {metrics.questions_found}")
    emit(f"Questões prontas para revisão: {metrics.questions_ready}")
    emit(f"Questões em quarentena: {metrics.questions_quarantined}")
    emit(f"Arquivos rejeitados: {metrics.rejected_files}")
    emit(f"Duplicatas: {metrics.duplicate_documents + metrics.duplicate_questions}")
    emit(f"Falhas tratadas: {metrics.handled_failures}")
    emit(f"Chamadas ao Qwen: {metrics.qwen_calls}")
    emit("")
    emit(f"Pacote: {package_path}")
    emit(f"Relatório: {report_path}")
    return OperatorRunResult(
        state=state,
        package=package,
        failures=failures,
        report_path=str(report_path),
    )
