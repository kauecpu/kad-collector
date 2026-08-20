import ast
import gc
import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from kad_collector.desktop_models import DesktopImportMetadata
from kad_collector.desktop_store import DesktopStore
from kad_collector.models import DocumentRecord


class DocumentPipelineContractTests(unittest.TestCase):
    def test_interpretation_modules_do_not_import_acquisition_dependencies(self) -> None:
        forbidden = {
            "collector",
            "collection_transport",
            "collection_state",
            "config",
            "discovery",
            "security",
            "desktop_collection",
        }
        modules = (
            "desktop_processor",
            "desktop_parser",
            "desktop_classifier",
            "answer_key",
            "document_matching",
            "review_queue",
        )
        violations: list[str] = []

        for module_name in modules:
            qualified_name = f"kad_collector.{module_name}"
            spec = importlib.util.find_spec(qualified_name)
            self.assertIsNotNone(spec)
            assert spec is not None and spec.origin is not None
            tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    prefix = "kad_collector" if node.level else ""
                    imported.append(
                        ".".join(part for part in (prefix, node.module or "") if part)
                    )
                for name in imported:
                    parts = name.split(".")
                    dependency = parts[1] if parts[:1] == ["kad_collector"] else parts[0]
                    if dependency in forbidden:
                        violations.append(f"{module_name}: {name}")

        self.assertEqual(violations, [])

    def test_normalizes_local_pdf_and_computes_integrity(self) -> None:
        payload = b"%PDF-1.7\ncontract fixture\n"
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "sample.pdf"
            pdf_path.write_bytes(payload)

            from kad_collector.document_contract import normalize_local_document

            document = normalize_local_document(pdf_path)

            self.assertEqual(document.entry_method, "direct_import")
            self.assertEqual(document.declared_type, "auto")
            self.assertEqual(document.title, "sample.pdf")
            self.assertEqual(document.local_path, str(pdf_path.resolve()))
            self.assertEqual(document.size_bytes, len(payload))
            self.assertEqual(document.sha256, hashlib.sha256(payload).hexdigest())
            self.assertEqual(document.metadata, {})
            self.assertIsNone(document.original_url)
            self.assertIsNone(document.external_id)

    def test_normalizes_collected_document_preserving_record_and_evidence(self) -> None:
        acquired_at = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)
        record = DocumentRecord(
            source_id="source-1",
            source_name="Fictitious Source",
            document_type="exam",
            title="Exam 2026",
            original_url="https://example.test/exam.pdf",
            resolved_url="https://cdn.example.test/exam.pdf",
            local_path="/tmp/exam.pdf",
            sha256="a" * 64,
            content_type="application/pdf",
            size_bytes=123,
            downloaded_at=acquired_at,
            authorization_basis="written permission",
            terms_url="https://example.test/terms",
            metadata={"board": "Fictitious"},
        )

        from kad_collector.document_contract import normalize_collected_document

        document = normalize_collected_document(
            record, source_page_url="https://example.test/archive"
        )

        self.assertEqual(document.entry_method, "automated_collection")
        self.assertEqual(document.declared_type, "exam")
        self.assertEqual(document.title, record.title)
        self.assertEqual(document.local_path, record.local_path)
        self.assertEqual(document.sha256, record.sha256)
        self.assertEqual(document.size_bytes, record.size_bytes)
        self.assertEqual(document.original_url, record.original_url)
        self.assertEqual(document.resolved_url, record.resolved_url)
        self.assertEqual(document.source_page_url, "https://example.test/archive")
        self.assertEqual(document.source_id, record.source_id)
        self.assertEqual(document.source_name, record.source_name)
        self.assertEqual(document.acquired_at, acquired_at)
        self.assertEqual(document.content_type, record.content_type)
        self.assertEqual(document.metadata, record.metadata)
        self.assertEqual(document.evidence, ["written permission", "https://example.test/terms"])

    def test_collected_document_leaves_optional_values_empty(self) -> None:
        record = DocumentRecord(
            source_id="source-1",
            source_name="Fictitious Source",
            document_type="other",
            title="Document",
            original_url="https://example.test/document.pdf",
            resolved_url="https://example.test/document.pdf",
            local_path="/tmp/document.pdf",
            sha256="b" * 64,
            content_type="application/pdf",
            size_bytes=1,
            downloaded_at=datetime(2026, 8, 20, tzinfo=UTC),
            authorization_basis="",
            terms_url=None,
        )

        from kad_collector.document_contract import normalize_collected_document

        document = normalize_collected_document(record)

        self.assertEqual(document.evidence, [])
        self.assertEqual(document.metadata, {})
        self.assertIsNone(document.source_page_url)

    def test_reprocessing_changes_only_entry_method(self) -> None:
        payload = b"%PDF-reprocess"
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "sample.pdf"
            pdf_path.write_bytes(payload)

            from kad_collector.document_contract import (
                as_reprocessing_document,
                normalize_local_document,
            )

            document = normalize_local_document(pdf_path)
            reprocessed = as_reprocessing_document(document)

            self.assertEqual(reprocessed.entry_method, "reprocessing")
            self.assertEqual(
                reprocessed.model_copy(update={"entry_method": "reprocessing"}), reprocessed
            )
            self.assertEqual(
                document.model_dump(exclude={"entry_method"}),
                reprocessed.model_dump(exclude={"entry_method"}),
            )

    def test_local_validation_rejects_missing_directory_empty_wrong_size_and_hash(self) -> None:
        payload = b"%PDF-valid"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.pdf"
            folder = root / "folder"
            folder.mkdir()
            empty = root / "empty.pdf"
            empty.touch()
            valid = root / "valid.pdf"
            valid.write_bytes(payload)

            from kad_collector.document_contract import normalize_local_document

            for path in (missing, folder, empty):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    normalize_local_document(path)

            document = normalize_local_document(valid)
            with self.assertRaises(ValueError):
                document.model_copy(update={"size_bytes": len(payload) + 1}).validate_local_file()
            with self.assertRaises(ValueError):
                document.model_copy(update={"sha256": "0" * 64}).validate_local_file()


class RecordingRunner:
    def __init__(self) -> None:
        self.started_ids: list[str] = []

    def start(self, job_id: str) -> None:
        self.started_ids.append(job_id)


class DocumentPipelinePersistenceTests(unittest.TestCase):
    def test_legacy_database_adds_contract_column_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "collector.sqlite3"
            store = DesktopStore(database_path)
            job_id = "legacy-job"
            document_id = "legacy-document"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "INSERT INTO jobs (id, created_at, updated_at, status, classifier_provider) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        "2026-08-20T00:00:00+00:00",
                        "2026-08-20T00:00:00+00:00",
                        "queued",
                        "local",
                    ),
                )
                connection.execute(
                    "INSERT INTO documents ("
                    "id, job_id, local_path, filename, metadata_json, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        document_id,
                        job_id,
                        "/tmp/legacy.pdf",
                        "legacy.pdf",
                        "{}",
                        "2026-08-20T00:00:00+00:00",
                        "2026-08-20T00:00:00+00:00",
                    ),
                )
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(documents)")
                }
                if "normalized_json" in columns:
                    connection.execute("ALTER TABLE documents DROP COLUMN normalized_json")
                connection.commit()

            reopened = DesktopStore(database_path)

            self.assertEqual(len(reopened.documents_for_job(job_id)), 1)
            self.assertIsNone(reopened.documents_for_job(job_id)[0]["normalized_document"])
            with closing(sqlite3.connect(database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(documents)")
                }
                count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            self.assertIn("normalized_json", columns)
            self.assertEqual(count, 1)
            del store, reopened
            gc.collect()

    def test_direct_import_persists_validated_normalized_documents_and_starts_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "first.pdf", root / "second.pdf"]
            for path in paths:
                path.write_bytes(b"%PDF-1.7\nlocal fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            job_ids = pipeline.import_paths(
                paths,
                DesktopImportMetadata(provider="manual", board="Manual Board", year=2026),
                "local",
            )

            self.assertEqual(runner.started_ids, job_ids)
            self.assertEqual(len(job_ids), 1)
            documents = store.documents_for_job(job_ids[0])
            self.assertEqual(len(documents), 2)
            for document in documents:
                normalized = document["normalized_document"]
                self.assertEqual(normalized.entry_method, "direct_import")
                normalized.validate_local_file()

    def test_direct_import_with_empty_metadata_preserves_contract_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "plain.pdf"
            path.write_bytes(b"%PDF-1.7\nplain local fixture\n")
            store = DesktopStore(root / "collector.sqlite3")

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, RecordingRunner())

            job_id = pipeline.import_paths([path], DesktopImportMetadata(), "local")[0]

            stored = store.documents_for_job(job_id)[0]
            document = stored["normalized_document"]
            self.assertEqual(document.metadata, {})
            self.assertIsNone(document.external_id)
            self.assertIsNone(document.source_id)
            self.assertIsNone(document.source_name)
            self.assertIsNone(document.original_url)
            self.assertIsNone(document.resolved_url)
            self.assertIsNone(document.source_page_url)
            self.assertIsNone(stored["metadata"]["provider"])
            self.assertIsNone(stored["metadata"]["external_id"])
            self.assertIsNone(stored["metadata"]["source_url"])
            self.assertIsNone(stored["metadata"]["canonical_url"])

    def test_collected_submission_persists_automated_contract_and_uses_same_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "collected.pdf"
            payload = b"%PDF-1.7\ncollected fixture\n"
            path.write_bytes(payload)
            record = DocumentRecord(
                source_id="source-1",
                source_name="Source One",
                document_type="exam",
                title="Collected exam",
                original_url="https://example.test/exam.pdf",
                resolved_url="https://example.test/exam.pdf",
                local_path=str(path),
                sha256=hashlib.sha256(payload).hexdigest(),
                content_type="application/pdf",
                size_bytes=len(payload),
                downloaded_at=datetime(2026, 8, 20, tzinfo=UTC),
                authorization_basis="permission",
                metadata={"banca": "Source Board", "ano": "2026"},
            )
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_contract import normalize_collected_document
            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            job_ids = pipeline.submit([normalize_collected_document(record)], "local")

            self.assertEqual(runner.started_ids, job_ids)
            stored = store.documents_for_job(job_ids[0])[0]
            self.assertEqual(stored["normalized_document"].entry_method, "automated_collection")
            self.assertEqual(stored["metadata"]["board"], "Source Board")
            self.assertEqual(stored["metadata"]["year"], 2026)


if __name__ == "__main__":
    unittest.main()
