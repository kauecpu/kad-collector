from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kad_collector.cli import _finish_operator_review
from kad_collector.editorial_export import EditorialImportRecordV2
from kad_collector.json_utils import read_json, write_json
from kad_collector.local_review import (
    decide_review_question,
    load_or_create_review_session,
    question_content_sha256,
    save_review_session,
    update_review_question,
)
from kad_collector.models import DocumentRecord, DownloadManifest, QuestionBatch
from kad_collector.operator_review import (
    _question_record,
    build_operator_review_index,
    first_reviewable_batch,
)
from kad_collector.operator_run import OperatorParameters, OperatorRunState
from kad_collector.review_server import ReviewApplication, serve_review_application
from kad_collector.structured_questions import (
    AnswerAssociationTrace,
    QwenRuntimeTrace,
    StructuredPackageMetrics,
    StructuredQuestion,
    StructuredQuestionPackage,
)
from kad_collector.validation import batch_content_sha256


def _document(root: Path, name: str, document_type: str) -> DocumentRecord:
    path = root / f"{name}.pdf"
    content = f"%PDF-1.4\n{name}\n%%EOF".encode()
    path.write_bytes(content)
    return DocumentRecord(
        source_id="cebraspe_pf",
        source_name="Cebraspe - Polícia Federal",
        document_type=document_type,
        title=name,
        original_url=f"https://example.test/{name}.pdf",
        resolved_url=f"https://example.test/{name}.pdf",
        local_path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/pdf",
        size_bytes=len(content),
        downloaded_at=datetime(2026, 9, 8, tzinfo=UTC),
        authorization_basis="Fixture pública.",
        metadata={"banca": "CEBRASPE", "orgao": "Polícia Federal", "ano": "2021"},
    )


def _structured(
    number: int,
    exam: DocumentRecord,
    answer_key: DocumentRecord,
    *,
    status: str,
) -> StructuredQuestion:
    return StructuredQuestion(
        stable_id=hashlib.sha256(f"question-{number}".encode()).hexdigest(),
        board="CEBRASPE",
        organization="Polícia Federal",
        contest="Polícia Federal 2021",
        year=2021,
        role="Agente",
        area="Direito",
        block="Direito Administrativo",
        original_number=number,
        statement=f"Enunciado suficientemente completo da questão número {number}.",
        alternatives={"A": "Certo", "B": "Errado"},
        question_format="true_false",
        correct_answer="A",
        correct_answer_label="C",
        supporting_text=None,
        exam_url=exam.resolved_url,
        answer_key_url=answer_key.resolved_url,
        exam_sha256=exam.sha256,
        answer_key_sha256=answer_key.sha256,
        source_pages=[number],
        extraction_method="text",
        parser_version="fixture-1",
        validation_status=status,
        validation_reasons=(
            ["conteúdo visual exige conferência"] if status != "accepted" else []
        ),
        answer_association=AnswerAssociationTrace(
            answer_key_id=answer_key.sha256,
            original_answer="A",
            internal_answer="A",
            reason="gabarito oficial associado pelo identificador do caderno",
        ),
    )


def _write_operator_artifacts(root: Path, *, rejected_only: bool = False) -> Path:
    output = root / "run"
    output.mkdir()
    exam = _document(root, "prova", "exam")
    answer_key = _document(root, "gabarito", "answer_key")
    created_at = datetime(2026, 9, 8, tzinfo=UTC)
    accepted = [] if rejected_only else [_structured(1, exam, answer_key, status="accepted")]
    quarantined = (
        [] if rejected_only else [_structured(2, exam, answer_key, status="quarantined")]
    )
    rejected = [_structured(3, exam, answer_key, status="rejected")]
    package = StructuredQuestionPackage(
        parser_version="fixture-1",
        input_manifest_sha256s=["a" * 64],
        accepted=accepted,
        quarantined=quarantined,
        rejected=rejected,
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
            expected_questions=3,
            detected_questions=3,
            accepted_questions=len(accepted),
            quarantined_questions=len(quarantined),
            rejected_questions=1,
            segmentation_precision=1,
            segmentation_coverage=1,
            associated_answers=3,
            missing_answers=0,
            duplicate_questions=0,
            ocr_pages=0,
            qwen_calls=0,
            intervention_free_percent=1,
            duration_ms=1,
        ),
        content_sha256="b" * 64,
    )
    state = OperatorRunState(
        run_id="operator-run-fixture",
        parameters=OperatorParameters(
            organization="Polícia Federal",
            board="CEBRASPE",
            start_year=2021,
            end_year=2021,
        ),
        status="completed",
        created_at=created_at,
        updated_at=created_at,
        completed_stages=["discovery", "download", "extraction", "structuring"],
        semantic_sha256=package.content_sha256,
    )
    manifest = DownloadManifest(
        created_at=created_at,
        documents=[exam, answer_key],
        filters=state.parameters.filters(),
    )
    write_json(output / "run.json", state.model_dump(mode="json"))
    write_json(output / "manifest.json", manifest.model_dump(mode="json"))
    write_json(output / "review-package.json", package.model_dump(mode="json"))
    return output


class OperatorReviewBridgeTests(unittest.TestCase):
    def test_new_optional_fields_do_not_change_legacy_integrity_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary))
            index, _ = build_operator_review_index(output)
            entry = first_reviewable_batch(index)
            assert entry is not None and entry.batch_path
            batch_payload = read_json(Path(entry.batch_path))
            for question in batch_payload["questions"]:
                question.pop("source_stable_id")
                question.pop("editorial_blocks")
            legacy_question = batch_payload["questions"][0]
            expected_question = hashlib.sha256(
                json.dumps(
                    legacy_question,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            parsed_question = QuestionBatch.model_validate(batch_payload).questions[0]
            self.assertEqual(question_content_sha256(parsed_question), expected_question)

            content = {
                "batch_id": batch_payload["batch_id"],
                "model": batch_payload["model"],
                "source_document": batch_payload["source_document"],
                "answer_key_document": batch_payload["answer_key_document"],
                "filters": batch_payload["filters"],
                "filtered_out_questions": batch_payload["filtered_out_questions"],
                "questions": batch_payload["questions"],
            }
            expected_batch = hashlib.sha256(
                json.dumps(
                    content,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(
                batch_content_sha256(QuestionBatch.model_validate(batch_payload)),
                expected_batch,
            )

    def test_preserves_annulled_answer_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exam = _document(root, "prova", "exam")
            answer_key = _document(root, "gabarito", "answer_key")
            structured = _structured(1, exam, answer_key, status="quarantined")
            structured = structured.model_copy(
                update={
                    "correct_answer": None,
                    "correct_answer_label": "ANULADA",
                    "answer_association": structured.answer_association.model_copy(
                        update={"original_answer": "X", "internal_answer": None}
                    ),
                }
            )
            record = _question_record(structured, state="quarantined")
            self.assertEqual(record.answer_status, "annulled")
            self.assertIsNone(record.correct_answer)

    def test_preserves_supporting_text_in_exportable_statement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exam = _document(root, "prova", "exam")
            answer_key = _document(root, "gabarito", "answer_key")
            structured = _structured(1, exam, answer_key, status="accepted").model_copy(
                update={"supporting_text": "Texto de apoio oficial da questão."}
            )

            record = _question_record(structured, state="accepted")

            self.assertTrue(record.statement.startswith("Texto de apoio oficial da questão."))
            self.assertIn(structured.statement, record.statement)

    def test_builds_pending_batches_and_preserves_quarantine_and_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary))
            first, first_path = build_operator_review_index(output)
            second, second_path = build_operator_review_index(output)

            self.assertEqual(first.content_sha256, second.content_sha256)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first.pending_questions, 2)
            self.assertEqual(first.quarantined_questions, 1)
            self.assertEqual(first.rejected_questions, 1)
            entry = first_reviewable_batch(first)
            assert entry is not None and entry.batch_path and entry.session_path
            session, _ = load_or_create_review_session(
                Path(entry.batch_path), Path(entry.session_path)
            )
            self.assertEqual([item.status for item in session.decisions], ["pending", "pending"])
            self.assertIn(
                "Estado estrutural: quarantined.",
                session.batch.questions[1].review_notes,
            )
            self.assertEqual(
                session.batch.questions[1].editorial_blocks,
                ["conteúdo visual exige conferência"],
            )
            run = read_json(output / "run.json")
            self.assertEqual(run["artifacts"]["operator_review_index"], str(first_path))
            exceptions = read_json(Path(entry.exceptions_path))
            self.assertEqual(exceptions[0]["stableId"], hashlib.sha256(b"question-3").hexdigest())
            self.assertIn("conteúdo visual exige conferência", exceptions[0]["issues"])
            self.assertEqual(
                session.batch.source_document.metadata["operator_run_id"],
                "operator-run-fixture",
            )

    def test_rejected_only_package_creates_exceptions_without_empty_review_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary), rejected_only=True)
            index, _ = build_operator_review_index(output)
            self.assertIsNone(first_reviewable_batch(index))
            self.assertEqual(index.pending_questions, 0)
            self.assertEqual(index.rejected_questions, 1)
            self.assertIsNone(index.batches[0].batch_path)
            self.assertTrue(Path(index.batches[0].exceptions_path).is_file())

    def test_review_exports_only_human_approved_question_and_all_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary))
            index, _ = build_operator_review_index(output)
            entry = first_reviewable_batch(index)
            assert entry is not None and entry.batch_path and entry.session_path
            session, _ = load_or_create_review_session(
                Path(entry.batch_path), Path(entry.session_path)
            )
            edited = session.batch.questions[0].model_copy(
                update={
                    "discipline": "Direito",
                    "matter": "Direito Administrativo",
                    "subject": "Atos administrativos",
                    "level": "Superior",
                    "difficulty": "Média",
                }
            )
            session = update_review_question(session, 1, edited)
            session = decide_review_question(
                session, 1, "approved", "operador_local", notes="Conferida no PDF."
            )
            session = decide_review_question(
                session,
                2,
                "rejected",
                "operador_local",
                notes="Conteúdo visual incompleto.",
            )
            save_review_session(session, Path(entry.session_path))

            application = ReviewApplication(
                Path(entry.batch_path),
                session_path=Path(entry.session_path),
                admin_output_root=output / "exports",
                additional_exceptions_path=Path(entry.exceptions_path),
            )
            _approved_path, result = application.export("operador_local", None)

            self.assertEqual(result.exported_count, 1)
            self.assertEqual(result.exception_count, 2)
            lines = result.questions_path.read_text(encoding="utf-8").splitlines()
            record = EditorialImportRecordV2.model_validate(json.loads(lines[0]))
            self.assertEqual(record.data.publication_status, "draft")
            self.assertEqual(
                record.data.id, f"q-{hashlib.sha256(b'question-1').hexdigest()}"
            )
            self.assertEqual(record.data.concurso, "Polícia Federal 2021")
            self.assertEqual(
                len(result.exceptions_path.read_text(encoding="utf-8").splitlines()), 2
            )

    def test_batch_approval_skips_quarantine_until_block_is_explicitly_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary))
            index, _ = build_operator_review_index(output)
            entry = first_reviewable_batch(index)
            assert entry is not None and entry.batch_path and entry.session_path
            session, _ = load_or_create_review_session(
                Path(entry.batch_path), Path(entry.session_path)
            )
            completed = []
            for question in session.batch.questions:
                completed.append(
                    question.model_copy(
                        update={
                            "discipline": "Direito",
                            "matter": "Direito Administrativo",
                            "subject": "Atos administrativos",
                            "level": "Superior",
                            "difficulty": "Média",
                        }
                    )
                )
            for question in completed:
                session = update_review_question(session, question.number, question)
            save_review_session(session, Path(entry.session_path))
            application = ReviewApplication(
                Path(entry.batch_path), session_path=Path(entry.session_path)
            )

            approved = application.approve_ready("operador_local", None)

            self.assertEqual(approved, 1)
            decisions = {item.question_number: item for item in application.session.decisions}
            self.assertEqual(decisions[1].status, "approved")
            self.assertEqual(decisions[2].status, "pending")
            self.assertTrue(application.session.batch.questions[1].editorial_blocks)

            resolved = application.session.batch.questions[1].model_copy(
                update={"editorial_blocks": []}
            )
            application.update_question(2, resolved)
            self.assertEqual(application.approve_ready("operador_local", None), 1)
            self.assertEqual(application.session.decisions[1].status, "approved")

    def test_defer_records_actor_time_and_integrity_without_approving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary))
            index, _ = build_operator_review_index(output)
            entry = first_reviewable_batch(index)
            assert entry is not None and entry.batch_path and entry.session_path
            application = ReviewApplication(
                Path(entry.batch_path), session_path=Path(entry.session_path)
            )

            application.defer(1, "operador_local", "Conferir diagrama depois.")

            decision = application.session.decisions[0]
            self.assertEqual(decision.status, "deferred")
            self.assertEqual(decision.reviewed_by, "operador_local")
            self.assertIsNotNone(decision.reviewed_at)
            self.assertIsNotNone(decision.content_sha256)
            self.assertEqual(decision.notes, "Conferir diagrama depois.")

    def test_open_review_uses_existing_server_and_bridge_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary))
            args = SimpleNamespace(open_review=True, interactive=False, review_port=0)
            with patch("kad_collector.cli.serve_review_application") as serve:
                _finish_operator_review(args, output)
            kwargs = serve.call_args.kwargs
            self.assertTrue(str(serve.call_args.args[0]).endswith(".json"))
            self.assertTrue(str(kwargs["session_path"]).endswith(".json"))
            self.assertTrue(str(kwargs["additional_exceptions_path"]).endswith(".json"))
            self.assertTrue(kwargs["open_browser"])

    def test_missing_browser_does_not_abort_local_review_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = _write_operator_artifacts(Path(temporary))
            index, _ = build_operator_review_index(output)
            entry = first_reviewable_batch(index)
            assert entry is not None and entry.batch_path and entry.session_path
            server = Mock()
            server.server_address = ("127.0.0.1", 8765)
            server.serve_forever.side_effect = KeyboardInterrupt
            with (
                patch("kad_collector.review_server.create_review_server", return_value=server),
                patch(
                    "kad_collector.review_server.webbrowser.open",
                    side_effect=OSError("headless"),
                ),
                patch("builtins.print") as printer,
            ):
                serve_review_application(
                    Path(entry.batch_path),
                    session_path=Path(entry.session_path),
                    port=0,
                    open_browser=True,
                )
            messages = [str(call.args[0]) for call in printer.call_args_list if call.args]
            self.assertTrue(any("não foi possível abrir o navegador" in item for item in messages))
            server.serve_forever.assert_called_once_with()
            server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
