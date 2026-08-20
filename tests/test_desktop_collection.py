from __future__ import annotations

import threading
import unittest
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
                    "processingStatus": "new",
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
        self.assertEqual(
            imported["metadata"]["canonical_url"],
            "https://conhecimento.fgv.br/prova.pdf",
        )

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

    def test_only_a_sole_in_batch_answer_key_gets_compatibility_shortcut(self) -> None:
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

        self.assertIs(_select_answer_key(exam, [in_batch]), in_batch)
        self.assertIsNone(_select_answer_key(exam, [cached]))

    def test_same_job_key_with_known_year_or_variant_conflict_keeps_answer_missing(self) -> None:
        exam_text = (
            "{01}\nEnunciado completo de Direito.\n"
            "(A) Alternativa errada.\n(B) Alternativa correta.\n#####"
        )
        for label, answer_update in (
            ("wrong-year", {"year": 2025, "variant": "V1"}),
            ("wrong-variant", {"year": 2026, "variant": "V2"}),
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
        key_job = self.store.create_job([key_path], base, "local", metadata_by_path={
            str(key_path.resolve()).casefold(): key_metadata
        })
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
        exam_job = self.store.create_job([exam_path], base, "local", metadata_by_path={
            str(exam_path.resolve()).casefold(): exam_metadata
        })
        exam_document = self.store.documents_for_job(exam_job)[0]
        self.store.save_page(
            exam_document["id"],
            1,
            "QUESTAO 1\nEnunciado completo.\nA) Errada.\nB) Errada.\nC) Correta.",
            status="text",
        )
        self.store.update_document(exam_document["id"], status="extracted", page_count=1)

        self.processor._structure_job(exam_job, threading.Event())

        question = self.store.query(DesktopFilterSet())["questions"][0]
        self.assertEqual(question["question"]["correct_answer"], "C")
        self.assertEqual(question["status"], "pending")
        self.assertEqual(len(self.store.documents_for_job(exam_job)), 1)

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
                job = next(
                    item for item in self.manager.list_jobs() if item["id"] == collection_id
                )
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
