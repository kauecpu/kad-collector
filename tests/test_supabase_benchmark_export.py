from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import ANY, MagicMock, call, patch

import psycopg

from kad_collector.json_utils import write_json
from kad_collector.models import QuestionRecord
from kad_collector.ollama_local_paths import LOCAL_ARTIFACT_ROOT
from kad_collector.question_equivalence import question_fingerprints
from kad_collector.supabase_benchmark_export import (
    SupabaseBenchmarkExportError,
    _load_import_records,
    export_supabase_benchmark_snapshot,
)

SOURCE_ID = "fc9e067e-666e-53a6-b23e-b5088fe98e31"
DOCUMENT_SHA256 = "a" * 64
SOURCE_URL = "https://conhecimento.fgv.br/sites/default/files/concursos/prova.pdf"


def _question_payload() -> dict[str, object]:
    return QuestionRecord(
        number=7,
        statement="Assinale a alternativa correta sobre o controle aduaneiro.",
        alternatives=[
            {"letter": "A", "text": "A primeira alternativa está incorreta."},
            {"letter": "B", "text": "A segunda alternativa está correta."},
        ],
        matter="Administração Aduaneira",
        subject="Controle Aduaneiro",
        board="FGV",
        organization="Receita Federal",
        role="Auditor-Fiscal",
        year=2023,
        source_pages=[4],
        discipline="Legislação Aduaneira",
        concurso="Receita Federal 2022",
        level="Superior",
        difficulty="Média",
        explanation="A alternativa B corresponde ao comando da questão.",
        correct_answer="B",
        answer_status="matched",
    ).model_dump(mode="json")


def _review_payload(*, fingerprint: str | None = None) -> dict[str, object]:
    question = _question_payload()
    return {
        "schemaVersion": 2,
        "taxonomyVersion": "2.0.1",
        "humanReview": False,
        "records": [
            {
                "sourceQuestionId": SOURCE_ID,
                "contentFingerprint": fingerprint
                or question_fingerprints(question).invariant,
                "structuralExpected": {
                    "discipline": "Legislação Aduaneira",
                    "matter": "Administração Aduaneira",
                    "subject": "Controle Aduaneiro",
                    "level": "Superior",
                },
                "status": "agent_reviewed_reference",
                "reviewedExpected": {
                    "discipline": "Legislação Aduaneira",
                    "matter": "Administração Aduaneira",
                    "subject": "Controle Aduaneiro",
                    "level": "Superior",
                },
                "reasonCode": "content_confirms_structural_reference",
            }
        ],
    }


def _remote_row() -> dict[str, object]:
    question = _question_payload()
    data = {
        "id": "q-rfb22-example-7",
        "discipline": question["discipline"],
        "subject": question["matter"],
        "topic": question["subject"],
        "board": question["board"],
        "year": question["year"],
        "role": question["role"],
        "institution": question["organization"],
        "concurso": question["concurso"],
        "level": question["level"],
        "difficulty": question["difficulty"],
        "statement": question["statement"],
        "alternatives": [
            {"id": item["letter"], "text": item["text"]}
            for item in question["alternatives"]  # type: ignore[union-attr]
        ],
        "correct": question["correct_answer"],
        "explanation": question["explanation"],
        "publicationStatus": "draft",
        "canonicalQuestion": {
            "questionId": "canonical-question",
            "groupId": "canonical-group",
            "occurrenceCount": 1,
            "provenances": [
                {
                    "occurrenceId": "occurrence-1",
                    "questionId": SOURCE_ID,
                    "documentId": "document-1",
                    "questionNumber": question["number"],
                    "pages": question["source_pages"],
                    "sha256": DOCUMENT_SHA256,
                    "url": SOURCE_URL,
                    "answer": question["correct_answer"],
                    "answerStatus": "matched",
                }
            ],
        },
    }
    return {
        "payload": {
            "schemaVersion": 1,
            "kind": "question",
            "source": {
                "provider": "kad-collector",
                "externalId": SOURCE_ID,
                "url": SOURCE_URL,
                "collectedAt": "2026-08-24T00:00:00Z",
                "fingerprint": question_fingerprints(question).invariant,
            },
            "data": data,
        }
    }


class SupabaseBenchmarkExportTests(unittest.TestCase):
    def setUp(self) -> None:
        LOCAL_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=LOCAL_ARTIFACT_ROOT)
        self.root = Path(self.temp_dir.name)
        self.review_path = self.root / "review.json"
        self.output_path = self.root / "collector-copy.sqlite3"
        write_json(self.review_path, _review_payload())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_preview_does_not_open_remote_connection(self) -> None:
        def forbidden_loader(_: str, __: object) -> list[dict[str, object]]:
            raise AssertionError("loader should not be called")

        result = export_supabase_benchmark_snapshot(
            reference_review_path=self.review_path,
            output_path=self.output_path,
            record_loader=forbidden_loader,  # type: ignore[arg-type]
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.requested_questions, 1)
        self.assertFalse(self.output_path.exists())

    def test_database_loader_sets_read_only_before_select_and_rolls_back(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [{"payload": {"source": {"externalId": SOURCE_ID}}}]

        with patch("psycopg.connect", return_value=connection) as connect:
            rows = _load_import_records("postgresql://secret", (SOURCE_ID,))

        connect.assert_called_once_with(
            "postgresql://secret", connect_timeout=10, row_factory=ANY
        )
        self.assertEqual(cursor.execute.call_args_list[0], call("SET TRANSACTION READ ONLY"))
        self.assertEqual(
            cursor.execute.call_args_list[1], call("SET LOCAL statement_timeout = '60s'")
        )
        self.assertIn("SELECT DISTINCT ON", cursor.execute.call_args_list[2].args[0])
        self.assertEqual(cursor.execute.call_args_list[2].args[1], ([SOURCE_ID],))
        connection.rollback.assert_called_once_with()
        self.assertEqual(len(rows), 1)

    def test_database_error_does_not_echo_connection_secret(self) -> None:
        with (
            patch(
                "psycopg.connect",
                side_effect=psycopg.OperationalError("postgresql://user:secret@example.invalid"),
            ),
            self.assertRaisesRegex(RuntimeError, "histórico editorial") as raised,
        ):
            _load_import_records("postgresql://user:secret@example.invalid", (SOURCE_ID,))

        self.assertNotIn("secret", str(raised.exception))

    def test_execute_requires_database_url(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "KAD_DATABASE_URL"):
            export_supabase_benchmark_snapshot(
                reference_review_path=self.review_path,
                output_path=self.output_path,
                execute=True,
                database_url="",
            )

    def test_exports_validated_minimal_sqlite_snapshot(self) -> None:
        calls: list[tuple[str, tuple[str, ...]]] = []

        def loader(database_url: str, source_ids: object) -> list[dict[str, object]]:
            ids = tuple(source_ids)  # type: ignore[arg-type]
            calls.append((database_url, ids))
            return [_remote_row()]

        result = export_supabase_benchmark_snapshot(
            reference_review_path=self.review_path,
            output_path=self.output_path,
            execute=True,
            database_url="postgresql://readonly@example.invalid/postgres",
            record_loader=loader,  # type: ignore[arg-type]
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.exported_questions, 1)
        self.assertEqual(result.exported_documents, 1)
        self.assertEqual(len(result.sha256 or ""), 64)
        self.assertEqual(calls, [("postgresql://readonly@example.invalid/postgres", (SOURCE_ID,))])
        with closing(sqlite3.connect(self.output_path)) as connection:
            question = connection.execute(
                "SELECT id, payload_json, classification_json FROM questions"
            ).fetchone()
            document = connection.execute(
                "SELECT id, metadata_json, sha256 FROM documents"
            ).fetchone()
        self.assertIsNotNone(question)
        self.assertEqual(question[0], SOURCE_ID)
        self.assertEqual(json.loads(question[1])["statement"], _question_payload()["statement"])
        self.assertEqual(
            json.loads(question[2])["discipline"]["source"], "section_title"
        )
        self.assertEqual(document[0], "document-1")
        self.assertEqual(document[2], DOCUMENT_SHA256)

    def test_changed_remote_content_does_not_replace_existing_snapshot(self) -> None:
        write_json(self.review_path, _review_payload(fingerprint="b" * 64))
        self.output_path.write_bytes(b"previous-safe-snapshot")

        with self.assertRaisesRegex(SupabaseBenchmarkExportError, "fingerprint divergente"):
            export_supabase_benchmark_snapshot(
                reference_review_path=self.review_path,
                output_path=self.output_path,
                execute=True,
                database_url="postgresql://readonly@example.invalid/postgres",
                record_loader=lambda _url, _ids: [_remote_row()],
            )

        self.assertEqual(self.output_path.read_bytes(), b"previous-safe-snapshot")

    def test_missing_remote_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(SupabaseBenchmarkExportError, "faltam 1 referências"):
            export_supabase_benchmark_snapshot(
                reference_review_path=self.review_path,
                output_path=self.output_path,
                execute=True,
                database_url="postgresql://readonly@example.invalid/postgres",
                record_loader=lambda _url, _ids: [],
            )
        self.assertFalse(self.output_path.exists())


if __name__ == "__main__":
    unittest.main()
