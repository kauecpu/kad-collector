from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kad_collector.approval_server import ApprovalApplication, create_approval_server
from kad_collector.consolidated_review import (
    ClassificationInventory,
    ConsolidatedReviewIndex,
    InventoryCounts,
)
from kad_collector.editorial_approval import (
    ApprovalConfig,
    ApprovalQuestion,
    _evaluate_question,
    _refresh_groups,
    _select_sample,
    build_approval_campaign,
    decide_audit_item,
    export_staging_package,
    reprocess_group,
)
from kad_collector.editorial_export import EditorialImportRecordV2
from kad_collector.json_utils import write_json
from kad_collector.local_review import (
    create_review_session,
    decide_review_question,
    save_review_session,
)
from kad_collector.models import (
    Alternative,
    DocumentRecord,
    QuestionBatch,
    QuestionRecord,
)
from kad_collector.operator_review import OperatorReviewBatch
from kad_collector.validation import validate_questions


def _document(root: Path, kind: str) -> DocumentRecord:
    content = f"%PDF-1.4\nfixture-{kind}\n%%EOF".encode()
    path = root / f"{kind}.pdf"
    path.write_bytes(content)
    return DocumentRecord(
        source_id="fixture-oficial",
        source_name="Fonte oficial fixture",
        document_type=kind,
        title=kind,
        original_url=f"https://example.test/{kind}.pdf",
        resolved_url=f"https://example.test/{kind}.pdf",
        local_path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        content_type="application/pdf",
        size_bytes=len(content),
        downloaded_at=datetime(2026, 9, 9, tzinfo=UTC),
        authorization_basis="Fixture local.",
    )


def _question(
    number: int,
    *,
    method: str = "deterministic",
    confidence: float = 0.84,
    extraction: str = "text",
    association: str = "fixture; associação exata por caderno e cargo",
    extra_notes: list[str] | None = None,
    stable_seed: str | None = None,
) -> QuestionRecord:
    return QuestionRecord(
        source_stable_id=hashlib.sha256((stable_seed or f"q-{number}").encode()).hexdigest(),
        number=number,
        statement=f"Enunciado completo e verificável da questão número {number}.",
        alternatives=[
            Alternative(letter="A", text="Certo"),
            Alternative(letter="B", text="Errado"),
        ],
        discipline="Língua Portuguesa",
        matter="Compreensão de textos",
        subject="Interpretação de texto",
        board="CEBRASPE",
        organization="Polícia Federal",
        concurso="Polícia Federal 2021",
        role="Agente",
        level="Superior",
        difficulty="Média",
        year=2021,
        source_pages=[1],
        correct_answer="A",
        answer_status="matched",
        review_notes=[
            "Estado estrutural: accepted.",
            f"Extração: {extraction}; parser: fixture.",
            f"Associação de gabarito: {association}.",
            (
                "Classificação automática: "
                f"método={method}; confiança={confidence:.2f}; taxonomia=2.0; "
                "caminho=portugues-texto; evidência=regra oficial; pendências=nenhuma."
            ),
            *(extra_notes or []),
        ],
    )


def _batch(root: Path, questions: list[QuestionRecord]) -> QuestionBatch:
    exam = _document(root, "exam")
    answer = _document(root, "answer_key")
    return QuestionBatch(
        batch_id="fixture-batch",
        created_at=datetime(2026, 9, 9, tzinfo=UTC),
        model="fixture",
        source_document=exam,
        answer_key_document=answer,
        questions=questions,
        validation=validate_questions(questions, require_answers=True),
    )


def _counts(total: int) -> InventoryCounts:
    return InventoryCounts(
        documents=2,
        exams=1,
        answer_keys=1,
        questions_extracted=total,
        structurally_accepted=total,
        quarantined=0,
        rejected=0,
        annulled=0,
        ready_for_review=total,
        classified=total,
        editorial_decisions=0,
        ready_for_export=0,
        exported=0,
    )


def _campaign_fixture(
    root: Path, questions: list[QuestionRecord], *, approve_first_locally: bool = False
) -> tuple[Path, Path]:
    batch = _batch(root, questions)
    session = create_review_session(batch, now=datetime(2026, 9, 9, tzinfo=UTC))
    if approve_first_locally:
        session = decide_review_question(
            session,
            questions[0].number,
            "approved",
            "revisor anterior",
            now=datetime(2026, 9, 9, 1, tzinfo=UTC),
        )
    session_path = root / "session.json"
    save_review_session(session, session_path)
    total = len(questions)
    index = ConsolidatedReviewIndex(
        corpus_id="fixture-corpus",
        base_commit="a" * 40,
        branch="fixture",
        created_at=datetime(2026, 9, 9, tzinfo=UTC),
        packages=[],
        batches=[
            OperatorReviewBatch(
                batch_id=batch.batch_id,
                exam_sha256=batch.source_document.sha256,
                batch_path=None,
                session_path=str(session_path),
                exceptions_path=str(root / "exceptions.jsonl"),
                accepted=total,
                quarantined=0,
                rejected=0,
            )
        ],
        total=_counts(total),
        homologation_sample=_counts(total),
        open_batch=_counts(total),
        open_batch_id=batch.batch_id,
        classification=ClassificationInventory(
            taxonomy_version="2.0",
            trace_path=str(root / "trace.jsonl"),
            deterministic=total,
            qwen=0,
            unresolved=0,
            qwen_calls=0,
            sample_size=0,
            precision_by_field={},
            accepted_without_correction=True,
        ),
        grouped={},
        quarantine_reasons={},
        quarantine_exclusive_combinations={},
        lineage_warnings=[],
        duplicate_stable_ids=0,
        content_sha256="b" * 64,
    )
    index_path = root / "index.json"
    state_path = root / "approval.json"
    write_json(index_path, index.model_dump(mode="json"))
    return index_path, state_path


class EditorialApprovalTests(unittest.TestCase):
    def test_valid_question_is_auto_ready_at_configured_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = create_review_session(_batch(root, [_question(1)]))
            result = _evaluate_question(
                session,
                session.batch.questions[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=set(),
                document_cache={},
            )
            self.assertEqual(result.state, "auto_ready")
            self.assertTrue(result.dimensions["taxonomy"].passed)

    def test_ambiguous_answer_ocr_visual_duplicate_and_qwen_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                _question(1, association="ambígua; duas possibilidades"),
                _question(2, extraction="ocr", extra_notes=["OCR confiança=0.70"]),
                _question(3, extra_notes=["Dependência visual necessária."]),
                _question(4, method="qwen", confidence=0.99),
            ]
            session = create_review_session(_batch(root, cases))
            results = [
                _evaluate_question(
                    session,
                    item,
                    config=ApprovalConfig(authorized_hosts=["example.test"]),
                    seen_fingerprints=set(),
                    document_cache={},
                )
                for item in cases
            ]
            self.assertFalse(results[0].dimensions["answer_association"].passed)
            self.assertFalse(results[1].dimensions["extraction"].passed)
            self.assertFalse(results[2].dimensions["visual_dependency"].passed)
            self.assertFalse(results[3].dimensions["taxonomy"].passed)
            self.assertTrue(all(item.state == "needs_review" for item in results))

            seen: set[str] = set()
            first = _evaluate_question(
                session,
                cases[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=seen,
                document_cache={},
            )
            duplicate = _evaluate_question(
                session,
                cases[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=seen,
                document_cache={},
            )
            self.assertTrue(first.dimensions["deduplication"].passed)
            self.assertFalse(duplicate.dimensions["deduplication"].passed)

    def test_stratified_sample_has_no_repeated_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = create_review_session(_batch(root, [_question(i) for i in range(1, 11)]))
            questions: list[ApprovalQuestion] = []
            seen: set[str] = set()
            cache: dict[str, tuple[bool, list[str]]] = {}
            for item in session.batch.questions:
                questions.append(
                    _evaluate_question(
                        session,
                        item,
                        config=ApprovalConfig(authorized_hosts=["example.test"]),
                        seen_fingerprints=seen,
                        document_cache=cache,
                    )
                )
            selected = _select_sample(
                questions,
                ApprovalConfig(
                    sample_rate=0.2,
                    minimum_sample=2,
                    maximum_sample=4,
                    authorized_hosts=["example.test"],
                ),
            )
            selected_fingerprints = {
                item.semantic_fingerprint for item in questions if item.stable_id in selected
            }
            self.assertEqual(len(selected), len(selected_fingerprints))

    def test_empty_or_failed_audit_never_approves_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path, state_path = _campaign_fixture(root, [_question(1), _question(2)])
            config = ApprovalConfig(
                minimum_sample=1,
                maximum_sample=1,
                authorized_hosts=["example.test"],
            )
            state = build_approval_campaign(index_path, state_path, config=config)
            sample = next(item for item in state.questions if item.state == "audit_sample")
            state = decide_audit_item(
                state_path,
                sample.stable_id,
                reviewer="auditora",
                status="rejected",
                structural_correct=False,
                answer_correct=True,
                taxonomy_correct=True,
                critical_errors=["questao_incompleta"],
                notes="Enunciado cortado.",
            )
            self.assertEqual(state.groups[0].status, "blocked")
            with self.assertRaisesRegex(ValueError, "nenhum grupo"):
                export_staging_package(state_path, root / "staging")

    def test_valid_audit_approves_only_group_and_export_passes_real_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path, state_path = _campaign_fixture(root, [_question(1), _question(2)])
            state = build_approval_campaign(
                index_path,
                state_path,
                config=ApprovalConfig(
                    minimum_sample=2,
                    maximum_sample=2,
                    authorized_hosts=["example.test"],
                ),
            )
            for item in [value for value in state.questions if value.state == "audit_sample"]:
                state = decide_audit_item(
                    state_path,
                    item.stable_id,
                    reviewer="auditora",
                    status="approved",
                    structural_correct=True,
                    answer_correct=True,
                    taxonomy_correct=True,
                )
            self.assertEqual(state.groups[0].status, "approved")
            self.assertTrue(all(item.state == "approved_for_staging" for item in state.questions))
            manifest = export_staging_package(state_path, root / "staging")
            self.assertEqual(manifest["questions"], 2)
            lines = (root / "staging" / "questoes.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                EditorialImportRecordV2.model_validate(json.loads(line))

    def test_human_decision_is_preserved_but_rule_change_rechecks_automation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path, state_path = _campaign_fixture(
                root, [_question(1)], approve_first_locally=True
            )
            state = build_approval_campaign(
                index_path,
                state_path,
                config=ApprovalConfig(
                    minimum_sample=1,
                    maximum_sample=1,
                    authorized_hosts=["example.test"],
                ),
            )
            self.assertEqual(state.questions[0].audit_decision.status, "approved")
            changed = build_approval_campaign(
                index_path,
                state_path,
                config=ApprovalConfig(
                    minimum_sample=1,
                    maximum_sample=1,
                    minimum_taxonomy_confidence=0.90,
                    authorized_hosts=["example.test"],
                ),
            )
            self.assertEqual(changed.questions[0].audit_decision.status, "approved")
            self.assertFalse(changed.questions[0].eligible_for_auto)
            self.assertEqual(changed.questions[0].state, "needs_review")

    def test_repeated_run_and_reprocessing_do_not_duplicate_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path, state_path = _campaign_fixture(root, [_question(1), _question(2)])
            config = ApprovalConfig(
                minimum_sample=1,
                maximum_sample=1,
                authorized_hosts=["example.test"],
            )
            first = build_approval_campaign(index_path, state_path, config=config)
            second = build_approval_campaign(index_path, state_path, config=config)
            self.assertEqual(first.content_sha256, second.content_sha256)
            self.assertEqual(len(second.questions), 2)
            processed = reprocess_group(state_path, second.groups[0].group_id)
            self.assertEqual(len(processed.groups[0].sample_ids), 1)
            self.assertEqual(len(set(processed.groups[0].sample_ids)), 1)

    def test_group_without_sample_is_pending(self) -> None:
        question = ApprovalQuestion.model_construct(
            stable_id="q",
            semantic_fingerprint="a" * 64,
            content_sha256="b" * 64,
            group_id="g",
            batch_id="b",
            session_path="session.json",
            question_number=1,
            state="auto_ready",
            eligible_for_auto=True,
            dimensions={},
            blockers=[],
            warnings=[],
            question_format="true_false",
            extraction_method="text",
            classification_method="deterministic",
        )
        groups = _refresh_groups([question], ApprovalConfig())
        self.assertEqual(groups[0].status, "pending_audit")
        self.assertIn("auditoria_sem_amostra", groups[0].blockers)

    def test_local_interface_reads_state_and_protects_writes_with_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path, state_path = _campaign_fixture(root, [_question(1)])
            state = build_approval_campaign(
                index_path,
                state_path,
                config=ApprovalConfig(
                    minimum_sample=1,
                    maximum_sample=1,
                    authorized_hosts=["example.test"],
                ),
            )
            sample = next(item for item in state.questions if item.state == "audit_sample")
            application = ApprovalApplication(state_path, staging_output=root / "staging")
            server = create_approval_server(application, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                with urlopen(f"http://{host}:{port}/api/state", timeout=2) as response:
                    payload = json.load(response)
                self.assertEqual(payload["summary"]["audit_sample"], 1)
                body = json.dumps(
                    {
                        "status": "approved",
                        "reviewer": "auditora",
                        "structuralCorrect": True,
                        "answerCorrect": True,
                        "taxonomyCorrect": True,
                    }
                ).encode()
                unauthorized = Request(
                    f"http://{host}:{port}/api/questions/{sample.stable_id}/decision",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(unauthorized, timeout=2)
                self.assertEqual(caught.exception.code, 403)
                authorized = Request(
                    f"http://{host}:{port}/api/questions/{sample.stable_id}/decision",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-KAD-Approval-Token": application.token,
                    },
                )
                with urlopen(authorized, timeout=2) as response:
                    updated = json.load(response)
                self.assertEqual(updated["summary"]["approved_for_staging"], 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
