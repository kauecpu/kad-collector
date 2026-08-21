import ast
import gc
import hashlib
import importlib.util
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen import canvas

from kad_collector.desktop_limits import MAX_BATCH_PDFS
from kad_collector.desktop_models import (
    ClassificationValue,
    DesktopFilterSet,
    DesktopImportMetadata,
    QuestionClassification,
)
from kad_collector.desktop_processor import DesktopProcessor
from kad_collector.desktop_store import DesktopStore
from kad_collector.document_contract import normalize_collected_document
from kad_collector.models import Alternative, DocumentRecord, QuestionRecord


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
        self.assertEqual(document.warnings, [])
        self.assertEqual(document.metadata, {})
        self.assertIsNone(document.external_id)
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

    def test_batches_collected_documents_by_semantic_metadata_not_source_id(self) -> None:
        acquired_at = datetime(2026, 8, 20, tzinfo=UTC)

        def collected(
            source_id: str, document_type: str, title: str, suffix: str
        ) -> DocumentRecord:
            return DocumentRecord(
                source_id=source_id,
                source_name=f"Fonte {source_id}",
                document_type=document_type,
                title=title,
                original_url=f"https://{source_id}.example.test/{suffix}.pdf",
                resolved_url=f"https://{source_id}.example.test/{suffix}.pdf",
                local_path=f"/tmp/{suffix}.pdf",
                sha256=(suffix[0] * 64),
                content_type="application/pdf",
                size_bytes=1,
                downloaded_at=acquired_at,
                authorization_basis="permission",
                metadata={
                    "concurso": "Concurso 2026",
                    "ano": "2026",
                    "cargo": "Analista",
                    "orgao": "Instituto",
                },
            )

        from kad_collector.document_contract import normalize_collected_document
        from kad_collector.document_pipeline import processing_batches

        documents = [
            normalize_collected_document(collected("source-a", "exam", "Prova A", "a")),
            normalize_collected_document(collected("source-b", "exam", "Prova B", "b")),
            normalize_collected_document(
                collected("source-c", "answer_key", "Gabarito", "c")
            ),
        ]

        batches = processing_batches(documents)

        self.assertEqual(len(batches), 1)
        self.assertEqual(
            {document.local_path for document in batches[0]},
            {"/tmp/a.pdf", "/tmp/b.pdf", "/tmp/c.pdf"},
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


def _stored_question() -> QuestionRecord:
    return QuestionRecord(
        number=1,
        statement="Enunciado preservado para verificar o histórico editorial.",
        alternatives=[
            Alternative(letter="A", text="Alternativa A."),
            Alternative(letter="B", text="Alternativa B."),
        ],
        matter="Matéria",
        subject="Assunto",
        discipline="Disciplina",
        board="Banca",
        organization="Órgão",
        concurso="Concurso 2026",
        role="Analista",
        year=2026,
        level="Superior",
        difficulty="Média",
        source_pages=[1],
        explanation="Explicação editorial preservada.",
    )


def _stored_classification() -> QuestionClassification:
    value = lambda item: ClassificationValue(value=item, confidence=1, evidence="fixture")  # noqa: E731
    return QuestionClassification(
        concurso=value("Concurso 2026"),
        board=value("Banca"),
        year=value(2026),
        role=value("Analista"),
        organization=value("Órgão"),
        level=value("Superior"),
        discipline=value("Disciplina"),
        subject=value("Matéria"),
        topic=value("Assunto"),
        difficulty=value("Média"),
    )


class DocumentPipelinePersistenceTests(unittest.TestCase):
    def test_same_pdf_twice_creates_one_document_job_and_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "prova.pdf"
            path.write_bytes(b"%PDF-1.7\nexact duplicate exam fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            metadata = DesktopImportMetadata(document_type="exam", year=2026)
            first = pipeline.import_paths([path], metadata, "local")
            second = pipeline.import_paths([path], metadata, "local")

            with closing(store._connect()) as connection:
                counts = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("jobs", "documents", "document_observations")
                }
            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(counts, {"jobs": 1, "documents": 1, "document_observations": 1})
            self.assertEqual(runner.started_ids, first)

    def test_partially_duplicate_batch_creates_job_only_for_new_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            known = root / "known.pdf"
            new = root / "new.pdf"
            known.write_bytes(b"%PDF-1.7\nknown document fixture\n")
            new.write_bytes(b"%PDF-1.7\nnew document fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            metadata = DesktopImportMetadata(document_type="exam", year=2026)
            first = pipeline.import_paths([known], metadata, "local")
            second = pipeline.import_paths([known, new], metadata, "local")

            with closing(store._connect()) as connection:
                second_documents = connection.execute(
                    "SELECT filename FROM documents WHERE job_id = ?",
                    (second[0],),
                ).fetchall()
                counts = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("jobs", "documents", "document_observations")
                }
                duplicate_events = connection.execute(
                    "SELECT COUNT(*) FROM document_identity_events "
                    "WHERE action = 'exact_duplicate'"
                ).fetchone()[0]

            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual([row["filename"] for row in second_documents], ["new.pdf"])
            self.assertEqual(counts, {"jobs": 2, "documents": 2, "document_observations": 2})
            self.assertEqual(duplicate_events, 1)
            self.assertEqual(runner.started_ids, [first[0], second[0]])

    def test_force_reprocess_reuses_observation_and_creates_operational_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reprocess.pdf"
            path.write_bytes(b"%PDF-1.7\nforced reprocessing fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            first = pipeline.import_paths(
                [path], DesktopImportMetadata(document_type="exam", year=2026), "local"
            )
            original = store.documents_for_job(first[0])[0]
            second = pipeline.reprocess([original["id"]], "local")

            with closing(store._connect()) as connection:
                documents = connection.execute(
                    "SELECT observation_id, document_version_id FROM documents "
                    "ORDER BY created_at, id"
                ).fetchall()
                observations = connection.execute(
                    "SELECT COUNT(*) FROM document_observations"
                ).fetchone()[0]

            self.assertEqual(len(second), 1)
            self.assertEqual(len(documents), 2)
            self.assertEqual(
                {row["observation_id"] for row in documents},
                {documents[0]["observation_id"]},
            )
            self.assertEqual(
                {row["document_version_id"] for row in documents},
                {documents[0]["document_version_id"]},
            )
            self.assertEqual(observations, 1)
            self.assertEqual(runner.started_ids, [first[0], second[0]])

    def test_same_answer_key_twice_creates_no_second_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "gabarito.pdf"
            path.write_bytes(b"%PDF-1.7\nexact duplicate answer key fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            metadata = DesktopImportMetadata(document_type="answer_key", year=2026)
            first = pipeline.import_paths([path], metadata, "local")
            second = pipeline.import_paths([path], metadata, "local")

            with closing(store._connect()) as connection:
                jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                questions = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual((jobs, documents, questions), (1, 1, 0))
            self.assertEqual(runner.started_ids, first)

    def test_collection_and_direct_import_with_same_sha_converge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "shared.pdf"
            payload = b"%PDF-1.7\ncollection and import convergence fixture\n"
            path.write_bytes(payload)
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()
            record = DocumentRecord(
                source_id="official-source",
                source_name="Official Source",
                document_type="exam",
                title="Official exam",
                original_url="https://example.test/exam.pdf",
                resolved_url="https://cdn.example.test/exam.pdf",
                local_path=str(path.resolve()),
                sha256=hashlib.sha256(payload).hexdigest(),
                content_type="application/pdf",
                size_bytes=len(payload),
                downloaded_at=datetime(2026, 8, 20, tzinfo=UTC),
                authorization_basis="permission",
                metadata={"year": "2026"},
            )

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            collected = pipeline.submit([normalize_collected_document(record)], "local")
            imported = pipeline.import_paths(
                [path], DesktopImportMetadata(document_type="exam", year=2026), "local"
            )

            with closing(store._connect()) as connection:
                origin_count = connection.execute(
                    "SELECT COUNT(*) FROM document_observation_origins origins "
                    "JOIN document_observations observations "
                    "ON observations.id = origins.observation_id "
                    "WHERE observations.binary_sha256 = ?",
                    (record.sha256,),
                ).fetchone()[0]
                jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            self.assertEqual(len(collected), 1)
            self.assertEqual(imported, [])
            self.assertEqual(origin_count, 2)
            self.assertEqual(jobs, 1)

    def test_concurrent_claims_have_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "race.pdf"
            path.write_bytes(b"%PDF-1.7\nconcurrent duplicate fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()
            metadata = DesktopImportMetadata(document_type="exam", year=2026)

            from kad_collector.document_pipeline import DocumentPipeline

            def submit_once(_: int) -> list[str]:
                return DocumentPipeline(store, runner).import_paths([path], metadata, "local")

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(submit_once, range(2)))

            with closing(store._connect()) as connection:
                jobs = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                observations = connection.execute(
                    "SELECT COUNT(*) FROM document_observations"
                ).fetchone()[0]
            self.assertEqual(sorted(len(result) for result in results), [0, 1])
            self.assertEqual((jobs, documents, observations), (1, 1, 1))
            self.assertEqual(len(runner.started_ids), 1)

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
            for index, path in enumerate(paths):
                path.write_bytes(f"%PDF-1.7\nlocal fixture {index}\n".encode())
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

    def test_processor_rejects_pdf_changed_after_submission_without_replacing_integrity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "mutable.pdf"
            pdf = canvas.Canvas(str(path))
            pdf.drawString(54, 800, "QUESTAO 1")
            pdf.save()
            store = DesktopStore(root / "collector.sqlite3")
            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, RecordingRunner())
            job_id = pipeline.import_paths([path], DesktopImportMetadata(), "local")[0]
            submitted = store.documents_for_job(job_id)[0]
            expected_sha256 = submitted["sha256"]
            expected_size = submitted["size_bytes"]

            path.write_bytes(b"%PDF-1.7\nmutated after submission\n")
            DesktopProcessor(store).run(job_id, threading.Event())

            stored = store.document(submitted["id"])
            self.assertEqual(stored["status"], "exception")
            self.assertEqual(stored["sha256"], expected_sha256)
            self.assertEqual(stored["size_bytes"], expected_size)
            self.assertEqual(store.pages(submitted["id"]), [])
            self.assertTrue(any("integridade" in warning for warning in stored["warnings"]))

    def test_processor_interprets_the_same_pdf_snapshot_it_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "snapshot.pdf"
            replacement_path = root / "replacement.pdf"
            original = canvas.Canvas(str(path))
            original.drawString(54, 800, "CONTEUDO ORIGINAL PRESERVADO NO SNAPSHOT")
            original.save()
            replacement = canvas.Canvas(str(replacement_path))
            replacement.drawString(54, 800, "CONTEUDO ALTERADO DEPOIS DA LEITURA")
            replacement.save()
            original_payload = path.read_bytes()
            replacement_payload = replacement_path.read_bytes()
            store = DesktopStore(root / "collector.sqlite3")
            from kad_collector.document_pipeline import DocumentPipeline

            job_id = DocumentPipeline(store, RecordingRunner()).import_paths(
                [path], DesktopImportMetadata(), "local"
            )[0]
            submitted = store.documents_for_job(job_id)[0]
            real_read_bytes = Path.read_bytes

            def read_then_replace(candidate: Path) -> bytes:
                payload = real_read_bytes(candidate)
                if candidate == path:
                    path.write_bytes(replacement_payload)
                return payload

            with patch.object(Path, "read_bytes", autospec=True, side_effect=read_then_replace):
                DesktopProcessor(store)._extract_document(
                    job_id,
                    submitted,
                    threading.Event(),
                    time.monotonic(),
                )

            self.assertEqual(path.read_bytes(), replacement_payload)
            pages = store.pages(submitted["id"])
            self.assertEqual(len(pages), 1)
            self.assertIn("CONTEUDO ORIGINAL PRESERVADO", pages[0]["text"])
            self.assertNotIn("CONTEUDO ALTERADO", pages[0]["text"])
            self.assertEqual(
                store.document(submitted["id"])["sha256"],
                hashlib.sha256(original_payload).hexdigest(),
            )

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

    def test_reprocesses_stored_documents_from_local_contracts_without_mutating_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "original.pdf"
            path.write_bytes(b"%PDF-1.7\nlocal reprocessing fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            failed_runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            initial_pipeline = DocumentPipeline(store, failed_runner)
            original_job_id = initial_pipeline.import_paths(
                [path], DesktopImportMetadata(provider="manual", year=2026), "local"
            )[0]
            store.update_job(original_job_id, status="failed", error="interpretação indisponível")
            original_document = store.documents_for_job(original_job_id)[0]
            question_id = store.save_question(
                original_document["id"], _stored_question(), _stored_classification()
            )
            store.decide_question(question_id, "exception", actor="revisor", notes="aguardar")
            before_document = store.document(original_document["id"])
            before_question = store.question(question_id)
            before_audit = store.audit_log(question_id)

            reprocessing_runner = RecordingRunner()
            pipeline = DocumentPipeline(store, reprocessing_runner)
            job_ids = pipeline.reprocess([original_document["id"]], "local")

            self.assertEqual(reprocessing_runner.started_ids, job_ids)
            self.assertEqual(len(job_ids), 1)
            self.assertEqual(store.document(original_document["id"]), before_document)
            self.assertEqual(store.question(question_id), before_question)
            self.assertEqual(store.audit_log(question_id), before_audit)
            replacement = store.documents_for_job(job_ids[0])[0]
            self.assertNotEqual(replacement["id"], original_document["id"])
            self.assertEqual(replacement["normalized_document"].entry_method, "reprocessing")
            self.assertEqual(replacement["normalized_document"].local_path, str(path.resolve()))

    def test_reprocess_rejects_missing_local_file_before_creating_a_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "missing.pdf"
            path.write_bytes(b"%PDF-1.7\nmissing fixture\n")
            store = DesktopStore(root / "collector.sqlite3")

            from kad_collector.document_pipeline import DocumentPipeline

            runner = RecordingRunner()
            pipeline = DocumentPipeline(store, runner)
            original_job_id = pipeline.import_paths([path], DesktopImportMetadata(), "local")[0]
            original_document = store.documents_for_job(original_job_id)[0]
            path.unlink()
            jobs_before = store.list_jobs(limit=20)

            with self.assertRaisesRegex(ValueError, "arquivo local nao existe"):
                pipeline.reprocess([original_document["id"]], "local")

            self.assertEqual(store.list_jobs(limit=20), jobs_before)
            self.assertEqual(runner.started_ids, [original_job_id])
            self.assertEqual(store.document(original_document["id"]), original_document)

    def test_reprocesses_legacy_row_with_a_compatibility_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "legacy.pdf"
            payload = b"%PDF-1.7\nlegacy fixture\n"
            path.write_bytes(payload)
            store = DesktopStore(root / "collector.sqlite3")
            legacy_job_id = "legacy-job"
            legacy_document_id = "legacy-document"
            now = "2026-08-20T00:00:00+00:00"
            with closing(sqlite3.connect(store.path)) as connection:
                connection.execute(
                    "INSERT INTO jobs (id, created_at, updated_at, status, classifier_provider) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (legacy_job_id, now, now, "failed", "local"),
                )
                connection.execute(
                    "INSERT INTO documents (id, job_id, local_path, filename, sha256, size_bytes, "
                    "metadata_json, normalized_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        legacy_document_id,
                        legacy_job_id,
                        str(path.resolve()),
                        path.name,
                        hashlib.sha256(payload).hexdigest(),
                        len(payload),
                        '{"year":2026,"document_type":"exam"}',
                        now,
                        now,
                    ),
                )
                connection.commit()

            runner = RecordingRunner()
            from kad_collector.document_pipeline import DocumentPipeline

            replacement_job_id = DocumentPipeline(store, runner).reprocess(
                [legacy_document_id], "local"
            )[0]
            replacement = store.documents_for_job(replacement_job_id)[0]["normalized_document"]

            self.assertEqual(replacement.entry_method, "reprocessing")
            self.assertIsNone(replacement.original_url)
            self.assertIsNone(replacement.resolved_url)
            self.assertTrue(any("compatibilidade" in warning for warning in replacement.warnings))

    def test_real_reprocessing_never_mutates_historical_duplicate_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "duplicate.pdf"
            pdf = canvas.Canvas(str(path))
            for index, line in enumerate((
                "QUESTAO 1",
                "Este enunciado completo reproduz a mesma questão no reprocessamento.",
                "A) Primeira alternativa.",
                "B) Segunda alternativa.",
            )):
                pdf.drawString(54, 800 - (20 * index), line)
            pdf.save()
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            original_job_id = pipeline.import_paths(
                [path], DesktopImportMetadata(document_type="exam", year=2026), "local"
            )[0]
            processor = DesktopProcessor(store)
            processor.run(original_job_id, threading.Event())
            original_document = store.documents_for_job(original_job_id)[0]
            original_question = store.query(DesktopFilterSet())["questions"][0]
            store.decide_question(
                original_question["id"], "exception", actor="revisor", notes="preservar histórico"
            )
            before_document = store.document(original_document["id"])
            before_question = store.question(original_question["id"])
            before_audit = store.audit_log(original_question["id"])

            reprocessing_job_id = pipeline.reprocess([original_document["id"]], "local")[0]
            processor.run(reprocessing_job_id, threading.Event())

            self.assertEqual(store.document(original_document["id"]), before_document)
            self.assertEqual(store.question(original_question["id"]), before_question)
            self.assertEqual(store.audit_log(original_question["id"]), before_audit)
            reprocessed_questions = store.query(DesktopFilterSet())["questions"]
            self.assertEqual(len(reprocessed_questions), 2)
            reprocessed_question = next(
                question
                for question in reprocessed_questions
                if question["document_id"] != original_document["id"]
            )
            self.assertIn("duplicate", reprocessed_question["flags"])
            self.assertNotIn("duplicate", before_question["flags"])

    def test_reprocess_rejects_duplicate_ids_before_multi_batch_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for number in range(MAX_BATCH_PDFS + 1):
                path = root / f"document-{number}.pdf"
                path.write_bytes(f"%PDF-1.7\npreflight fixture {number}\n".encode())
                paths.append(path)
            store = DesktopStore(root / "collector.sqlite3")
            runner = RecordingRunner()

            from kad_collector.document_pipeline import DocumentPipeline

            pipeline = DocumentPipeline(store, runner)
            first_job_id = pipeline.import_paths(
                paths[:MAX_BATCH_PDFS], DesktopImportMetadata(), "local"
            )[0]
            second_job_id = pipeline.import_paths(
                paths[MAX_BATCH_PDFS:], DesktopImportMetadata(), "local"
            )[0]
            document_ids = [
                *(document["id"] for document in store.documents_for_job(first_job_id)),
                *(document["id"] for document in store.documents_for_job(second_job_id)),
            ]
            jobs_before = store.list_jobs(limit=MAX_BATCH_PDFS + 5)
            starts_before = list(runner.started_ids)

            with self.assertRaisesRegex(ValueError, "documentos duplicados"):
                pipeline.reprocess([*document_ids, document_ids[0]], "local")

            self.assertEqual(store.list_jobs(limit=MAX_BATCH_PDFS + 5), jobs_before)
            self.assertEqual(runner.started_ids, starts_before)


if __name__ == "__main__":
    unittest.main()
