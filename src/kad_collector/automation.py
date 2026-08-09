from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .ai_processor import ChunkExtractor, process_extraction_manifest
from .collector import collect_documents
from .config import load_config
from .json_utils import read_json, write_json
from .models import (
    AppConfig,
    AutomationMetrics,
    AutomationReport,
    AutomationState,
    CollectionFailure,
    CollectionFilters,
    DownloadManifest,
    QuestionBatch,
    RetryRecord,
)
from .pdf_extractor import extract_manifest
from .reporting import build_question_report


def load_automation_state(path: Path) -> AutomationState:
    if not path.exists():
        return AutomationState()
    try:
        return AutomationState.model_validate(read_json(path))
    except (OSError, ValueError, ValidationError) as exc:
        raise ValueError(f"estado automatico invalido em {path}: {exc}") from exc


def _config_with_due_retries(config: AppConfig, state: AutomationState, now: datetime) -> AppConfig:
    retry_urls: dict[str, list[str]] = {}
    for retry in state.retries:
        if retry.exhausted or (retry.next_attempt_at and retry.next_attempt_at > now):
            continue
        retry_urls.setdefault(retry.source_id, []).append(retry.url)

    sources = []
    for source in config.sources:
        additional = retry_urls.get(source.id, [])
        sources.append(
            source.model_copy(
                update={"start_urls": list(dict.fromkeys(source.start_urls + additional))}
            )
        )
    return AppConfig(collector=config.collector, sources=sources)


def update_retry_queue(
    state: AutomationState,
    failures: list[CollectionFailure],
    *,
    now: datetime,
    max_attempts: int,
    base_delay_seconds: int,
) -> list[RetryRecord]:
    if max_attempts < 1:
        raise ValueError("max_attempts deve ser pelo menos 1")
    if base_delay_seconds < 1:
        raise ValueError("base_delay_seconds deve ser pelo menos 1")

    previous = {(item.source_id, item.url, item.stage): item for item in state.retries}
    current_failures = {
        (item.source_id, item.url, item.stage): item
        for item in failures
        if item.retryable and item.stage in {"discovery", "download"}
    }
    updated: list[RetryRecord] = []
    for key, failure in current_failures.items():
        old = previous.get(key)
        if old and old.exhausted:
            updated.append(old)
            continue
        attempts = (old.attempts if old else 0) + 1
        exhausted = attempts >= max_attempts
        next_attempt = None
        if not exhausted:
            delay = base_delay_seconds * (2 ** (attempts - 1))
            next_attempt = now + timedelta(seconds=delay)
        stage: Literal["discovery", "download"] = (
            "discovery" if failure.stage == "discovery" else "download"
        )
        updated.append(
            RetryRecord(
                source_id=failure.source_id,
                url=failure.url,
                stage=stage,
                attempts=attempts,
                last_error=failure.message,
                last_attempt_at=now,
                next_attempt_at=next_attempt,
                exhausted=exhausted,
            )
        )

    blocked_sources = {
        failure.source_id for failure in current_failures.values() if failure.stage == "discovery"
    }
    for key, old in previous.items():
        if key in current_failures:
            continue
        if old.source_id in blocked_sources or (
            not old.exhausted and old.next_attempt_at and old.next_attempt_at > now
        ):
            updated.append(old)
    return sorted(updated, key=lambda item: (item.source_id, item.url, item.stage))


def _update_source_snapshots(
    state: AutomationState,
    manifest: DownloadManifest,
    source_ids: list[str],
) -> list[str]:
    snapshots: dict[str, set[str]] = {source_id: set() for source_id in source_ids}
    for document in manifest.documents:
        snapshots.setdefault(document.source_id, set()).add(
            f"{document.original_url}#{document.sha256}"
        )
    for reference in manifest.references:
        snapshots.setdefault(reference.source_id, set()).add(reference.url)

    unavailable_sources = {
        failure.source_id
        for failure in manifest.failures
        if failure.stage in {"robots", "discovery"}
    }
    changed: list[str] = []
    for source_id, urls in snapshots.items():
        if source_id in unavailable_sources:
            continue
        current = sorted(urls)
        previous = state.source_snapshots.get(source_id)
        if previous is not None and previous != current:
            changed.append(source_id)
        state.source_snapshots[source_id] = current
    return sorted(changed)


def run_automatic(
    *,
    config_path: Path,
    filters: CollectionFilters | None = None,
    state_path: Path | None = None,
    output_path: Path | None = None,
    model: str | None = None,
    max_chars: int = 40_000,
    overlap_chars: int = 3_000,
    max_attempts: int = 3,
    base_delay_seconds: int = 300,
    extractor: ChunkExtractor | None = None,
    now: datetime | None = None,
) -> tuple[AutomationReport, Path]:
    current_time = now or datetime.now(UTC)
    base_config = load_config(config_path)
    data_dir = Path(base_config.collector.data_dir)
    state_path = state_path or data_dir / "state" / "automation.json"
    state = load_automation_state(state_path)
    config = _config_with_due_retries(base_config, state, current_time)

    full_manifest, full_manifest_path = collect_documents(config, filters)
    known_hashes = set(state.processed_documents)
    new_documents = [
        document for document in full_manifest.documents if document.sha256 not in known_hashes
    ]
    known_document_count = len(full_manifest.documents) - len(new_documents)
    new_references = [
        reference
        for reference in full_manifest.references
        if reference.url not in state.known_references
    ]
    known_reference_count = len(full_manifest.references) - len(new_references)

    new_manifest = full_manifest.model_copy(
        update={"documents": new_documents, "references": new_references}
    )
    new_manifest_path = full_manifest_path.with_name(
        full_manifest_path.name.replace("download-", "new-download-", 1)
    )
    write_json(new_manifest_path, new_manifest.model_dump(mode="json"))
    extraction_path = data_dir / "extracted" / f"{new_manifest_path.stem}-extracted.json"
    extraction_manifest, extraction_path = extract_manifest(new_manifest_path, extraction_path)

    batch_paths: list[Path] = []
    processing_error: str | None = None
    eligible_documents = [
        document
        for document in extraction_manifest.documents
        if document.document.document_type == "exam" and not document.needs_ocr
    ]
    eligible_hashes = {document.document.sha256 for document in eligible_documents}
    if eligible_documents:
        try:
            batch_paths = process_extraction_manifest(
                extraction_path,
                data_dir / "processed",
                model=model,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                extractor=extractor,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            processing_error = f"processamento automatico falhou: {exc}"

    batches = [QuestionBatch.model_validate(read_json(path)) for path in batch_paths]
    requested_urls = [
        url for source in base_config.sources if source.enabled for url in source.start_urls
    ]
    question_report = build_question_report(
        requested_urls=requested_urls,
        download_manifest=new_manifest,
        download_path=new_manifest_path,
        extraction_manifest=extraction_manifest,
        extraction_path=extraction_path,
        batches=batches,
        batch_paths=batch_paths,
    )
    if processing_error:
        question_report.warnings.append(processing_error)
    for document in new_documents:
        if not processing_error or document.sha256 not in eligible_hashes:
            state.processed_documents[document.sha256] = current_time

    for reference in new_references:
        state.known_references[reference.url] = current_time
    changed_sources = _update_source_snapshots(
        state,
        full_manifest,
        [source.id for source in base_config.sources if source.enabled],
    )
    state.retries = update_retry_queue(
        state,
        full_manifest.failures,
        now=current_time,
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
    )
    state.updated_at = current_time

    automatic_metrics = AutomationMetrics(
        new_documents=len(new_documents),
        known_documents=known_document_count,
        new_references=len(new_references),
        known_references=known_reference_count,
        changed_sources=len(changed_sources),
        pending_retries=sum(not retry.exhausted for retry in state.retries),
        exhausted_retries=sum(retry.exhausted for retry in state.retries),
    )
    automatic_report = AutomationReport(
        created_at=current_time,
        state_path=str(state_path),
        full_download_manifest=str(full_manifest_path),
        automatic_metrics=automatic_metrics,
        changed_sources=changed_sources,
        retry_queue=state.retries,
        result=question_report,
    )
    if output_path is None:
        timestamp = current_time.strftime("%Y%m%dT%H%M%SZ")
        output_path = (
            data_dir / "results" / f"automatic-{timestamp}-{question_report.run_id[:8]}.json"
        )
    write_json(state_path, state.model_dump(mode="json"))
    write_json(output_path, automatic_report.model_dump(mode="json"))
    return automatic_report, output_path
