from __future__ import annotations

import hashlib
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kad_collector.desktop_collection import (
    DesktopCollectionManager,
    _import_metadata,
)
from kad_collector.desktop_models import DesktopFilterSet, DesktopImportMetadata
from kad_collector.desktop_processor import (
    DesktopProcessor,
    _canonical_exam_documents,
    _document_type,
    _select_answer_key,
)
from kad_collector.desktop_store import DesktopStore
from kad_collector.document_pipeline import DocumentPipeline
from kad_collector.models import DocumentRecord, DownloadManifest
from kad_collector.semantic_identity import DocumentAssociationDecision


class DesktopCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = DesktopStore(self.root / "collector.sqlite3")
        self.processor = DesktopProcessor(self.store)
        self.manager = DesktopCollectionManager(self.root, self.store, self.processor)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def resolve_job_documents(self, job_id: str) -> None:
        for document in self.store.documents_for_job(job_id):
            if self.store.pages(document["id"]):
                self.store.resolve_extracted_document(document["id"])

    def process_text_documents(
        self,
        specifications: list[tuple[str, str, DesktopImportMetadata]],
    ) -> list[dict[str, object]]:
        paths: list[Path] = []
        metadata_by_path: dict[str, DesktopImportMetadata] = {}
        text_by_name: dict[str, str] = {}
        for filename, text, metadata in specifications:
            path = self.root / filename
            path.write_bytes(f"%PDF-1.4\n{filename}\n{text}\n%%EOF".encode())
            paths.append(path)
            metadata_by_path[str(path.resolve()).casefold()] = metadata
            text_by_name[filename] = text
        job_id = self.store.create_job(
            paths, DesktopImportMetadata(), "local", metadata_by_path=metadata_by_path
        )
        for document in self.store.documents_for_job(job_id):
            self.store.save_page(
                document["id"], 1, text_by_name[document["filename"]], status="text"
            )
            self.store.update_document(document["id"], status="extracted", page_count=1)
        self.resolve_job_documents(job_id)
        self.processor._structure_job(job_id, threading.Event())
        return self.store.documents_for_job(job_id)

    def stored_question(self, document_id: str, number: int) -> dict[str, object]:
        with closing(self.store._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM questions "
                "WHERE document_id = ? AND question_number = ?",
                (document_id, number),
            ).fetchone()
        self.assertIsNotNone(row)
        return json.loads(row["payload_json"])

    @staticmethod
    def semantic_metadata(
        document_type: str,
        title: str,
        *,
        role: str = "Analista",
        variant: str | None = None,
    ) -> DesktopImportMetadata:
        return DesktopImportMetadata(
            document_type=document_type,
            document_title=title,
            board="Banca Semantica",
            concurso="Concurso Semantico",
            year=2026,
            role=role,
            variant=variant,
        )

    def test_catalog_exposes_collectable_sources_and_reference_only_obmep(self) -> None:
        catalog = {source["id"]: source for source in self.manager.catalog()}
        self.assertTrue(catalog["fgv_conhecimento"]["collectable"])
        self.assertTrue(catalog["inep_enem"]["collectable"])
        self.assertTrue(catalog["comvest_unicamp"]["collectable"])
        self.assertFalse(catalog["obmep_referencias"]["collectable"])
        self.assertIn("robots.txt", catalog["obmep_referencias"]["notice"])
        self.assertTrue(
            all(source["engine"]["robotsPolicy"] == "ignore" for source in catalog.values())
        )
        self.assertTrue(
            all(source["engine"]["crawlDelayPolicy"] == "ignore" for source in catalog.values())
        )

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
        self.assertNotIn("byId('source-robots-policy').value = 'ignore'", javascript)
        self.assertNotIn("byId('source-crawl-delay-policy').value = 'ignore'", javascript)

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
            metadata={"banca": "FGV", "campo_conhecido": "preservado"},
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
                    "processingStatus": "new",
                }
            ],
        )
        self.assertEqual(len(job["importJobIds"]), 1)
        start_processor.assert_called_once_with(job["importJobIds"][0])
        imported = self.store.documents_for_job(job["importJobIds"][0])[0]
        normalized = imported["normalized_document"]
        self.assertEqual(
            normalized.source_page_url,
            "https://conhecimento.fgv.br/concursos/mprj2025",
        )
        self.assertEqual(normalized.metadata["banca"], "FGV")
        self.assertEqual(normalized.metadata["campo_conhecido"], "preservado")
        self.assertNotIn("external_id", normalized.metadata)
        self.assertIsNone(normalized.external_id)
        self.assertEqual(imported["metadata"]["board"], "FGV")
        self.assertEqual(imported["metadata"]["year"], 2025)
        self.assertEqual(
            imported["metadata"]["source_url"],
            "https://conhecimento.fgv.br/prova.pdf",
        )
        self.assertEqual(imported["metadata"]["document_title"], "Prova de analista")
        self.assertEqual(imported["metadata"]["document_type"], "exam")
        self.assertIsNone(imported["metadata"]["external_id"])
        self.assertEqual(
            imported["metadata"]["canonical_url"],
            "https://conhecimento.fgv.br/prova.pdf",
        )

    def test_acquisition_failure_starts_no_interpretation_job(self) -> None:
        class RecordingRunner:
            def __init__(self) -> None:
                self.started_ids: list[str] = []

            def start(self, job_id: str) -> None:
                self.started_ids.append(job_id)

        runner = RecordingRunner()
        manager = DesktopCollectionManager(
            self.root,
            self.store,
            self.processor,
            DocumentPipeline(self.store, runner),
        )
        with patch(
            "kad_collector.desktop_collection.collect_documents",
            side_effect=RuntimeError("download indisponível"),
        ):
            collection_id = manager.start(
                {
                    "sourceId": "fgv_conhecimento",
                    "url": "https://conhecimento.fgv.br/concursos/mprj2025",
                }
            )
            for _ in range(100):
                job = next(item for item in manager.list_jobs() if item["id"] == collection_id)
                if job["status"] not in {"queued", "running", "processing"}:
                    break
                threading.Event().wait(0.01)

        self.assertEqual(job["status"], "failed")
        self.assertEqual(runner.started_ids, [])
        self.assertEqual(self.store.list_jobs(), [])

    def test_repeated_collection_uses_transactional_duplicate_barrier(self) -> None:
        pdf_path = self.root / "repeated-collection.pdf"
        payload = b"%PDF-1.7\nrepeated collection fixture\n"
        pdf_path.write_bytes(payload)
        document = DocumentRecord(
            source_id="fgv_conhecimento",
            source_name="FGV Conhecimento - Concursos",
            document_type="exam",
            title="Prova oficial repetida",
            original_url="https://conhecimento.fgv.br/prova.pdf",
            resolved_url="https://conhecimento.fgv.br/prova.pdf",
            local_path=str(pdf_path.resolve()),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type="application/pdf",
            size_bytes=len(payload),
            downloaded_at=datetime(2026, 8, 20, tzinfo=UTC),
            authorization_basis="Fonte oficial.",
            metadata={"ano": "2026"},
        )
        manifest = DownloadManifest(
            created_at=datetime(2026, 8, 20, tzinfo=UTC),
            documents=[document],
        )
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")

        class CompletingRunner:
            def __init__(self, store: DesktopStore) -> None:
                self.store = store
                self.started_ids: list[str] = []

            def start(self, job_id: str) -> None:
                self.started_ids.append(job_id)
                self.store.update_job(job_id, status="completed")

        runner = CompletingRunner(self.store)
        manager = DesktopCollectionManager(
            self.root,
            self.store,
            self.processor,
            DocumentPipeline(self.store, runner),
        )

        def wait_until_done(collection_id: str) -> dict[str, object]:
            for _ in range(200):
                current = next(item for item in manager.list_jobs() if item["id"] == collection_id)
                if current["status"] not in {"queued", "running", "processing"}:
                    return current
                threading.Event().wait(0.01)
            self.fail("a coleta não terminou")

        request = {
            "sourceId": "fgv_conhecimento",
            "url": "https://conhecimento.fgv.br/concursos/mprj2025",
        }
        with patch(
            "kad_collector.desktop_collection.collect_documents",
            return_value=(manifest, manifest_path),
        ):
            first = wait_until_done(manager.start(request))
            second = wait_until_done(manager.start(request))

        self.assertIn(first["status"], {"completed", "needs_attention"})
        self.assertEqual(len(first["importJobIds"]), 1)
        self.assertIn(second["status"], {"completed", "needs_attention"})
        self.assertEqual(second["importJobIds"], [])
        self.assertEqual(len(runner.started_ids), 1)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_second_collection_skips_processed_sha_without_creating_another_job(self) -> None:
        pdf_path = self.root / "fgv_conhecimento-exam-repeat.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
        document = DocumentRecord(
            source_id="fgv_conhecimento",
            source_name="FGV Conhecimento - Concursos",
            document_type="exam",
            title="Prova repetida",
            original_url="https://conhecimento.fgv.br/prova.pdf#download",
            resolved_url="https://conhecimento.fgv.br/prova.pdf",
            local_path=str(pdf_path),
            sha256="c" * 64,
            content_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
            downloaded_at=datetime.now(UTC),
            authorization_basis="Fonte oficial.",
            metadata={"banca": "FGV"},
        )
        documents = [document]
        for index in range(6):
            key_path = self.root / f"gabarito-repeat-{index}.pdf"
            key_path.write_bytes(f"%PDF-1.4\nkey-{index}\n%%EOF".encode())
            documents.append(
                DocumentRecord(
                    source_id="fgv_conhecimento",
                    source_name="FGV Conhecimento - Concursos",
                    document_type="answer_key",
                    title=f"Gabarito {index}",
                    original_url=f"https://conhecimento.fgv.br/gabarito-{index}.pdf",
                    resolved_url=f"https://conhecimento.fgv.br/gabarito-{index}.pdf",
                    local_path=str(key_path),
                    sha256=f"{index + 1:064x}",
                    content_type="application/pdf",
                    size_bytes=key_path.stat().st_size,
                    downloaded_at=datetime.now(UTC),
                    authorization_basis="Fonte oficial.",
                    metadata={"banca": "FGV"},
                )
            )
        manifest = DownloadManifest(created_at=datetime.now(UTC), documents=documents)
        manifest_path = self.root / "manifest-repeat.json"
        manifest_path.write_text("{}", encoding="utf-8")

        def complete_processing(job_id: str) -> None:
            for imported in self.store.documents_for_job(job_id):
                self.store.update_document(imported["id"], status="processed")
            self.store.update_job(job_id, status="completed")

        def wait_for_collection(collection_id: str) -> dict[str, object]:
            for _ in range(200):
                current = next(
                    item for item in self.manager.list_jobs() if item["id"] == collection_id
                )
                if current["status"] not in {"queued", "running", "processing"}:
                    return current
                threading.Event().wait(0.01)
            self.fail("coleta não terminou")

        payload = {
            "sourceId": "fgv_conhecimento",
            "url": "https://conhecimento.fgv.br/concursos/rfb22",
            "classifierProvider": "local",
        }
        with (
            patch(
                "kad_collector.desktop_collection.collect_documents",
                return_value=(manifest, manifest_path),
            ),
            patch.object(self.processor, "start", side_effect=complete_processing),
        ):
            first = wait_for_collection(self.manager.start(payload))
            second = wait_for_collection(self.manager.start(payload))

        self.assertEqual(first["skippedDocuments"], 0)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["skippedDocuments"], 7)
        self.assertEqual(second["importJobIds"], [])
        self.assertIn("já processado — ignorado", " ".join(second["warnings"]))
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_fuvest_uses_v1_as_canonical_and_matches_versioned_answer_key(self) -> None:
        paths = [self.root / f"arquivo-{index}.pdf" for index in range(1, 6)]
        for index, path in enumerate(paths, start=1):
            path.write_bytes(f"%PDF-1.4\nfixture {index}\n%%EOF".encode())

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

        self.resolve_job_documents(job_id)
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
        base = DesktopImportMetadata(
            provider="banca", board="Banca", concurso="Concurso", year=2026
        )
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

        self.resolve_job_documents(job_id)
        self.processor._structure_job(job_id, threading.Event())

        questions = self.store.query(DesktopFilterSet())["questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"]["answer_status"], "matched")
        self.assertEqual(questions[0]["question"]["correct_answer"], "B")

    def test_fictitious_source_uses_v1_as_canonical_and_preserves_v2_evidence(self) -> None:
        common = DesktopImportMetadata(
            provider="fictitious_new_source",
            concurso="Selecao Nacional",
            board="Banca Ficticia",
            year=2026,
            role="Analista de Dados",
            organization="Instituto Ficticio",
            document_type="exam",
        )
        v1 = {
            "filename": "selecao-2026-v1.pdf",
            "metadata": common.model_copy(
                update={"document_title": "Selecao Nacional 2026 V1", "variant": "V1"}
            ).model_dump(mode="json"),
        }
        v2 = {
            "filename": "selecao-2026-v2.pdf",
            "metadata": common.model_copy(
                update={"document_title": "Selecao Nacional 2026 V2", "variant": "V2"}
            ).model_dump(mode="json"),
        }

        selected, evidence = _canonical_exam_documents([v2, v1])

        self.assertEqual(selected, [v1])
        self.assertEqual(evidence, [v2])

    def test_explicit_tipo_variants_stay_distinct_despite_v_tokens(self) -> None:
        common = DesktopImportMetadata(
            concurso="Selecao Nacional",
            year=2026,
            role="Analista",
            organization="Instituto Ficticio",
            document_type="exam",
        )
        tipo_1 = {
            "filename": "caderno-v1.pdf",
            "metadata": common.model_copy(
                update={"document_title": "Caderno V1", "variant": "Tipo 1"}
            ).model_dump(mode="json"),
        }
        tipo_2 = {
            "filename": "caderno-v2.pdf",
            "metadata": common.model_copy(
                update={"document_title": "Caderno V2", "variant": "Tipo 2"}
            ).model_dump(mode="json"),
        }

        selected, evidence = _canonical_exam_documents([tipo_1, tipo_2])

        self.assertEqual(selected, [tipo_1, tipo_2])
        self.assertEqual(evidence, [])

    def test_sole_weak_answer_key_is_rejected_in_batch_and_cache(self) -> None:
        exam = {
            "job_id": "current-job",
            "filename": "prova-direito.pdf",
            "metadata": DesktopImportMetadata(
                document_type="exam", document_title="Prova de Direito"
            ).model_dump(mode="json"),
            "exam_text": "Conteudo de Direito Administrativo",
        }
        in_batch = {
            "job_id": "current-job",
            "filename": "respostas.pdf",
            "metadata": DesktopImportMetadata(
                document_type="answer_key", document_title="Respostas"
            ).model_dump(mode="json"),
            "answer_key_text": "1 A",
        }
        cached = {
            **in_batch,
            "job_id": "historical-job",
            "filename": "gabarito-quimica.pdf",
            "metadata": DesktopImportMetadata(
                document_type="answer_key",
                document_title="Gabarito definitivo de Quimica",
            ).model_dump(mode="json"),
        }

        self.assertIsNone(_select_answer_key(exam, [in_batch]))
        self.assertIsNone(_select_answer_key(exam, [cached]))

        morning_exam = {
            **exam,
            "metadata": DesktopImportMetadata(
                document_type="exam", document_title="Prova de Direito Manhã"
            ).model_dump(mode="json"),
        }
        afternoon_key = {
            **in_batch,
            "metadata": DesktopImportMetadata(
                document_type="answer_key", document_title="Respostas Tarde"
            ).model_dump(mode="json"),
        }
        self.assertIsNone(_select_answer_key(morning_exam, [afternoon_key]))

    def test_same_job_key_with_known_year_or_variant_conflict_keeps_answer_missing(self) -> None:
        exam_text = (
            "{01}\nEnunciado completo de Direito.\n"
            "(A) Alternativa errada.\n(B) Alternativa correta.\n#####"
        )
        for label, answer_update in (
            ("wrong-year", {"year": 2025, "variant": "V1"}),
            ("wrong-variant", {"year": 2026, "variant": "V2"}),
            ("wrong-role", {"role": "Auditor", "variant": "V1"}),
        ):
            with self.subTest(label=label):
                root = self.root / label
                root.mkdir()
                store = DesktopStore(root / "collector.sqlite3")
                processor = DesktopProcessor(store)
                exam_path = root / "exam.pdf"
                answer_path = root / "answer.pdf"
                exam_path.write_bytes(b"%PDF-1.4\nexam\n%%EOF")
                answer_path.write_bytes(b"%PDF-1.4\nanswer\n%%EOF")
                common = DesktopImportMetadata(
                    concurso="Concurso Fiscal",
                    board="Banca Fiscal",
                    year=2026,
                    role="Analista",
                    organization="Secretaria da Fazenda",
                )
                exam_metadata = common.model_copy(
                    update={
                        "document_type": "exam",
                        "document_title": f"Prova Fiscal 2026 V1 {label}",
                        "variant": "V1",
                    }
                )
                answer_metadata = common.model_copy(
                    update={
                        "document_type": "answer_key",
                        "document_title": f"Gabarito Fiscal {label}",
                        **answer_update,
                    }
                )
                job_id = store.create_job(
                    [exam_path, answer_path],
                    common,
                    "local",
                    metadata_by_path={
                        str(exam_path.resolve()).casefold(): exam_metadata,
                        str(answer_path.resolve()).casefold(): answer_metadata,
                    },
                )
                for stored in store.documents_for_job(job_id):
                    metadata = DesktopImportMetadata.model_validate(stored["metadata"])
                    text = "1 - B" if metadata.document_type == "answer_key" else exam_text
                    store.save_page(stored["id"], 1, text, status="text")
                    store.update_document(stored["id"], status="extracted", page_count=1)

                for stored in store.documents_for_job(job_id):
                    store.resolve_extracted_document(stored["id"])
                processor._structure_job(job_id, threading.Event())

                result = store.query(
                    DesktopFilterSet(source_files=[exam_metadata.document_title or ""])
                )
                self.assertEqual(result["total"], 1)
                question_payload = result["questions"][0]["question"]
                self.assertIsNone(question_payload["correct_answer"])
                self.assertEqual(question_payload["answer_status"], "missing")

    def test_new_exam_reuses_persisted_answer_key_without_reprocessing_it(self) -> None:
        key_path = self.root / "gabarito-cached.pdf"
        exam_path = self.root / "prova-nova.pdf"
        key_path.write_bytes(b"%PDF-1.4\nkey\n%%EOF")
        exam_path.write_bytes(b"%PDF-1.4\nexam\n%%EOF")
        base = DesktopImportMetadata(
            provider="banca",
            board="Banca Oficial",
            concurso="Concurso 2026",
            year=2026,
            role="Analista",
        )
        key_metadata = base.model_copy(
            update={
                "document_type": "answer_key",
                "document_title": "Gabarito Analista 2026",
                "external_id": "d" * 64,
            }
        )
        key_job = self.store.create_job(
            [key_path],
            base,
            "local",
            metadata_by_path={str(key_path.resolve()).casefold(): key_metadata},
        )
        key_document = self.store.documents_for_job(key_job)[0]
        self.store.save_page(key_document["id"], 1, "1 - C", status="text")
        self.store.update_document(key_document["id"], status="extracted", page_count=1)

        exam_metadata = base.model_copy(
            update={
                "document_type": "exam",
                "document_title": "Prova Analista 2026",
                "external_id": "e" * 64,
            }
        )
        exam_job = self.store.create_job(
            [exam_path],
            base,
            "local",
            metadata_by_path={str(exam_path.resolve()).casefold(): exam_metadata},
        )
        exam_document = self.store.documents_for_job(exam_job)[0]
        self.store.save_page(
            exam_document["id"],
            1,
            "QUESTAO 1\nEnunciado completo.\nA) Errada.\nB) Errada.\nC) Correta.",
            status="text",
        )
        self.store.update_document(exam_document["id"], status="extracted", page_count=1)

        self.store.resolve_extracted_document(key_document["id"])
        self.store.resolve_extracted_document(exam_document["id"])
        self.processor._structure_job(exam_job, threading.Event())

        question = self.store.query(DesktopFilterSet())["questions"][0]
        self.assertEqual(question["question"]["correct_answer"], "C")
        self.assertEqual(question["status"], "pending")
        self.assertEqual(len(self.store.documents_for_job(exam_job)), 1)
        with closing(self.store._connect()) as connection:
            link = connection.execute(
                "SELECT exam_version_id, answer_key_version_id FROM document_links "
                "WHERE status = 'active'"
            ).fetchone()
        self.assertEqual(
            link["exam_version_id"],
            self.store.document(str(exam_document["id"]))["document_version_id"],
        )
        self.assertEqual(
            link["answer_key_version_id"],
            self.store.document(str(key_document["id"]))["document_version_id"],
        )

    def test_key_imported_after_exam_reconciles_and_records_complete_decision(self) -> None:
        exam_text = (
            "{01}\nEnunciado completo da primeira questao.\n"
            "(A) Alternativa A.\n(B) Alternativa B.\n#####"
        )
        exam = self.process_text_documents([(
            "exam-before-key.pdf", exam_text,
            self.semantic_metadata("exam", "Prova Analista 2026"),
        )])[0]
        self.assertEqual(self.stored_question(str(exam["id"]), 1)["answer_status"], "missing")

        key = self.process_text_documents([(
            "gabarito-after.pdf", "Gabarito preliminar\n1 - B",
            self.semantic_metadata("answer_key", "Gabarito preliminar Analista 2026"),
        )])[0]

        question = self.stored_question(str(exam["id"]), 1)
        with closing(self.store._connect()) as connection:
            link = connection.execute(
                "SELECT * FROM document_links WHERE status = 'active'"
            ).fetchone()
        self.assertEqual(question["answer_status"], "matched")
        self.assertEqual(question["correct_answer"], "B")
        self.assertEqual(link["exam_version_id"], exam["document_version_id"])
        self.assertEqual(link["answer_key_version_id"], key["document_version_id"])
        self.assertEqual(
            json.loads(link["decision_json"])["selected_version_id"],
            key["document_version_id"],
        )

    def test_definitive_key_supersedes_preliminary_and_reapplies_answers(self) -> None:
        exam_text = (
            "{01}\nEnunciado completo da primeira questao.\n(A) A.\n(B) B.\n#####\n"
            "{02}\nEnunciado completo da segunda questao.\n(A) A.\n(B) B.\n(C) C.\n#####"
        )
        exam = self.process_text_documents([(
            "exam-succession.pdf", exam_text,
            self.semantic_metadata("exam", "Prova de sucessao 2026"),
        )])[0]
        preliminary = self.process_text_documents([(
            "key-preliminary.pdf", "Gabarito preliminar\n1 - A\n2 - B",
            self.semantic_metadata("answer_key", "Gabarito preliminar 2026"),
        )])[0]
        self.assertEqual(self.stored_question(str(exam["id"]), 2)["correct_answer"], "B")

        definitive = self.process_text_documents([(
            "key-definitive.pdf", "Gabarito definitivo\n1 - A\n2 - C",
            self.semantic_metadata("answer_key", "Gabarito definitivo 2026"),
        )])[0]

        with closing(self.store._connect()) as connection:
            links = connection.execute(
                "SELECT answer_key_version_id, status FROM document_links ORDER BY created_at"
            ).fetchall()
        self.assertEqual(self.stored_question(str(exam["id"]), 2)["correct_answer"], "C")
        self.assertEqual(
            [(row["answer_key_version_id"], row["status"]) for row in links],
            [
                (preliminary["document_version_id"], "superseded"),
                (definitive["document_version_id"], "active"),
            ],
        )

    def test_definitive_annulment_is_applied_and_audited_without_erasing_absent_answers(
        self,
    ) -> None:
        exam_text = (
            "{01}\nEnunciado completo da primeira questao.\n(A) A.\n(B) B.\n#####\n"
            "{02}\nEnunciado completo da segunda questao.\n(A) A.\n(B) B.\n#####"
        )
        exam = self.process_text_documents([(
            "exam-annulment.pdf", exam_text,
            self.semantic_metadata("exam", "Prova com anulacao 2026"),
        )])[0]
        self.process_text_documents([(
            "key-before-annulment.pdf", "Gabarito preliminar\n1 - A\n2 - B",
            self.semantic_metadata("answer_key", "Gabarito preliminar com anulacao"),
        )])
        definitive = self.process_text_documents([(
            "key-annulment.pdf", "Gabarito definitivo\n1 - ANULADA",
            self.semantic_metadata("answer_key", "Gabarito definitivo com anulacao"),
        )])[0]

        first = self.stored_question(str(exam["id"]), 1)
        second = self.stored_question(str(exam["id"]), 2)
        actions = [event["action"] for event in self.store.identity_events(str(exam["id"]))]
        self.assertEqual((first["answer_status"], first["correct_answer"]), ("annulled", None))
        self.assertEqual((second["answer_status"], second["correct_answer"]), ("matched", "B"))
        self.assertIn("association_superseded", actions)
        self.assertEqual(
            self.processor._reconcile_answer_key(str(definitive["document_version_id"])),
            0,
        )

    def test_equivalent_definitive_keys_leave_exam_unlinked_and_answers_unchanged(self) -> None:
        exam = self.process_text_documents([(
            "exam-ambiguous.pdf",
            "{01}\nEnunciado completo ambiguo.\n(A) A.\n(B) B.\n#####",
            self.semantic_metadata("exam", "Prova ambigua 2026"),
        )])[0]
        self.process_text_documents([
            (
                "gabarito-a.pdf", "Gabarito definitivo\n1 - A",
                self.semantic_metadata("answer_key", "Gabarito definitivo A"),
            ),
            (
                "gabarito-b.pdf", "Gabarito definitivo\n1 - B",
                self.semantic_metadata("answer_key", "Gabarito definitivo B"),
            ),
        ])

        with closing(self.store._connect()) as connection:
            link_count = connection.execute(
                "SELECT COUNT(*) FROM document_links WHERE status = 'active'"
            ).fetchone()[0]
        self.assertEqual(link_count, 0)
        self.assertEqual(self.stored_question(str(exam["id"]), 1)["answer_status"], "missing")

    def test_repeated_definitive_key_does_not_reapply_or_duplicate_events(self) -> None:
        exam = self.process_text_documents([(
            "exam-idempotent.pdf",
            "{01}\nEnunciado completo idempotente.\n(A) A.\n(B) B.\n#####",
            self.semantic_metadata("exam", "Prova idempotente 2026"),
        )])[0]
        definitive = self.process_text_documents([(
            "gabarito-idempotent.pdf", "Gabarito definitivo\n1 - B",
            self.semantic_metadata("answer_key", "Gabarito definitivo idempotente"),
        )])[0]
        with closing(self.store._connect()) as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM document_identity_events "
                "WHERE action LIKE 'association_%'"
            ).fetchone()[0]
            links_before = connection.execute("SELECT COUNT(*) FROM document_links").fetchone()[0]

        first_repeat = self.processor._reconcile_answer_key(
            str(definitive["document_version_id"])
        )
        second_repeat = self.processor._reconcile_answer_key(
            str(definitive["document_version_id"])
        )
        republication = self.process_text_documents([(
            "gabarito-idempotent-republished.pdf", "Gabarito definitivo\n1 - B",
            self.semantic_metadata("answer_key", "Gabarito definitivo idempotente"),
        )])[0]

        with closing(self.store._connect()) as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM document_identity_events "
                "WHERE action LIKE 'association_%'"
            ).fetchone()[0]
            links_after = connection.execute("SELECT COUNT(*) FROM document_links").fetchone()[0]
        self.assertEqual((first_repeat, second_repeat), (0, 0))
        self.assertEqual(republication["semantic_resolution"], "republication")
        self.assertEqual((links_after, after), (links_before, before))
        self.assertEqual(self.stored_question(str(exam["id"]), 1)["correct_answer"], "B")

    def test_concurrent_answer_key_applications_keep_link_and_payload_consistent(self) -> None:
        exam = self.process_text_documents([(
            "exam-concurrent-keys.pdf",
            "{01}\nEnunciado completo concorrente.\n(A) A.\n(B) B.\n#####",
            self.semantic_metadata("exam", "Prova concorrente 2026"),
        )])[0]
        keys = self.process_text_documents([
            (
                "gabarito-concurrent-a.pdf", "Gabarito preliminar\n1 - A",
                self.semantic_metadata("answer_key", "Gabarito preliminar concorrente A"),
            ),
            (
                "gabarito-concurrent-b.pdf", "Gabarito definitivo\n1 - B",
                self.semantic_metadata("answer_key", "Gabarito concorrente B"),
            ),
        ])
        versions = {
            str(key["document_version_id"]): answer
            for key, answer in zip(keys, ("A", "B"), strict=True)
        }
        with closing(self.store._connect()) as connection:
            connection.execute("DELETE FROM document_links")
            connection.commit()

        def apply(version_id: str, answer: str) -> bool:
            decision = DocumentAssociationDecision(
                outcome="selected", selected_version_id=version_id,
                assessments=(), minimum_score=36, minimum_margin=8,
                achieved_margin=None, reason="concurrent test",
                algorithm_version="semantic-association-v1",
            )
            return self.store.apply_answer_key_updates(
                str(exam["id"]), str(exam["document_version_id"]), version_id,
                decision, {1: ("matched", answer)},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda item: apply(*item), versions.items()))

        with closing(self.store._connect()) as connection:
            active = connection.execute(
                "SELECT answer_key_version_id FROM document_links WHERE status = 'active'"
            ).fetchone()[0]
        self.assertEqual(results, [False, True])
        self.assertEqual(
            self.stored_question(str(exam["id"]), 1)["correct_answer"], versions[active]
        )

    def test_multirole_multitype_key_updates_only_covered_exam_grid(self) -> None:
        def exam_text(label: str) -> str:
            return (
                "Turno: Manha\n"
                f"{{01}}\nEnunciado completo {label} um.\n(A) A.\n(B) B.\n(C) C.\n(D) D.\n#####\n"
                f"{{02}}\nEnunciado completo {label} dois.\n(A) A.\n(B) B.\n(C) C.\n(D) D.\n#####"
            )

        exams = self.process_text_documents([
            (
                "tecnico-tipo-1.pdf", exam_text("tecnico"),
                self.semantic_metadata(
                    "exam", "Prova Tecnico Tipo 1", role="Tecnico", variant="Tipo 1"
                ),
            ),
            (
                "analista-tipo-2.pdf", exam_text("analista"),
                self.semantic_metadata(
                    "exam", "Prova Analista Tipo 2", role="Analista", variant="Tipo 2"
                ),
            ),
            (
                "auditor-tipo-1.pdf", exam_text("auditor"),
                self.semantic_metadata(
                    "exam", "Prova Auditor Tipo 1", role="Auditor", variant="Tipo 1"
                ),
            ),
        ])
        key_metadata = self.semantic_metadata(
            "answer_key", "Gabarito definitivo multicargo"
        ).model_copy(update={"role": None, "variant": None})
        key = self.process_text_documents([(
            "gabarito-multicargo.pdf",
            """Gabarito definitivo
Cargos: Tecnico, Analista
Turnos: Manha
Tipos: 1 a 2
Tecnico - Tipo 1 (Manha)
1 2
A B
Analista - Tipo 2 (Manha)
1 2
C D""",
            key_metadata,
        )])[0]
        by_name = {str(document["filename"]): document for document in exams}
        affected_ids = {
            row["id"]
            for row in self.store.exams_affected_by_answer_key(
                str(key["document_version_id"])
            )
        }

        self.assertEqual(
            affected_ids,
            {
                by_name["tecnico-tipo-1.pdf"]["id"],
                by_name["analista-tipo-2.pdf"]["id"],
            },
        )
        self.assertEqual(
            [
                self.stored_question(str(by_name["tecnico-tipo-1.pdf"]["id"]), number)[
                    "correct_answer"
                ]
                for number in (1, 2)
            ],
            ["A", "B"],
        )
        self.assertEqual(
            [
                self.stored_question(str(by_name["analista-tipo-2.pdf"]["id"]), number)[
                    "correct_answer"
                ]
                for number in (1, 2)
            ],
            ["C", "D"],
        )
        self.assertEqual(
            self.stored_question(str(by_name["auditor-tipo-1.pdf"]["id"]), 1)[
                "answer_status"
            ],
            "missing",
        )

    def test_collection_rejects_host_outside_selected_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "host nao permitido"):
            self.manager.start(
                {
                    "sourceId": "fgv_conhecimento",
                    "url": "https://example.com/prova.pdf",
                }
            )

    def test_fgv_requires_a_specific_contest_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "pagina especifica"):
            self.manager.start(
                {
                    "sourceId": "fgv_conhecimento",
                    "url": "https://conhecimento.fgv.br/concursos",
                }
            )

    def test_high_performance_preserves_the_file_limit(self) -> None:
        captured: list[object] = []

        def collect(config: object, **_kwargs: object) -> tuple[DownloadManifest, Path]:
            captured.append(config)
            path = self.root / "manifest.json"
            path.write_text("{}", encoding="utf-8")
            return DownloadManifest(created_at=datetime.now(UTC)), path

        with patch("kad_collector.desktop_collection.collect_documents", side_effect=collect):
            collection_id = self.manager.start(
                {
                    "sourceId": "fgv_conhecimento",
                    "url": "https://conhecimento.fgv.br/concursos/rfb22",
                    "capacityProfile": "high_performance",
                }
            )
            for _ in range(100):
                job = next(item for item in self.manager.list_jobs() if item["id"] == collection_id)
                if job["status"] not in {"queued", "running", "processing"}:
                    break
                threading.Event().wait(0.01)

        self.assertTrue(captured)
        config = captured[0]
        self.assertEqual(config.collector.max_files_per_source, 40)  # type: ignore[attr-defined]
        self.assertEqual(config.collector.max_concurrency, 8)  # type: ignore[attr-defined]

    def test_fgv_metadata_groups_contest_role_and_variant(self) -> None:
        source = self.manager._source("fgv_conhecimento")
        pdf_path = self.root / "cuidador.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
        document = DocumentRecord(
            source_id=source.id,
            source_name=source.name,
            document_type="exam",
            title="Tipo 2",
            original_url=(
                "https://conhecimento.fgv.br/sites/default/files/concursos/"
                "cuidadorcnm001_tipo_2.pdf"
            ),
            resolved_url=(
                "https://conhecimento.fgv.br/sites/default/files/concursos/"
                "cuidadorcnm001_tipo_2.pdf"
            ),
            local_path=str(pdf_path),
            sha256="b" * 64,
            content_type="application/pdf",
            size_bytes=pdf_path.stat().st_size,
            downloaded_at=datetime.now(UTC),
            authorization_basis="Fonte oficial.",
            metadata={"banca": "FGV"},
        )

        metadata = _import_metadata(
            source, "https://conhecimento.fgv.br/concursos/seadap2022", document
        )

        self.assertEqual(metadata.concurso, "SEADAP2022")
        self.assertEqual(metadata.role, "Cuidador")
        self.assertEqual(metadata.variant, "Tipo 2")

    def test_large_contest_batches_keep_the_answer_key_with_every_exam_group(self) -> None:
        source = self.manager._source("fgv_conhecimento")
        documents: list[DocumentRecord] = []
        metadata_by_path: dict[str, DesktopImportMetadata] = {}
        for number in range(25):
            path = self.root / f"analista_tipo_{number % 4 + 1}_{number}.pdf"
            path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
            document = DocumentRecord(
                source_id=source.id,
                source_name=source.name,
                document_type="exam",
                title=f"Analista - Tipo {number % 4 + 1}",
                original_url=f"https://conhecimento.fgv.br/concursos/exam-{number}.pdf",
                resolved_url=f"https://conhecimento.fgv.br/concursos/exam-{number}.pdf",
                local_path=str(path),
                sha256=f"{number + 1:064x}",
                content_type="application/pdf",
                size_bytes=path.stat().st_size,
                downloaded_at=datetime.now(UTC),
                authorization_basis="Fonte oficial.",
                metadata={"banca": "FGV", "cargo": "Analista"},
            )
            documents.append(document)
            metadata_by_path[str(path.resolve()).casefold()] = _import_metadata(
                source, "https://conhecimento.fgv.br/concursos/teste2026", document
            )
        key_path = self.root / "gabarito-definitivo.pdf"
        key_path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
        answer_key = DocumentRecord(
            source_id=source.id,
            source_name=source.name,
            document_type="answer_key",
            title="Gabarito definitivo",
            original_url="https://conhecimento.fgv.br/concursos/gabarito.pdf",
            resolved_url="https://conhecimento.fgv.br/concursos/gabarito.pdf",
            local_path=str(key_path),
            sha256="f" * 64,
            content_type="application/pdf",
            size_bytes=key_path.stat().st_size,
            downloaded_at=datetime.now(UTC),
            authorization_basis="Fonte oficial.",
            metadata={"banca": "FGV"},
        )
        documents.append(answer_key)
        metadata_by_path[str(key_path.resolve()).casefold()] = _import_metadata(
            source, "https://conhecimento.fgv.br/concursos/teste2026", answer_key
        )

        from kad_collector.document_contract import normalize_collected_document
        from kad_collector.document_pipeline import processing_batches

        normalized_documents = [normalize_collected_document(document) for document in documents]
        batches = processing_batches(normalized_documents)

        self.assertEqual(len(batches), 2)
        self.assertTrue(
            all(
                str(key_path.resolve()) in {document.local_path for document in batch}
                for batch in batches
            )
        )
        self.assertTrue(all(len(batch) <= 20 for batch in batches))

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
