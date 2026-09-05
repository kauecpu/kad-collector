from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast

from PIL import Image, ImageDraw

from kad_collector.ocr import OcrEngine, ocr_pdf_pages


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


if __name__ == "__main__":
    unittest.main()
