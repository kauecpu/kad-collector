from __future__ import annotations

from pathlib import Path

MAX_BATCH_PDFS = 20
MAX_BATCH_PAGES = 5_000
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 1_000
PDF_STREAM_CHUNK_BYTES = 1024 * 1024


def validate_pdf_batch(paths: list[Path]) -> list[Path]:
    resolved = [path.resolve() for path in paths]
    if not resolved:
        raise ValueError("selecione ao menos um PDF")
    if len(resolved) > MAX_BATCH_PDFS:
        raise ValueError(f"o lote excede o limite de {MAX_BATCH_PDFS} PDFs")

    for path in resolved:
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError(f"arquivo PDF invalido: {path}")
        size = path.stat().st_size
        if size > MAX_PDF_BYTES:
            raise ValueError(
                f"PDF excede o limite de {MAX_PDF_BYTES // (1024 * 1024)} MB: {path.name}"
            )
    return resolved
