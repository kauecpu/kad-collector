from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .json_utils import read_json, write_json
from .models import (
    DownloadManifest,
    ExtractedDocument,
    ExtractedPage,
    ExtractionManifest,
)


def _extract_document(local_path: Path, record) -> ExtractedDocument:  # type: ignore[no-untyped-def]
    warnings: list[str] = []
    pages: list[ExtractedPage] = []
    try:
        reader = PdfReader(local_path, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return ExtractedDocument(
                document=record,
                pages=[],
                text="",
                needs_ocr=True,
                warnings=["PDF criptografado; extracao automatica nao realizada"],
            )
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").replace("\x00", "").strip()
            except Exception as exc:  # pypdf pode expor erros internos especificos por pagina
                text = ""
                warnings.append(f"pagina {number}: falha de extracao ({type(exc).__name__})")
            pages.append(ExtractedPage(number=number, text=text, character_count=len(text)))
    except (OSError, PdfReadError) as exc:
        return ExtractedDocument(
            document=record,
            pages=[],
            text="",
            needs_ocr=True,
            warnings=[f"PDF ilegivel: {exc}"],
        )

    non_empty_pages = sum(page.character_count >= 20 for page in pages)
    needs_ocr = not pages or non_empty_pages < max(1, (len(pages) + 1) // 2)
    if needs_ocr:
        warnings.append("pouco texto detectado; encaminhar para OCR e revisao manual")
    combined = "\n\n".join(
        f"--- Pagina {page.number} ---\n{page.text}" for page in pages if page.text
    )
    return ExtractedDocument(
        document=record,
        pages=pages,
        text=combined,
        needs_ocr=needs_ocr,
        warnings=warnings,
    )


def extract_manifest(
    manifest_path: Path, output_path: Path | None = None
) -> tuple[ExtractionManifest, Path]:
    manifest = DownloadManifest.model_validate(read_json(manifest_path))
    extracted: list[ExtractedDocument] = []
    for record in manifest.documents:
        local_path = Path(record.local_path)
        extracted.append(_extract_document(local_path, record))

    result = ExtractionManifest(created_at=datetime.now(UTC), documents=extracted)
    if output_path is None:
        output_path = Path("data/extracted") / f"{manifest_path.stem}-extracted.json"
    write_json(output_path, result.model_dump(mode="json"))
    return result, output_path
