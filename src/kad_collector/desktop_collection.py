from __future__ import annotations

import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .collection_state import CollectionStateStore
from .collector import CollectionPaused, collect_documents
from .config import ConfigError, load_config_text
from .desktop_models import DesktopDocumentType, DesktopImportMetadata
from .desktop_processor import DesktopProcessor
from .desktop_store import DesktopStore
from .document_contract import normalize_collected_document
from .document_pipeline import DocumentPipeline
from .models import AppConfig, DocumentRecord, SourceDefinition
from .security import UnsafeUrlError, validate_public_url

_SOURCE_PRESENTATION: dict[str, tuple[str, str]] = {
    "fuvest_vestibular": (
        "Vestibulares",
        "Primeiras fases e gabaritos do Vestibular USP.",
    ),
    "coperve_ufsc_2026": (
        "Vestibulares",
        "Provas e gabaritos definitivos da selecao unificada UFSC/IFSC/IFC.",
    ),
    "fgv_conhecimento": (
        "Concursos",
        "Concursos e exames organizados pela FGV Conhecimento.",
    ),
    "inep_enem": (
        "Educacao",
        "Cadernos e gabaritos das edicoes anteriores do ENEM.",
    ),
    "inep_enade": (
        "Educacao",
        "Provas, gabaritos e padroes de resposta do ENADE por curso.",
    ),
    "inep_encceja": (
        "Educacao",
        "Cadernos e gabaritos do Encceja por nivel e aplicacao.",
    ),
    "inep_revalida": (
        "Educacao",
        "Provas e padroes de resposta das edicoes do Revalida.",
    ),
    "comvest_unicamp": (
        "Vestibulares",
        "Acervo historico e provas comentadas do Vestibular Unicamp.",
    ),
    "obmep_referencias": (
        "Olimpiadas",
        "Links oficiais de provas e solucoes da OBMEP; registro de referencia somente.",
    ),
    "uerj_vestibular": (
        "Vestibulares",
        "Acervo de provas, gabaritos e padroes de resposta do Vestibular Estadual.",
    ),
}


def load_desktop_source_config(data_dir: Path) -> AppConfig:
    source_file = resources.files("kad_collector").joinpath("sources.official.toml")
    config = load_config_text(
        source_file.read_text(encoding="utf-8"),
        source_name="kad_collector/sources.official.toml",
    )
    settings = config.collector.model_copy(
        update={"data_dir": str((data_dir / "collected").resolve())}
    )
    return config.model_copy(update={"collector": settings})


def _year_from_url(url: str) -> int | None:
    match = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", url)
    return int(match.group(1)) if match else None


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    port = parsed.port
    netloc = host if port in {None, 443} else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path) or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit(("https", netloc, path, query, ""))


def _document_variant(document: DocumentRecord) -> str | None:
    inherited = document.metadata.get("variant")
    if inherited is not None:
        match = re.fullmatch(r"(?i)tipo\s*([1-9]\d*)", inherited.strip())
        if match is not None:
            return f"Tipo {int(match.group(1))}"
    candidate = f"{document.title} {document.original_url} {document.resolved_url}"
    match = re.search(
        r"(?<![A-Z0-9])(?P<label>V|TIPO|PROVA)[-_ ]*(?P<number>[1-9]\d*)(?!\d)",
        candidate,
        re.IGNORECASE,
    )
    if match is None:
        return None
    label = "V" if match.group("label").casefold() == "v" else "Tipo "
    return f"{label}{match.group('number')}"


def _contest_name(source: SourceDefinition, url: str) -> str:
    path_parts = [unquote(part) for part in urlsplit(url).path.split("/") if part]
    if "concursos" in path_parts and path_parts[-1] != "concursos":
        return " ".join(path_parts[-1].replace("_", "-").split("-")).upper()
    return source.name


def _document_role(document: DocumentRecord | None) -> str | None:
    if document is None:
        return None
    if document.document_type == "answer_key":
        return None
    title = " ".join(document.title.split()).strip()
    if title and not re.fullmatch(
        r"(?i)(?:tipo|prova)\s*\d+|(?:caderno\s+de\s+)?quest(?:ões|oes)(?:\s+completo)?",
        title,
    ):
        return title
    stem = unquote(Path(urlsplit(document.original_url).path).stem)
    stem = re.sub(r"(?i)[-_ ]+(?:tipo|prova)[-_ ]*\d+.*$", "", stem)
    stem = re.sub(r"(?i)^(?:cn[sm]|nm|ns)\d+[-_ ]+", "", stem)
    stem = re.sub(r"(?i)(?:cn[sm]|nm|ns)\d{3,}$", "", stem)
    stem = re.sub(r"(?i)[-_ ]+(?:afrfb|atrfb)$", "", stem)
    role = " ".join(part for part in re.split(r"[-_]+", stem) if part)
    return role.title() if role else None


def _import_metadata(
    source: SourceDefinition,
    url: str,
    document: DocumentRecord | None = None,
) -> DesktopImportMetadata:
    metadata = {**source.metadata, **(document.metadata if document else {})}
    raw_year = metadata.get("ano") or metadata.get("ano_publicacao")
    year_candidate = (
        f"{document.title} {document.original_url} {document.resolved_url} {url}"
        if document is not None
        else url
    )
    year = int(raw_year) if raw_year and raw_year.isdigit() else _year_from_url(year_candidate)
    raw_level = metadata.get("nivel")
    level = (
        cast(Literal["Fundamental", "Médio", "Superior"], raw_level)
        if raw_level in {"Fundamental", "Médio", "Superior"}
        else None
    )
    document_type: DesktopDocumentType = "auto"
    if document is not None and document.document_type in {"exam", "answer_key"}:
        document_type = cast(DesktopDocumentType, document.document_type)
    return DesktopImportMetadata(
        provider=source.id,
        source_url=document.original_url if document is not None else url,
        canonical_url=(
            _canonical_url(document.resolved_url) if document is not None else _canonical_url(url)
        ),
        external_id=None,
        document_title=document.title if document is not None else None,
        variant=_document_variant(document) if document is not None else None,
        document_type=document_type,
        concurso=metadata.get("concurso") or _contest_name(source, url),
        board=metadata.get("banca"),
        year=year,
        role=metadata.get("cargo") or _document_role(document),
        stage=metadata.get("etapa") or metadata.get("fase"),
        turn=metadata.get("turno"),
        organization=metadata.get("orgao"),
        level=level,
    )


class DesktopCollectionManager:
    def __init__(
        self,
        data_dir: Path,
        store: DesktopStore,
        processor: DesktopProcessor,
        pipeline: DocumentPipeline | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.store = store
        self.processor = processor
        self.pipeline = pipeline or DocumentPipeline(store, processor)
        self.config = load_desktop_source_config(data_dir)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._controls: dict[str, threading.Event] = {}
        self._requests: dict[
            str,
            tuple[SourceDefinition, str, Literal["local", "openai"], dict[str, Any]],
        ] = {}

    def catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for source in self.config.sources:
            category, description = _SOURCE_PRESENTATION.get(
                source.id, ("Outras", "Fonte oficial cadastrada.")
            )
            catalog.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "category": category,
                    "description": description,
                    "defaultUrl": source.start_urls[0],
                    "urlHint": (
                        "Use a página específica do concurso, não o índice geral."
                        if source.collection_url_patterns
                        else None
                    ),
                    "allowedHosts": source.allowed_hosts,
                    "mode": source.access_mode,
                    "collectable": source.enabled and source.access_mode == "content",
                    "termsUrl": source.terms_url,
                    "metadata": source.metadata,
                    "notice": (
                        "Somente as referencias sao registradas; os arquivos recentes usam "
                        "uma rota de download bloqueada pelo robots.txt do host."
                        if source.id == "obmep_referencias"
                        else None
                    ),
                    "engine": {
                        "strategies": source.discovery_strategies,
                        "browserAvailable": source.browser_enabled,
                        "robotsPolicy": source.robots_policy,
                        "crawlDelayPolicy": source.crawl_delay_policy,
                        "maxConcurrency": source.max_concurrency,
                        "requestIntervalSeconds": source.request_interval_seconds,
                    },
                }
            )
        return catalog

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [dict(job) for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: str(item["createdAt"]), reverse=True)

    def engine_summary(self) -> dict[str, Any]:
        state = CollectionStateStore(
            Path(self.config.collector.data_dir) / "collection-engine.sqlite3"
        )
        return {
            "cache": state.cache_summary(),
            "profiles": ["conservative", "balanced", "high_performance", "custom"],
            "policyModes": ["enforce", "observe", "ignore"],
            "cloudflareBypassEnabled": self.cloudflare_bypass_enabled(),
        }

    def cloudflare_bypass_enabled(self) -> bool:
        return bool(self.store.get_preference("cloudflare_bypass_enabled", True))

    def set_cloudflare_bypass_enabled(self, enabled: bool) -> bool:
        value = bool(enabled)
        self.store.set_preference("cloudflare_bypass_enabled", value)
        return value

    def start(self, payload: dict[str, Any]) -> str:
        source_id = payload.get("sourceId")
        url = payload.get("url")
        raw_classifier_provider = payload.get("classifierProvider", "local")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("selecione uma fonte oficial")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("informe o link oficial que sera coletado")
        if raw_classifier_provider not in {"local", "openai"}:
            raise ValueError("classificador deve ser local ou openai")
        classifier_provider = cast(Literal["local", "openai"], raw_classifier_provider)
        if classifier_provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY nao esta configurada nesta sessao")

        source = self._source(source_id)
        if not source.enabled or source.access_mode != "content":
            raise ValueError("esta fonte esta disponivel somente para registro de referencias")
        normalized_url = url.strip()
        try:
            validate_public_url(normalized_url, source.allowed_hosts, resolve_dns=False)
        except UnsafeUrlError as exc:
            raise ValueError(str(exc)) from exc
        if source.collection_url_patterns and not any(
            re.search(pattern, normalized_url) for pattern in source.collection_url_patterns
        ):
            raise ValueError("use a pagina especifica do concurso, nao o indice geral")

        profile = payload.get("capacityProfile", "balanced")
        if profile not in {"conservative", "balanced", "high_performance", "custom"}:
            raise ValueError("perfil de capacidade invalido")
        browser_enabled = payload.get("browserEnabled", False)
        if not isinstance(browser_enabled, bool):
            raise ValueError("browserEnabled deve ser booleano")
        custom_concurrency = payload.get("maxConcurrency")
        if custom_concurrency is not None and (
            not isinstance(custom_concurrency, int) or not 1 <= custom_concurrency <= 32
        ):
            raise ValueError("maxConcurrency deve estar entre 1 e 32")
        custom_interval = payload.get("requestIntervalSeconds")
        if custom_interval is not None and (
            not isinstance(custom_interval, (int, float)) or not 0 <= custom_interval <= 300
        ):
            raise ValueError("requestIntervalSeconds deve estar entre 0 e 300")
        robots_policy = payload.get("robotsPolicy", source.robots_policy)
        crawl_delay_policy = payload.get("crawlDelayPolicy", source.crawl_delay_policy)
        policy_modes = {"enforce", "observe", "ignore"}
        if robots_policy not in policy_modes or crawl_delay_policy not in policy_modes:
            raise ValueError("politica de robots ou Crawl-delay invalida")
        cloudflare_bypass = payload.get("cloudflareBypass", self.cloudflare_bypass_enabled())
        if not isinstance(cloudflare_bypass, bool):
            raise ValueError("cloudflareBypass deve ser booleano")
        engine_options = {
            "capacityProfile": profile,
            "browserEnabled": browser_enabled,
            "maxConcurrency": custom_concurrency,
            "requestIntervalSeconds": custom_interval,
            "robotsPolicy": robots_policy,
            "crawlDelayPolicy": crawl_delay_policy,
            "cloudflareBypass": cloudflare_bypass,
        }

        job_id = str(uuid.uuid4())
        job: dict[str, Any] = {
            "id": job_id,
            "sourceId": source.id,
            "sourceName": source.name,
            "url": normalized_url,
            "status": "queued",
            "createdAt": datetime.now(UTC).isoformat(),
            "completedAt": None,
            "documents": 0,
            "discoveredDocuments": 0,
            "downloadedDocuments": 0,
            "processedDocuments": 0,
            "failedDocuments": 0,
            "skippedDocuments": 0,
            "references": 0,
            "failures": 0,
            "questions": 0,
            "matchedAnswers": 0,
            "annulledAnswers": 0,
            "missingAnswers": 0,
            "warnings": [],
            "manifestPath": None,
            "outputDirectory": None,
            "files": [],
            "failureDetails": [],
            "importJobIds": [],
            "error": None,
            "capacityProfile": profile,
            "robotsPolicy": robots_policy,
            "crawlDelayPolicy": crawl_delay_policy,
            "strategies": [
                *source.discovery_strategies,
                *(
                    ["browser"]
                    if browser_enabled and "browser" not in source.discovery_strategies
                    else []
                ),
            ],
            "telemetry": {
                "requests": 0,
                "bytes": 0,
                "retries": 0,
                "cacheHits": 0,
            },
        }
        with self._lock:
            self._jobs[job_id] = job
            self._controls[job_id] = threading.Event()
            self._requests[job_id] = (
                source,
                normalized_url,
                classifier_provider,
                engine_options,
            )
        thread = threading.Thread(
            target=self._run,
            args=(job_id, source, normalized_url, classifier_provider, engine_options),
            name=f"kad-collection-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job_id

    def action(self, job_id: str, action: str) -> None:
        with self._lock:
            if job_id not in self._jobs:
                raise ValueError("coleta nao encontrada")
            job = self._jobs[job_id]
            control = self._controls[job_id]
            request = self._requests[job_id]
            if action in {"pause", "cancel"}:
                if job["status"] not in {"queued", "running"}:
                    raise ValueError("esta coleta nao pode ser interrompida neste estado")
                job["requestedAction"] = action
                job["status"] = "pausing" if action == "pause" else "cancelling"
                control.set()
                return
            if action != "resume" or job["status"] != "paused":
                raise ValueError("esta coleta nao pode ser retomada neste estado")
            control.clear()
            job["requestedAction"] = None
            job["status"] = "queued"
        source, url, classifier, engine_options = request
        threading.Thread(
            target=self._run,
            args=(job_id, source, url, classifier, engine_options),
            name=f"kad-collection-{job_id[:8]}",
            daemon=True,
        ).start()

    def _source(self, source_id: str) -> SourceDefinition:
        matches = [source for source in self.config.sources if source.id == source_id]
        if not matches:
            raise ConfigError(f"fonte oficial desconhecida: {source_id}")
        return matches[0]

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(changes)

    def _wait_for_processing(self, collection_id: str, import_job_ids: list[str]) -> None:
        if not import_job_ids:
            self._update(
                collection_id,
                status="needs_attention",
                completedAt=datetime.now(UTC).isoformat(),
            )
            return
        while True:
            jobs = [self.store.job(job_id) for job_id in import_job_ids]
            failed = [job for job in jobs if job["status"] == "failed"]
            if failed:
                errors = [str(job["error"]) for job in failed if job.get("error")]
                self._update(
                    collection_id,
                    status="failed",
                    completedAt=datetime.now(UTC).isoformat(),
                    error="; ".join(errors) or "processamento dos PDFs falhou",
                )
                return
            if all(job["status"] == "completed" for job in jobs):
                self.processor.reconcile_all_answer_keys()
                summary = self.store.job_question_summary(import_job_ids)
                processed_paths: set[str] = set()
                failed_paths: set[str] = set()
                for import_job_id in import_job_ids:
                    for document in self.store.documents_for_job(import_job_id):
                        if document["status"] == "processed":
                            processed_paths.add(str(document["local_path"]).casefold())
                        elif document["status"] == "exception":
                            failed_paths.add(str(document["local_path"]).casefold())
                warnings = list(self._jobs[collection_id]["warnings"])
                needs_attention = not summary["questions"] or bool(summary["missing_answers"])
                if not summary["questions"]:
                    warnings.append("Nenhuma questão foi reconhecida nos PDFs coletados.")
                elif summary["missing_answers"]:
                    warnings.append(
                        f"{summary['missing_answers']} questão(ões) ficaram sem resposta oficial."
                    )
                self._update(
                    collection_id,
                    status="needs_attention" if needs_attention else "completed",
                    completedAt=datetime.now(UTC).isoformat(),
                    questions=summary["questions"],
                    matchedAnswers=summary["matched_answers"],
                    annulledAnswers=summary["annulled_answers"],
                    missingAnswers=summary["missing_answers"],
                    processedDocuments=len(processed_paths),
                    failedDocuments=len(failed_paths - processed_paths),
                    warnings=list(dict.fromkeys(warnings)),
                )
                return
            time.sleep(0.2)

    def _run(
        self,
        job_id: str,
        source: SourceDefinition,
        url: str,
        classifier_provider: Literal["local", "openai"],
        engine_options: dict[str, Any],
    ) -> None:
        self._update(job_id, status="running")
        try:
            strategies = list(source.discovery_strategies)
            browser_required = source.browser_enabled and "browser" in strategies
            browser_enabled = bool(engine_options["browserEnabled"]) or browser_required
            if browser_enabled and "browser" not in strategies:
                strategies.append("browser")
            selected_source = source.model_copy(
                update={
                    "start_urls": [url],
                    "browser_enabled": browser_enabled,
                    "discovery_strategies": strategies,
                    "page_transport": "scrapling" if browser_enabled else "http",
                    "max_concurrency": engine_options.get("maxConcurrency"),
                    "request_interval_seconds": engine_options.get("requestIntervalSeconds"),
                    "robots_policy": engine_options["robotsPolicy"],
                    "crawl_delay_policy": engine_options["crawlDelayPolicy"],
                }
            )
            profile = engine_options["capacityProfile"]
            profile_updates: dict[str, Any] = {"capacity_profile": profile}
            if profile == "conservative":
                profile_updates.update({"max_concurrency": 2, "request_interval_seconds": 3.0})
            elif profile == "high_performance":
                profile_updates.update(
                    {
                        "max_concurrency": engine_options.get("maxConcurrency") or 8,
                        "request_interval_seconds": (
                            engine_options.get("requestIntervalSeconds")
                            if engine_options.get("requestIntervalSeconds") is not None
                            else 0.0
                        ),
                    }
                )
            elif profile == "custom":
                if engine_options.get("maxConcurrency") is not None:
                    profile_updates["max_concurrency"] = engine_options["maxConcurrency"]
                if engine_options.get("requestIntervalSeconds") is not None:
                    profile_updates["request_interval_seconds"] = engine_options[
                        "requestIntervalSeconds"
                    ]
            profile_updates["cloudflare_bypass_enabled"] = bool(engine_options["cloudflareBypass"])
            run_settings = self.config.collector.model_copy(update=profile_updates)
            run_config = AppConfig(
                collector=run_settings,
                sources=[selected_source],
            )
            manifest, manifest_path = collect_documents(
                run_config,
                run_id=job_id,
                stop_event=self._controls[job_id],
            )
            paths = [Path(document.local_path).resolve() for document in manifest.documents]
            known_hashes = self.store.processed_sha256s(
                document.sha256 for document in manifest.documents
            )
            skipped_documents = [
                document for document in manifest.documents if document.sha256 in known_hashes
            ]
            processing_documents = [
                document for document in manifest.documents if document.sha256 not in known_hashes
            ]
            if (
                processing_documents
                and not any(
                    document.document_type == "exam" for document in processing_documents
                )
                and any(document.document_type == "exam" for document in skipped_documents)
            ):
                skipped_documents.extend(processing_documents)
                processing_documents = []
            normalized_documents = []
            for document in processing_documents:
                editorial_metadata = _import_metadata(source, url, document).model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude={
                        "provider",
                        "source_url",
                        "canonical_url",
                        "external_id",
                        "document_title",
                        "document_type",
                    },
                )
                if (
                    "ano_publicacao" in document.metadata
                    and "ano" not in document.metadata
                    and "year" not in document.metadata
                ):
                    editorial_metadata.pop("year", None)
                contract_metadata = {**document.metadata, **editorial_metadata}
                contract_metadata.pop("external_id", None)
                normalized_documents.append(
                    normalize_collected_document(document, source_page_url=url).model_copy(
                        update={"metadata": contract_metadata}
                    )
                )
            import_job_ids = self.pipeline.submit(normalized_documents, classifier_provider)
            warnings = list(manifest.warnings)
            if skipped_documents:
                warnings.append(f"{len(skipped_documents)} PDF(s): já processado — ignorado.")
            if not paths:
                warnings.append("Nenhum PDF compativel foi encontrado neste link.")
            files = [
                {
                    "title": document.title,
                    "documentType": document.document_type,
                    "localPath": str(Path(document.local_path).resolve()),
                    "sourceUrl": document.original_url,
                    "sizeBytes": document.size_bytes,
                    "processingStatus": (
                        "already_processed" if document.sha256 in known_hashes else "new"
                    ),
                }
                for document in manifest.documents
            ]
            self._update(
                job_id,
                status="processing",
                documents=len(manifest.documents),
                discoveredDocuments=(
                    len(manifest.documents)
                    + manifest.filtered_out_documents
                    + sum(failure.stage == "download" for failure in manifest.failures)
                ),
                downloadedDocuments=len(manifest.documents),
                skippedDocuments=len(skipped_documents),
                references=len(manifest.references),
                failures=len(manifest.failures),
                warnings=warnings,
                manifestPath=str(manifest_path.resolve()),
                outputDirectory=(
                    str(paths[0].parent)
                    if paths
                    else str((self.data_dir / "collected" / "raw").resolve())
                ),
                files=files,
                failureDetails=[
                    {
                        "stage": failure.stage,
                        "url": failure.url,
                        "message": failure.message,
                    }
                    for failure in manifest.failures
                ],
                importJobIds=import_job_ids,
                telemetry={
                    "requests": len(manifest.telemetry),
                    "bytes": sum(item.bytes_received for item in manifest.telemetry),
                    "retries": sum(item.attempt > 1 for item in manifest.telemetry),
                    "cacheHits": sum(
                        item.cache_status in {"hit", "revalidated"} for item in manifest.telemetry
                    ),
                },
            )
            if import_job_ids:
                self._wait_for_processing(job_id, import_job_ids)
            else:
                self._update(
                    job_id,
                    status="completed",
                    completedAt=datetime.now(UTC).isoformat(),
                )
        except CollectionPaused:
            with self._lock:
                requested = self._jobs[job_id].get("requestedAction")
            self._update(
                job_id,
                status="cancelled" if requested == "cancel" else "paused",
                completedAt=(datetime.now(UTC).isoformat() if requested == "cancel" else None),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._update(
                job_id,
                status="failed",
                completedAt=datetime.now(UTC).isoformat(),
                error=str(exc),
            )
