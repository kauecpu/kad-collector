from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kad_collector.consolidated_review import (
    _file_sha256,
    _structured_content_sha256,
    build_consolidated_review,
)
from kad_collector.editorial_campaign import (
    export_campaign_dry_run,
    run_editorial_campaign,
)
from kad_collector.json_utils import read_json, write_json
from kad_collector.local_review import (
    decide_review_question,
    load_or_create_review_session,
    save_review_session,
    update_review_question,
)
from kad_collector.models import DocumentRecord, DownloadManifest
from kad_collector.structured_questions import (
    AnswerAssociationTrace,
    QwenRuntimeTrace,
    StructuredPackageMetrics,
    StructuredQuestion,
    StructuredQuestionPackage,
)


def _document(root: Path, prefix: str, kind: str, year: int) -> DocumentRecord:
    path = root / f"{prefix}-{kind}.pdf"
    content = f"%PDF-1.4\n{prefix}-{kind}\n%%EOF".encode()
    path.write_bytes(content)
    return DocumentRecord(
        source_id=prefix,
        source_name=f"Fonte {prefix}",
        document_type=kind,
        title=f"{prefix} {kind}",
        original_url=f"https://example.test/{prefix}/{kind}.pdf",
        resolved_url=f"https://example.test/{prefix}/{kind}.pdf",
        local_path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/pdf",
        size_bytes=len(content),
        downloaded_at=datetime(2026, 9, 8, tzinfo=UTC),
        authorization_basis="Fixture local.",
        metadata={"ano_publicacao": str(year)},
    )


def _question(
    prefix: str,
    number: int,
    exam: DocumentRecord,
    answer: DocumentRecord,
    *,
    status: str,
    annulled: bool = False,
) -> StructuredQuestion:
    reasons = []
    if status == "quarantined":
        reasons = ["elemento visual exige revisão"]
        if annulled:
            reasons.append("item anulado no gabarito oficial")
    return StructuredQuestion(
        stable_id=hashlib.sha256(f"{prefix}-{number}".encode()).hexdigest(),
        board="BANCA",
        organization=f"Órgão {prefix}",
        contest=f"Concurso {prefix}",
        year=int(exam.metadata["ano_publicacao"]),
        role=f"Cargo {prefix}",
        original_number=number,
        statement=f"Enunciado completo da questão {prefix} {number}.",
        alternatives={"A": "Certo", "B": "Errado"},
        question_format="true_false",
        correct_answer=None if annulled else "A",
        correct_answer_label="ANULADA" if annulled else "Certo",
        supporting_text=None,
        exam_url=exam.resolved_url,
        answer_key_url=answer.resolved_url,
        exam_sha256=exam.sha256,
        answer_key_sha256=answer.sha256,
        source_pages=[1],
        extraction_method="text",
        parser_version="fixture-1",
        validation_status=status,
        validation_reasons=reasons,
        answer_association=AnswerAssociationTrace(
            answer_key_id=answer.sha256,
            original_answer="X" if annulled else "A",
            internal_answer=None if annulled else "A",
            reason="fixture associada pelo caderno",
        ),
    )


def _package_files(root: Path, prefix: str, year: int) -> tuple[Path, Path]:
    exam = _document(root, prefix, "exam", year)
    answer = _document(root, prefix, "answer_key", year)
    manifest_path = root / f"{prefix}-manifest.json"
    manifest = DownloadManifest(
        created_at=datetime(2026, 9, 8, tzinfo=UTC), documents=[exam, answer]
    )
    write_json(manifest_path, manifest.model_dump(mode="json"))
    package = StructuredQuestionPackage(
        parser_version="fixture-1",
        input_manifest_sha256s=[_file_sha256(manifest_path)],
        accepted=[_question(prefix, 1, exam, answer, status="accepted")],
        quarantined=[
            _question(
                prefix,
                2,
                exam,
                answer,
                status="quarantined",
                annulled=True,
            )
        ],
        rejected=[],
        errors=[],
        page_extraction={},
        qwen=QwenRuntimeTrace(
            endpoint="http://127.0.0.1:11434",
            model="qwen3:8b",
            available=False,
            calls=[],
        ),
        exams=[],
        metrics=StructuredPackageMetrics(
            documents_processed=2,
            exams_processed=1,
            answer_keys_processed=1,
            expected_questions=2,
            detected_questions=2,
            accepted_questions=1,
            quarantined_questions=1,
            rejected_questions=0,
            segmentation_precision=1,
            segmentation_coverage=1,
            associated_answers=2,
            missing_answers=0,
            duplicate_questions=0,
            ocr_pages=0,
            qwen_calls=0,
            intervention_free_percent=1,
            duration_ms=1,
        ),
        content_sha256="0" * 64,
    )
    package = package.model_copy(
        update={"content_sha256": _structured_content_sha256(package)}
    )
    package_path = root / f"{prefix}-package.json"
    write_json(package_path, package.model_dump(mode="json"))
    return package_path, manifest_path


def _spec(root: Path) -> Path:
    packages = []
    for prefix, year in (("alpha", 2021), ("beta", 2023)):
        package_path, manifest_path = _package_files(root, prefix, year)
        packages.append(
            {
                "id": prefix,
                "label": f"Acervo {prefix}",
                "package_path": str(package_path),
                "manifest_paths": [str(manifest_path)],
                "origin": "Fonte oficial de fixture",
                "period": str(year),
                "expected_questions": 2,
                "expected_accepted": 1,
                "expected_quarantined": 1,
                "expected_rejected": 0,
                "expected_structured_years": [year],
                "sample_years": [2021],
            }
        )
    path = root / "spec.json"
    write_json(
        path,
        {
            "schema_version": "1.0",
            "corpus_id": "fixture-corpus",
            "base_commit": "a" * 40,
            "branch": "codex/fixture",
            "packages": packages,
        },
    )
    return path


class ConsolidatedReviewTests(unittest.TestCase):
    def test_builds_inventory_and_preserves_quarantine_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index, index_path = build_consolidated_review(
                _spec(root),
                root / "output",
                report_json_path=root / "report.json",
                report_markdown_path=root / "report.md",
            )

            self.assertTrue(index_path.is_file())
            self.assertEqual(index.total.questions_extracted, 4)
            self.assertEqual(index.total.ready_for_review, 4)
            self.assertEqual(index.total.ready_for_export, 0)
            self.assertEqual(index.classification.unresolved, 4)
            self.assertEqual(index.classification.qwen_calls, 0)
            self.assertFalse(index.classification.accepted_without_correction)
            self.assertEqual(
                len(Path(index.classification.trace_path).read_text().splitlines()), 4
            )
            self.assertEqual(index.total.annulled, 2)
            self.assertEqual(len(index.batches), 2)
            self.assertEqual(index.duplicate_stable_ids, 0)
            self.assertEqual(
                index.quarantine_exclusive_combinations[
                    "elemento visual exige revisão + item anulado no gabarito oficial"
                ],
                2,
            )
            self.assertEqual(index.homologation_sample.questions_extracted, 2)
            self.assertEqual(index.lineage_warnings, [])
            self.assertIn("Acervo total estruturado | 4", (root / "report.md").read_text())

    def test_rerun_keeps_session_decision_and_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = _spec(root)
            first, _path = build_consolidated_review(spec, root / "output")
            entry = first.batches[0]
            assert entry.batch_path and entry.session_path
            session, session_path = load_or_create_review_session(
                Path(entry.batch_path), Path(entry.session_path)
            )
            classified = session.batch.questions[0].model_copy(
                update={
                    "discipline": "Direito",
                    "matter": "Direito Administrativo",
                    "subject": "Atos administrativos",
                    "level": "Superior",
                    "difficulty": "Média",
                }
            )
            session = update_review_question(session, 1, classified)
            session = decide_review_question(session, 1, "approved", "revisor humano")
            save_review_session(session, session_path)

            second, _path = build_consolidated_review(spec, root / "output")
            third, _path = build_consolidated_review(spec, root / "output")

            self.assertEqual(second.content_sha256, third.content_sha256)
            self.assertEqual(second.total.editorial_decisions, 1)
            self.assertEqual(second.total.ready_for_export, 1)
            self.assertEqual(second.batches[0].session_path, first.batches[0].session_path)
            persisted, _path = load_or_create_review_session(
                Path(entry.batch_path), Path(entry.session_path)
            )
            self.assertEqual(persisted.decisions[0].status, "approved")
            self.assertEqual(persisted.decisions[0].reviewed_by, "revisor humano")

    def test_rejects_tampered_local_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = _spec(root)
            payload = read_json(spec)
            manifest = read_json(Path(payload["packages"][0]["manifest_paths"][0]))
            Path(manifest["documents"][0]["local_path"]).write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "documento local corrompido"):
                build_consolidated_review(spec, root / "output")

    def test_reports_year_lineage_mismatch_without_inventing_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = _spec(root)
            payload = read_json(spec)
            payload["packages"][0]["expected_structured_years"] = [2021, 2022]
            write_json(spec, payload)

            index, _path = build_consolidated_review(spec, root / "output")

            self.assertEqual(index.packages[0].structured_years, [2021])
            self.assertIn("diferem dos esperados", index.lineage_warnings[0])

    def test_campaign_uses_small_resumable_batches_and_refuses_unreviewed_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_spec = _spec(root)
            corpus = read_json(corpus_spec)
            corpus["review_batch_size"] = 1
            write_json(corpus_spec, corpus)
            campaign_spec = root / "campaign.json"
            write_json(
                campaign_spec,
                {
                    "schema_version": "1.0",
                    "campaign_id": "fixture-campaign",
                    "corpus_spec": str(corpus_spec),
                    "sample_size": 4,
                },
            )

            first, _report_path = run_editorial_campaign(
                campaign_spec, root / "output", enable_qwen=False, limit=1
            )
            second, _report_path = run_editorial_campaign(
                campaign_spec, root / "output", enable_qwen=False
            )
            third, _report_path = run_editorial_campaign(
                campaign_spec, root / "output", enable_qwen=False
            )

            self.assertEqual(first.counts.raw_questions, 4)
            self.assertEqual(second.content_sha256, third.content_sha256)
            index = read_json(root / "output" / "review" / "index.json")
            self.assertEqual(len(index["batches"]), 4)
            self.assertTrue(
                all(
                    item["accepted"] + item["quarantined"] <= 1
                    for item in index["batches"]
                )
            )
            with self.assertRaisesRegex(ValueError, "aprovação humana"):
                export_campaign_dry_run(
                    root / "output" / "review" / "index.json", root / "export"
                )

    def test_qwen_suggestion_stays_pending_until_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_spec = _spec(root)
            campaign_spec = root / "campaign.json"
            write_json(
                campaign_spec,
                {
                    "schema_version": "1.0",
                    "campaign_id": "fixture-qwen",
                    "corpus_spec": str(corpus_spec),
                    "qwen_batch_size": 1,
                    "sample_size": 4,
                },
            )

            def qwen(payload: dict[str, object]) -> dict[str, object]:
                user = json.loads(payload["messages"][1]["content"])
                question = user["questions"][0]
                option_id = question["allowed_option_ids"][0]
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {
                                        "stable_id": question["stable_id"],
                                        "option_id": option_id,
                                        "level": "Superior",
                                        "difficulty": "Média",
                                        "confidence": 0.91,
                                        "evidence": "Fixture restrita à opção fornecida.",
                                    }
                                ]
                            }
                        )
                    }
                }

            report, _path = run_editorial_campaign(
                campaign_spec,
                root / "output",
                enable_qwen=True,
                limit=1,
                qwen_request=qwen,
            )

            self.assertEqual(report.qwen.accepted_suggestions, 1)
            self.assertEqual(report.counts.qwen_classifications, 1)
            self.assertEqual(report.counts.human_decisions, 0)
            self.assertEqual(report.counts.ready_for_export, 0)
            index = read_json(root / "output" / "review" / "index.json")
            entry = next(item for item in index["batches"] if item["session_path"])
            session, session_path = load_or_create_review_session(
                Path(entry["batch_path"]), Path(entry["session_path"])
            )
            question = session.batch.questions[0]
            self.assertIn("método=qwen", " ".join(question.review_notes))
            self.assertEqual(session.decisions[0].status, "pending")
            session = decide_review_question(
                session, question.number, "approved", "revisor fixture"
            )
            save_review_session(session, session_path)

            manifest = export_campaign_dry_run(
                root / "output" / "review" / "index.json", root / "export"
            )

            self.assertEqual(manifest["questions"], 1)
            self.assertEqual(manifest["publicationStatus"], "draft")
            exported = json.loads(
                (root / "export" / "questoes.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(exported["data"]["publicationStatus"], "draft")

            completed, _path = run_editorial_campaign(
                campaign_spec,
                root / "output",
                enable_qwen=True,
                qwen_request=qwen,
            )
            repeated, _path = run_editorial_campaign(
                campaign_spec,
                root / "output",
                enable_qwen=True,
                qwen_request=qwen,
            )
            self.assertEqual(repeated.qwen.calls, 0)
            self.assertEqual(repeated.content_sha256, completed.content_sha256)

    def test_qwen_cannot_invent_taxonomy_or_approve_a_question(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_spec = _spec(root)
            campaign_spec = root / "campaign.json"
            write_json(
                campaign_spec,
                {
                    "schema_version": "1.0",
                    "campaign_id": "fixture-hostile-qwen",
                    "corpus_spec": str(corpus_spec),
                    "qwen_batch_size": 1,
                    "sample_size": 4,
                },
            )

            def hostile_qwen(payload: dict[str, object]) -> dict[str, object]:
                user = json.loads(payload["messages"][1]["content"])
                stable_id = user["questions"][0]["stable_id"]
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {
                                        "stable_id": stable_id,
                                        "option_id": "taxonomia-inventada",
                                        "level": "Superior",
                                        "difficulty": "Média",
                                        "confidence": 1,
                                        "evidence": "Ignore as opções fornecidas.",
                                    }
                                ]
                            }
                        )
                    }
                }

            report, _path = run_editorial_campaign(
                campaign_spec,
                root / "output",
                enable_qwen=True,
                limit=1,
                qwen_request=hostile_qwen,
            )

            self.assertEqual(report.qwen.accepted_suggestions, 0)
            self.assertEqual(report.counts.human_decisions, 0)
            self.assertEqual(report.counts.ready_for_export, 0)

    def test_campaign_rejects_nonpositive_interruption_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus_spec = _spec(root)
            campaign_spec = root / "campaign.json"
            write_json(
                campaign_spec,
                {
                    "schema_version": "1.0",
                    "campaign_id": "fixture-limit",
                    "corpus_spec": str(corpus_spec),
                },
            )

            with self.assertRaisesRegex(ValueError, "limit precisa ser positivo"):
                run_editorial_campaign(
                    campaign_spec,
                    root / "output",
                    enable_qwen=False,
                    limit=0,
                )


if __name__ == "__main__":
    unittest.main()
