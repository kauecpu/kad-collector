from __future__ import annotations

import threading
import unittest
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kad_collector.desktop_collection import DesktopCollectionManager
from kad_collector.desktop_models import DesktopImportMetadata
from kad_collector.desktop_processor import DesktopProcessor, _document_type
from kad_collector.desktop_store import DesktopStore
from kad_collector.models import DocumentRecord, DownloadManifest


class DesktopCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = DesktopStore(self.root / "collector.sqlite3")
        self.processor = DesktopProcessor(self.store)
        self.manager = DesktopCollectionManager(self.root, self.store, self.processor)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_catalog_exposes_collectable_sources_and_reference_only_obmep(self) -> None:
        catalog = {source["id"]: source for source in self.manager.catalog()}
        self.assertTrue(catalog["fgv_conhecimento"]["collectable"])
        self.assertTrue(catalog["inep_enem"]["collectable"])
        self.assertTrue(catalog["comvest_unicamp"]["collectable"])
        self.assertFalse(catalog["obmep_referencias"]["collectable"])
        self.assertIn("robots.txt", catalog["obmep_referencias"]["notice"])

    def test_packaged_interface_contains_link_collection_tab(self) -> None:
        package = resources.files("kad_collector")
        html = package.joinpath("desktop_ui.html").read_text(encoding="utf-8")
        javascript = package.joinpath("desktop_app.js").read_text(encoding="utf-8")
        self.assertIn('data-section="sources"', html)
        self.assertIn('id="source-form"', html)
        self.assertIn("/api/collections", javascript)

    def test_collection_downloads_then_creates_local_processing_job(self) -> None:
        pdf_path = self.root / "fgv_conhecimento-exam-fixture.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
        document = DocumentRecord(
            source_id="fgv_conhecimento",
            source_name="FGV Conhecimento - Concursos",
            document_type="exam",
            title="Prova de analista",
            original_url="https://conhecimento.fgv.br/prova.pdf",
            resolved_url="https://conhecimento.fgv.br/prova.pdf",
            local_path=str(pdf_path),
            sha256="a" * 64,
            content_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
            downloaded_at=datetime.now(UTC),
            authorization_basis="Fonte oficial.",
            metadata={"banca": "FGV"},
        )
        manifest = DownloadManifest(created_at=datetime.now(UTC), documents=[document])
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")

        with (
            patch(
                "kad_collector.desktop_collection.collect_documents",
                return_value=(manifest, manifest_path),
            ),
            patch.object(self.processor, "start") as start_processor,
        ):
            collection_id = self.manager.start(
                {
                    "sourceId": "fgv_conhecimento",
                    "url": "https://conhecimento.fgv.br/concursos/mprj2025",
                    "classifierProvider": "local",
                }
            )
            for _ in range(100):
                job = next(item for item in self.manager.list_jobs() if item["id"] == collection_id)
                if job["status"] not in {"queued", "running"}:
                    break
                threading.Event().wait(0.01)

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["documents"], 1)
        self.assertEqual(job["outputDirectory"], str(pdf_path.parent.resolve()))
        self.assertEqual(
            job["files"],
            [
                {
                    "title": "Prova de analista",
                    "documentType": "exam",
                    "localPath": str(pdf_path.resolve()),
                    "sourceUrl": "https://conhecimento.fgv.br/prova.pdf",
                    "sizeBytes": pdf_path.stat().st_size,
                }
            ],
        )
        self.assertEqual(len(job["importJobIds"]), 1)
        start_processor.assert_called_once_with(job["importJobIds"][0])
        imported = self.store.documents_for_job(job["importJobIds"][0])[0]
        self.assertEqual(imported["metadata"]["board"], "FGV")
        self.assertEqual(imported["metadata"]["year"], 2025)
        self.assertEqual(
            imported["metadata"]["source_url"],
            "https://conhecimento.fgv.br/concursos/mprj2025",
        )

    def test_collection_rejects_host_outside_selected_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "host nao permitido"):
            self.manager.start(
                {
                    "sourceId": "fgv_conhecimento",
                    "url": "https://example.com/prova.pdf",
                }
            )

    def test_reference_only_source_cannot_start_content_download(self) -> None:
        with self.assertRaisesRegex(ValueError, "somente para registro"):
            self.manager.start(
                {
                    "sourceId": "obmep_referencias",
                    "url": "https://www.obmep.org.br/provas-2025.htm",
                }
            )

    def test_downloaded_answer_key_filename_is_detected(self) -> None:
        self.assertEqual(
            _document_type("fgv_conhecimento-answer_key-123.pdf", DesktopImportMetadata()),
            "answer_key",
        )


if __name__ == "__main__":
    unittest.main()
