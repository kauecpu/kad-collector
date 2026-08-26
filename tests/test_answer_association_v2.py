from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kad_collector.answer_association import (
    decide_runtime_association,
    revalidate_answer_key_associations,
)
from kad_collector.cli import main
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

    def test_definitive_priority_never_crosses_an_incompatible_shift(self) -> None:
        decision = self.decide(
            candidate("preliminary", state="preliminary"),
            candidate("wrong-definitive", state="definitive", turn="Tarde"),
        )

        self.assertEqual(decision.selected_version_id, "preliminary")

    def test_definitive_wins_over_preliminary_inside_the_same_scope(self) -> None:
        decision = self.decide(
            candidate("preliminary", state="preliminary"),
            candidate("definitive", state="definitive"),
        )

        self.assertEqual(decision.selected_version_id, "definitive")


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
    def metadata(
        document_type: str,
        *,
        role: str = "Analista",
        turn: str | None = "Manha",
        variant: str | None = "Tipo 1",
    ) -> DesktopImportMetadata:
        return DesktopImportMetadata(
            document_type=document_type,  # type: ignore[arg-type]
            document_title=f"{document_type} {role}",
            board="FGV",
            concurso="Concurso teste",
            organization="Orgao teste",
            year=2026,
            role=role,
            stage="Prova objetiva",
            turn=turn,
            variant=variant,
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

    def add_fgv_exam(
        self,
        prefix: str,
        *,
        role: str,
        shift: str,
        variant: int,
        question_count: int,
    ) -> dict[str, object]:
        exam = self.add_document(
            f"exam-{prefix}.pdf",
            f"FUNDAÇÃO GETULIO VARGAS\n{shift}\nPROVA OBJETIVA\n"
            f"Banca: FGV\nConcurso: Concurso teste\nAno: 2026\n"
            f"Cargo: {role}\nEtapa: Prova objetiva\nTipo: {variant}",
            self.metadata("exam", role=role, turn=None, variant=f"Tipo {variant}"),
        )
        for number in range(1, question_count + 1):
            self.store.save_question(
                str(exam["id"]),
                QuestionRecord(
                    number=number,
                    statement=f"Enunciado controlado {prefix} {number}.",
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
                ),
                QuestionClassification(),
            )
        return exam

    def add_fgv_multi_grid_key(
        self,
        prefix: str,
        *,
        role: str,
        variants: tuple[int, ...],
        morning_count: int,
        afternoon_count: int,
        state: str = "definitivo",
    ) -> dict[str, object]:
        blocks: list[str] = []
        for variant in variants:
            for shift, count in (("Manhã", morning_count), ("Tarde", afternoon_count)):
                numbers = " ".join(str(number) for number in range(1, count + 1))
                answers = " ".join("A" if number % 2 else "B" for number in range(1, count + 1))
                blocks.append(
                    f"{role} - TIPO {variant} ({shift})\n{numbers}\n{answers}"
                )
        return self.add_document(
            f"key-{prefix}-{state}.pdf",
            f"Gabarito {state}\nBanca: FGV\nConcurso: Concurso teste\n"
            f"Ano: 2026\nCargo: {role}\nEtapa: Prova objetiva\n" + "\n".join(blocks),
            self.metadata("answer_key", role=role, turn=None, variant=None),
        )

    def erase_stored_turns(self, *version_ids: object) -> None:
        unknown = SemanticField.unknown("turno ausente no manifesto").model_dump(mode="json")
        with closing(self.store._connect()) as connection:
            for version_id in version_ids:
                row = connection.execute(
                    "SELECT profile_json FROM document_versions WHERE id = ?",
                    (str(version_id),),
                ).fetchone()
                payload = json.loads(row["profile_json"])
                payload["identity"]["turns"] = unknown
                payload["coverage"]["turns"] = unknown
                connection.execute(
                    "UPDATE document_versions SET profile_json = ?, coverage_json = ? WHERE id = ?",
                    (
                        json.dumps(payload, ensure_ascii=False),
                        json.dumps(payload["coverage"], ensure_ascii=False),
                        str(version_id),
                    ),
                )
            connection.commit()

    def test_runtime_recovers_pdf_turn_from_legacy_profiles_and_selects_morning(self) -> None:
        exam = self.add_fgv_exam(
            "morning", role="Auditor", shift="MANHÃ", variant=1, question_count=3
        )
        key = self.add_fgv_multi_grid_key(
            "both", role="Auditor", variants=(1,), morning_count=3, afternoon_count=2
        )
        self.erase_stored_turns(exam["document_version_id"], key["document_version_id"])

        with closing(self.store._connect()) as connection:
            context, decision = decide_runtime_association(
                connection, str(exam["document_version_id"])
            )

        self.assertEqual(decision.selected_version_id, key["document_version_id"])
        self.assertEqual(context.exam_profile.identity.turns.normalized_values, ("manhã",))
        self.assertEqual(context.candidates[0].question_interval, QuestionInterval(first=1, last=3))

    def test_runtime_selects_afternoon_block_before_interval_comparison(self) -> None:
        exam = self.add_fgv_exam(
            "afternoon", role="Auditor", shift="TARDE", variant=1, question_count=2
        )
        key = self.add_fgv_multi_grid_key(
            "both", role="Auditor", variants=(1,), morning_count=3, afternoon_count=2
        )
        self.erase_stored_turns(exam["document_version_id"], key["document_version_id"])

        with closing(self.store._connect()) as connection:
            context, decision = decide_runtime_association(
                connection, str(exam["document_version_id"])
            )

        self.assertEqual(decision.selected_version_id, key["document_version_id"])
        self.assertEqual(context.candidates[0].question_interval, QuestionInterval(first=1, last=2))

    def test_runtime_selects_types_one_through_four(self) -> None:
        key = self.add_fgv_multi_grid_key(
            "types", role="Auditor", variants=(1, 2, 3, 4),
            morning_count=2, afternoon_count=2,
        )
        exams = [
            self.add_fgv_exam(
                f"type-{variant}", role="Auditor", shift="MANHÃ",
                variant=variant, question_count=2,
            )
            for variant in range(1, 5)
        ]
        self.erase_stored_turns(
            key["document_version_id"],
            *(exam["document_version_id"] for exam in exams),
        )

        with closing(self.store._connect()) as connection:
            for variant, exam in enumerate(exams, start=1):
                with self.subTest(variant=variant):
                    _, decision = decide_runtime_association(
                        connection, str(exam["document_version_id"])
                    )
                    self.assertEqual(decision.selected_version_id, key["document_version_id"])

    def test_runtime_respects_official_auditor_morning_and_afternoon_intervals(self) -> None:
        key = self.add_fgv_multi_grid_key(
            "auditor", role="Auditor", variants=(1,), morning_count=80, afternoon_count=60
        )
        exams = (
            self.add_fgv_exam(
                "auditor-morning", role="Auditor", shift="MANHÃ",
                variant=1, question_count=80,
            ),
            self.add_fgv_exam(
                "auditor-afternoon", role="Auditor", shift="TARDE",
                variant=1, question_count=60,
            ),
        )
        self.erase_stored_turns(
            key["document_version_id"],
            *(exam["document_version_id"] for exam in exams),
        )

        with closing(self.store._connect()) as connection:
            intervals = [
                decide_runtime_association(connection, str(exam["document_version_id"]))[0]
                .candidates[0]
                .question_interval
                for exam in exams
            ]

        self.assertEqual(
            intervals,
            [QuestionInterval(first=1, last=80), QuestionInterval(first=1, last=60)],
        )

    def test_runtime_respects_official_analyst_interval(self) -> None:
        exam = self.add_fgv_exam(
            "analyst", role="Analista", shift="MANHÃ", variant=1, question_count=70
        )
        key = self.add_fgv_multi_grid_key(
            "analyst", role="Analista", variants=(1,), morning_count=70, afternoon_count=70
        )
        self.erase_stored_turns(exam["document_version_id"], key["document_version_id"])

        with closing(self.store._connect()) as connection:
            context, decision = decide_runtime_association(
                connection, str(exam["document_version_id"])
            )

        self.assertEqual(decision.selected_version_id, key["document_version_id"])
        self.assertEqual(
            context.candidates[0].question_interval,
            QuestionInterval(first=1, last=70),
        )

    def test_initial_unresolved_exam_enters_specific_review_queue_once(self) -> None:
        exam = self.add_document(
            "exam-no-turn.pdf",
            "Banca: FGV\nConcurso: Concurso teste\nAno: 2026\nCargo: Analista\n"
            "Etapa: Prova objetiva\nTipo: 1\nPROVA OBJETIVA",
            self.metadata("exam", turn=None),
        )
        key = self.add_fgv_multi_grid_key(
            "no-turn", role="Analista", variants=(1,), morning_count=2, afternoon_count=2
        )
        self.erase_stored_turns(key["document_version_id"])

        with closing(self.store._connect()) as connection:
            first = revalidate_answer_key_associations(
                connection, apply=True, run_id="initial-no-turn"
            )
            second = revalidate_answer_key_associations(
                connection, apply=True, run_id="initial-no-turn-repeat"
            )
            review = connection.execute(
                "SELECT status, reason FROM association_review_queue WHERE exam_version_id = ?",
                (exam["document_version_id"],),
            ).fetchone()
            review_count = connection.execute(
                "SELECT COUNT(*) FROM association_review_queue WHERE exam_version_id = ?",
                (exam["document_version_id"],),
            ).fetchone()[0]

        self.assertEqual(first.incomplete, 1)
        self.assertEqual(second.examined, 0)
        self.assertEqual(review_count, 1)
        self.assertEqual(review["status"], "pending")
        self.assertIn("turn", review["reason"].casefold())

    def test_initial_unlinked_non_fgv_exam_is_outside_revalidation_scope(self) -> None:
        exam = self.add_document(
            "exam-outra-banca.pdf",
            "Banca: Cebraspe\nConcurso: Concurso teste\nAno: 2026\n"
            "Cargo: Analista\nEtapa: Prova objetiva\nTurno: Manha\nTipo: 1",
            DesktopImportMetadata(
                document_type="exam",
                document_title="Prova de outra banca",
                board="Cebraspe",
                concurso="Concurso teste",
                organization="Orgao teste",
                year=2026,
                role="Analista",
                stage="Prova objetiva",
                turn="Manha",
                variant="Tipo 1",
            ),
        )

        with closing(self.store._connect()) as connection:
            report = revalidate_answer_key_associations(
                connection, apply=True, run_id="non-fgv-zero-link"
            )
            review_count = connection.execute(
                "SELECT COUNT(*) FROM association_review_queue WHERE exam_version_id = ?",
                (exam["document_version_id"],),
            ).fetchone()[0]
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM association_revalidation_audit "
                "WHERE exam_version_id = ?",
                (exam["document_version_id"],),
            ).fetchone()[0]

        self.assertEqual(report.examined, 0)
        self.assertEqual(review_count, 0)
        self.assertEqual(audit_count, 0)

    def test_non_fgv_exam_with_legacy_link_remains_in_migration_scope(self) -> None:
        exam, _ = self.add_exam("legacy-other-board")
        key = self.add_key("legacy-other-board")
        self.make_old_link(exam, key)
        cebraspe = known("board", "Cebraspe").model_dump(mode="json")

        with closing(self.store._connect()) as connection:
            for version_id in (
                exam["document_version_id"], key["document_version_id"]
            ):
                row = connection.execute(
                    "SELECT profile_json FROM document_versions WHERE id = ?",
                    (version_id,),
                ).fetchone()
                payload = json.loads(row["profile_json"])
                payload["identity"]["board"] = cebraspe
                connection.execute(
                    "UPDATE document_versions SET profile_json = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), version_id),
                )
            connection.commit()

            report = revalidate_answer_key_associations(connection)

        self.assertEqual(report.examined, 1)
        self.assertEqual(report.maintained, 1)

    def test_initial_safe_association_creates_one_link_and_is_idempotent(self) -> None:
        exam = self.add_fgv_exam(
            "initial-safe", role="Analista", shift="MANHÃ", variant=1, question_count=2
        )
        key = self.add_fgv_multi_grid_key(
            "initial-safe", role="Analista", variants=(1,),
            morning_count=2, afternoon_count=2,
        )
        self.erase_stored_turns(exam["document_version_id"], key["document_version_id"])

        with closing(self.store._connect()) as connection:
            first = revalidate_answer_key_associations(
                connection, apply=True, run_id="initial-safe"
            )
            second = revalidate_answer_key_associations(
                connection, apply=True, run_id="initial-safe-repeat"
            )
            active_links = connection.execute(
                "SELECT COUNT(*) FROM document_links WHERE exam_version_id = ? "
                "AND status = 'active'",
                (exam["document_version_id"],),
            ).fetchone()[0]

        self.assertEqual((first.changed, first.examined), (1, 1))
        self.assertEqual(second.examined, 0)
        self.assertEqual(active_links, 1)

    def test_completed_human_review_is_not_reopened_or_rewritten(self) -> None:
        exam = self.add_document(
            "exam-reviewed.pdf",
            "Banca: FGV\nConcurso: Concurso teste\nAno: 2026\nCargo: Analista\n"
            "Etapa: Prova objetiva\nTipo: 1",
            self.metadata("exam", turn=None),
        )
        with closing(self.store._connect()) as connection:
            connection.execute(
                "INSERT INTO association_review_queue "
                "(exam_version_id, status, reason, candidates_json, created_at, updated_at) "
                "VALUES (?, 'resolved', 'decisão humana preservada', '[]', ?, ?)",
                (
                    exam["document_version_id"],
                    "2026-08-25T00:00:00+00:00",
                    "2026-08-25T00:00:00+00:00",
                ),
            )
            connection.commit()

            report = revalidate_answer_key_associations(
                connection, apply=True, run_id="preserve-human-review"
            )
            review = connection.execute(
                "SELECT status, reason FROM association_review_queue WHERE exam_version_id = ?",
                (exam["document_version_id"],),
            ).fetchone()

        self.assertEqual(report.examined, 0)
        self.assertEqual((review["status"], review["reason"]), (
            "resolved", "decisão humana preservada"
        ))

    def test_obsolete_review_is_reactivated_when_case_is_still_unresolved(self) -> None:
        exam = self.add_document(
            "exam-obsolete-review.pdf",
            "Banca: FGV\nConcurso: Concurso teste\nAno: 2026\nCargo: Analista\n"
            "Etapa: Prova objetiva\nTipo: 1",
            self.metadata("exam", turn=None),
        )
        with closing(self.store._connect()) as connection:
            connection.execute(
                "INSERT INTO association_review_queue "
                "(exam_version_id, status, reason, candidates_json, created_at, updated_at) "
                "VALUES (?, 'obsolete', 'caso antigo', '[]', ?, ?)",
                (
                    exam["document_version_id"],
                    "2026-08-25T00:00:00+00:00",
                    "2026-08-25T00:00:00+00:00",
                ),
            )
            connection.commit()

            report = revalidate_answer_key_associations(
                connection, apply=True, run_id="reactivate-obsolete-review"
            )
            review = connection.execute(
                "SELECT status, reason FROM association_review_queue WHERE exam_version_id = ?",
                (exam["document_version_id"],),
            ).fetchone()

        self.assertEqual(report.examined, 1)
        self.assertEqual(review["status"], "pending")
        self.assertNotEqual(review["reason"], "caso antigo")

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

    def test_cli_dry_run_does_not_change_database_or_sidecars(self) -> None:
        self.add_fgv_exam(
            "cli-read-only", role="Analista", shift="MANHÃ",
            variant=1, question_count=2,
        )
        database = self.store.path
        report = self.root / "dry-run-report.json"

        def snapshot() -> dict[str, bytes]:
            return {
                path.name: path.read_bytes()
                for path in database.parent.glob(database.name + "*")
                if path.is_file()
            }

        before = snapshot()
        exit_code = main(
            [
                "revalidate-answer-keys",
                "--database", str(database),
                "--report", str(report),
            ]
        )
        after = snapshot()

        self.assertEqual(exit_code, 0)
        self.assertTrue(report.is_file())
        self.assertEqual(after, before)

    def test_cli_dry_run_rejects_missing_database_without_creating_it(self) -> None:
        database = self.root / "missing.sqlite3"

        exit_code = main(
            [
                "revalidate-answer-keys",
                "--database", str(database),
                "--report", str(self.root / "unused-report.json"),
            ]
        )

        self.assertEqual(exit_code, 1)
        self.assertFalse(database.exists())

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
            review = connection.execute(
                "SELECT status, reason FROM association_review_queue "
                "WHERE exam_version_id = ?",
                (exam["document_version_id"],),
            ).fetchone()
        self.assertEqual(report.invalidated, 1)
        self.assertEqual(report.sent_to_review, 1)
        self.assertEqual(review["status"], "pending")
        self.assertIn("role", review["reason"].casefold())
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
