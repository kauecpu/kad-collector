from __future__ import annotations

import json
import threading
import unittest
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

from kad_collector.desktop_app import _smoke_test, build_parser
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
            job_id = store.create_job([readable, blocked], metadata(), "local")
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
        self.assertEqual(records[0]["schemaVersion"], 1)
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

    def test_batch_approval_changes_nothing_when_one_question_is_invalid(self) -> None:
        valid_id = self.store.save_question(
            self.document["id"], valid_question(1), full_classification()
        )
        invalid = valid_question(2).model_copy(
            update={"explanation": None, "difficulty": None}
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

    def test_packaged_resources_and_database_bootstrap(self) -> None:
        with TemporaryDirectory() as directory:
            application = DesktopApplication(Path(directory))
            self.assertEqual(_smoke_test(application), 0)
            self.assertTrue((Path(directory) / "collector.sqlite3").is_file())

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
