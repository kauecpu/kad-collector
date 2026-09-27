from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from publication_contract import publication_blockers

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
from kad_collector.editorial_export import EditorialImportRecordV2, stable_question_id
from kad_collector.json_utils import write_json
from kad_collector.local_review import (
    create_review_session,
    decide_review_question,
    question_content_sha256,
    save_review_session,
)
from kad_collector.models import (
    Alternative,
    DocumentRecord,
    QuestionBatch,
    QuestionRecord,
)
from kad_collector.operator_review import OperatorReviewBatch
from kad_collector.staging_provenance import StagingOccurrence, build_canonical_staging_record
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
        matter="Interpretação de Textos",
        subject="Compreensão e Interpretação",
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
                f"método={method}; confiança={confidence:.2f}; taxonomia=3.2.0; "
                "caminho=topic:portugues:interpretacao; evidência=regra oficial; "
                "pendências=nenhuma."
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
            question = _question(1)
            question.difficulty = None
            session = create_review_session(_batch(root, [question]))
            result = _evaluate_question(
                session,
                session.batch.questions[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=set(),
                document_cache={},
            )
            self.assertEqual(result.state, "auto_ready")
            self.assertTrue(result.dimensions["taxonomy"].passed)
            self.assertIn("difficulty=opcional", result.dimensions["taxonomy"].evidence)

    def test_pdf_context_and_page_bleed_do_not_pass_as_complete_structure(self) -> None:
        cases = [
            ("No parágrafo 5, conclui-se que...", "contexto anexado"),
            ("A palavra em destaque está correta?", "destaque"),
            ("Enunciado completo e verificável.", "rascunho"),
            ("Enunciado completo e verificável.", "cabeçalho"),
            ("Enunciado completo e verificável.", "glifo"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (statement, defect) in enumerate(cases, start=1):
                with self.subTest(defect=defect):
                    question = _question(index)
                    question.statement = statement
                    if defect == "rascunho":
                        question.alternatives[1].text = "Errado\nRASCUNHO"
                    elif defect == "cabeçalho":
                        question.alternatives[1].text = "Errado\nBANCO DO BRASIL"
                    elif defect == "glifo":
                        question.alternatives[1].text = "\uf02d2"
                    session = create_review_session(_batch(root, [question]))
                    result = _evaluate_question(
                        session,
                        session.batch.questions[0],
                        config=ApprovalConfig(authorized_hosts=["example.test"]),
                        seen_fingerprints=set(),
                        document_cache={},
                    )
                    self.assertEqual(result.state, "quarantined")
                    self.assertFalse(result.dimensions["structure"].passed)
                    self.assertTrue(
                        any(defect in item for item in result.dimensions["structure"].evidence)
                    )

    def test_text_reference_with_attached_context_remains_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question = _question(1)
            question.statement = (
                "Texto de apoio completo e relevante.\n\n"
                "No parágrafo 1, qual afirmação é sustentada?"
            )
            session = create_review_session(_batch(root, [question]))
            result = _evaluate_question(
                session,
                session.batch.questions[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=set(),
                document_cache={},
            )
            self.assertEqual(result.state, "auto_ready")

    def test_legitimate_bank_name_as_single_alternative_is_not_page_bleed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question = _question(1)
            question.alternatives[1].text = "Banco do Brasil"
            session = create_review_session(_batch(root, [question]))
            result = _evaluate_question(
                session,
                session.batch.questions[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=set(),
                document_cache={},
            )
            self.assertEqual(result.state, "auto_ready")

    def test_stale_semantic_rule_supported_only_by_distractor_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question = _question(1)
            question.alternatives[1].text = "Interpretação de textos"
            question.review_notes.append(
                "Evidências da classificação: "
                + json.dumps(
                    {
                        "matter": {"source": "local_semantic_rule"},
                        "subject": {"source": "local_semantic_rule"},
                    },
                    ensure_ascii=False,
                )
            )
            session = create_review_session(_batch(root, [question]))
            result = _evaluate_question(
                session,
                session.batch.questions[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=set(),
                document_cache={},
            )
            self.assertEqual(result.state, "needs_review")
            self.assertIn(
                "regra_semântica_no_enunciado=não",
                result.dimensions["taxonomy"].evidence,
            )

    def test_structural_block_label_cannot_pass_as_a_taxonomy_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question = _question(1)
            question.subject = "Bloco I"
            session = create_review_session(_batch(root, [question]))

            result = _evaluate_question(
                session,
                session.batch.questions[0],
                config=ApprovalConfig(authorized_hosts=["example.test"]),
                seen_fingerprints=set(),
                document_cache={},
            )

            self.assertFalse(result.dimensions["taxonomy"].passed)
            self.assertEqual(result.state, "needs_review")
            self.assertIn(
                "caminho_fechado=não", result.dimensions["taxonomy"].evidence
            )

    def test_ambiguous_answer_ocr_visual_duplicate_and_qwen_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            qwen_without_closed_path = _question(4, method="qwen", confidence=0.99)
            qwen_without_closed_path.review_notes = [
                note.replace(
                    "caminho=topic:portugues:interpretacao",
                    "caminho=portugues-texto-inexistente",
                )
                for note in qwen_without_closed_path.review_notes
            ]
            cases = [
                _question(1, association="ambígua; duas possibilidades"),
                _question(2, extraction="ocr", extra_notes=["OCR confiança=0.70"]),
                _question(3, extra_notes=["Dependência visual necessária."]),
                qwen_without_closed_path,
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
            original_state = state_path.read_bytes()
            with (
                patch("kad_collector.editorial_approval.validate_package",
                      return_value=["fixture contract blocker"]),
                self.assertRaisesRegex(ValueError, "contrato de publicação bloqueado"),
            ):
                export_staging_package(state_path, root / "blocked-contract")
            self.assertFalse((root / "blocked-contract").exists())
            self.assertEqual(state_path.read_bytes(), original_state)
            manifest = export_staging_package(state_path, root / "staging")
            self.assertEqual(manifest["questions"], 2)
            lines = (root / "staging" / "questoes.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                record = json.loads(line)
                EditorialImportRecordV2.model_validate(record)
                self.assertEqual(publication_blockers(record), set())
                self.assertEqual(record["data"]["publicationStatus"], "draft")
                without_evidence = deepcopy(record)
                del without_evidence["data"]["canonicalQuestion"]
                self.assertEqual(publication_blockers(without_evidence), {"official_answer"})
                for field, bad in (("answerKeyLinkId", " "), ("answerStatus", "missing"),
                                   ("answer", "B")):
                    changed = deepcopy(record)
                    changed["data"]["canonicalQuestion"]["provenances"][0][field] = bad
                    self.assertEqual(publication_blockers(changed), {"official_answer"})
            before = {p.name: p.read_bytes() for p in (root / "staging").iterdir()}
            export_staging_package(state_path, root / "again")
            self.assertEqual(before, {p.name: p.read_bytes() for p in (root / "again").iterdir()})

    def test_canonical_export_groups_permutations_and_keeps_original_answer_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _question(1)
            second = first.model_copy(deep=True, update={"number": 2, "correct_answer": "B"})
            second.alternatives = [Alternative(letter="A", text="Errado"),
                                   Alternative(letter="B", text="Certo")]
            batch = _batch(root, [first, second])
            occurrences = [StagingOccurrence("first", batch, first, {}),
                           StagingOccurrence("second", batch, second, {})]
            record, lineage = build_canonical_staging_record(occurrences)
            raw = record.model_dump(mode="json", by_alias=True, exclude_none=True)
            self.assertEqual(record.data.id, stable_question_id(batch, first))
            self.assertEqual(raw["data"]["canonicalQuestion"]["occurrenceCount"], 2)
            self.assertEqual(publication_blockers(raw), set())
            self.assertEqual(lineage["occurrences"][1]["originalAnswer"], "B")
            self.assertEqual(lineage["occurrences"][1]["canonicalAnswer"], "A")
            self.assertEqual(lineage["occurrences"][1]["answerKeySha256"],
                             batch.answer_key_document.sha256)
            again, again_lineage = build_canonical_staging_record(list(reversed(occurrences)))
            self.assertEqual(record, again)
            self.assertEqual(lineage, again_lineage)
            second.correct_answer = "A"
            with self.assertRaisesRegex(ValueError, "respostas oficiais divergentes"):
                build_canonical_staging_record(occurrences)

    def test_approval_export_groups_only_the_approved_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = _question(1), _question(2)
            second.statement = first.statement
            second.alternatives = [Alternative(letter="A", text="Errado"),
                                   Alternative(letter="B", text="Certo")]
            second.correct_answer = "B"
            index_path, state_path = _campaign_fixture(root, [first, second])
            state = build_approval_campaign(index_path, state_path, config=ApprovalConfig(
                minimum_sample=2, maximum_sample=2, authorized_hosts=["example.test"]))
            for item in state.questions:
                decide_audit_item(state_path, item.stable_id, reviewer="auditora",
                                  status="approved", structural_correct=True,
                                  answer_correct=True, taxonomy_correct=True)
            manifest = export_staging_package(state_path, root / "staging")
            self.assertEqual((manifest["questions"], manifest["occurrences"]), (1, 2))
            record = json.loads((root / "staging/questoes.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(publication_blockers(record), set())
            approved = json.loads(state_path.read_text(encoding="utf-8"))
            # A fixture simulates a withdrawn occurrence; the exporter must not infer approval.
            approved["questions"][1]["state"] = "needs_review"
            write_json(state_path, approved)
            remaining = export_staging_package(state_path, root / "remaining")
            self.assertEqual((remaining["questions"], remaining["occurrences"]), (1, 1))

    def test_publication_contract_rejects_incomplete_data_and_duplicate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            question = _question(1)
            batch = _batch(root, [question])
            record, _ = build_canonical_staging_record([
                StagingOccurrence("first", batch, question, {})])
            valid = record.model_dump(mode="json", by_alias=True, exclude_none=True)
            for field, value, blocker in (
                ("statement", "curto", "statement"),
                ("discipline", "", "taxonomy"),
                ("subject", "", "taxonomy"),
                ("topic", "", "taxonomy"),
                ("level", "", "taxonomy"),
                ("correct", "E", "alternatives"),
                ("alternatives", [], "alternatives"),
            ):
                changed = deepcopy(valid)
                changed["data"][field] = value
                self.assertIn(blocker, publication_blockers(changed))
            for field in ("provider", "externalId", "url", "collectedAt", "fingerprint"):
                changed = deepcopy(valid)
                changed["source"][field] = None
                self.assertEqual(publication_blockers(changed), {"source"})
            duplicate = deepcopy(valid)
            duplicate["data"]["id"] += "-duplicate"
            self.assertEqual(publication_blockers(valid, [duplicate]), {"duplicate_source"})

    def test_canonical_export_does_not_erase_operators_or_taxonomy_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _question(1)
            first.statement = "A operação x + y é positiva."
            second = first.model_copy(deep=True, update={"number": 2})
            batch = _batch(root, [first, second])
            occurrences = [StagingOccurrence("first", batch, first, {}),
                           StagingOccurrence("second", batch, second, {})]
            second.statement = "A operação x - y é positiva."
            self.assertEqual(occurrences[0].equivalence_key, occurrences[1].equivalence_key)
            with self.assertRaisesRegex(ValueError, "colisão de equivalência"):
                build_canonical_staging_record(occurrences)
            second.statement = first.statement
            second.discipline = "Matemática"
            with self.assertRaisesRegex(ValueError, "classificações divergentes"):
                build_canonical_staging_record(occurrences)

    def test_export_rechecks_document_and_content_without_changing_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index_path, state_path = _campaign_fixture(root, [_question(1)])
            state = build_approval_campaign(index_path, state_path, config=ApprovalConfig(
                minimum_sample=1, maximum_sample=1, authorized_hosts=["example.test"]))
            decide_audit_item(state_path, state.questions[0].stable_id, reviewer="auditora",
                              status="approved", structural_correct=True, answer_correct=True,
                              taxonomy_correct=True)
            original_state = state_path.read_bytes()
            session_path = root / "session.json"
            original_session = session_path.read_bytes()
            session = json.loads(original_session)
            session["batch"]["answer_key_document"]["sha256"] = "0" * 64
            write_json(session_path, session)
            with self.assertRaisesRegex(ValueError, "nenhuma questão válida"):
                export_staging_package(state_path, root / "wrong-key")
            session_path.write_bytes(original_session)
            (root / "answer_key.pdf").write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "nenhuma questão válida"):
                export_staging_package(state_path, root / "changed-pdf")
            _document(root, "answer_key")
            session = json.loads(original_session)
            session["batch"]["questions"][0]["statement"] += " Conteúdo alterado."
            changed = QuestionRecord.model_validate(session["batch"]["questions"][0])
            self.assertNotEqual(question_content_sha256(changed), state.questions[0].content_sha256)
            write_json(session_path, session)
            with self.assertRaisesRegex(ValueError, "nenhuma questão válida"):
                export_staging_package(state_path, root / "changed-content")
            self.assertEqual(state_path.read_bytes(), original_state)

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
                self.assertEqual(payload["sample"][0]["discipline"], "Língua Portuguesa")
                self.assertEqual(payload["sample"][0]["difficulty"], "Média")
                self.assertEqual(
                    payload["sample"][0]["classificationMethod"], "deterministic"
                )
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
