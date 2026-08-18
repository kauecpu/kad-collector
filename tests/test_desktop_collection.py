from __future__ import annotations

import threading
import unittest
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kad_collector.desktop_collection import DesktopCollectionManager
from kad_collector.desktop_models import DesktopFilterSet, DesktopImportMetadata
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
        self.assertIn('id="source-capacity-profile"', html)
        self.assertIn('id="source-browser-enabled"', html)
        self.assertIn('id="source-robots-policy"', html)
        self.assertIn('id="source-crawl-delay-policy"', html)
        self.assertIn("/api/collections", javascript)
        self.assertIn("capacityProfile", javascript)
        self.assertIn("collectionAction", javascript)
        self.assertIn("robotsPolicy", javascript)
        self.assertIn("crawlDelayPolicy", javascript)

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

        def complete_processing(job_id: str) -> None:
            self.store.update_job(job_id, status="completed")

        with (
            patch(
                "kad_collector.desktop_collection.collect_documents",
                return_value=(manifest, manifest_path),
            ),
            patch.object(
                self.processor, "start", side_effect=complete_processing
            ) as start_processor,
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
                if job["status"] not in {"queued", "running", "processing"}:
                    break
                threading.Event().wait(0.01)

        self.assertEqual(job["status"], "needs_attention")
        self.assertEqual(job["documents"], 1)
        self.assertEqual(job["questions"], 0)
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
            "https://conhecimento.fgv.br/prova.pdf",
        )
        self.assertEqual(imported["metadata"]["document_title"], "Prova de analista")
        self.assertEqual(imported["metadata"]["document_type"], "exam")
        self.assertEqual(imported["metadata"]["external_id"], "a" * 64)

    def test_fuvest_uses_v1_as_canonical_and_matches_versioned_answer_key(self) -> None:
        paths = [self.root / f"arquivo-{index}.pdf" for index in range(1, 6)]
        for path in paths:
            path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")

        base = DesktopImportMetadata(
            provider="fuvest_vestibular",
            source_url="https://www.fuvest.br/acervo-vestibular-2026/",
            concurso="Vestibular",
            board="FUVEST",
            year=2026,
            role="Vestibular",
            organization="Universidade de Sao Paulo",
        )
        metadata_by_path: dict[str, DesktopImportMetadata] = {}
        for index, path in enumerate(paths[:4], start=1):
            metadata_by_path[str(path.resolve()).casefold()] = base.model_copy(
                update={
                    "source_url": (
                        f"https://www.fuvest.br/wp-content/uploads/"
                        f"fuvest2026-fase1-prova-V{index}.pdf"
                    ),
                    "document_title": f"Prova 2026 V{index}",
                    "variant": f"V{index}",
                    "document_type": "exam",
                }
            )
        metadata_by_path[str(paths[4].resolve()).casefold()] = base.model_copy(
            update={
                "source_url": (
                    "https://www.fuvest.br/wp-content/uploads/fuvest2026-fase1-gabarito.pdf"
                ),
                "document_title": "Gabarito FUVEST 2026",
                "document_type": "answer_key",
            }
        )
        job_id = self.store.create_job(
            paths,
            base,
            "local",
            metadata_by_path=metadata_by_path,
        )
        exam_text = """{01}
Enunciado completo da primeira questao.
(A) Primeira alternativa.
(B) Segunda alternativa.
#####
{02}
Enunciado completo da segunda questao.
(A) Primeira alternativa.
(B) Segunda alternativa.
#####
"""
        answer_key_text = "PROVA V1 PROVA V2 PROVA V3 PROVA V4\n1 A 2 B 1 B 2 A 1 A 2 A 1 B 2 B"
        documents = self.store.documents_for_job(job_id)
        for document in documents:
            metadata = DesktopImportMetadata.model_validate(document["metadata"])
            text = answer_key_text if metadata.document_type == "answer_key" else exam_text
            self.store.save_page(document["id"], 1, text, status="text")
            self.store.update_document(document["id"], status="extracted", page_count=1)

        self.processor._structure_job(job_id, threading.Event())

        result = self.store.query(DesktopFilterSet())
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            [item["question"]["correct_answer"] for item in result["questions"]],
            ["A", "B"],
        )
        self.assertEqual({item["filename"] for item in result["questions"]}, {"Prova 2026 V1"})
        self.assertTrue(all(item["status"] == "pending" for item in result["questions"]))
        self.assertTrue(all("without_answer" not in item["flags"] for item in result["questions"]))
        skipped = [
            item
            for item in self.store.documents_for_job(job_id)
            if DesktopImportMetadata.model_validate(item["metadata"]).variant in {"V2", "V3", "V4"}
        ]
        self.assertTrue(
            all(
                any("não duplicadas" in warning for warning in item["warnings"]) for item in skipped
            )
        )

    def test_single_exam_matches_plain_answer_key_without_runtime_error(self) -> None:
        exam_path = self.root / "prova.pdf"
        answer_path = self.root / "gabarito.pdf"
        exam_path.write_bytes(b"%PDF-1.4\nexam\n%%EOF")
        answer_path.write_bytes(b"%PDF-1.4\nanswer\n%%EOF")
        base = DesktopImportMetadata(provider="banca", year=2026)
        job_id = self.store.create_job(
            [exam_path, answer_path],
            base,
            "local",
            metadata_by_path={
                str(exam_path.resolve()).casefold(): base.model_copy(
                    update={"document_type": "exam", "document_title": "Prova oficial"}
                ),
                str(answer_path.resolve()).casefold(): base.model_copy(
                    update={"document_type": "answer_key", "document_title": "Gabarito oficial"}
                ),
            },
        )
        documents = self.store.documents_for_job(job_id)
        for document in documents:
            metadata = DesktopImportMetadata.model_validate(document["metadata"])
            text = (
                "1 - B"
                if metadata.document_type == "answer_key"
                else "{01}\nEnunciado completo da questao.\n(A) Errada.\n(B) Correta.\n#####"
            )
            self.store.save_page(document["id"], 1, text, status="text")
            self.store.update_document(document["id"], status="extracted", page_count=1)

        self.processor._structure_job(job_id, threading.Event())

        questions = self.store.query(DesktopFilterSet())["questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"]["answer_status"], "matched")
        self.assertEqual(questions[0]["question"]["correct_answer"], "B")

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
