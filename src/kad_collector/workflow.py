from __future__ import annotations

from pathlib import Path

from .ai_processor import ChunkExtractor, process_extraction_manifest
from .collector import collect_documents
from .config import config_for_urls, load_config
from .json_utils import read_json, write_json
from .models import CollectionFilters, QuestionBatch, QuestionReport
from .pdf_extractor import extract_manifest
from .reporting import build_question_report


def read_requested_urls(urls: list[str], files: list[Path]) -> list[str]:
    requested = list(urls)
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError as exc:
            raise ValueError(f"arquivo de links nao encontrado: {path}") from exc
        requested.extend(
            line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
        )
    return list(dict.fromkeys(url.strip() for url in requested if url.strip()))


def run_semiautomatic(
    *,
    config_path: Path,
    urls: list[str],
    filters: CollectionFilters | None = None,
    output_path: Path | None = None,
    model: str | None = None,
    max_chars: int = 40_000,
    overlap_chars: int = 3_000,
    extractor: ChunkExtractor | None = None,
) -> tuple[QuestionReport, Path]:
    config = config_for_urls(load_config(config_path), urls)
    download_manifest, download_path = collect_documents(config, filters)
    data_dir = Path(config.collector.data_dir)
    extraction_path = data_dir / "extracted" / f"{download_path.stem}-extracted.json"
    extraction_manifest, extraction_path = extract_manifest(download_path, extraction_path)

    eligible_documents = [
        document
        for document in extraction_manifest.documents
        if document.document.document_type == "exam" and not document.needs_ocr
    ]
    batch_paths: list[Path] = []
    if eligible_documents:
        batch_paths = process_extraction_manifest(
            extraction_path,
            data_dir / "processed",
            model=model,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            extractor=extractor,
        )
    batches = [QuestionBatch.model_validate(read_json(path)) for path in batch_paths]
    report = build_question_report(
        requested_urls=urls,
        download_manifest=download_manifest,
        download_path=download_path,
        extraction_manifest=extraction_manifest,
        extraction_path=extraction_path,
        batches=batches,
        batch_paths=batch_paths,
    )
    if output_path is None:
        timestamp = report.created_at.strftime("%Y%m%dT%H%M%SZ")
        output_path = data_dir / "results" / f"semi-{timestamp}-{report.run_id[:8]}.json"
    write_json(output_path, report.model_dump(mode="json"))
    return report, output_path
