from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import unittest
from contextlib import closing
from datetime import UTC, datetime
from http import HTTPStatus
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from kad_collector.desktop_app import _smoke_test, build_parser, main
from kad_collector.desktop_classifier import LocalRuleClassifier
from kad_collector.desktop_export import export_filtered_questions
from kad_collector.desktop_limits import MAX_BATCH_PDFS
from kad_collector.desktop_models import (
    ClassificationRequest,
    ClassificationValue,
    DesktopFilterSet,
    DesktopImportMetadata,
    QuestionClassification,
)
from kad_collector.desktop_parser import parse_question_pages
from kad_collector.desktop_processor import DesktopProcessor
from kad_collector.desktop_server import DesktopApplication, start_desktop_server
from kad_collector.desktop_store import DesktopStore
from kad_collector.document_contract import normalize_collected_document
from kad_collector.document_pipeline import DocumentPipeline
from kad_collector.models import Alternative, DocumentRecord, QuestionRecord


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
    def test_headless_mode_is_available_for_packaged_end_to_end_validation(self) -> None:
        arguments = build_parser().parse_args(["--headless", "--port", "8878"])

        self.assertTrue(arguments.headless)
        self.assertEqual(arguments.port, 8878)

    def test_generic_parser_supports_fgv_standalone_numbers_and_parentheses(self) -> None:
        questions, warnings = parse_question_pages(
            [
                {
                    "page_number": 8,
                    "text": """
33
Assinale a alternativa correta sobre o tema apresentado.
(A) Primeira alternativa.
(B) Segunda alternativa.
(C) Terceira alternativa.
34
Considere o segundo enunciado e escolha a opção correta.
(A) Primeira opção.
(B) Segunda opção.
(C) Terceira opção.
""",
                }
            ]
        )

        self.assertEqual([question.number for question in questions], [33, 34])
        self.assertEqual(
            [alternative.letter for alternative in questions[0].alternatives],
            ["A", "B", "C"],
        )
        self.assertEqual(warnings, [])

    def test_comvest_commented_question_uses_inline_official_answer(self) -> None:
        questions, warnings = parse_question_pages(
            [
                {
                    "page_number": 7,
                    "text": """
QUESTÃO 1
Com base no texto, assinale a alternativa correta.
a) Primeira alternativa.
b) Segunda alternativa.
c) Terceira alternativa.
d) Quarta alternativa.
Objetivo da Questão
Este comentário não faz parte do enunciado.
Alternativa Correta: C
Comentários Gerais
Informações editoriais da banca.
""",
                }
            ]
        )

        self.assertEqual(warnings, [])
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].answer_status, "matched")
        self.assertEqual(questions[0].correct_answer, "C")
        self.assertNotIn("comentário", questions[0].statement.casefold())

    def test_processing_queue_limits_simultaneous_jobs(self) -> None:
        with TemporaryDirectory() as directory:
            store = DesktopStore(Path(directory) / "collector.sqlite3")
            processor = DesktopProcessor(store, max_workers=2)
            gate = threading.Event()
            two_running = threading.Event()
            lock = threading.Lock()
            active = 0
            peak = 0

            def blocked_run(_job_id: str, _event: threading.Event) -> None:
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                    if active == 2:
                        two_running.set()
                gate.wait(5)
                with lock:
                    active -= 1

            with patch.object(processor, "run", side_effect=blocked_run):
                for number in range(8):
                    processor.start(f"job-{number}")
                self.assertTrue(two_running.wait(2))
                threading.Event().wait(0.05)
                self.assertEqual(peak, 2)
                gate.set()
                for _ in range(200):
                    if all(future.done() for future in processor._futures.values()):
                        break
                    threading.Event().wait(0.01)
                self.assertTrue(all(future.done() for future in processor._futures.values()))

    def test_aes_pdf_is_supported_and_password_failure_is_isolated(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            readable = root / "aes-readable.pdf"
            blocked = root / "aes-password.pdf"
            write_text_pdf(
                source,
                [
                    [
                        "QUESTAO 1",
                        "Assinale a alternativa correta sobre a escala cartografica.",
                        "A) Primeira alternativa.",
                        "B) Segunda alternativa.",
                        "C) Terceira alternativa.",
                        "Alternativa Correta: B",
                    ]
                ],
            )
            readable_writer = PdfWriter()
            readable_writer.append_pages_from_reader(PdfReader(source))
            readable_writer.encrypt(user_password="", algorithm="AES-256")
            with readable.open("wb") as stream:
                readable_writer.write(stream)
            blocked_writer = PdfWriter()
            blocked_writer.add_blank_page(width=595, height=842)
            blocked_writer.encrypt(user_password="segredo", algorithm="AES-256")
            with blocked.open("wb") as stream:
                blocked_writer.write(stream)

            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job(
                [readable, blocked], metadata(document_type="exam"), "local"
            )
            DesktopProcessor(store).run(job_id)

            self.assertEqual(store.job(job_id)["status"], "completed")
            documents = store.documents_for_job(job_id)
            self.assertEqual(sum(item["status"] == "exception" for item in documents), 1)
            result = store.query(DesktopFilterSet())
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["questions"][0]["status"], "pending")
            self.assertEqual(result["questions"][0]["question"]["correct_answer"], "B")

    def test_import_limits_reject_oversized_files_and_batches(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "primeiro.pdf"
            second = root / "segundo.pdf"
            write_blank_pdf(first)
            write_blank_pdf(second)
            second.write_bytes(second.read_bytes() + b"\n% distinct second fixture")
            store = DesktopStore(root / "collector.sqlite3")

            with (
                patch("kad_collector.desktop_limits.MAX_BATCH_PDFS", 1),
                self.assertRaisesRegex(ValueError, "limite de 1 PDFs"),
            ):
                store.create_job([first, second], metadata(), "local")
            with (
                patch("kad_collector.desktop_limits.MAX_PDF_BYTES", 1),
                self.assertRaisesRegex(ValueError, "excede o limite"),
            ):
                store.create_job([first], metadata(), "local")

            for number in range(2, MAX_BATCH_PDFS + 1):
                (root / f"extra-{number}.pdf").touch()
            with self.assertRaisesRegex(ValueError, f"limite de {MAX_BATCH_PDFS} PDFs"):
                DesktopApplication._expand_paths([str(root)])

    def test_processing_rejects_pdf_above_page_limit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "muitas-paginas.pdf"
            write_blank_pdf(pdf_path, 2)
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job([pdf_path], metadata(), "local")

            with patch("kad_collector.desktop_processor.MAX_PDF_PAGES", 1):
                DesktopProcessor(store).run(job_id)

            document = store.documents_for_job(job_id)[0]
            self.assertEqual(document["status"], "exception")
            self.assertEqual(store.pages(document["id"]), [])
            self.assertTrue(any("limite de 1 páginas" in item for item in document["warnings"]))

    def test_processing_rejects_documents_above_batch_page_limit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a-primeiro.pdf"
            second = root / "b-segundo.pdf"
            write_blank_pdf(first)
            write_blank_pdf(second)
            second.write_bytes(second.read_bytes() + b"\n% distinct batch fixture")
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job([first, second], metadata(), "local")

            with patch("kad_collector.desktop_processor.MAX_BATCH_PAGES", 1):
                DesktopProcessor(store).run(job_id)

            documents = store.documents_for_job(job_id)
            self.assertEqual(documents[1]["status"], "exception")
            self.assertTrue(
                any(
                    "lote excede o limite de 1 páginas" in item
                    for item in documents[1]["warnings"]
                )
            )

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
            with closing(store._connect()) as connection:
                automatic_preparation = connection.execute(
                    "SELECT COUNT(*) FROM question_equivalence_runs "
                    "WHERE id=? AND status='completed'",
                    (f"automatic-after-collection-{job_id}-equivalence",),
                ).fetchone()[0]
            self.assertEqual(automatic_preparation, 1)
            question = result["questions"][0]
            self.assertEqual(question["status"], "exception")
            self.assertIn("without_explanation", question["flags"])
            self.assertEqual(question["question"]["source_pages"], [1])

    def test_fgv_document_with_open_interval_is_preserved_as_incomplete(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "rfb22-incompleta.pdf"
            write_text_pdf(
                pdf_path,
                [
                    [
                        "MANHA",
                        "Auditor-Fiscal da Receita Federal do Brasil",
                        "TIPO 1",
                        "1",
                        "Assinale a alternativa correta sobre o tema apresentado.",
                        "(A) Primeira alternativa.",
                        "(B) Segunda alternativa.",
                    ]
                ],
            )
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job(
                [pdf_path],
                metadata(
                    provider="fgv_conhecimento",
                    board="FGV",
                    concurso="RFB22",
                    role="Auditor-Fiscal da Receita Federal do Brasil",
                    turn="Manhã",
                    variant="Tipo 1",
                    document_type="exam",
                ),
                "local",
            )

            processor = DesktopProcessor(store)
            try:
                processor.run(job_id)

                document = store.documents_for_job(job_id)[0]
                parsing = document["parsing_result"]
                self.assertEqual(document["status"], "exception")
                self.assertEqual(parsing["status"], "incomplete")
                self.assertEqual(parsing["summary"]["objectiveFound"], 1)
                self.assertEqual(len(parsing["exceptions"]), 79)
                self.assertEqual(len(store.question_records(document["id"])), 1)

                store.update_document(document["id"], status="structuring")
                processor._structure_job(job_id, threading.Event())
                recovered = store.document(document["id"])
                self.assertEqual(recovered["status"], "exception")
                self.assertEqual(len(store.question_records(document["id"])), 1)
            finally:
                processor._executor.shutdown(wait=True)

    def test_direct_import_and_automatic_collection_converge_with_the_real_processor(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-compartilhada.pdf"
            write_text_pdf(
                pdf_path,
                [
                    [
                        "QUESTAO 1",
                        "A primeira questão possui texto suficiente para a extração.",
                        "A) Primeira alternativa.",
                        "B) Segunda alternativa.",
                        "#####",
                        "QUESTAO 2",
                        "A segunda questão também possui texto suficiente para a extração.",
                        "A) Terceira alternativa.",
                        "B) Quarta alternativa.",
                    ]
                ],
            )
            shared_metadata = metadata(
                document_type="exam",
                document_title="Prova oficial de cartografia",
                source_url="https://example.gov.br/provas/cartografia-2026.pdf",
                canonical_url="https://cdn.example.gov.br/cartografia-2026.pdf",
            )
            direct_store = DesktopStore(root / "direct.sqlite3")
            collected_store = DesktopStore(root / "collected.sqlite3")

            class SynchronousRunner:
                def __init__(self, processor: DesktopProcessor) -> None:
                    self.processor = processor

                def start(self, job_id: str) -> None:
                    self.processor.run(job_id)

            direct_processor = DesktopProcessor(direct_store)
            collected_processor = DesktopProcessor(collected_store)
            direct_job_id = DocumentPipeline(
                direct_store, SynchronousRunner(direct_processor)
            ).import_paths([pdf_path], shared_metadata, "local")[0]
            payload = pdf_path.read_bytes()
            collected_contract = normalize_collected_document(
                DocumentRecord(
                    source_id="fonte-oficial",
                    source_name="Fonte oficial",
                    document_type="exam",
                    title="Prova oficial de cartografia",
                    original_url="https://example.gov.br/provas/cartografia-2026.pdf",
                    resolved_url="https://cdn.example.gov.br/cartografia-2026.pdf",
                    local_path=str(pdf_path.resolve()),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    content_type="application/pdf",
                    size_bytes=len(payload),
                    downloaded_at=datetime(2026, 8, 20, tzinfo=UTC),
                    authorization_basis="Fonte pública autorizada.",
                    metadata={},
                ),
                source_page_url="https://example.gov.br/provas/2026",
            ).model_copy(
                update={"metadata": shared_metadata.model_dump(mode="json", exclude_none=True)}
            )
            collected_job_id = DocumentPipeline(
                collected_store, SynchronousRunner(collected_processor)
            ).submit([collected_contract], "local")[0]

            direct_questions = [
                item["question"] for item in direct_store.query(DesktopFilterSet())["questions"]
            ]
            collected_questions = [
                item["question"] for item in collected_store.query(DesktopFilterSet())["questions"]
            ]
            self.assertEqual(len(direct_questions), 2)
            self.assertEqual(direct_questions, collected_questions)
            direct_document = direct_store.documents_for_job(direct_job_id)[0]
            collected_document = collected_store.documents_for_job(collected_job_id)[0]
            self.assertEqual(direct_document["normalized_document"].entry_method, "direct_import")
            self.assertEqual(direct_document["metadata"]["source_url"], shared_metadata.source_url)
            self.assertEqual(
                collected_document["normalized_document"].entry_method, "automated_collection"
            )
            self.assertEqual(
                collected_document["normalized_document"].original_url, shared_metadata.source_url
            )
            self.assertEqual(
                collected_document["normalized_document"].source_page_url,
                "https://example.gov.br/provas/2026",
            )
            self.assertEqual(direct_document["local_path"], collected_document["local_path"])
            self.assertEqual(direct_document["sha256"], collected_document["sha256"])
            self.assertEqual(direct_document["size_bytes"], collected_document["size_bytes"])

    def test_application_exposes_local_reprocessing_without_a_ui_route(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "reprocessar.pdf"
            write_text_pdf(
                pdf_path,
                [
                    [
                        "QUESTAO 1",
                        "Texto suficiente para reprocessar localmente.",
                        "A) Uma.",
                        "B) Duas.",
                    ]
                ],
            )
            application = DesktopApplication(root)
            original_job_id = application.store.create_job([pdf_path], metadata(), "local")
            original_document = application.store.documents_for_job(original_job_id)[0]

            job_ids = application.reprocess_documents([original_document["id"]], "local")

            self.assertEqual(len(job_ids), 1)
            replacement = application.store.documents_for_job(job_ids[0])[0]
            self.assertEqual(replacement["normalized_document"].entry_method, "reprocessing")
            application.processor._executor.shutdown(wait=True)

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

    def test_text_pdf_with_blank_trailing_page_does_not_require_document_ocr(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-com-verso-em-branco.pdf"
            write_text_pdf(
                pdf_path,
                [
                    [
                        "QUESTAO 1",
                        "Assinale a alternativa correta sobre a escala cartografica.",
                        "A) Primeira alternativa.",
                        "B) Segunda alternativa.",
                        "C) Terceira alternativa.",
                    ],
                    [],
                ],
            )
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_job(
                [pdf_path], metadata(document_type="exam"), "local"
            )
            DesktopProcessor(store).run(job_id)

            document = store.documents_for_job(job_id)[0]
            self.assertFalse(document["needs_ocr"])
            self.assertEqual(document["status"], "processed")
            self.assertEqual(len(store.question_records(document["id"])), 1)

    def test_explicit_application_year_replaces_source_publication_year(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "curso-formacao.pdf"
            write_text_pdf(
                pdf_path,
                [
                    [
                        "PROVA APLICADA EM 21/07/2023",
                        "QUESTAO 1",
                        "Assinale a alternativa correta sobre a escala cartografica.",
                        "A) Primeira alternativa.",
                        "B) Segunda alternativa.",
                        "C) Terceira alternativa.",
                    ]
                ],
            )
            normalized = normalize_collected_document(
                DocumentRecord(
                    source_id="fgv_conhecimento",
                    source_name="FGV Conhecimento",
                    document_type="exam",
                    title="Curso de Formação",
                    original_url="https://example.gov.br/curso-formacao.pdf",
                    resolved_url="https://example.gov.br/curso-formacao.pdf",
                    local_path=str(pdf_path),
                    sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                    content_type="application/pdf",
                    size_bytes=pdf_path.stat().st_size,
                    downloaded_at=datetime.now(UTC),
                    authorization_basis="Fonte oficial.",
                    metadata={
                        "banca": "FGV",
                        "concurso": "RFB",
                        "ano_publicacao": "2024",
                    },
                )
            )
            store = DesktopStore(root / "collector.sqlite3")
            job_id = store.create_interpretation_job([normalized], "local")
            self.assertIsNotNone(job_id)
            DesktopProcessor(store).run(job_id or "")

            document = store.documents_for_job(job_id or "")[0]
            self.assertEqual(document["metadata"]["year"], 2023)
            questions = store.question_records(document["id"])
            self.assertEqual(len(questions), 1)
            self.assertEqual(questions[0][0].year, 2023)

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

    def test_summary_separates_official_answers_from_editorial_status(self) -> None:
        matched = valid_question(
            1, "Questão com alternativa oficialmente relacionada ao gabarito."
        )
        annulled = valid_question(
            2, "Questão oficialmente anulada no documento de gabarito."
        ).model_copy(update={"answer_status": "annulled", "correct_answer": None})
        missing = valid_question(
            3, "Questão ainda sem resposta localizada no gabarito oficial."
        ).model_copy(update={"answer_status": "missing", "correct_answer": None})
        for question in (matched, annulled, missing):
            self.store.save_question(self.document["id"], question, full_classification())

        summary = self.store.query(DesktopFilterSet())["summary"]

        self.assertEqual(summary["answer_matched"], 1)
        self.assertEqual(summary["answer_annulled"], 1)
        self.assertEqual(summary["answer_official"], 2)
        self.assertEqual(summary["answer_missing"], 1)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["exception"], 2)

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
        self.assertEqual(records[0]["schemaVersion"], 2)
        self.assertEqual(records[0]["data"]["publicationStatus"], "draft")
        self.assertEqual(self.store.question(question_id)["status"], "exported")
        self.assertTrue((result.directory / "fontes").is_dir())

    def test_batch_approval_is_atomic_audited_and_persistent(self) -> None:
        first_id = self.store.save_question(
            self.document["id"],
            valid_question(1, "Primeira questão completa selecionada para revisão em lote."),
            full_classification(),
        )
        second_id = self.store.save_question(
            self.document["id"],
            valid_question(2, "Segunda questão completa selecionada para revisão em lote."),
            full_classification(),
        )

        approved = self.store.approve_questions(
            [first_id, second_id], actor="revisora", notes="Lote conferido no PDF."
        )

        self.assertEqual(approved, 2)
        self.assertEqual(self.store.query(DesktopFilterSet())["summary"]["pending"], 0)
        self.assertEqual(self.store.query(DesktopFilterSet())["summary"]["exportable"], 2)
        audit = self.store.audit_log(first_id)[0]
        self.assertEqual(audit["action"], "approved")
        self.assertEqual(audit["notes"], "Lote conferido no PDF.")
        self.assertIsNotNone(audit["before_json"])
        self.assertIsNotNone(audit["after_json"])

        reopened = DesktopStore(self.root / "collector.sqlite3")
        self.assertEqual(reopened.question(first_id)["status"], "approved")
        self.assertEqual(reopened.question(second_id)["status"], "approved")

    def test_desktop_mutations_use_internal_actor_without_visible_reviewer(self) -> None:
        application = DesktopApplication(self.root / "automatic-audit")
        job_id = application.store.create_job([self.pdf_path], metadata(), "local")
        document = application.store.documents_for_job(job_id)[0]
        question_id = application.store.save_question(
            document["id"], valid_question(1), full_classification()
        )
        application.store.decide_question(
            question_id,
            "pending",
            actor="revisora-antiga",
            notes="Anotação histórica preservada.",
        )
        edited = valid_question(
            1, "Enunciado salvo sem solicitar o nome de uma pessoa revisora."
        )

        application.update_question(
            question_id,
            {
                "question": edited.model_dump(mode="json"),
                "classification": full_classification().model_dump(mode="json"),
            },
        )
        after_edit = application.store.question(question_id)
        self.assertEqual(after_edit["reviewer"], "operador_local")
        self.assertEqual(after_edit["review_notes"], "Anotação histórica preservada.")

        approved = application.approve_batch({"questionIds": [question_id]})

        self.assertEqual(approved, 1)
        self.assertEqual(application.store.question(question_id)["reviewer"], "operador_local")
        self.assertEqual(application.store.audit_log(question_id)[0]["actor"], "operador_local")

    def test_automatic_actor_does_not_remove_exception_justification(self) -> None:
        application = DesktopApplication(self.root / "automatic-reason")
        job_id = application.store.create_job([self.pdf_path], metadata(), "local")
        document = application.store.documents_for_job(job_id)[0]
        question_id = application.store.save_question(
            document["id"], valid_question(1), full_classification()
        )

        with self.assertRaisesRegex(ValueError, "excecao exige justificativa"):
            application.decide(question_id, {"status": "exception"})

        application.decide(
            question_id,
            {
                "status": "exception",
                "notes": "A origem visual precisa de conferência manual.",
            },
        )
        stored = application.store.question(question_id)
        self.assertEqual(stored["reviewer"], "operador_local")
        self.assertEqual(
            stored["review_notes"],
            "A origem visual precisa de conferência manual.",
        )

    def test_batch_approval_changes_nothing_when_one_question_is_invalid(self) -> None:
        valid_id = self.store.save_question(
            self.document["id"], valid_question(1), full_classification()
        )
        invalid = valid_question(2).model_copy(
            update={"matter": None, "subject": None}
        )
        invalid_id = self.store.save_question(
            self.document["id"], invalid, full_classification()
        )

        with self.assertRaisesRegex(ValueError, "lote nao exportavel"):
            self.store.approve_questions(
                [valid_id, invalid_id], actor="revisora", notes=None
            )

        self.assertEqual(self.store.question(valid_id)["status"], "pending")
        self.assertNotEqual(self.store.question(invalid_id)["status"], "approved")

    def test_edit_before_approval_persists_human_content(self) -> None:
        question_id = self.store.save_question(
            self.document["id"], valid_question(1), full_classification()
        )
        edited = valid_question(
            1, "Depois da revisão humana, este é o enunciado editorial definitivo."
        )

        self.store.update_question(
            question_id,
            edited,
            full_classification(),
            actor="revisora",
            notes="Enunciado corrigido.",
        )
        self.store.decide_question(
            question_id, "approved", actor="revisora", notes="Conferida no PDF."
        )

        stored = self.store.question(question_id)
        self.assertEqual(stored["question"]["statement"], edited.statement)
        self.assertEqual(stored["status"], "approved")
        self.assertEqual(
            [event["action"] for event in self.store.audit_log(question_id)[:2]],
            ["approved", "updated"],
        )

    def test_exception_requires_reason_and_defer_keeps_question_pending(self) -> None:
        question_id = self.store.save_question(
            self.document["id"], valid_question(1), full_classification()
        )

        with self.assertRaisesRegex(ValueError, "excecao exige justificativa"):
            self.store.decide_question(
                question_id, "exception", actor="revisora", notes=""
            )
        self.assertEqual(self.store.question(question_id)["status"], "pending")

        self.store.decide_question(
            question_id,
            "pending",
            actor="revisora",
            notes="Revisar a diagramação depois.",
        )
        deferred = self.store.question(question_id)
        self.assertEqual(deferred["status"], "pending")
        self.assertEqual(self.store.audit_log(question_id)[0]["action"], "deferred")

        self.store.decide_question(
            question_id,
            "exception",
            actor="revisora",
            notes="Alternativa ilegível na página de origem.",
        )
        exception = self.store.question(question_id)
        self.assertEqual(exception["status"], "exception")
        self.assertEqual(exception["review_notes"], "Alternativa ilegível na página de origem.")

    def test_variant_and_document_filters_are_available(self) -> None:
        variant = metadata(variant="Tipo 2")
        self.store.update_document_metadata(self.document["id"], variant, actor="revisora")
        self.store.save_question(self.document["id"], valid_question(1), full_classification())

        selected = self.store.query(
            DesktopFilterSet(variants=["Tipo 2"], source_files=["prova.pdf"])
        )

        self.assertEqual(selected["total"], 1)
        self.assertEqual(selected["facets"]["variants"][0]["value"], "Tipo 2")

    def test_reprocessing_changed_content_invalidates_editorial_decision(self) -> None:
        original = valid_question(1)
        question_id = self.store.save_question(
            self.document["id"], original, full_classification()
        )
        self.store.decide_question(
            question_id, "approved", actor="revisora", notes="Conferida no PDF."
        )

        self.store.save_question(self.document["id"], original, full_classification())
        unchanged = self.store.question(question_id)
        self.assertEqual(unchanged["status"], "approved")
        self.assertEqual(unchanged["reviewer"], "revisora")

        changed = valid_question(
            1, "Após reprocessamento, o enunciado oficial desta questão foi alterado."
        )
        self.store.save_question(self.document["id"], changed, full_classification())
        invalidated = self.store.question(question_id)
        self.assertEqual(invalidated["status"], "pending")
        self.assertIsNone(invalidated["reviewer"])
        self.assertIsNone(invalidated["review_notes"])
        self.assertIsNone(invalidated["exported_at"])
        self.assertEqual(self.store.audit_log(question_id)[0]["action"], "decision_invalidated")

    def test_question_content_and_decision_fingerprints_are_persisted_separately(self) -> None:
        original = valid_question(1)
        question_id = self.store.save_question(
            self.document["id"], original, full_classification()
        )
        with closing(self.store._connect()) as connection:
            before = connection.execute(
                "SELECT fingerprint, decision_fingerprint FROM questions WHERE id = ?",
                (question_id,),
            ).fetchone()

        changed_answer = original.model_copy(update={"correct_answer": "C"})
        self.store.save_question(
            self.document["id"], changed_answer, full_classification()
        )
        with closing(self.store._connect()) as connection:
            after = connection.execute(
                "SELECT fingerprint, decision_fingerprint FROM questions WHERE id = ?",
                (question_id,),
            ).fetchone()

        self.assertIsNotNone(before["decision_fingerprint"])
        self.assertEqual(after["fingerprint"], before["fingerprint"])
        self.assertNotEqual(after["decision_fingerprint"], before["decision_fingerprint"])

    def test_changed_official_answer_invalidates_decision(self) -> None:
        original = valid_question(1)
        question_id = self.store.save_question(
            self.document["id"], original, full_classification()
        )
        self.store.decide_question(
            question_id, "approved", actor="revisora", notes="Conferida no PDF."
        )

        self.store.save_question(
            self.document["id"],
            original.model_copy(update={"correct_answer": "C"}),
            full_classification(),
        )

        invalidated = self.store.question(question_id)
        self.assertEqual(invalidated["status"], "pending")
        self.assertIsNone(invalidated["reviewer"])
        self.assertIsNone(invalidated["review_notes"])
        self.assertEqual(self.store.audit_log(question_id)[0]["action"], "decision_invalidated")

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
    def test_bootstrap_exposes_semantic_counts(self) -> None:
        with TemporaryDirectory() as directory:
            payload = DesktopApplication(Path(directory)).bootstrap()

        self.assertEqual(
            set(payload["semanticSummary"]),
            {
                "observations",
                "logicalVersions",
                "exactDuplicates",
                "republications",
                "activeLinks",
                "uncertain",
            },
        )

    def test_identity_endpoint_exposes_evidence_without_pdf_text(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-identidade.pdf"
            write_text_pdf(
                pdf_path,
                [["Banca: Banca Oficial", "Concurso: Concurso Nacional 2026", "Ano: 2026"]],
            )
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job(
                [pdf_path], metadata(document_type="exam"), "local"
            )
            document = application.store.documents_for_job(job_id)[0]
            page_text = "TEXTO INTEGRAL SIGILOSO DA PÁGINA"
            application.store.save_page(document["id"], 1, page_text, status="text")
            application.store.resolve_extracted_document(document["id"])
            with closing(application.store._connect()) as connection:
                connection.execute(
                    """
                    INSERT INTO document_identity_events
                    (event_key, document_id, document_version_id, action, actor,
                     algorithm_version, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "test-raw-origin-event",
                        document["id"],
                        application.store.semantic_document_view(document["id"])["documentVersionId"],
                        "test",
                        "system",
                        "semantic-identity-v1",
                        json.dumps({"origin": "https://private.example/path"}),
                        "2026-08-21T00:00:00+00:00",
                    ),
                )
                connection.commit()
            server, thread, url = start_desktop_server(application)
            try:
                endpoint = f"{url}api/documents/{document['id']}/identity"
                with self.assertRaises(HTTPError) as context:
                    urlopen(endpoint, timeout=3)
                self.assertEqual(context.exception.code, HTTPStatus.FORBIDDEN)

                request = Request(
                    endpoint,
                    headers={"X-KAD-Desktop-Token": application.token},
                )
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        self.assertEqual(payload["resolution"], "new_identity")
        self.assertIn("algorithmVersion", payload)
        self.assertIn("evidence", payload)
        self.assertIn("reason", payload)
        self.assertNotIn("canonicalText", payload)
        self.assertNotIn(page_text, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("origin", json.dumps(payload, ensure_ascii=False))
        self.assertTrue(payload["events"])
        self.assertTrue(all("actor" in event for event in payload["events"]))

    def test_identity_get_and_correction_put_return_bounded_sanitized_dtos(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-dto.pdf"
            write_blank_pdf(pdf_path)
            secret = "DO-NOT-RETURN-SEMANTIC-SECRET"
            long_board = ("B" * 220) + secret
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job(
                [pdf_path],
                metadata(
                    document_type="exam", board=None, concurso=None, year=None,
                    role=None, organization=None,
                ),
                "local",
            )
            document = application.store.documents_for_job(job_id)[0]
            application.store.save_page(
                document["id"], 1,
                f"Banca: {long_board}\nConcurso: Concurso DTO\nAno: 2026",
                status="text",
            )
            self.assertEqual(
                application.store.resolve_extracted_document(document["id"]).outcome,
                "new_identity",
            )
            server, thread, url = start_desktop_server(application)
            try:
                origin = url.rstrip("/")
                get_request = Request(
                    f"{url}api/documents/{document['id']}/identity",
                    headers={"X-KAD-Desktop-Token": application.token},
                )
                with urlopen(get_request, timeout=3) as response:
                    get_payload = json.loads(response.read())

                correction = metadata(
                    document_type="exam", board="Banca corrigida", concurso="Concurso DTO",
                    year=2026, role=None, organization=None,
                ).model_dump(mode="json")
                put_request = Request(
                    f"{url}api/documents/{document['id']}",
                    data=json.dumps({"metadata": correction, "actor": "coordenador"}).encode(),
                    method="PUT",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": origin,
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with urlopen(put_request, timeout=3) as response:
                    put_payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        for payload in (get_payload, put_payload):
            encoded = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(secret, encoded)
            self.assertNotIn('"raw_values"', encoded)
            self.assertNotIn('"raw_value"', encoded)
            self.assertLess(len(encoded), 20_000)

    def test_cloudflare_bypass_setting_round_trips_through_the_http_api(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            application = DesktopApplication(root / "data")
            server, thread, url = start_desktop_server(application)
            try:
                origin = url.rstrip("/")
                bootstrap_request = Request(
                    f"{url}api/bootstrap",
                    headers={"X-KAD-Desktop-Token": application.token},
                )
                with urlopen(bootstrap_request, timeout=3) as response:
                    bootstrap_payload = json.loads(response.read())
                self.assertTrue(bootstrap_payload["collectionEngine"]["cloudflareBypassEnabled"])

                toggle_off = Request(
                    f"{url}api/settings/cloudflare-bypass",
                    data=json.dumps({"enabled": False}).encode(),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": origin,
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with urlopen(toggle_off, timeout=3) as response:
                    toggle_payload = json.loads(response.read())
                self.assertEqual(toggle_payload, {"ok": True, "cloudflareBypassEnabled": False})

                invalid = Request(
                    f"{url}api/settings/cloudflare-bypass",
                    data=json.dumps({"enabled": "nope"}).encode(),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": origin,
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(invalid, timeout=3)
                self.assertEqual(context.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            # o novo estado sobrevive a reabertura do banco (equivalente a reiniciar o app).
            reopened = DesktopApplication(root / "data")
            self.assertFalse(reopened.bootstrap()["collectionEngine"]["cloudflareBypassEnabled"])

    def test_identity_endpoint_preserves_evidence_for_an_uncertain_document(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-incerta.pdf"
            write_text_pdf(pdf_path, [["Questão 1", "A) Azul", "B) Verde"]])
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job(
                [pdf_path],
                metadata(
                    document_type="exam",
                    board=None,
                    concurso=None,
                    year=None,
                    role=None,
                    organization=None,
                ),
                "local",
            )
            document = application.store.documents_for_job(job_id)[0]
            application.store.save_page(
                document["id"], 1, "Questão 1\nA) Azul\nB) Verde", status="text"
            )
            self.assertEqual(
                application.store.resolve_extracted_document(document["id"]).outcome,
                "uncertain",
            )
            server, thread, url = start_desktop_server(application)
            try:
                request = Request(
                    f"{url}api/documents/{document['id']}/identity",
                    headers={"X-KAD-Desktop-Token": application.token},
                )
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        self.assertEqual(payload["identityStatus"], "unknown")
        self.assertEqual(payload["resolution"], "uncertain")
        self.assertIsInstance(payload["evidence"], dict)
        self.assertIn("board", payload["evidence"])
        self.assertIsNotNone(payload["algorithmVersion"])

    def test_identity_endpoint_explains_legacy_uncertain_events_without_inventing_identity(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-incerta-legada.pdf"
            write_text_pdf(pdf_path, [["Questão 1", "A) Azul", "B) Verde"]])
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job(
                [pdf_path],
                metadata(
                    document_type="exam",
                    board=None,
                    concurso=None,
                    year=None,
                    role=None,
                    organization=None,
                ),
                "local",
            )
            document = application.store.documents_for_job(job_id)[0]
            application.store.save_page(
                document["id"], 1, "Questão 1\nA) Azul\nB) Verde", status="text"
            )
            application.store.resolve_extracted_document(document["id"])
            legacy_reason = "identidade semântica insuficiente no registro legado"
            with closing(application.store._connect()) as connection:
                connection.execute(
                    "UPDATE document_identity_events SET payload_json = ? "
                    "WHERE document_id = ? AND action = 'uncertain'",
                    (json.dumps({"reason": legacy_reason}), document["id"]),
                )
                connection.commit()
            server, thread, url = start_desktop_server(application)
            try:
                endpoint = f"{url}api/documents/{document['id']}/identity"
                request = Request(
                    endpoint,
                    headers={"X-KAD-Desktop-Token": application.token},
                )
                with urlopen(request, timeout=3) as response:
                    legacy_payload = json.loads(response.read())

                with closing(application.store._connect()) as connection:
                    connection.execute(
                        "UPDATE document_identity_events SET payload_json = ? "
                        "WHERE document_id = ? AND action = 'uncertain'",
                        (json.dumps({}), document["id"]),
                    )
                    connection.execute(
                        """
                        INSERT INTO document_identity_events
                        (event_key, document_id, action, actor, algorithm_version, payload_json,
                         created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "legacy-unrelated-reason",
                            document["id"],
                            "observed",
                            "system",
                            "semantic-identity-v1",
                            json.dumps({"reason": "motivo de outro evento"}),
                            "2099-01-01T00:00:00+00:00",
                        ),
                    )
                    connection.commit()
                with urlopen(request, timeout=3) as response:
                    missing_reason_payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        self.assertEqual(legacy_payload["identityStatus"], "unknown")
        self.assertIsNone(legacy_payload["identity"])
        self.assertEqual(
            legacy_payload["evidence"], {"resolution": {"reason": legacy_reason}}
        )
        self.assertEqual(legacy_payload["reason"], legacy_reason)
        self.assertEqual(legacy_payload["algorithmVersion"], "semantic-identity-v1")
        self.assertIsNone(missing_reason_payload["identity"])
        self.assertEqual(missing_reason_payload["evidence"], {})
        self.assertIsNone(missing_reason_payload["reason"])
        self.assertEqual(missing_reason_payload["algorithmVersion"], "semantic-identity-v1")

    def test_packaged_ui_renders_semantic_identity_through_exported_helpers(self) -> None:
        with TemporaryDirectory() as directory:
            application = DesktopApplication(Path(directory))
            server, thread, url = start_desktop_server(application)
            try:
                with urlopen(f"{url}desktop.js", timeout=3) as response:
                    self.assertEqual(response.status, HTTPStatus.OK)
                    javascript = response.read()
                with urlopen(f"{url}desktop.css", timeout=3) as response:
                    self.assertEqual(response.status, HTTPStatus.OK)
                    self.assertTrue(response.read())
                resource_path = Path(directory) / "desktop_app.js"
                resource_path.write_bytes(javascript)
                view = {
                    "identityStatus": "unknown",
                    "resolution": "uncertain",
                    "documentRole": "exam",
                    "answerKeyState": "unknown",
                    "versionNumber": None,
                    "identity": None,
                    "evidence": {"year": {"reason": "sem evidência"}},
                    "reason": "identidade semântica insuficiente",
                    "algorithmVersion": "semantic-identity-v1",
                }
                runner = (
                    "const helpers = require(process.argv[1]);"
                    "const view = JSON.parse(process.argv[2]);"
                    "console.log(JSON.stringify({"
                    "badge: helpers.semanticIdentityBadge(view),"
                    "fallbackBadges: [null, 'observed', 'unexpected',"
                    " 'new_identity', 'new_version']"
                    ".map((resolution) => helpers.semanticIdentityBadge({resolution})),"
                    "presentation: helpers.semanticIdentityPresentation(view)"
                    "}));"
                )
                completed = subprocess.run(
                    ["node", "-e", runner, str(resource_path), json.dumps(view)],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=10,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        contract = json.loads(completed.stdout)
        self.assertEqual(contract["badge"], "Exceção")
        self.assertEqual(
            contract["fallbackBadges"],
            [None, None, None, "Nova versão", "Nova versão"],
        )
        self.assertEqual(contract["presentation"]["identityLabel"], "Identidade desconhecida")
        self.assertFalse(contract["presentation"]["showIdentityConfidence"])
        self.assertEqual(
            contract["presentation"]["details"]["reason"],
            "identidade semântica insuficiente",
        )

    def test_packaged_ui_renders_safe_semantic_event_history_with_text_content(self) -> None:
        package = resources.files("kad_collector")
        javascript = package.joinpath("desktop_app.js").read_text(encoding="utf-8")
        with TemporaryDirectory() as directory:
            resource_path = Path(directory) / "desktop_app.js"
            resource_path.write_text(javascript, encoding="utf-8")
            runner = r"""
const helpers = require(process.argv[1]);
const nodes = [];
global.document = {createElement(tag) {
  const node = {
    tag, children: [], value: '',
    append(...children) {this.children.push(...children);}
  };
  Object.defineProperty(node, 'textContent', {
    set(value) {this.value = String(value);}, get() {return this.value;}
  });
  Object.defineProperty(node, 'innerHTML', {set() {throw new Error('innerHTML is forbidden');}});
  nodes.push(node);
  return node;
}};
const root = {children: [], append(...children) {this.children.push(...children);}};
helpers.renderSemanticIdentityHistory(root, [{
  action: '<img src=x onerror=alert(1)>', actor: 'coordenador',
  createdAt: '2026-08-21T00:00:00+00:00', reason: 'correção humana',
  algorithmVersion: 'semantic-identity-v1'
}]);
function text(node) {return [node.value || '', ...(node.children || []).flatMap(text)];}
console.log(JSON.stringify(text(root).filter(Boolean)));
"""
            completed = subprocess.run(
                ["node", "-e", runner, str(resource_path)],
                check=False, capture_output=True, text=True, encoding="utf-8", timeout=10,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        rendered = " ".join(json.loads(completed.stdout))
        for value in (
            "<img src=x onerror=alert(1)>", "coordenador",
            "2026-08-21T00:00:00+00:00", "correção humana", "semantic-identity-v1",
        ):
            self.assertIn(value, rendered)

    def test_packaged_editorial_ui_contains_pending_and_batch_review_controls(self) -> None:
        package = resources.files("kad_collector")
        html = package.joinpath("desktop_ui.html").read_text(encoding="utf-8")
        javascript = package.joinpath("desktop_app.js").read_text(encoding="utf-8")

        for control_id in (
            "metric-card-pending",
            "metric-answer-summary",
            "metric-missing-answers",
            "batch-toolbar",
            "batch-approve-dialog",
            "defer-question",
            "review-context",
        ):
            self.assertIn(f'id="{control_id}"', html)
        self.assertIn("/api/questions/batch-approve", javascript)
        self.assertIn("activateEditorialQueue('pending')", javascript)
        self.assertIn("vinculadas ao gabarito", html)
        self.assertIn("Resposta oficial (gabarito)", html)
        self.assertIn("Arquivo já conhecido; nenhuma nova tarefa foi criada.", javascript)
        for removed_control in (
            "edit-review-notes",
            "edit-actor",
            "batch-actor",
            "batch-notes",
        ):
            self.assertNotIn(f'id="{removed_control}"', html)
        self.assertNotIn("Revisor responsável", html)
        self.assertNotIn(">Observações<", html)

    def test_packaged_resources_and_database_bootstrap(self) -> None:
        with TemporaryDirectory() as directory:
            application = DesktopApplication(Path(directory))
            self.assertEqual(_smoke_test(application), 0)
            self.assertTrue((Path(directory) / "collector.sqlite3").is_file())

    def test_main_configures_playwright_browsers_path_before_anything_else(self) -> None:
        # Precisa acontecer antes de qualquer chamada ao Patchright, para o
        # .exe empacotado (PyInstaller) encontrar o Chromium instalado pelo
        # usuario em vez da pasta temporaria de extracao.
        call_order: list[str] = []
        with (
            TemporaryDirectory() as directory,
            patch(
                "kad_collector.desktop_app.configure_playwright_browsers_path",
                side_effect=lambda: call_order.append("configure"),
            ),
            patch(
                "kad_collector.desktop_app.DesktopApplication",
                side_effect=lambda *_a, **_k: call_order.append("application")
                or DesktopApplication(Path(directory)),
            ),
        ):
            result = main(
                ["--smoke-test", "--data-dir", str(Path(directory) / "data")]
            )
        self.assertEqual(result, 0)
        self.assertEqual(call_order, ["configure", "application"])

    def test_local_server_enforces_host_origin_and_session_token(self) -> None:
        with TemporaryDirectory() as directory:
            application = DesktopApplication(Path(directory))
            server, thread, url = start_desktop_server(application)
            try:
                origin = url.rstrip("/")
                with self.assertRaises(HTTPError) as context:
                    urlopen(f"{url}api/bootstrap", timeout=3)
                self.assertEqual(context.exception.code, HTTPStatus.FORBIDDEN)

                bootstrap = Request(
                    f"{url}api/bootstrap",
                    headers={"X-KAD-Desktop-Token": application.token},
                )
                with urlopen(bootstrap, timeout=3) as response:
                    self.assertEqual(response.status, HTTPStatus.OK)

                forged_host = Request(
                    f"{url}api/bootstrap",
                    headers={
                        "Host": "evil.example",
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(forged_host, timeout=3)
                self.assertEqual(context.exception.code, HTTPStatus.FORBIDDEN)

                body = json.dumps({"filters": {}}).encode("utf-8")
                unauthorized = Request(
                    f"{url}api/query",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json", "Origin": origin},
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(unauthorized, timeout=3)
                self.assertEqual(context.exception.code, HTTPStatus.FORBIDDEN)

                wrong_origin = Request(
                    f"{url}api/query",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://evil.example",
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(wrong_origin, timeout=3)
                self.assertEqual(context.exception.code, HTTPStatus.FORBIDDEN)

                missing_origin = Request(
                    f"{url}api/query",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(missing_origin, timeout=3)
                self.assertEqual(context.exception.code, HTTPStatus.FORBIDDEN)

                authorized = Request(
                    f"{url}api/query",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": origin,
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with urlopen(authorized, timeout=3) as response:
                    self.assertEqual(response.status, HTTPStatus.OK)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_document_identity_correction_endpoint_uses_internal_actor_and_returns_result(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-correcao.pdf"
            write_text_pdf(
                pdf_path,
                [["Banca: Banca Oficial", "Concurso: Concurso Nacional 2026", "Ano: 2026"]],
            )
            application = DesktopApplication(root / "data")
            original_metadata = metadata(document_type="exam", role=None, variant=None)
            job_id = application.store.create_job([pdf_path], original_metadata, "local")
            document = application.store.documents_for_job(job_id)[0]
            application.store.save_page(
                document["id"],
                1,
                "Banca: Banca Oficial\nConcurso: Concurso Nacional 2026\nAno: 2026",
                status="text",
            )
            original = application.store.resolve_extracted_document(document["id"])
            server, thread, url = start_desktop_server(application)
            try:
                origin = url.rstrip("/")

                def put(
                    payload: dict[str, object], *, token: bool = True
                ) -> tuple[int, dict[str, object]]:
                    headers = {"Content-Type": "application/json", "Origin": origin}
                    if token:
                        headers["X-KAD-Desktop-Token"] = application.token
                    request = Request(
                        f"{url}api/documents/{document['id']}",
                        data=json.dumps(payload).encode("utf-8"),
                        method="PUT",
                        headers=headers,
                    )
                    try:
                        with urlopen(request, timeout=3) as response:
                            return response.status, json.loads(response.read())
                    except HTTPError as exc:
                        return exc.code, json.loads(exc.read())

                correction = metadata(
                    document_type="exam",
                    role="Auditor",
                    stage="Segunda fase",
                    turn="Manhã",
                    variant="Tipo 2",
                ).model_dump(mode="json")
                status, body = put({"metadata": correction, "actor": "coordenador"}, token=False)
                self.assertEqual(status, HTTPStatus.FORBIDDEN)
                self.assertEqual(set(body), {"error"})

                status, body = put(
                    {"metadata": correction, "actor": "coordenador", "unexpected": "secret"}
                )
                self.assertEqual(status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(set(body), {"error"})
                self.assertNotIn("Traceback", str(body))
                self.assertNotIn(application.token, str(body))

                status, body = put(
                    {
                        "metadata": {**correction, "unexpected": "secret"},
                        "actor": "coordenador",
                    }
                )
                self.assertEqual(status, HTTPStatus.BAD_REQUEST)
                self.assertEqual(body, {"error": "metadados do documento inválidos"})
                self.assertNotIn("secret", str(body))
                self.assertNotIn("pydantic.dev", str(body))

                status, body = put({"metadata": correction})
                self.assertEqual(status, HTTPStatus.OK)
                self.assertEqual(set(body), {"ok", "identityResolution"})
                self.assertTrue(body["ok"])
                resolution = body["identityResolution"]
                self.assertEqual(resolution["document_version_id"], original.document_version_id)
                self.assertEqual(
                    resolution["profile"]["identity"]["roles"]["normalized_values"],
                    ["auditor"],
                )
                self.assertEqual(
                    resolution["profile"]["identity"]["turns"]["normalized_values"],
                    ["manhã"],
                )
                corrected = next(
                    event
                    for event in application.store.identity_events(document["id"])
                    if event["action"] == "identity_corrected"
                )
                self.assertEqual(corrected["actor"], "operador_local")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_import_api_reports_exact_duplicate_without_creating_an_empty_job(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova-repetida.pdf"
            write_blank_pdf(pdf_path)
            application = DesktopApplication(root / "data")
            server, thread, url = start_desktop_server(application)
            try:
                origin = url.rstrip("/")
                body = json.dumps(
                    {
                        "paths": [str(pdf_path)],
                        "metadata": {"document_type": "exam", "year": 2026},
                        "classifierProvider": "local",
                    }
                ).encode("utf-8")

                def import_once() -> tuple[int, dict[str, object]]:
                    request = Request(
                        f"{url}api/import",
                        data=body,
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "Origin": origin,
                            "X-KAD-Desktop-Token": application.token,
                        },
                    )
                    with urlopen(request, timeout=3) as response:
                        return response.status, json.loads(response.read())

                with patch.object(application.processor, "start") as start:
                    first_status, first = import_once()
                    second_status, second = import_once()

                with patch.object(
                    application, "import_pdfs", return_value=["batch-one", "batch-two"]
                ):
                    batch_status, batch = import_once()

                self.assertEqual(first_status, HTTPStatus.CREATED)
                self.assertEqual(set(first), {"jobId", "jobIds", "exactDuplicate"})
                self.assertEqual(len(first["jobIds"]), 1)
                self.assertEqual(first["jobId"], first["jobIds"][0])
                self.assertFalse(first["exactDuplicate"])
                self.assertEqual(second_status, HTTPStatus.CREATED)
                self.assertEqual(
                    second, {"jobId": None, "jobIds": [], "exactDuplicate": True}
                )
                self.assertEqual(batch_status, HTTPStatus.CREATED)
                self.assertEqual(
                    batch,
                    {
                        "jobId": None,
                        "jobIds": ["batch-one", "batch-two"],
                        "exactDuplicate": False,
                    },
                )
                start.assert_called_once_with(first["jobIds"][0])
                self.assertEqual(len(application.store.list_jobs()), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_import_api_rejects_empty_selection_and_directory_without_pdfs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            empty_directory = root / "empty"
            empty_directory.mkdir()
            application = DesktopApplication(root / "data")
            server, thread, url = start_desktop_server(application)
            try:
                origin = url.rstrip("/")
                for paths in ([], [str(empty_directory)]):
                    with self.subTest(paths=paths):
                        request = Request(
                            f"{url}api/import",
                            data=json.dumps({"paths": paths, "metadata": {}}).encode(),
                            method="POST",
                            headers={
                                "Content-Type": "application/json",
                                "Origin": origin,
                                "X-KAD-Desktop-Token": application.token,
                            },
                        )
                        with self.assertRaises(HTTPError) as context:
                            urlopen(request, timeout=3)
                        self.assertEqual(context.exception.code, HTTPStatus.BAD_REQUEST)
                        self.assertEqual(
                            json.loads(context.exception.read()),
                            {"error": "selecione ao menos um PDF"},
                        )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_local_server_batch_approval_updates_bootstrap_counters(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova.pdf"
            write_text_pdf(pdf_path, [["Documento oficial para revisão."]])
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job([pdf_path], metadata(), "local")
            document = application.store.documents_for_job(job_id)[0]
            question_ids = [
                application.store.save_question(
                    document["id"],
                    valid_question(
                        number,
                        f"Questão completa número {number} para aprovação pela API local.",
                    ),
                    full_classification(),
                )
                for number in (1, 2)
            ]
            server, thread, url = start_desktop_server(application)
            try:
                origin = url.rstrip("/")
                request = Request(
                    f"{url}api/questions/batch-approve",
                    data=json.dumps(
                        {"questionIds": question_ids, "actor": "revisora", "notes": None}
                    ).encode("utf-8"),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": origin,
                        "X-KAD-Desktop-Token": application.token,
                    },
                )
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(json.loads(response.read())["approved"], 2)
                summary = application.bootstrap()["summary"]
                self.assertEqual(summary["pending"], 0)
                self.assertEqual(summary["exportable"], 2)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_authenticated_pdf_response_streams_without_read_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova.pdf"
            write_blank_pdf(pdf_path, 2)
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job([pdf_path], metadata(), "local")
            document = application.store.documents_for_job(job_id)[0]
            server, thread, url = start_desktop_server(application)
            try:
                request = Request(
                    f"{url}api/documents/{document['id']}/pdf",
                    headers={"X-KAD-Desktop-Token": application.token},
                )
                with (
                    patch("kad_collector.desktop_server.PDF_STREAM_CHUNK_BYTES", 64),
                    patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes")),
                    urlopen(request, timeout=3) as response,
                ):
                    self.assertEqual(response.status, HTTPStatus.OK)
                    self.assertEqual(response.headers["Content-Type"], "application/pdf")
                    self.assertEqual(len(response.read()), pdf_path.stat().st_size)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
