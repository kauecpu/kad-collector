from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .document_contract import NormalizedDocument, normalize_collected_document
from .json_utils import read_json, write_json
from .models import (
    DocumentRecord,
    DownloadManifest,
    ExtractedDocument,
    ExtractedPage,
    ExtractionManifest,
)
from .ocr import OCR_MIN_TEXT_CHARACTERS, OcrEngine, OcrError, ocr_pdf_pages


def _verify_local_document(document: NormalizedDocument) -> None:
    local_path = Path(document.local_path)
    digest = hashlib.sha256()
    size = 0
    with local_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if size != document.size_bytes:
        raise ValueError(
            f"tamanho do PDF diverge do manifesto ({local_path}): {size} != {document.size_bytes}"
        )
    if digest.hexdigest() != document.sha256:
        raise ValueError(f"SHA-256 do PDF diverge do manifesto: {local_path}")


def _extract_document(
    normalized: NormalizedDocument,
    record: DocumentRecord,
    *,
    ocr_engine: OcrEngine | None = None,
) -> ExtractedDocument:
    local_path = Path(normalized.local_path)
    warnings: list[str] = []
    pages: list[ExtractedPage] = []
    _verify_local_document(normalized)
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

    ocr_page_numbers = [
        page.number
        for page in pages
        if page.character_count < OCR_MIN_TEXT_CHARACTERS
    ]
    if ocr_page_numbers:
        try:
            recovered = ocr_pdf_pages(local_path, ocr_page_numbers, engine=ocr_engine)
            page_indexes = {page.number: index for index, page in enumerate(pages)}
            for number in ocr_page_numbers:
                result = recovered.get(number)
                if result is None:
                    continue
                if len(result.text) >= OCR_MIN_TEXT_CHARACTERS:
                    pages[page_indexes[number]] = ExtractedPage(
                        number=number, text=result.text, character_count=len(result.text)
                    )
                    confidence = (
                        f" ({result.confidence:.0%} de confianca media)"
                        if result.confidence is not None
                        else ""
                    )
                    warnings.append(f"pagina {number}: texto recuperado por OCR local{confidence}")
                elif result.error:
                    warnings.append(f"pagina {number}: {result.error}")
        except OcrError as exc:
            warnings.append(f"OCR local indisponivel: {exc}")

    non_empty_pages = sum(
        page.character_count >= OCR_MIN_TEXT_CHARACTERS for page in pages
    )
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
    manifest_path: Path,
    output_path: Path | None = None,
    *,
    ocr_engine: OcrEngine | None = None,
) -> tuple[ExtractionManifest, Path]:
    manifest = DownloadManifest.model_validate(read_json(manifest_path))
    extracted: list[ExtractedDocument] = []
    for record in manifest.documents:
        normalized = normalize_collected_document(record)
        extracted.append(_extract_document(normalized, record, ocr_engine=ocr_engine))

    result = ExtractionManifest(
        created_at=datetime.now(UTC),
        documents=extracted,
        filters=manifest.filters,
        filtered_out_documents=manifest.filtered_out_documents,
    )
    if output_path is None:
        output_path = Path("data/extracted") / f"{manifest_path.stem}-extracted.json"
    write_json(output_path, result.model_dump(mode="json"))
    return result, output_path
