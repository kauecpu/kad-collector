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

from .collector import collect_documents
from .config import ConfigError, load_config_text
from .desktop_limits import MAX_BATCH_PDFS
from .desktop_models import DesktopDocumentType, DesktopImportMetadata
from .desktop_processor import DesktopProcessor
from .desktop_store import DesktopStore
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


def _document_variant(document: DocumentRecord) -> str | None:
    candidate = f"{document.title} {document.original_url} {document.resolved_url}"
    match = re.search(r"(?<![A-Z0-9])V(?P<number>[1-9]\d*)(?!\d)", candidate, re.IGNORECASE)
    return f"V{match.group('number')}" if match else None


def _import_metadata(
    source: SourceDefinition,
    url: str,
    document: DocumentRecord | None = None,
) -> DesktopImportMetadata:
    metadata = {**source.metadata, **(document.metadata if document else {})}
    raw_year = metadata.get("ano")
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
        external_id=document.sha256 if document is not None else None,
        document_title=document.title if document is not None else None,
        variant=_document_variant(document) if document is not None else None,
        document_type=document_type,
        concurso=metadata.get("concurso") or metadata.get("cargo") or source.name,
        board=metadata.get("banca"),
        year=year,
        role=metadata.get("cargo"),
        organization=metadata.get("orgao"),
        level=level,
    )


class DesktopCollectionManager:
    def __init__(
        self,
        data_dir: Path,
        store: DesktopStore,
        processor: DesktopProcessor,
    ) -> None:
        self.data_dir = data_dir
        self.store = store
        self.processor = processor
        self.config = load_desktop_source_config(data_dir)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

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
                }
            )
        return catalog

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [dict(job) for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: str(item["createdAt"]), reverse=True)

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
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run,
            args=(job_id, source, normalized_url, classifier_provider),
            name=f"kad-collection-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job_id

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
                summary = self.store.job_question_summary(import_job_ids)
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
    ) -> None:
        self._update(job_id, status="running")
        try:
            selected_source = source.model_copy(update={"start_urls": [url]})
            run_config = AppConfig(
                collector=self.config.collector,
                sources=[selected_source],
            )
            manifest, manifest_path = collect_documents(run_config)
            paths = [Path(document.local_path).resolve() for document in manifest.documents]
            import_job_ids: list[str] = []
            metadata = _import_metadata(source, url)
            metadata_by_path = {
                str(Path(document.local_path).resolve()).casefold(): _import_metadata(
                    source, url, document
                )
                for document in manifest.documents
            }
            for start in range(0, len(paths), MAX_BATCH_PDFS):
                batch_paths = paths[start : start + MAX_BATCH_PDFS]
                import_job_id = self.store.create_job(
                    batch_paths,
                    metadata,
                    classifier_provider,
                    metadata_by_path={
                        str(path).casefold(): metadata_by_path[str(path).casefold()]
                        for path in batch_paths
                    },
                )
                import_job_ids.append(import_job_id)
                self.processor.start(import_job_id)
            warnings = list(manifest.warnings)
            if not paths:
                warnings.append("Nenhum PDF compativel foi encontrado neste link.")
            files = [
                {
                    "title": document.title,
                    "documentType": document.document_type,
                    "localPath": str(Path(document.local_path).resolve()),
                    "sourceUrl": document.original_url,
                    "sizeBytes": document.size_bytes,
                }
                for document in manifest.documents
            ]
            self._update(
                job_id,
                status="processing",
                documents=len(manifest.documents),
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
            )
            self._wait_for_processing(job_id, import_job_ids)
        except (OSError, RuntimeError, ValueError) as exc:
            self._update(
                job_id,
                status="failed",
                completedAt=datetime.now(UTC).isoformat(),
                error=str(exc),
            )
