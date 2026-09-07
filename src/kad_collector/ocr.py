"""Local OCR for PDF pages without a usable text layer."""

from __future__ import annotations

import argparse
import math
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

OCR_RENDER_SCALE = 2.5
OCR_MIN_TEXT_CHARACTERS = 20
OCR_BLANK_IMAGE_STDDEV = 1.0
OCR_MIN_QUALITY_SCORE = 0.58
OCR_EARLY_ACCEPT_QUALITY_SCORE = 0.82


class OcrError(RuntimeError):
    """The local OCR pipeline could not process a document or page."""


class OcrEngine(Protocol):
    def __call__(self, image: Any) -> Any: ...


@dataclass(frozen=True)
class OcrConfig:
    """Resource and retry limits for local OCR."""

    render_scale: float = OCR_RENDER_SCALE
    max_render_megapixels: float = 12.0
    max_attempts: int = 5
    page_timeout_seconds: float = 45.0
    min_quality_score: float = OCR_MIN_QUALITY_SCORE
    early_accept_quality_score: float = OCR_EARLY_ACCEPT_QUALITY_SCORE

    def __post_init__(self) -> None:
        if self.render_scale <= 0:
            raise ValueError("render_scale deve ser positivo")
        if self.max_render_megapixels <= 0:
            raise ValueError("max_render_megapixels deve ser positivo")
        if self.max_attempts < 1:
            raise ValueError("max_attempts deve ser pelo menos 1")
        if self.page_timeout_seconds <= 0:
            raise ValueError("page_timeout_seconds deve ser positivo")
        if not 0 <= self.min_quality_score <= 1:
            raise ValueError("min_quality_score deve estar entre 0 e 1")
        if not self.min_quality_score <= self.early_accept_quality_score <= 1:
            raise ValueError(
                "early_accept_quality_score deve estar entre min_quality_score e 1"
            )


@dataclass(frozen=True)
class OcrTextQuality:
    score: float
    usable: bool
    reason: str | None
    valid_character_ratio: float
    word_like_ratio: float


@dataclass(frozen=True)
class OcrPageResult:
    page_number: int
    text: str
    confidence: float | None
    error: str | None = None
    usable: bool = False
    quality_score: float = 0.0
    strategy: str = "none"
    attempts: int = 0
    duration_seconds: float = 0.0
    rotation_degrees: float = 0.0
    render_scale: float = OCR_RENDER_SCALE


_ENGINE_INIT_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", re.UNICODE)
_QUESTION_RE = re.compile(r"(?m)(?:^|\s)(?:quest[aã]o\s+)?\d{1,3}[.)]?\s", re.IGNORECASE)


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


def page_requires_ocr(text: str) -> bool:
    """Return whether an extracted text layer is too small or corrupt to trust."""

    clean = text.replace("\x00", "").strip()
    if len(clean) < OCR_MIN_TEXT_CHARACTERS:
        return True
    visible = [character for character in clean if not character.isspace()]
    if not visible:
        return True
    printable_ratio = sum(character.isprintable() for character in visible) / len(visible)
    word_characters = sum(character.isalnum() for character in visible)
    return printable_ratio < 0.9 or word_characters < 8


def assess_ocr_text(
    text: str,
    confidence: float | None,
    *,
    min_quality_score: float = OCR_MIN_QUALITY_SCORE,
) -> OcrTextQuality:
    """Grade OCR output using textual evidence, not character count alone."""

    clean = text.replace("\x00", "").strip()
    if len(clean) < OCR_MIN_TEXT_CHARACTERS:
        return OcrTextQuality(0.0, False, "OCR nao encontrou texto suficiente", 0.0, 0.0)
    visible = [character for character in clean if not character.isspace()]
    if not visible:
        return OcrTextQuality(0.0, False, "OCR nao encontrou texto suficiente", 0.0, 0.0)

    accepted_punctuation = set(".,;:!?()[]{}+-/%°ºª'\"–—_=<>@#&*\\|")
    valid = sum(character.isalnum() or character in accepted_punctuation for character in visible)
    valid_ratio = valid / len(visible)
    tokens = _WORD_RE.findall(clean)
    word_like = sum(len(token) >= 2 for token in tokens)
    word_like_ratio = word_like / max(1, len(tokens))
    longest_repetition = max(
        (len(match.group(0)) for match in re.finditer(r"(.)\1{3,}", clean)),
        default=0,
    )
    repetition_ratio = longest_repetition / len(clean)
    confidence_score = 0.55 if confidence is None else max(0.0, min(1.0, confidence))
    question_evidence = 1.0 if _QUESTION_RE.search(clean) else 0.0
    useful_lines = [line for line in clean.splitlines() if len(line.strip()) >= 8]
    line_evidence = min(1.0, len(useful_lines) / 4)
    score = (
        0.34 * valid_ratio
        + 0.24 * word_like_ratio
        + 0.24 * confidence_score
        + 0.10 * line_evidence
        + 0.08 * question_evidence
    )
    score = max(0.0, min(1.0, score - min(0.45, repetition_ratio * 1.5)))

    reason = None
    if repetition_ratio > 0.20:
        reason = "OCR produziu repeticao dominante de simbolos"
    elif valid_ratio < 0.72:
        reason = "OCR produziu caracteres corrompidos em excesso"
    elif word_like_ratio < 0.35:
        reason = "OCR nao encontrou palavras reconheciveis suficientes"
    elif score < min_quality_score:
        reason = "qualidade do texto OCR abaixo do limite"
    return OcrTextQuality(
        score=round(score, 4),
        usable=reason is None,
        reason=reason,
        valid_character_ratio=round(valid_ratio, 4),
        word_like_ratio=round(word_like_ratio, 4),
    )


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


def _image_is_blank(image: Any) -> bool:
    import numpy as np

    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    if gray.size == 0 or float(gray.std()) < OCR_BLANK_IMAGE_STDDEV:
        return True
    background = float(np.percentile(gray, 99.5))
    dark_value = float(np.percentile(gray, 0.1))
    foreground_ratio = float(np.count_nonzero(gray < background - 5)) / gray.size
    return background - dark_value < 5.0 or foreground_ratio < 0.0002


def _estimate_skew_angle(image: Any) -> float:
    """Estimate a small corrective angle from horizontal text-line projections."""

    import numpy as np
    from PIL import Image, ImageOps

    gray = ImageOps.autocontrast(image.convert("L"))
    if max(gray.size) > 900:
        ratio = 900 / max(gray.size)
        gray = gray.resize(
            (max(1, round(gray.width * ratio)), max(1, round(gray.height * ratio))),
            Image.Resampling.BILINEAR,
        )

    def projection_score(candidate: Image.Image) -> float:
        values = np.asarray(candidate, dtype=np.uint8)
        ink = values < 190
        ratio = float(np.count_nonzero(ink)) / max(1, ink.size)
        if ratio < 0.002 or ratio > 0.45:
            return 0.0
        return float(np.var(ink.sum(axis=1)))

    scores: list[tuple[float, float]] = []
    for angle in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0):
        candidate = gray if angle == 0 else gray.rotate(
            angle, resample=Image.Resampling.BILINEAR, fillcolor=255
        )
        scores.append((projection_score(candidate), angle))
    baseline = next(score for score, angle in scores if angle == 0)
    best_score, best_angle = max(scores)
    if baseline <= 0 or best_score < baseline * 1.08 or best_angle == 0:
        return 0.0
    return best_angle


def _candidate_images(image: Any) -> Iterable[tuple[str, Any, float]]:
    from PIL import Image, ImageFilter, ImageOps

    yield "original", image, 0.0
    gray = ImageOps.autocontrast(image.convert("L"))
    enhanced = gray.filter(ImageFilter.MedianFilter(size=3)).convert("RGB")
    yield "grayscale_autocontrast_denoise", enhanced, 0.0
    skew_angle = _estimate_skew_angle(gray)
    if skew_angle:
        deskewed = enhanced.rotate(
            skew_angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white"
        )
        yield "deskew", deskewed, skew_angle
    yield "rotate_90", enhanced.rotate(90, expand=True, fillcolor="white"), 90.0
    yield "rotate_270", enhanced.rotate(270, expand=True, fillcolor="white"), 270.0
    threshold = gray.point(lambda value: 255 if value >= 175 else 0).convert("RGB")
    yield "threshold", threshold, 0.0


def _bounded_render_scale(page: Any, config: OcrConfig) -> float:
    width, height = page.get_size()
    predicted_pixels = width * height * config.render_scale * config.render_scale
    limit = config.max_render_megapixels * 1_000_000
    if predicted_pixels <= limit:
        return config.render_scale
    return max(0.5, config.render_scale * math.sqrt(limit / predicted_pixels))


def ocr_pdf_pages(
    source: bytes | Path,
    page_numbers: list[int],
    *,
    engine: OcrEngine | None = None,
    render_scale: float = OCR_RENDER_SCALE,
    config: OcrConfig | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[int, OcrPageResult]:
    """Render and recognize selected one-based PDF pages without external services."""

    try:
        import numpy as np
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - installation boundary
        raise OcrError("renderizador local de OCR nao esta instalado") from exc

    active_config = config or OcrConfig(render_scale=render_scale)
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
            started = time.monotonic()
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
            effective_scale = active_config.render_scale
            try:
                page = document.get_page(page_number - 1)
                effective_scale = _bounded_render_scale(page, active_config)
                bitmap = page.render(scale=effective_scale)
                pil_image = bitmap.to_pil().convert("RGB")
                if _image_is_blank(pil_image):
                    results[page_number] = OcrPageResult(
                        page_number=page_number,
                        text="",
                        confidence=None,
                        error="pagina visualmente vazia",
                        duration_seconds=round(time.monotonic() - started, 3),
                        render_scale=effective_scale,
                    )
                    continue
                if active_engine is None:
                    active_engine = _default_engine()

                best_text = ""
                best_confidence: float | None = None
                best_quality = OcrTextQuality(0.0, False, "OCR nao executado", 0.0, 0.0)
                best_strategy = "none"
                best_rotation = 0.0
                attempts = 0
                timed_out = False
                for strategy, candidate, rotation in _candidate_images(pil_image):
                    if attempts >= active_config.max_attempts:
                        break
                    if time.monotonic() - started >= active_config.page_timeout_seconds:
                        timed_out = True
                        break
                    attempts += 1
                    text, confidence = _recognize(np.asarray(candidate), active_engine)
                    quality = assess_ocr_text(
                        text,
                        confidence,
                        min_quality_score=active_config.min_quality_score,
                    )
                    if quality.score > best_quality.score:
                        best_text = text
                        best_confidence = confidence
                        best_quality = quality
                        best_strategy = strategy
                        best_rotation = rotation
                    if quality.usable and quality.score >= active_config.early_accept_quality_score:
                        break
                elapsed = time.monotonic() - started
                if elapsed >= active_config.page_timeout_seconds:
                    timed_out = True
                error = best_quality.reason
                usable = best_quality.usable and not timed_out
                if timed_out:
                    error = "tempo limite da pagina excedido"
                results[page_number] = OcrPageResult(
                    page_number=page_number,
                    text=best_text,
                    confidence=best_confidence,
                    error=None if usable else error,
                    usable=usable,
                    quality_score=best_quality.score,
                    strategy=best_strategy,
                    attempts=attempts,
                    duration_seconds=round(elapsed, 3),
                    rotation_degrees=best_rotation,
                    render_scale=effective_scale,
                )
            except Exception as exc:  # noqa: BLE001 - page-level isolation
                results[page_number] = OcrPageResult(
                    page_number=page_number,
                    text="",
                    confidence=None,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_seconds=round(time.monotonic() - started, 3),
                    render_scale=effective_scale,
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
