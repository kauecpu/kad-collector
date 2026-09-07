from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from PIL import Image, ImageDraw

from kad_collector.ocr import (
    OcrConfig,
    OcrEngine,
    _estimate_skew_angle,
    assess_ocr_text,
    ocr_pdf_pages,
    page_requires_ocr,
)


def write_image_pdf(path: Path, page_count: int = 2) -> None:
    pages: list[Image.Image] = []
    for number in range(1, page_count + 1):
        image = Image.new("RGB", (900, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 860, 1160), outline="black", width=3)
        draw.text((80, 90), f"PAGINA DIGITALIZADA {number}", fill="black")
        pages.append(image)
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:], resolution=150)


class OcrTests(unittest.TestCase):
    def test_text_layer_detection_avoids_unnecessary_ocr(self) -> None:
        self.assertFalse(
            page_requires_ocr("QUESTÃO 1. Assinale a alternativa correta sobre cartografia.")
        )
        self.assertTrue(page_requires_ocr("  1  "))
        self.assertTrue(page_requires_ocr("\x00" * 40))

    def test_quality_rejects_long_corrupted_output(self) -> None:
        quality = assess_ocr_text("||||||||||||||||||||||||||||||||||||||||", 0.99)

        self.assertFalse(quality.usable)
        self.assertIn("repeticao", quality.reason or "")

    def test_quality_rejects_insufficient_text(self) -> None:
        quality = assess_ocr_text("QUESTAO 1", 0.99)

        self.assertFalse(quality.usable)
        self.assertIn("suficiente", quality.reason or "")

    def test_page_failure_does_not_discard_other_pages(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "duas-paginas.pdf"
            write_image_pdf(pdf_path)

            class FailingFirstPageOcr:
                def __init__(self) -> None:
                    self.calls = 0

                def __call__(self, _image: object) -> SimpleNamespace:
                    self.calls += 1
                    if self.calls == 1:
                        raise RuntimeError("fixture de falha")
                    return SimpleNamespace(
                        txts=("QUESTAO 2", "Texto preservado da segunda pagina digitalizada."),
                        scores=(0.98, 0.97),
                    )

            results = ocr_pdf_pages(
                pdf_path, [1, 2], engine=cast(OcrEngine, FailingFirstPageOcr())
            )

            self.assertIn("RuntimeError", results[1].error or "")
            self.assertIn("QUESTAO 2", results[2].text)
            self.assertIsNone(results[2].error)

    def test_cancellation_stops_before_next_page(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "cancelamento.pdf"
            write_image_pdf(pdf_path, 3)
            stop_event = threading.Event()

            class CancellingOcr:
                def __call__(self, _image: object) -> SimpleNamespace:
                    stop_event.set()
                    return SimpleNamespace(
                        txts=("QUESTAO 1", "Texto suficiente antes do cancelamento."),
                        scores=(0.99, 0.98),
                    )

            results = ocr_pdf_pages(
                pdf_path,
                [1, 2, 3],
                engine=cast(OcrEngine, CancellingOcr()),
                should_stop=stop_event.is_set,
            )

            self.assertEqual(set(results), {1})

    def test_portuguese_characters_are_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "portugues.pdf"
            write_image_pdf(pdf_path, 1)

            class PortugueseOcr:
                def __call__(self, _image: object) -> SimpleNamespace:
                    return SimpleNamespace(
                        txts=(
                            "QUESTÃO 1",
                            "Órgão público: assinale a opção correta sobre legislação.",
                        ),
                        scores=(0.99, 0.98),
                    )

            result = ocr_pdf_pages(
                pdf_path, [1], engine=cast(OcrEngine, PortugueseOcr())
            )[1]

            self.assertIn("QUESTÃO", result.text)
            self.assertIn("Órgão", result.text)
            self.assertIn("legislação", result.text)

    def test_blank_page_does_not_initialize_ocr_engine(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "vazia.pdf"
            Image.new("RGB", (900, 1200), "white").save(
                pdf_path, "PDF", resolution=150
            )

            class UnexpectedOcr:
                def __call__(self, _image: object) -> object:
                    raise AssertionError("OCR não deve executar em uma página vazia")

            result = ocr_pdf_pages(
                pdf_path, [1], engine=cast(OcrEngine, UnexpectedOcr())
            )[1]

            self.assertEqual(result.error, "pagina visualmente vazia")

    def test_rotation_is_retried_and_best_result_is_selected(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "rotacionada.pdf"
            write_image_pdf(pdf_path, 1)

            class LandscapeOnlyOcr:
                def __call__(self, image: object) -> SimpleNamespace:
                    height, width = cast(Any, image).shape[:2]
                    if width > height:
                        return SimpleNamespace(
                            txts=("QUESTAO 1", "Texto legivel depois da correcao de rotacao."),
                            scores=(0.99, 0.98),
                        )
                    return SimpleNamespace(txts=("???",), scores=(0.1,))

            result = ocr_pdf_pages(
                pdf_path,
                [1],
                engine=cast(OcrEngine, LandscapeOnlyOcr()),
            )[1]

            self.assertTrue(result.usable)
            self.assertEqual(result.strategy, "rotate_90")
            self.assertEqual(result.rotation_degrees, 90)
            self.assertGreaterEqual(result.attempts, 3)

    def test_low_contrast_page_uses_enhancement(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "contraste-fraco.pdf"
            image = Image.new("L", (900, 1200), 238)
            draw = ImageDraw.Draw(image)
            for y in range(100, 1000, 55):
                draw.rectangle((80, y, 820, y + 14), fill=218)
            image.save(pdf_path, "PDF", resolution=150)

            class ContrastAwareOcr:
                def __call__(self, image: object) -> SimpleNamespace:
                    values = cast(Any, image)
                    if float(values.std()) > 35:
                        return SimpleNamespace(
                            txts=("QUESTAO 12", "Enunciado recuperado depois do contraste."),
                            scores=(0.98, 0.97),
                        )
                    return SimpleNamespace(txts=("ruido",), scores=(0.2,))

            result = ocr_pdf_pages(
                pdf_path,
                [1],
                engine=cast(OcrEngine, ContrastAwareOcr()),
            )[1]

            self.assertTrue(result.usable)
            self.assertEqual(result.strategy, "grayscale_autocontrast_denoise")

    def test_skew_estimation_finds_corrective_angle(self) -> None:
        image = Image.new("L", (900, 1200), 255)
        draw = ImageDraw.Draw(image)
        for y in range(100, 1000, 80):
            draw.rectangle((100, y, 800, y + 8), fill=0)

        angle = _estimate_skew_angle(image.rotate(3, fillcolor=255))

        self.assertEqual(angle, -3)

    def test_page_timeout_is_reported_without_discarding_text(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "timeout.pdf"
            write_image_pdf(pdf_path, 1)

            class ValidOcr:
                def __call__(self, _image: object) -> SimpleNamespace:
                    return SimpleNamespace(
                        txts=("QUESTAO 1", "Texto obtido depois do limite permitido."),
                        scores=(0.99, 0.98),
                    )

            with patch("kad_collector.ocr.time.monotonic", side_effect=(0.0, 0.1, 2.0)):
                result = ocr_pdf_pages(
                    pdf_path,
                    [1],
                    engine=cast(OcrEngine, ValidOcr()),
                    config=OcrConfig(page_timeout_seconds=1),
                )[1]

            self.assertFalse(result.usable)
            self.assertEqual(result.error, "tempo limite da pagina excedido")
            self.assertIn("QUESTAO 1", result.text)

    @patch("shutil.which", return_value=None)
    def test_tesseract_unavailable_does_not_break_rapidocr(self, _which: object) -> None:
        self._assert_external_binary_is_not_required()

    @patch("shutil.which", return_value=None)
    def test_poppler_unavailable_does_not_break_pdfium(self, _which: object) -> None:
        self._assert_external_binary_is_not_required()

    def _assert_external_binary_is_not_required(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "sem-binario-externo.pdf"
            write_image_pdf(pdf_path, 1)

            class ValidOcr:
                def __call__(self, _image: object) -> SimpleNamespace:
                    return SimpleNamespace(
                        txts=("QUESTAO 1", "Fluxo local funciona com PDFium e RapidOCR."),
                        scores=(0.99, 0.98),
                    )

            result = ocr_pdf_pages(
                pdf_path, [1], engine=cast(OcrEngine, ValidOcr())
            )[1]

            self.assertTrue(result.usable)

    def test_page_order_and_idempotence_are_stable(self) -> None:
        with TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "ordem.pdf"
            write_image_pdf(pdf_path, 3)

            class StableOcr:
                def __call__(self, _image: object) -> SimpleNamespace:
                    return SimpleNamespace(
                        txts=("QUESTAO 1", "Mesmo resultado deterministico em cada execucao."),
                        scores=(0.99, 0.98),
                    )

            engine = cast(OcrEngine, StableOcr())
            first = ocr_pdf_pages(pdf_path, [3, 1, 2, 2], engine=engine)
            second = ocr_pdf_pages(pdf_path, [3, 1, 2, 2], engine=engine)

            self.assertEqual(list(first), [1, 2, 3])
            self.assertEqual(
                [(item.text, item.strategy, item.quality_score) for item in first.values()],
                [(item.text, item.strategy, item.quality_score) for item in second.values()],
            )


if __name__ == "__main__":
    unittest.main()
