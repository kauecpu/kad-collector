from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from kad_collector.ai_processor import OpenAIChunkExtractor, chunk_text, process_document
from kad_collector.answer_key import match_answer_key, parse_answer_key
from kad_collector.database import stage_batch
from kad_collector.json_utils import write_json
from kad_collector.models import (
    AIChunkResult,
    AIQuestion,
    Alternative,
    DocumentRecord,
    DownloadManifest,
    ExtractedDocument,
    ExtractedPage,
)
from kad_collector.pdf_extractor import extract_manifest
from kad_collector.review import approve_batch
from kad_collector.validation import verify_approved_batch

FIXTURES = Path(__file__).parent / "fixtures"


def document_record(local_path: str = "data/raw/prova.pdf") -> DocumentRecord:
    return DocumentRecord(
        source_id="orgao_teste",
        source_name="Orgao de teste",
        document_type="exam",
        title="Prova de Analista",
        original_url="https://provas.example.gov.br/prova.pdf",
        resolved_url="https://provas.example.gov.br/prova.pdf",
        local_path=local_path,
        sha256="a" * 64,
        content_type="application/pdf",
        size_bytes=100,
        downloaded_at=datetime.now(UTC),
        authorization_basis="Publicacao oficial conferida.",
        metadata={"banca": "Banca Teste", "orgao": "Orgao Teste", "ano": "2026"},
    )


def ai_question(number: int) -> AIQuestion:
    return AIQuestion(
        number=number,
        statement=f"Enunciado completo da questao {number}.",
        alternatives=[
            Alternative(letter="A", text="Alternativa incorreta"),
            Alternative(letter="B", text="Alternativa correta"),
        ],
        matter="Direito Administrativo",
        subject="Atos administrativos",
        board=None,
        organization=None,
        role="Analista",
        year=None,
        source_pages=[1],
    )


class FakeExtractor:
    model = "fake-model"

    def extract(self, text: str, metadata: dict[str, object]) -> AIChunkResult:
        return AIChunkResult(
            questions=[ai_question(1), ai_question(2)],
            chunk_has_continuation=False,
            warnings=[],
        )


class PipelineTests(unittest.TestCase):
    def test_openai_processor_requests_strict_non_stored_json(self) -> None:
        expected = AIChunkResult(
            questions=[ai_question(1)],
            chunk_has_continuation=False,
            warnings=[],
        )

        class FakeResponses:
            def __init__(self) -> None:
                self.arguments: dict[str, object] = {}

            def create(self, **kwargs: object) -> SimpleNamespace:
                self.arguments = kwargs
                return SimpleNamespace(output_text=expected.model_dump_json())

        responses = FakeResponses()
        extractor = object.__new__(OpenAIChunkExtractor)
        extractor.model = "gpt-test"
        extractor._client = SimpleNamespace(responses=responses)  # type: ignore[assignment]
        result = extractor.extract("Questao 1...", {"source_url": "https://example.test"})

        self.assertEqual(result.questions[0].number, 1)
        self.assertFalse(responses.arguments["store"])
        text_config = responses.arguments["text"]
        self.assertIsInstance(text_config, dict)
        assert isinstance(text_config, dict)
        output_format = text_config["format"]
        self.assertIsInstance(output_format, dict)
        assert isinstance(output_format, dict)
        self.assertTrue(output_format["strict"])
        self.assertEqual(output_format["type"], "json_schema")

    def test_chunking_preserves_overlap(self) -> None:
        text = "a" * 2_500 + "\n" + "b" * 2_500
        chunks = chunk_text(text, max_chars=3_000, overlap_chars=500)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(chunks[0].endswith("a"))
        self.assertIn("a", chunks[1][:600])

    def test_processes_questions_and_applies_known_metadata(self) -> None:
        record = document_record()
        extracted = ExtractedDocument(
            document=record,
            pages=[ExtractedPage(number=1, text="texto", character_count=5)],
            text="--- Pagina 1 ---\nTexto integral da prova.",
            needs_ocr=False,
        )
        batch = process_document(extracted, FakeExtractor())
        self.assertEqual(len(batch.questions), 2)
        self.assertEqual(batch.questions[0].board, "Banca Teste")
        self.assertEqual(batch.questions[0].organization, "Orgao Teste")
        self.assertEqual(batch.questions[0].year, 2026)
        self.assertEqual(batch.review.status, "pending")

    def test_parses_common_answer_key_formats(self) -> None:
        entries = parse_answer_key((FIXTURES / "gabarito.txt").read_text(encoding="utf-8"))
        self.assertEqual(entries[1].answer, "B")
        self.assertTrue(entries[2].annulled)
        self.assertEqual(entries[3].answer, "D")

    def test_review_approval_and_staging_preview_require_integrity(self) -> None:
        extracted = ExtractedDocument(
            document=document_record(),
            pages=[ExtractedPage(number=1, text="texto", character_count=5)],
            text="--- Pagina 1 ---\nTexto integral.",
            needs_ocr=False,
        )
        batch = process_document(extracted, FakeExtractor())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pending = root / "pending.json"
            answers = root / "answers.txt"
            reviewed = root / "reviewed.json"
            approved = root / "approved.json"
            write_json(pending, batch.model_dump(mode="json"))
            answers.write_text("1 - B\n2 - A\n", encoding="utf-8")
            match_answer_key(pending, answers, reviewed)
            approved_batch, _ = approve_batch(
                reviewed, "revisor.teste", output_path=approved
            )
            verify_approved_batch(approved_batch)
            preview = stage_batch(approved)
            self.assertFalse(preview.executed)
            self.assertEqual(preview.question_count, 2)

            approved_batch.questions[0].statement = "conteudo alterado"
            with self.assertRaises(ValueError):
                verify_approved_batch(approved_batch)

    def test_blank_pdf_is_flagged_for_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_path = root / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with pdf_path.open("wb") as handle:
                writer.write(handle)
            record = document_record(str(pdf_path))
            record.size_bytes = pdf_path.stat().st_size
            manifest = DownloadManifest(created_at=datetime.now(UTC), documents=[record])
            manifest_path = root / "manifest.json"
            output_path = root / "extracted.json"
            write_json(manifest_path, manifest.model_dump(mode="json"))
            result, _ = extract_manifest(manifest_path, output_path)
            self.assertTrue(result.documents[0].needs_ocr)


if __name__ == "__main__":
    unittest.main()
