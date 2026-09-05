"""Local OCR for PDF pages without a usable text layer."""

from __future__ import annotations

import argparse
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

OCR_RENDER_SCALE = 2.5
OCR_MIN_TEXT_CHARACTERS = 20
OCR_BLANK_IMAGE_STDDEV = 1.0


class OcrError(RuntimeError):
    """The local OCR pipeline could not process a document or page."""


class OcrEngine(Protocol):
    def __call__(self, image: Any) -> Any: ...


@dataclass(frozen=True)
class OcrPageResult:
    page_number: int
    text: str
    confidence: float | None
    error: str | None = None


_ENGINE_INIT_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _build_default_engine() -> OcrEngine:
    try:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    except ImportError as exc:  # pragma: no cover - installation boundary
        raise OcrError("dependencias locais de OCR nao estao instaladas") from exc
    try:
        return cast(
            OcrEngine,
            RapidOCR(
                params={
                    "Global.log_level": "error",
                    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    "Rec.lang_type": LangRec.LATIN,
                    "Rec.model_type": ModelType.MOBILE,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001 - external model/runtime boundary
        raise OcrError(f"motor OCR local indisponivel: {type(exc).__name__}: {exc}") from exc


def _default_engine() -> OcrEngine:
    with _ENGINE_INIT_LOCK:
        return _build_default_engine()


def prepare_ocr_runtime() -> None:
    """Load the Latin model now so builds can bundle and validate it."""

    _default_engine()


def _recognize(image: Any, engine: OcrEngine) -> tuple[str, float | None]:
    with _INFERENCE_LOCK:
        output = engine(image)
    texts = getattr(output, "txts", ()) or ()
    scores = getattr(output, "scores", ()) or ()
    lines = [str(value).replace("\x00", "").strip() for value in texts]
    text = "\n".join(value for value in lines if value)
    numeric_scores = [float(value) for value in scores]
    confidence = sum(numeric_scores) / len(numeric_scores) if numeric_scores else None
    return text, confidence


def ocr_pdf_pages(
    source: bytes | Path,
    page_numbers: list[int],
    *,
    engine: OcrEngine | None = None,
    render_scale: float = OCR_RENDER_SCALE,
    should_stop: Callable[[], bool] | None = None,
) -> dict[int, OcrPageResult]:
    """Render and recognize selected one-based PDF pages without external services."""

    try:
        import numpy as np
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - installation boundary
        raise OcrError("renderizador local de OCR nao esta instalado") from exc

    requested = sorted(set(page_numbers))
    if not requested:
        return {}
    document_source: bytes | str = source if isinstance(source, bytes) else str(source)
    try:
        document = pdfium.PdfDocument(document_source)
    except Exception as exc:  # noqa: BLE001 - PDFium boundary
        raise OcrError(f"nao foi possivel abrir o PDF para OCR: {exc}") from exc

    results: dict[int, OcrPageResult] = {}
    active_engine = engine
    try:
        page_count = len(document)
        for page_number in requested:
            if should_stop is not None and should_stop():
                break
            if page_number < 1 or page_number > page_count:
                results[page_number] = OcrPageResult(
                    page_number=page_number,
                    text="",
                    confidence=None,
                    error="pagina fora do intervalo do PDF",
                )
                continue
            page = None
            bitmap = None
            try:
                page = document.get_page(page_number - 1)
                bitmap = page.render(scale=render_scale)
                image = np.asarray(bitmap.to_pil().convert("RGB"))
                if image.size == 0 or float(image.std()) < OCR_BLANK_IMAGE_STDDEV:
                    results[page_number] = OcrPageResult(
                        page_number=page_number,
                        text="",
                        confidence=None,
                        error="pagina visualmente vazia",
                    )
                    continue
                if active_engine is None:
                    active_engine = _default_engine()
                text, confidence = _recognize(image, active_engine)
                results[page_number] = OcrPageResult(
                    page_number=page_number,
                    text=text,
                    confidence=confidence,
                    error=(
                        None
                        if len(text) >= OCR_MIN_TEXT_CHARACTERS
                        else "OCR nao encontrou texto suficiente"
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - page-level isolation
                results[page_number] = OcrPageResult(
                    page_number=page_number,
                    text="",
                    confidence=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                if bitmap is not None:
                    bitmap.close()
                if page is not None:
                    page.close()
    finally:
        document.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepara o motor OCR local do KAD Collector")
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args(argv)
    if not args.prepare:
        parser.error("use --prepare")
    prepare_ocr_runtime()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
