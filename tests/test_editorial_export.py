from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kad_collector.editorial_export import (
    EDITORIAL_IMPORT_V2_FINGERPRINT,
    EditorialExplanation,
    EditorialImportRecordV1,
    EditorialImportRecordV2,
    build_editorial_record,
    export_admin_package,
    stable_question_id,
)
from kad_collector.models import (
    Alternative,
    DocumentRecord,
    QuestionBatch,
    QuestionRecord,
    ValidationState,
)
from kad_collector.review import approve_batch_model
from kad_collector.validation import validate_editorial_question


def question(number: int, *, statement: str | None = None) -> QuestionRecord:
    return QuestionRecord(
        number=number,
        statement=statement or f"Enunciado completo e verificável da questão {number}.",
        alternatives=[
            Alternative(letter="A", text="Alternativa incorreta"),
            Alternative(letter="B", text="Alternativa correta"),
        ],
        discipline="Direito",
        matter="Direito Administrativo",
        subject="Atos administrativos",
        board="FGV",
        organization="Órgão Exemplo",
        concurso="Concurso Exemplo 2026",
        role="Analista",
        year=2026,
        level="Superior",
        difficulty="Média",
        explanation=f"A alternativa B resolve corretamente a questão {number}.",
        source_pages=[number],
        correct_answer="B",
        answer_status="matched",
    )


def batch(source_path: Path, questions: list[QuestionRecord]) -> QuestionBatch:
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return QuestionBatch(
        batch_id="lote-editorial-2026",
        created_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        model="fake-model",
        source_document=DocumentRecord(
            source_id="banca_exemplo",
            source_name="Banca Exemplo",
            document_type="exam",
            title="Prova 2026",
            original_url="https://example.com/provas/2026.pdf",
            resolved_url="https://example.com/provas/2026.pdf",
            local_path=str(source_path),
            sha256=digest,
            content_type="application/pdf",
            size_bytes=source_path.stat().st_size,
            downloaded_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
            authorization_basis="Fonte autorizada para coleta.",
        ),
        questions=questions,
        validation=ValidationState(valid=True),
    )


class EditorialExportTests(unittest.TestCase):
    def test_shared_contract_fixture_is_valid_version_one(self) -> None:
        fixture = (
            Path(__file__).parents[1]
            / "contracts"
            / "editorial-question-v1.fixture.jsonl"
        )
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        record = EditorialImportRecordV1.model_validate(payload)

        self.assertEqual(record.schema_version, 1)
        self.assertEqual(record.data.publication_status, "draft")
        self.assertEqual([item.id for item in record.data.alternatives], ["A", "B"])

    def test_shared_contract_fixture_is_valid_version_two_without_explanation(self) -> None:
        fixture = (
            Path(__file__).parents[1]
            / "contracts"
            / "editorial-question-v2.fixture.jsonl"
        )
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        record = EditorialImportRecordV2.model_validate(payload)

        self.assertEqual(record.schema_version, 2)
        self.assertIsNone(record.data.explanation)

    def test_stable_id_depends_on_source_proof_and_question_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "prova.pdf"
            source.write_bytes(b"evidence")
            original = question(7)
            source_batch = batch(source, [original])
            first = stable_question_id(source_batch, original)
            original.statement = "Enunciado corrigido durante a revisão editorial."

            self.assertEqual(stable_question_id(source_batch, original), first)

    def test_optional_explanation_invalid_alternatives_and_annulled(self) -> None:
        missing = question(1)
        missing.explanation = None
        self.assertFalse(
            any("explicacao" in issue for issue in validate_editorial_question(missing))
        )

        invalid_alternatives = question(2)
        invalid_alternatives.alternatives.extend(
            [
                Alternative(letter="C", text="C"),
                Alternative(letter="D", text="D"),
                Alternative(letter="E", text="E"),
                Alternative(letter="F", text="F"),
            ]
        )
        self.assertTrue(
            any(
                "2 a 5" in issue
                for issue in validate_editorial_question(invalid_alternatives)
            )
        )

        annulled = question(3)
        annulled.answer_status = "annulled"
        annulled.correct_answer = None
        self.assertTrue(any("anulada" in issue for issue in validate_editorial_question(annulled)))

    def test_valid_export_writes_one_record_per_line_and_copies_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "prova.pdf"
            source.write_bytes(b"pdf-evidence")
            approved = approve_batch_model(batch(source, [question(1), question(2)]), "editor")
            result = export_admin_package(
                approved,
                output_root=root / "exports",
                now=datetime(2026, 8, 16, 13, tzinfo=UTC),
            )
            lines = result.questions_path.read_text(encoding="utf-8").splitlines()

            self.assertEqual(len(lines), 2)
            self.assertEqual(result.exported_count, 2)
            self.assertEqual(result.exception_count, 0)
            self.assertTrue((result.directory / "fontes").is_dir())
            self.assertTrue(
                all(
                    json.loads(line)["schemaVersion"] == 2
                    and json.loads(line)["data"]["publicationStatus"] == "draft"
                    for line in lines
                )
            )
            manifest = json.loads(
                (result.directory / "manifesto.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["contract"]["fingerprint"],
                EDITORIAL_IMPORT_V2_FINGERPRINT,
            )

    def test_version_two_omits_empty_explanation_and_difficulty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "prova.pdf"
            source.write_bytes(b"evidence")
            item = question(1)
            item.explanation = None
            item.difficulty = None
            record = build_editorial_record(batch(source, [item]), item)
            payload = record.model_dump(mode="json", by_alias=True, exclude_none=True)

        self.assertNotIn("explanation", payload["data"])
        self.assertNotIn("difficulty", payload["data"])

    def test_ai_explanation_requires_traceability(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider"):
            EditorialExplanation(
                text="Explicação suficientemente longa para validação.",
                origin="ai",
                reviewStatus="draft",
            )
        explanation = EditorialExplanation(
            text="Explicação suficientemente longa para validação.",
            origin="ai",
            reviewStatus="draft",
            provider="openai",
            model="modelo-teste",
            promptVersion="explanation-v1",
        )
        self.assertEqual(explanation.prompt_version, "explanation-v1")

    def test_duplicate_content_is_sent_to_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "prova.pdf"
            source.write_bytes(b"pdf-evidence")
            first = question(1, statement="Enunciado duplicado com conteúdo suficiente.")
            second = question(2, statement=first.statement)
            second.explanation = first.explanation
            approved = approve_batch_model(batch(source, [first, second]), "editor")
            result = export_admin_package(approved, output_root=root / "exports")

            self.assertEqual(result.exported_count, 1)
            self.assertEqual(result.exception_count, 1)
            self.assertIn(
                "conteudo duplicado",
                result.exceptions_path.read_text(encoding="utf-8"),
            )

    def test_build_record_rejects_insecure_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "prova.pdf"
            source.write_bytes(b"evidence")
            source_batch = batch(source, [question(1)])
            source_batch.source_document.resolved_url = "http://example.com/prova.pdf"
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                build_editorial_record(source_batch, source_batch.questions[0])

    def test_record_exports_canonical_identity_without_replacing_display_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "prova.pdf"
            source.write_bytes(b"evidence")
            source_batch = batch(source, [question(1)])
            record = build_editorial_record(
                source_batch,
                source_batch.questions[0],
                canonical_identity={
                    "contestId": "contest-id",
                    "contestKey": "contest-key",
                    "contestName": "Concurso Exemplo 2026",
                    "applicationId": "application-id",
                    "applicationKey": "application-key",
                    "applicationName": "Aplicação principal",
                    "documentId": "document-id",
                    "scopeIds": ["scope-id"],
                    "aliases": ["EX26"],
                },
            )
            payload = record.model_dump(mode="json", by_alias=True, exclude_none=True)

        self.assertEqual(payload["data"]["concurso"], "Concurso Exemplo 2026")
        self.assertEqual(payload["data"]["canonicalIdentity"]["contestId"], "contest-id")
        self.assertEqual(payload["data"]["canonicalIdentity"]["scopeIds"], ["scope-id"])


if __name__ == "__main__":
    unittest.main()
