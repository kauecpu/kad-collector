from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kad_collector.answer_association import revalidate_answer_key_associations
from kad_collector.desktop_models import (
    DesktopFilterSet,
    DesktopImportMetadata,
    QuestionClassification,
)
from kad_collector.desktop_processor import DesktopProcessor
from kad_collector.desktop_store import DesktopStore
from kad_collector.models import Alternative, QuestionRecord
from kad_collector.official_regression import load_official_manifest
from kad_collector.semantic_identity import (
    AnswerKeyCoverage,
    AssociationCandidate,
    ContentFingerprint,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    QuestionInterval,
    SemanticEvidence,
    SemanticField,
)
from kad_collector.semantic_resolution import (
    ASSOCIATION_ALGORITHM_VERSION,
    select_answer_key,
)


def known(name: str, *values: str | int) -> SemanticField:
    return SemanticField.from_evidence(
        name,
        tuple(SemanticEvidence.metadata(name, value) for value in values),
    )


def profile(
    *,
    role: str = "Analista",
    stage: str = "Prova objetiva",
    turn: str = "Manha",
    variant: str = "Tipo 1",
    document_role: str = "exam",
    state: str = "definitive",
) -> DocumentSemanticProfile:
    identity = ExamSemanticIdentity(
        board=known("board", "FGV"),
        concurso=known("concurso", "Concurso teste"),
        organization=known("organization", "Orgao teste"),
        year=known("year", 2026),
        roles=known("roles", role),
        stage=known("stage", stage),
        turns=known("turns", turn),
        variants=known("variants", variant),
    )
    return DocumentSemanticProfile(
        identity=identity,
        identity_key=f"test:{role}:{stage}:{turn}:{variant}",
        document_role=document_role,  # type: ignore[arg-type]
        answer_key_state=state if document_role == "answer_key" else "unknown",  # type: ignore[arg-type]
        coverage=AnswerKeyCoverage(
            roles=identity.roles,
            stage=identity.stage,
            turns=identity.turns,
            variants=identity.variants,
        ),
        content_fingerprint=ContentFingerprint(
            sha256=f"sha-{document_role}-{role}-{stage}-{turn}-{variant}-{state}",
            page_sha256s=(),
            page_count=1,
            character_count=100,
        ),
        has_conflict=False,
    )


def candidate(
    version_id: str = "key",
    *,
    interval: tuple[int, int] = (1, 2),
    **changes: str,
) -> AssociationCandidate:
    return AssociationCandidate(
        version_id=version_id,
        profile=profile(document_role="answer_key", **changes),
        question_interval=QuestionInterval(first=interval[0], last=interval[1]),
    )


class SemanticAssociationV2DecisionTests(unittest.TestCase):
    def decide(self, *candidates: AssociationCandidate):
        return select_answer_key(
            profile(),
            list(candidates),
            exam_interval=QuestionInterval(first=1, last=2),
        )

    def test_valid_association_requires_every_compatible_field(self) -> None:
        decision = self.decide(candidate())
        self.assertEqual(decision.selected_version_id, "key")
        self.assertEqual(decision.algorithm_version, "semantic-association-v2")
        self.assertEqual(
            set(decision.assessments[0].matched_fields),
            {
                "board", "concurso", "organization", "year", "role",
                "stage", "turn", "variant", "interval",
            },
        )

    def test_each_incompatible_scope_is_rejected(self) -> None:
        cases = {
            "role": {"role": "Auditor"},
            "stage": {"stage": "Discursiva"},
            "turn": {"turn": "Tarde"},
            "variant": {"variant": "Tipo 2"},
        }
        for expected, changes in cases.items():
            with self.subTest(field=expected):
                decision = self.decide(candidate(**changes))
                self.assertEqual(decision.outcome, "conflict")
                self.assertTrue(
                    any(item.startswith(expected) for item in decision.assessments[0].conflicts)
                )

    def test_incompatible_interval_is_rejected(self) -> None:
        decision = self.decide(candidate(interval=(1, 3)))
        self.assertEqual(decision.outcome, "conflict")
        self.assertIn("interval: valores incompatíveis", decision.assessments[0].conflicts)

    def test_missing_required_metadata_is_incomplete(self) -> None:
        incomplete = candidate().model_copy(
            update={
                "profile": candidate().profile.model_copy(
                    update={
                        "coverage": candidate().profile.coverage.model_copy(
                            update={"turns": SemanticField.unknown("turno ausente")}
                        )
                    }
                )
            }
        )
        decision = self.decide(incomplete)
        self.assertEqual(decision.outcome, "incomplete")
        self.assertIn("turn", decision.assessments[0].incomplete_fields)

    def test_tied_best_candidates_are_ambiguous_without_order_tiebreak(self) -> None:
        first = self.decide(candidate("a"), candidate("b"))
        second = self.decide(candidate("b"), candidate("a"))
        self.assertEqual(first.outcome, "ambiguous")
        self.assertEqual(second.outcome, "ambiguous")
        self.assertIsNone(first.selected_version_id)
        self.assertEqual(
            [item.version_id for item in first.assessments],
            [item.version_id for item in second.assessments],
        )


class AnswerAssociationRevalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = DesktopStore(self.root / "collector.sqlite3")
        self.processor = DesktopProcessor(self.store)

    def tearDown(self) -> None:
        self.processor._executor.shutdown(wait=True)
        self.directory.cleanup()

    @staticmethod
    def metadata(document_type: str, *, role: str = "Analista") -> DesktopImportMetadata:
        return DesktopImportMetadata(
            document_type=document_type,  # type: ignore[arg-type]
            document_title=f"{document_type} {role}",
            board="FGV",
            concurso="Concurso teste",
            organization="Orgao teste",
            year=2026,
            role=role,
            stage="Prova objetiva",
            turn="Manha",
            variant="Tipo 1",
        )

    def add_document(
        self, filename: str, text: str, metadata: DesktopImportMetadata
    ) -> dict[str, object]:
        path = self.root / filename
        path.write_bytes(f"%PDF-1.4\n{text}\n%%EOF".encode())
        job_id = self.store.create_job([path], metadata, "local")
        document = self.store.documents_for_job(job_id)[0]
        self.store.save_page(str(document["id"]), 1, text, status="text")
        self.store.update_document(str(document["id"]), status="extracted", page_count=1)
        self.store.resolve_extracted_document(str(document["id"]))
        return self.store.document(str(document["id"]))

    def add_exam(
        self, prefix: str = "one", *, role: str = "Analista"
    ) -> tuple[dict[str, object], list[str]]:
        exam = self.add_document(
            f"exam-{prefix}.pdf",
            f"Documento: {prefix}\nBanca: FGV\nConcurso: Concurso teste\nAno: 2026\n"
            f"Cargo: {role}\nEtapa: Prova objetiva\nTurno: Manha\nTipo: 1",
            self.metadata("exam", role=role),
        )
        question_ids = []
        for number in (1, 2):
            question = QuestionRecord(
                number=number,
                statement=f"Enunciado completo {prefix} {number}.",
                alternatives=[
                    Alternative(letter="A", text="Alternativa A."),
                    Alternative(letter="B", text="Alternativa B."),
                ],
                answer_status="missing",
                matter=None,
                subject=None,
                board="FGV",
                concurso="Concurso teste",
                organization="Orgao teste",
                year=2026,
                role=role,
                source_pages=[1],
            )
            question_ids.append(
                self.store.save_question(
                    str(exam["id"]), question, QuestionClassification()
                )
            )
        return exam, question_ids

    def add_key(
        self,
        prefix: str = "one",
        *,
        state: str = "definitivo",
        answers: tuple[str, str] = ("A", "B"),
        role: str = "Analista",
    ) -> dict[str, object]:
        return self.add_document(
            f"key-{prefix}-{state}.pdf",
            f"Documento: {prefix}\nGabarito {state}\nBanca: FGV\n"
            f"Concurso: Concurso teste\nAno: 2026\n"
            f"Cargo: {role}\nEtapa: Prova objetiva\nTurno: Manha\nTipo: 1\n"
            f"1 - {answers[0]}\n2 - {answers[1]}",
            self.metadata("answer_key", role=role),
        )

    def make_old_link(
        self, exam: dict[str, object], key: dict[str, object]
    ) -> str:
        self.assertEqual(
            self.processor._reconcile_answer_key(str(key["document_version_id"])), 1
        )
        with closing(self.store._connect()) as connection:
            link = connection.execute(
                "SELECT id, decision_json FROM document_links WHERE exam_version_id = ? "
                "AND status = 'active'",
                (exam["document_version_id"],),
            ).fetchone()
            decision = json.loads(link["decision_json"])
            decision["algorithm_version"] = "semantic-association-v1"
            connection.execute(
                "UPDATE document_links SET algorithm_version = 'semantic-association-v1', "
                "decision_json = ? WHERE id = ?",
                (json.dumps(decision, ensure_ascii=False, sort_keys=True), link["id"]),
            )
            connection.commit()
        return str(link["id"])

    def test_dry_run_does_not_write_and_apply_maintains_with_append_only_audit(self) -> None:
        exam, _ = self.add_exam()
        key = self.add_key()
        old_link = self.make_old_link(exam, key)
        with closing(self.store._connect()) as connection:
            dry = revalidate_answer_key_associations(connection)
            self.assertEqual(dry.maintained, 1)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM association_revalidation_runs"
                ).fetchone()[0],
                0,
            )
            applied = revalidate_answer_key_associations(
                connection, apply=True, run_id="maintained-run"
            )
            audit = connection.execute(
                "SELECT * FROM association_revalidation_audit"
            ).fetchone()
            links = connection.execute(
                "SELECT status, algorithm_version FROM document_links ORDER BY created_at, id"
            ).fetchall()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE association_revalidation_audit SET reason = 'rewritten'"
                )
            connection.rollback()
        self.assertEqual(applied.maintained, 1)
        self.assertEqual(audit["old_link_id"], old_link)
        self.assertEqual(audit["result_status"], "maintained")
        self.assertEqual(
            [(row["status"], row["algorithm_version"]) for row in links],
            [("superseded", "semantic-association-v1"), ("active", ASSOCIATION_ALGORITHM_VERSION)],
        )

    def test_changed_link_is_recalculated_and_history_preserves_old_and_new(self) -> None:
        exam, _ = self.add_exam()
        preliminary = self.add_key(state="preliminar", answers=("A", "A"))
        old_link = self.make_old_link(exam, preliminary)
        definitive = self.add_key(state="definitivo", answers=("B", "B"))
        with closing(self.store._connect()) as connection:
            report = revalidate_answer_key_associations(
                connection, apply=True, run_id="changed-run"
            )
            audit = connection.execute(
                "SELECT old_link_id, new_answer_key_version_id, comparison_json "
                "FROM association_revalidation_audit"
            ).fetchone()
        self.assertEqual(report.changed, 1)
        self.assertEqual(audit["old_link_id"], old_link)
        self.assertEqual(audit["new_answer_key_version_id"], definitive["document_version_id"])
        self.assertIn("oldAssociation", json.loads(audit["comparison_json"]))

    def test_invalid_link_invalidates_answers_but_preserves_raw_pages_and_export_gate(self) -> None:
        exam, question_ids = self.add_exam()
        key = self.add_key()
        self.make_old_link(exam, key)
        before_pages = self.store.pages(str(exam["id"]))
        with closing(self.store._connect()) as connection:
            profile_payload = json.loads(
                connection.execute(
                    "SELECT profile_json FROM document_versions WHERE id = ?",
                    (key["document_version_id"],),
                ).fetchone()[0]
            )
            wrong = known("roles", "Auditor").model_dump(mode="json")
            profile_payload["identity"]["roles"] = wrong
            profile_payload["coverage"]["roles"] = wrong
            connection.execute(
                "UPDATE document_versions SET profile_json = ?, coverage_json = ? WHERE id = ?",
                (
                    json.dumps(profile_payload, ensure_ascii=False),
                    json.dumps(profile_payload["coverage"], ensure_ascii=False),
                    key["document_version_id"],
                ),
            )
            connection.commit()
            report = revalidate_answer_key_associations(
                connection, apply=True, run_id="invalid-run"
            )
        self.assertEqual(report.invalidated, 1)
        self.assertEqual(report.answers_invalidated, 2)
        self.assertEqual(self.store.pages(str(exam["id"])), before_pages)
        for question_id in question_ids:
            view = self.store.question(question_id)
            self.assertEqual(view["question"]["answer_status"], "missing")
            self.assertEqual(view["status"], "exception")
            self.assertIsNotNone(view["answer_invalidation_reason"])
        self.assertEqual(self.store.query(DesktopFilterSet())["summary"]["exportable"], 0)

    def test_ambiguous_candidates_deactivate_old_link_and_enter_review(self) -> None:
        exam, _ = self.add_exam()
        first = self.add_key(prefix="first")
        self.make_old_link(exam, first)
        self.add_key(prefix="second")
        with closing(self.store._connect()) as connection:
            report = revalidate_answer_key_associations(
                connection, apply=True, run_id="ambiguous-run"
            )
            active = connection.execute(
                "SELECT COUNT(*) FROM document_links WHERE status = 'active'"
            ).fetchone()[0]
            review = connection.execute(
                "SELECT status, candidates_json FROM association_review_queue"
            ).fetchone()
        self.assertEqual(report.ambiguous, 1)
        self.assertEqual(active, 0)
        self.assertEqual(review["status"], "pending")
        self.assertEqual(len(json.loads(review["candidates_json"])), 2)

    def test_resume_and_reexecution_are_idempotent(self) -> None:
        for prefix, role in (("one", "Analista"), ("two", "Auditor")):
            exam, _ = self.add_exam(prefix, role=role)
            key = self.add_key(prefix, role=role)
            self.make_old_link(exam, key)
        with closing(self.store._connect()) as connection:
            first = revalidate_answer_key_associations(
                connection, apply=True, run_id="resume-run", limit=1
            )
            second = revalidate_answer_key_associations(
                connection, apply=True, run_id="resume-run"
            )
            repeat = revalidate_answer_key_associations(
                connection, apply=True, run_id="new-idempotent-run"
            )
            audits = connection.execute(
                "SELECT COUNT(*) FROM association_revalidation_audit"
            ).fetchone()[0]
        self.assertEqual((first.examined, first.status), (1, "paused"))
        self.assertEqual((second.examined, second.status), (2, "completed"))
        self.assertEqual(repeat.examined, 0)
        self.assertEqual(audits, 2)

    def test_run_resumes_after_transactional_interruption(self) -> None:
        for prefix, role in (("one", "Analista"), ("two", "Auditor")):
            exam, _ = self.add_exam(prefix, role=role)
            key = self.add_key(prefix, role=role)
            self.make_old_link(exam, key)
        with closing(self.store._connect()) as connection:
            connection.execute(
                "CREATE TRIGGER interrupt_second_revalidation "
                "BEFORE INSERT ON association_revalidation_audit "
                "WHEN (SELECT COUNT(*) FROM association_revalidation_audit) = 1 "
                "BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END"
            )
            connection.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated interruption"):
                revalidate_answer_key_associations(
                    connection, apply=True, run_id="interrupted-run"
                )
            state = connection.execute(
                "SELECT status FROM association_revalidation_runs "
                "WHERE id = 'interrupted-run'"
            ).fetchone()[0]
            self.assertEqual(state, "interrupted")
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM association_revalidation_audit "
                    "WHERE run_id = 'interrupted-run'"
                ).fetchone()[0],
                1,
            )
            connection.execute("DROP TRIGGER interrupt_second_revalidation")
            connection.commit()
            resumed = revalidate_answer_key_associations(
                connection, apply=True, run_id="interrupted-run"
            )
        self.assertEqual((resumed.examined, resumed.status), (2, "completed"))


class Rfb22SemanticAssociationRegressionTests(unittest.TestCase):
    def test_all_supported_rfb22_booklets_select_the_manifest_answer_key(self) -> None:
        manifest = load_official_manifest(
            Path("tests/regression/rfb22/manifest.v1.toml")
        ).spec
        applications = {application.id: application for application in manifest.applications}
        supported_exams = [
            document
            for document in manifest.documents
            if document.kind == "exam"
            and applications[document.application_id].support_status == "supported"
        ]
        self.assertEqual(len(supported_exams), 16)
        for exam in supported_exams:
            with self.subTest(document=exam.id):
                application = applications[exam.application_id]
                exam_profile = profile(
                    role=exam.roles[0],
                    stage=application.stage,
                    turn=exam.shift or "",
                    variant=f"Tipo {exam.booklet_type}",
                )
                exam_profile = exam_profile.model_copy(
                    update={
                        "identity": exam_profile.identity.model_copy(
                            update={
                                "board": known("board", manifest.board),
                                "concurso": known("concurso", manifest.contest_name),
                                "organization": known("organization", manifest.organization),
                                "year": known("year", manifest.notice_year),
                            }
                        )
                    }
                )
                candidates = []
                for key in (
                    document for document in manifest.documents if document.kind == "answer_key"
                ):
                    matching_scope = next(
                        (
                            scope
                            for scope in key.answer_scopes
                            if scope.role == exam.roles[0]
                            and scope.shift == exam.shift
                            and exam.booklet_type in scope.booklet_types
                        ),
                        None,
                    )
                    key_profile = profile(
                        role=exam.roles[0],
                        stage=application.stage,
                        turn=exam.shift or "",
                        variant=f"Tipo {exam.booklet_type}",
                        document_role="answer_key",
                        state=key.answer_key_status or "unknown",
                    )
                    key_profile = key_profile.model_copy(
                        update={
                            "identity": key_profile.identity.model_copy(
                                update={
                                    "board": known("board", manifest.board),
                                    "concurso": known("concurso", manifest.contest_name),
                                    "organization": known("organization", manifest.organization),
                                    "year": known("year", manifest.notice_year),
                                }
                            )
                        }
                    )
                    candidates.append(
                        AssociationCandidate(
                            version_id=key.id,
                            profile=key_profile,
                            question_interval=(
                                QuestionInterval(
                                    first=matching_scope.first,
                                    last=matching_scope.last,
                                )
                                if matching_scope is not None else None
                            ),
                        )
                    )
                objective = next(
                    section for section in exam.sections if section.kind == "objective"
                )
                decision = select_answer_key(
                    exam_profile,
                    candidates,
                    exam_interval=QuestionInterval(first=objective.first, last=objective.last),
                )
                self.assertEqual(decision.selected_version_id, exam.answer_key_id)
                self.assertEqual(decision.algorithm_version, ASSOCIATION_ALGORITHM_VERSION)
