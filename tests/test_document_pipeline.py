import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kad_collector.models import DocumentRecord


class DocumentPipelineContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
