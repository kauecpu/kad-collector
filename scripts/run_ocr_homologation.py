"""Measure local OCR against ignored copies of official scanned exams.

The output contains metrics only. It never persists extracted exam text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import tomllib
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium  # type: ignore[import-untyped]

from kad_collector.ocr import OcrConfig, ocr_pdf_pages

QUESTION_MARKER = re.compile(
    r"(?mi)(?:^|\n)\s*(?:quest[aã]o\s*)?(\d{1,3})[.)]?\s"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _document_metrics(spec: dict[str, Any], corpus_dir: Path, config: OcrConfig) -> dict[str, Any]:
    path = corpus_dir / str(spec["local_filename"])
    if not path.is_file():
        raise FileNotFoundError(f"PDF local ausente: {path}")
    digest = _sha256(path)
    if digest != spec["sha256"]:
        raise ValueError(f"hash inesperado para {path.name}: {digest}")

    document = pdfium.PdfDocument(path)
    try:
        page_count = len(document)
        text_layer_characters = [
            len((page.get_textpage().get_text_range() or "").strip()) for page in document
        ]
    finally:
        document.close()
    if page_count != spec["expected_pages"]:
        raise ValueError(f"numero de paginas inesperado para {path.name}: {page_count}")

    requested = [
        index
        for index, character_count in enumerate(text_layer_characters, start=1)
        if character_count < 20
    ]
    started = time.perf_counter()
    results = ocr_pdf_pages(path, requested, config=config)
    elapsed = time.perf_counter() - started
    markers = sorted(
        {
            int(value)
            for result in results.values()
            for value in QUESTION_MARKER.findall(result.text)
            if 0 < int(value) <= int(spec["expected_questions"])
        }
    )
    pages = [
        {
            "page": result.page_number,
            "characters": len(result.text),
            "confidence": result.confidence,
            "quality_score": result.quality_score,
            "usable": result.usable,
            "failure_reason": result.error,
            "strategy": result.strategy,
            "attempts": result.attempts,
            "duration_seconds": result.duration_seconds,
            "rotation_degrees": result.rotation_degrees,
            "render_scale": result.render_scale,
        }
        for result in results.values()
    ]
    return {
        "id": spec["id"],
        "source_page": spec["source_page"],
        "url": spec["url"],
        "sha256": digest,
        "page_count": page_count,
        "text_layer_pages": sum(value >= 20 for value in text_layer_characters),
        "ocr_pages": len(requested),
        "recovered_pages": sum(page["usable"] for page in pages),
        "failed_pages": sum(not page["usable"] for page in pages),
        "question_markers": markers,
        "question_marker_coverage": round(
            len(markers) / int(spec["expected_questions"]), 4
        ),
        "duration_seconds": round(elapsed, 3),
        "pages": pages,
    }


def run(manifest_path: Path, corpus_dir: Path, config: OcrConfig) -> dict[str, Any]:
    with manifest_path.open("rb") as handle:
        manifest = tomllib.load(handle)
    documents = [
        _document_metrics(spec, corpus_dir, config) for spec in manifest["documents"]
    ]
    requested = sum(document["ocr_pages"] for document in documents)
    recovered = sum(document["recovered_pages"] for document in documents)
    return {
        "schema_version": "1.0",
        "manifest": str(manifest_path),
        "config": {
            "render_scale": config.render_scale,
            "max_render_megapixels": config.max_render_megapixels,
            "max_attempts": config.max_attempts,
            "page_timeout_seconds": config.page_timeout_seconds,
            "min_quality_score": config.min_quality_score,
        },
        "summary": {
            "documents": len(documents),
            "ocr_pages": requested,
            "recovered_pages": recovered,
            "failed_pages": requested - recovered,
            "recovery_rate": round(recovered / max(1, requested), 4),
            "duration_seconds": round(sum(item["duration_seconds"] for item in documents), 3),
        },
        "documents": documents,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/homologation/ocr-real-corpus.v1.toml"),
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("data/homologation/ocr-official"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-timeout", type=float, default=45.0)
    parser.add_argument("--max-attempts", type=int, default=5)
    args = parser.parse_args()
    config = OcrConfig(
        page_timeout_seconds=args.page_timeout,
        max_attempts=args.max_attempts,
    )
    report = run(args.manifest, args.corpus_dir, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["recovery_rate"] >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
