from __future__ import annotations

import json
import threading
import unittest
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from kad_collector.desktop_app import _smoke_test
from kad_collector.desktop_classifier import LocalRuleClassifier
from kad_collector.desktop_export import export_filtered_questions
from kad_collector.desktop_models import (
    ClassificationRequest,
    ClassificationValue,
    DesktopFilterSet,
    DesktopImportMetadata,
    QuestionClassification,
)
from kad_collector.desktop_processor import DesktopProcessor
from kad_collector.desktop_server import DesktopApplication, start_desktop_server
from kad_collector.desktop_store import DesktopStore
from kad_collector.models import Alternative, QuestionRecord


def write_text_pdf(path: Path, pages: list[list[str]]) -> None:
    document = canvas.Canvas(str(path))
    for lines in pages:
        y = 800
        for line in lines:
            document.drawString(54, y, line)
            y -= 22
        document.showPage()
    document.save()


def write_blank_pdf(path: Path, page_count: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def metadata(**changes: object) -> DesktopImportMetadata:
    values: dict[str, object] = {
        "provider": "banca-oficial",
        "source_url": "https://example.gov.br/provas/prova.pdf",
        "concurso": "Concurso Nacional 2026",
        "board": "Banca Oficial",
        "year": 2026,
        "role": "Analista",
        "organization": "Órgão Público",
        "level": "Superior",
        "discipline": "Geografia",
        "subject": "Cartografia",
        "topic": "Escala cartográfica",
        "difficulty": "Média",
    }
    values.update(changes)
    return DesktopImportMetadata.model_validate(values)


def full_classification() -> QuestionClassification:
    value = lambda item: ClassificationValue(  # noqa: E731
        value=item, confidence=1, evidence="fixture revisada"
    )
    return QuestionClassification(
        concurso=value("Concurso Nacional 2026"),
        board=value("Banca Oficial"),
        year=value(2026),
        role=value("Analista"),
        organization=value("Órgão Público"),
        level=value("Superior"),
        discipline=value("Geografia"),
        subject=value("Cartografia"),
        topic=value("Escala cartográfica"),
        difficulty=value("Média"),
    )


def valid_question(number: int, statement: str | None = None) -> QuestionRecord:
    return QuestionRecord(
        number=number,
        statement=statement or "Em uma escala cartográfica, assinale a alternativa correta.",
        alternatives=[
            Alternative(letter="A", text="Um centímetro."),
            Alternative(letter="B", text="Cinco centímetros."),
            Alternative(letter="C", text="Dez centímetros."),
        ],
        matter="Cartografia",
        subject="Escala cartográfica",
        discipline="Geografia",
        board="Banca Oficial",
        organization="Órgão Público",
        concurso="Concurso Nacional 2026",
        role="Analista",
        year=2026,
        level="Superior",
        difficulty="Média",
        source_pages=[1],
        explanation="A conversão da escala demonstra que a resposta correta é B.",
        answer_status="matched",
        correct_answer="B",
    )


class DesktopPipelineTests(unittest.TestCase):
    def test_text_pdf_is_processed_and_incomplete_question_goes_to_exception(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova.pdf"
            write_text_pdf(
                pdf_path,
                [
                    [
                        "QUESTAO 1",
                        "Em um mapa na escala 1:100.000, cinco quilometros equivalem a:",
                        "A) 0,5 cm",
                        "B) 5 cm",
                        "C) 50 cm",
                    ]
                ],
            )
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job([pdf_path], metadata(), "local")
            DesktopProcessor(store).run(job_id)

            self.assertEqual(store.job(job_id)["status"], "completed")
            result = store.query(DesktopFilterSet())
            self.assertEqual(result["total"], 1)
            question = result["questions"][0]
            self.assertEqual(question["status"], "exception")
            self.assertIn("without_explanation", question["flags"])
            self.assertEqual(question["question"]["source_pages"], [1])

    def test_blank_pdf_is_preserved_as_ocr_exception(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "digitalizado.pdf"
            write_blank_pdf(pdf_path, 3)
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job([pdf_path], metadata(), "local")
            DesktopProcessor(store).run(job_id)

            exceptions = store.document_exceptions()
            self.assertEqual(len(exceptions), 1)
            self.assertTrue(exceptions[0]["needsOcr"])
            self.assertTrue(
                any("OCR" in issue or "texto" in issue for issue in exceptions[0]["issues"])
            )

    def test_processing_can_pause_and_resume_from_page_checkpoints(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "lote.pdf"
            write_text_pdf(
                pdf_path,
                [[f"Pagina textual {number} com conteudo suficiente."] for number in range(1, 13)],
            )
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job([pdf_path], metadata(), "local")
            event = threading.Event()
            original_save_page = store.save_page

            def save_and_pause(*args: object, **kwargs: object) -> None:
                original_save_page(*args, **kwargs)  # type: ignore[arg-type]
                if args[1] == 3:
                    event.set()

            store.save_page = save_and_pause  # type: ignore[method-assign]
            DesktopProcessor(store).run(job_id, event)
            self.assertEqual(store.job(job_id)["status"], "paused")
            document_id = store.documents_for_job(job_id)[0]["id"]
            self.assertEqual(len(store.pages(document_id)), 3)

            store.save_page = original_save_page  # type: ignore[method-assign]
            DesktopProcessor(store).run(job_id, threading.Event())
            self.assertEqual(store.job(job_id)["status"], "completed")
            self.assertEqual(len(store.pages(document_id)), 12)

    def test_three_hundred_text_pages_complete_without_losing_progress(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "lote-300-paginas.pdf"
            write_text_pdf(
                pdf_path,
                [
                    [f"Pagina {number} com camada textual valida para processamento."]
                    for number in range(1, 301)
                ],
            )
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job([pdf_path], metadata(), "local")
            DesktopProcessor(store).run(job_id)

            job = store.job(job_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["total_pages"], 300)
            self.assertEqual(job["processed_pages"], 300)

    def test_uncertain_local_classification_does_not_invent_fields(self) -> None:
        request = ClassificationRequest(
            question_number=1,
            statement="Assinale a alternativa correta.",
            alternatives=["Primeira opção", "Segunda opção"],
        )
        result = (
            LocalRuleClassifier()
            .classify_many([request], DesktopImportMetadata())[0]
            .classification
        )
        self.assertIsNone(result.discipline.value)
        self.assertEqual(result.discipline.confidence, 0)
        self.assertIsNone(result.difficulty.value)


class DesktopReviewAndFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.pdf_path = self.root / "prova.pdf"
        write_text_pdf(self.pdf_path, [["Evidencia textual da prova oficial."]])
        self.store = DesktopStore(self.root / "collector.sqlite3")
        self.job_id = self.store.create_job([self.pdf_path], metadata(), "local")
        self.document = self.store.documents_for_job(self.job_id)[0]
        self.store.update_document(
            self.document["id"],
            sha256=self._digest(self.pdf_path),
            page_count=1,
            processed_pages=1,
            status="processed",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    @staticmethod
    def _digest(path: Path) -> str:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_faceted_filters_use_or_inside_group_and_and_between_groups(self) -> None:
        first = valid_question(1)
        second = valid_question(
            2, "A concordância verbal está correta em qual alternativa apresentada?"
        ).model_copy(update={"discipline": "Língua Portuguesa", "matter": "Gramática"})
        self.store.save_question(self.document["id"], first, full_classification())
        portuguese = full_classification()
        portuguese.discipline = ClassificationValue(
            value="Língua Portuguesa", confidence=1, evidence="fixture"
        )
        portuguese.subject = ClassificationValue(
            value="Gramática", confidence=1, evidence="fixture"
        )
        self.store.save_question(self.document["id"], second, portuguese)

        result = self.store.query(
            DesktopFilterSet(
                disciplines=["Geografia", "Língua Portuguesa"],
                boards=["Banca Oficial"],
            )
        )
        self.assertEqual(result["total"], 2)
        narrowed = self.store.query(DesktopFilterSet(disciplines=["Geografia"], search="escala"))
        self.assertEqual(narrowed["total"], 1)
        self.assertTrue(narrowed["facets"]["disciplines"])

    def test_duplicate_content_is_flagged_without_being_deleted(self) -> None:
        first_id = self.store.save_question(
            self.document["id"], valid_question(1), full_classification()
        )
        second_pdf = self.root / "prova-2.pdf"
        write_text_pdf(second_pdf, [["Segunda evidencia."]])
        second_job = self.store.create_job([second_pdf], metadata(), "local")
        second_document = self.store.documents_for_job(second_job)[0]
        second_id = self.store.save_question(
            second_document["id"], valid_question(1), full_classification()
        )

        self.assertIn("duplicate", self.store.question(first_id)["flags"])
        self.assertIn("duplicate", self.store.question(second_id)["flags"])
        self.assertEqual(self.store.query(DesktopFilterSet())["total"], 2)

    def test_human_review_exports_only_valid_approved_selection(self) -> None:
        question_id = self.store.save_question(
            self.document["id"], valid_question(1), full_classification()
        )
        self.store.decide_question(
            question_id, "approved", actor="revisora", notes="Conferida no PDF."
        )
        filters = DesktopFilterSet(statuses=["exportable"])
        result = export_filtered_questions(
            self.store,
            filters,
            output_root=self.root / "exports",
            now=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        )

        records = [
            json.loads(line)
            for line in result.questions_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["schemaVersion"], 1)
        self.assertEqual(records[0]["data"]["publicationStatus"], "draft")
        self.assertEqual(self.store.question(question_id)["status"], "exported")
        self.assertTrue((result.directory / "fontes").is_dir())

    def test_missing_https_origin_is_sent_to_exceptions(self) -> None:
        missing_origin = metadata(provider=None, source_url=None)
        self.store.update_document_metadata(self.document["id"], missing_origin, actor="revisora")
        question_id = self.store.save_question(
            self.document["id"], valid_question(3), full_classification()
        )
        self.store.decide_question(question_id, "approved", actor="revisora", notes=None)
        result = export_filtered_questions(
            self.store,
            DesktopFilterSet(statuses=["exportable"]),
            output_root=self.root / "invalid-export",
        )
        self.assertEqual(result.exported_count, 0)
        exceptions = [
            json.loads(line)
            for line in result.exceptions_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(any("URL HTTPS" in issue for issue in exceptions[0]["issues"]))

    def test_saved_filter_round_trip(self) -> None:
        filters = DesktopFilterSet(boards=["Banca Oficial"], statuses=["exception"])
        saved = self.store.save_filter("Pendências da banca", filters)
        self.assertEqual(self.store.saved_filters()[0]["filters"], filters.model_dump(mode="json"))
        self.store.delete_filter(saved["id"])
        self.assertEqual(self.store.saved_filters(), [])


class DesktopSmokeTests(unittest.TestCase):
    def test_packaged_resources_and_database_bootstrap(self) -> None:
        with TemporaryDirectory() as directory:
            application = DesktopApplication(Path(directory))
            self.assertEqual(_smoke_test(application), 0)
            self.assertTrue((Path(directory) / "collector.sqlite3").is_file())

    def test_local_server_protects_mutations_with_session_token(self) -> None:
        with TemporaryDirectory() as directory:
            application = DesktopApplication(Path(directory))
            server, thread, url = start_desktop_server(application)
            try:
                with urlopen(f"{url}api/bootstrap", timeout=3) as response:
                    self.assertEqual(response.status, HTTPStatus.OK)
                body = json.dumps({"filters": {}}).encode("utf-8")
                unauthorized = Request(
                    f"{url}api/query",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(unauthorized, timeout=3)
                self.assertEqual(context.exception.code, HTTPStatus.FORBIDDEN)

                authorized = Request(
                    f"{url}api/query",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with urlopen(authorized, timeout=3) as response:
                    self.assertEqual(response.status, HTTPStatus.OK)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
