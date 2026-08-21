from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen import canvas

from kad_collector.desktop_models import DesktopImportMetadata, QuestionClassification
from kad_collector.desktop_processor import DesktopProcessor, parse_question_pages
from kad_collector.desktop_store import DesktopStore
from kad_collector.document_contract import NormalizedDocument
from kad_collector.models import Alternative, QuestionRecord
from kad_collector.semantic_identity import (
    AnswerKeyCoverage,
    AssociationCandidate,
    ContentFingerprint,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    KnownDocumentVersion,
    SemanticEvidence,
    SemanticField,
)
from kad_collector.semantic_registry import claim_document_observation
from kad_collector.semantic_resolution import decide_document_version, select_answer_key


def profile(
    *, key: str | None = "identity", sha: str = "sha", conflict: bool = False
) -> DocumentSemanticProfile:
    known = SemanticField.from_evidence(
        "test", (SemanticEvidence.metadata("test", "x"),)
    )
    identity = ExamSemanticIdentity(
        board=known,
        concurso=known,
        organization=SemanticField.unknown("test"),
        year=known,
        roles=SemanticField.unknown("test"),
        stage=SemanticField.unknown("test"),
        turns=SemanticField.unknown("test"),
        variants=SemanticField.unknown("test"),
    )
    return DocumentSemanticProfile(
        identity=identity,
        identity_key=key,
        document_role="exam",
        coverage=AnswerKeyCoverage(
            roles=identity.roles,
            stage=identity.stage,
            turns=identity.turns,
            variants=identity.variants,
        ),
        content_fingerprint=ContentFingerprint(
            sha256=sha, page_sha256s=(), page_count=1, character_count=1
        ),
        has_conflict=conflict,
    )


class SemanticResolutionDecisionTests(unittest.TestCase):
    def _exam(self, **updates: object) -> DocumentSemanticProfile:
        base = profile()
        identity = base.identity.model_copy(update=updates)
        coverage = base.coverage.model_copy(update={
            "roles": identity.roles, "stage": identity.stage,
            "turns": identity.turns, "variants": identity.variants,
        })
        return base.model_copy(update={"identity": identity, "coverage": coverage})

    def _key(self, version_id: str = "key-1", **updates: object) -> AssociationCandidate:
        key = self._exam(**updates).model_copy(update={"document_role": "answer_key"})
        key = key.model_copy(update={"coverage": AnswerKeyCoverage(
            roles=key.identity.roles, stage=key.identity.stage,
            turns=key.identity.turns, variants=key.identity.variants,
        )})
        return AssociationCandidate(version_id=version_id, profile=key)

    def test_known_scope_conflicts_block_candidate(self) -> None:
        decision = select_answer_key(self._exam(year=SemanticField(
            status="known", normalized_values=(2026,), method="test", reason="test", confidence=1.0
        )), [self._key(year=SemanticField(
            status="known", normalized_values=(2025,), method="test", reason="test", confidence=1.0
        ))])
        self.assertIsNone(decision.selected_version_id)
        self.assertEqual(decision.outcome, "conflict")

    def test_unknown_is_not_positive_evidence(self) -> None:
        decision = select_answer_key(
            self._exam(roles=SemanticField(
                status="known", normalized_values=("auditor",), method="test",
                reason="test", confidence=1.0,
            )),
            [self._key(roles=SemanticField.unknown("x"))],
        )
        self.assertIsNone(decision.selected_version_id)
        self.assertEqual(decision.outcome, "insufficient_evidence")

    def test_title_only_candidate_is_insufficient(self) -> None:
        weak = SemanticField(
            status="known", normalized_values=("x",), method="title",
            reason="title", confidence=1.0,
            evidence=(SemanticEvidence.title("title", "x"),),
        )
        candidate = self._key(board=weak, concurso=weak, year=weak)
        decision = select_answer_key(self._exam(), [candidate])
        self.assertIsNone(decision.selected_version_id)
        self.assertEqual(decision.outcome, "insufficient_evidence")

    def test_one_key_can_cover_multiple_roles(self) -> None:
        role = SemanticField(
            status="known", normalized_values=("analista", "auditor"),
            raw_values=("Auditor", "Analista"),
            evidence=(SemanticEvidence.metadata("roles", "Auditor"),
                      SemanticEvidence.metadata("roles", "Analista")),
            method="test", reason="coverage", confidence=1.0,
        )
        decision = select_answer_key(self._exam(roles=SemanticField.from_evidence(
            "roles", (SemanticEvidence.metadata("roles", "Analista"),)
        )), [self._key(roles=role)])
        self.assertEqual(decision.selected_version_id, "key-1")

    def test_types_one_to_four_do_not_mix_answers(self) -> None:
        candidates = []
        for number in range(1, 5):
            variant = SemanticField.from_evidence(
                "variants", (SemanticEvidence.metadata("variant", f"Tipo {number}"),)
            )
            candidates.append(self._key(version_id=f"key-{number}", variants=variant))
        for number in range(1, 5):
            variant = SemanticField.from_evidence(
                "variants", (SemanticEvidence.metadata("variant", f"Tipo {number}"),)
            )
            decision = select_answer_key(self._exam(variants=variant), candidates)
            self.assertEqual(decision.selected_version_id, f"key-{number}")

    def test_title_evidence_adds_at_most_two_points(self) -> None:
        weak = SemanticField(
            status="known", normalized_values=("tipo 1",), raw_values=("Tipo 1",),
            evidence=(SemanticEvidence.title("title", "Tipo 1"),),
            method="title", reason="title", confidence=1.0,
        )
        baseline = select_answer_key(self._exam(), [self._key()])
        decision = select_answer_key(
            self._exam(variants=weak), [self._key(variants=weak)]
        )
        self.assertLessEqual(
            decision.assessments[0].score - baseline.assessments[0].score, 2
        )

    def test_missing_without_candidates(self) -> None:
        self.assertEqual(select_answer_key(self._exam(), []).outcome, "missing")

    def test_known_scope_conflicts_cover_organization_role_stage_turn_and_variant(self) -> None:
        fields = ("organization", "roles", "stage", "turns", "variants")
        for name in fields:
            with self.subTest(name=name):
                exam_value = SemanticField.from_evidence(
                    name, (SemanticEvidence.metadata(name, "expected"),)
                )
                wrong = SemanticField.from_evidence(
                    name, (SemanticEvidence.metadata(name, "different"),)
                )
                decision = select_answer_key(self._exam(**{name: exam_value}), [
                    self._key(**{name: wrong})
                ])
                self.assertEqual(decision.outcome, "conflict")

    def test_two_definitives_equivalent_remain_ambiguous(self) -> None:
        first = self._key("a").profile.model_copy(update={"answer_key_state": "definitive"})
        second = self._key("b").profile.model_copy(update={"answer_key_state": "definitive"})
        decision = select_answer_key(self._exam(), [
            AssociationCandidate(version_id="a", profile=first),
            AssociationCandidate(version_id="b", profile=second),
        ])
        self.assertEqual(decision.outcome, "ambiguous")

    def test_definitive_without_predecessor_does_not_break_tie(self) -> None:
        preliminary = self._key("pre").profile.model_copy(
            update={"answer_key_state": "preliminary"}
        )
        definitive = self._key("def").profile.model_copy(update={"answer_key_state": "definitive"})
        decision = select_answer_key(self._exam(), [
            AssociationCandidate(version_id="pre", profile=preliminary),
            AssociationCandidate(version_id="def", profile=definitive),
        ])
        self.assertEqual(decision.outcome, "ambiguous")

    def test_linked_definitive_wins_equal_semantics_despite_preliminary_title_bonus(self) -> None:
        weak_variant = SemanticField(
            status="known", normalized_values=("tipo 1",), raw_values=("Tipo 1",),
            evidence=(SemanticEvidence.title("title", "Tipo 1"),),
            method="title", reason="title", confidence=1.0,
        )
        exam = self._exam(variants=weak_variant)
        preliminary = self._key("pre", variants=weak_variant).profile.model_copy(
            update={"answer_key_state": "preliminary"}
        )
        definitive_variant = weak_variant.model_copy(update={"evidence": ()})
        definitive = self._key("def", variants=definitive_variant).profile.model_copy(
            update={"answer_key_state": "definitive"}
        )
        decision = select_answer_key(exam, [
            AssociationCandidate(version_id="pre", profile=preliminary),
            AssociationCandidate(
                version_id="def", profile=definitive, predecessor_version_id="pre"
            ),
        ])
        self.assertEqual(decision.selected_version_id, "def")

    def test_definitive_does_not_override_more_compatible_preliminary(self) -> None:
        organization = SemanticField.from_evidence(
            "organization", (SemanticEvidence.metadata("organization", "Org"),)
        )
        exam = self._exam(organization=organization)
        preliminary = self._key("pre", organization=organization).profile.model_copy(
            update={"answer_key_state": "preliminary"}
        )
        definitive = self._key("def").profile.model_copy(
            update={"answer_key_state": "definitive"}
        )
        decision = select_answer_key(exam, [
            AssociationCandidate(version_id="pre", profile=preliminary),
            AssociationCandidate(
                version_id="def", profile=definitive, predecessor_version_id="pre"
            ),
        ])
        self.assertEqual(decision.selected_version_id, "pre")

    def test_third_equivalent_candidate_keeps_successor_pair_ambiguous(self) -> None:
        preliminary = self._key("pre").profile.model_copy(
            update={"answer_key_state": "preliminary"}
        )
        definitive = self._key("def").profile.model_copy(
            update={"answer_key_state": "definitive"}
        )
        third = self._key("third").profile
        decision = select_answer_key(self._exam(), [
            AssociationCandidate(version_id="pre", profile=preliminary),
            AssociationCandidate(
                version_id="def", profile=definitive, predecessor_version_id="pre"
            ),
            AssociationCandidate(version_id="third", profile=third),
        ])
        self.assertIsNone(decision.selected_version_id)
        self.assertEqual(decision.outcome, "ambiguous")

    def test_minimum_margin_six_is_ambiguous_and_eight_selects(self) -> None:
        weak_variant = SemanticField(
            status="known", normalized_values=("tipo 1",), raw_values=("Tipo 1",),
            evidence=(SemanticEvidence.title("title", "Tipo 1"),),
            method="title", reason="title", confidence=1.0,
        )
        organization = SemanticField.from_evidence(
            "organization", (SemanticEvidence.metadata("organization", "Org"),)
        )
        exam = self._exam(variants=weak_variant, organization=organization)
        known_variant = SemanticField(
            status="known", normalized_values=("tipo 1",), method="test",
            reason="known", confidence=1.0,
        )
        better = self._key("better", organization=organization, variants=known_variant)
        weaker = self._key("weaker", variants=weak_variant)
        ambiguous = select_answer_key(exam, [better, weaker])
        self.assertEqual(ambiguous.outcome, "ambiguous")
        clear = select_answer_key(
            self._exam(organization=organization), [better, self._key("weaker")]
        )
        self.assertEqual(clear.selected_version_id, "better")
        self.assertEqual(clear.achieved_margin, 8)

    def test_candidate_assessments_are_complete_and_stably_ordered(self) -> None:
        selected = self._key("z")
        conflicting = self._key("a", year=SemanticField(
            status="known", normalized_values=(2025,), method="test", reason="wrong",
            confidence=1.0, evidence=(SemanticEvidence.metadata("year", 2025),),
        ))
        insufficient = self._key("m", roles=SemanticField.unknown("missing"))
        first = select_answer_key(self._exam(), [insufficient, conflicting, selected])
        second = select_answer_key(self._exam(), [selected, insufficient, conflicting])
        self.assertEqual(
            [item.version_id for item in first.assessments],
            [item.version_id for item in second.assessments],
        )
        self.assertEqual({item.version_id for item in first.assessments}, {"a", "m", "z"})
        self.assertTrue(all(item.reasons or item.conflicts for item in first.assessments))

    def test_equal_candidates_are_ambiguous(self) -> None:
        decision = select_answer_key(self._exam(), [self._key("a"), self._key("b")])
        self.assertIsNone(decision.selected_version_id)
        self.assertEqual(decision.outcome, "ambiguous")
    def test_decision_has_the_five_expected_outcomes(self) -> None:
        current = KnownDocumentVersion(
            version_id="v1",
            identity_key="identity",
            document_role="exam",
            content_sha256="sha-1",
            version_number=1,
        )
        self.assertEqual(decide_document_version(profile(key=None), ()).outcome, "uncertain")
        self.assertEqual(
            decide_document_version(profile(sha="sha-1"), (current,)).outcome, "republication"
        )
        self.assertEqual(
            decide_document_version(profile(sha="sha-2"), (current,)).outcome, "new_version"
        )
        self.assertEqual(
            decide_document_version(profile(key="other"), (current,)).outcome, "new_identity"
        )
        self.assertEqual(
            decide_document_version(profile(conflict=True), (current,)).outcome, "uncertain"
        )

    def test_changed_content_can_add_or_remove_questions_without_changing_outcome(self) -> None:
        current = KnownDocumentVersion(
            version_id="v1",
            identity_key="identity",
            document_role="exam",
            content_sha256="sha-1",
            version_number=1,
        )
        for sha in ("added-question", "removed-question"):
            self.assertEqual(
                decide_document_version(profile(sha=sha), (current,)).outcome, "new_version"
            )


class SemanticWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = DesktopStore(Path(self.directory.name) / "collector.sqlite3")
        self.now = "2026-08-21T00:00:00+00:00"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def add_document(
        self,
        document_id: str,
        *,
        binary: bytes,
        text: str,
        metadata: dict[str, str | int],
        declared_type: str = "exam",
        title: str = "Prova 2026",
    ) -> None:
        path = Path(self.directory.name) / f"{document_id}.pdf"
        path.write_bytes(binary)
        normalized = NormalizedDocument(
            local_path=str(path),
            sha256=hashlib.sha256(binary).hexdigest(),
            size_bytes=len(binary),
            declared_type=declared_type,
            title=title,
            entry_method="direct_import",
            metadata=metadata,
        )
        with closing(self.store._connect()) as connection:
            connection.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, classifier_provider) "
                "VALUES (?, ?, ?, 'queued', 'local')",
                (f"job-{document_id}", self.now, self.now),
            )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            claim = claim_document_observation(connection, normalized, self.now)
            connection.execute(
                "INSERT INTO documents (id, job_id, local_path, filename, sha256, size_bytes, "
                "metadata_json, normalized_json, warnings_json, created_at, updated_at, "
                "observation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)",
                (
                    document_id,
                    f"job-{document_id}",
                    str(path),
                    path.name,
                    normalized.sha256,
                    normalized.size_bytes,
                    json.dumps(metadata),
                    normalized.model_dump_json(),
                    self.now,
                    self.now,
                    claim.observation_id,
                ),
            )
            connection.execute(
                "UPDATE document_observations SET document_id = ? WHERE id = ?",
                (document_id, claim.observation_id),
            )
            connection.commit()
        self.store.save_page(document_id, 1, text, status="text")

    def resolve(self, document_id: str):
        return self.store.resolve_extracted_document(document_id)

    @staticmethod
    def corrected_metadata(**changes: object) -> DesktopImportMetadata:
        values: dict[str, object] = {
            "document_type": "exam",
            "board": "Banca",
            "concurso": "Concurso",
            "year": 2026,
        }
        values.update(changes)
        return DesktopImportMetadata.model_validate(values)

    def semantic_state(self) -> dict[str, list[dict[str, object]]]:
        tables = (
            "documents",
            "semantic_identities",
            "document_versions",
            "document_observations",
            "document_observation_origins",
            "document_links",
            "document_identity_events",
            "questions",
            "audit_log",
        )
        with closing(self.store._connect()) as connection:
            return {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"  # noqa: S608
                    ).fetchall()
                ]
                for table in tables
            }

    def test_manual_identity_correction_is_audited_and_preserves_question_decision(self) -> None:
        self.add_document(
            "exam",
            binary=b"manual-correction",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nQuestão 1",
            metadata={"board": "Banca", "concurso": "Concurso", "year": 2026},
        )
        original = self.resolve("exam")
        question_id = self.store.save_question(
            "exam", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            question_id, "approved", actor="revisora", notes="Conteúdo conferido."
        )

        result = self.store.correct_document_identity(
            "exam",
            self.corrected_metadata(
                role="Auditor", stage="Segunda fase", turn="Manhã", variant="Tipo 2"
            ),
            actor="coordenador",
        )

        self.assertEqual(result.document_version_id, original.document_version_id)
        self.assertEqual(result.version_number, original.version_number)
        self.assertIsNotNone(result.profile)
        assert result.profile is not None
        self.assertEqual(result.profile.identity.roles.normalized_values, ("auditor",))
        self.assertEqual(result.profile.identity.stage.normalized_values, ("segunda fase",))
        self.assertEqual(result.profile.identity.turns.normalized_values, ("manhã",))
        self.assertEqual(result.profile.identity.variants.normalized_values, ("tipo 2",))
        self.assertEqual(result.profile.content_fingerprint, original.profile.content_fingerprint)
        question = self.store.question(question_id)
        self.assertEqual(question["status"], "approved")
        self.assertEqual(question["reviewer"], "revisora")
        corrected = [
            event for event in self.store.identity_events("exam")
            if event["action"] == "identity_corrected"
        ]
        self.assertEqual(len(corrected), 1)
        self.assertEqual(corrected[0]["actor"], "coordenador")
        self.assertEqual(corrected[0]["payload"]["oldIdentityKey"], original.profile.identity_key)
        self.assertEqual(corrected[0]["payload"]["newIdentityKey"], result.profile.identity_key)
        self.assertEqual(corrected[0]["payload"]["algorithmVersion"], result.algorithm_version)
        self.assertTrue(corrected[0]["payload"]["evidence"]["roles"])

        before_repeat = self.semantic_state()
        repeated = self.store.correct_document_identity(
            "exam",
            self.corrected_metadata(
                role="Auditor", stage="Segunda fase", turn="Manhã", variant="Tipo 2"
            ),
            actor="coordenador",
        )
        self.assertEqual(repeated.profile, result.profile)
        self.assertEqual(self.semantic_state(), before_repeat)

    def test_conflicting_manual_merge_rolls_back(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        text = "Questão 1\nA) Azul\nB) Verde"
        self.add_document("first", binary=b"first-collision", text=text, metadata=metadata)
        self.add_document(
            "second",
            binary=b"second-collision",
            text=text,
            metadata={"board": "Outra", "concurso": "Concurso", "year": 2026},
        )
        first = self.resolve("first")
        second = self.resolve("second")
        self.assertNotEqual(first.document_version_id, second.document_version_id)
        before = self.semantic_state()

        with self.assertRaisesRegex(ValueError, "correção colide com versão existente"):
            self.store.correct_document_identity(
                "second", self.corrected_metadata(), actor="coordenador"
            )

        self.assertEqual(self.semantic_state(), before)

    def test_invalid_manual_correction_rolls_back_every_observable_table(self) -> None:
        self.add_document(
            "exam",
            binary=b"invalid-correction",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nQuestão 1",
            metadata={"board": "Banca", "concurso": "Concurso", "year": 2026},
        )
        self.resolve("exam")
        self.store.save_question("exam", self.lineage_question(1), QuestionClassification())
        before = self.semantic_state()

        for actor in ("", "   "):
            with self.subTest(actor=actor), self.assertRaisesRegex(ValueError, "ator"):
                self.store.correct_document_identity(
                    "exam", self.corrected_metadata(role="Auditor"), actor=actor
                )
            self.assertEqual(self.semantic_state(), before)

        with closing(self.store._connect()) as connection:
            normalized = NormalizedDocument.model_validate_json(
                connection.execute(
                    "SELECT normalized_json FROM documents WHERE id = 'exam'"
                ).fetchone()["normalized_json"]
            ).model_copy(update={"metadata": {}})
            connection.execute(
                "UPDATE documents SET normalized_json = ? WHERE id = 'exam'",
                (normalized.model_dump_json(),),
            )
            connection.execute(
                "UPDATE pages SET text = 'Questão 1' WHERE document_id = 'exam'"
            )
            connection.commit()
        insufficient_before = self.semantic_state()
        with self.assertRaisesRegex(ValueError, "identidade semântica insuficiente"):
            self.store.correct_document_identity(
                "exam",
                DesktopImportMetadata(document_type="exam", role="Auditor"),
                actor="coordenador",
            )
        self.assertEqual(self.semantic_state(), insufficient_before)

        self.store.save_page(
            "exam", 1,
            "Banca: Banca\nConcurso: Concurso\nAno: 2025\nAno: 2026\nQuestão 1",
            status="text",
        )
        with closing(self.store._connect()) as connection:
            normalized = NormalizedDocument.model_validate_json(
                connection.execute(
                    "SELECT normalized_json FROM documents WHERE id = 'exam'"
                ).fetchone()["normalized_json"]
            ).model_copy(
                update={"metadata": {"board": "Banca", "concurso": "Concurso", "year": 2026}}
            )
            connection.execute(
                "UPDATE documents SET normalized_json = ? WHERE id = 'exam'",
                (normalized.model_dump_json(),),
            )
            connection.commit()
        conflicted_before = self.semantic_state()
        with self.assertRaisesRegex(ValueError, "perfil semântico conflitante"):
            self.store.correct_document_identity(
                "exam",
                DesktopImportMetadata(document_type="exam", role="Auditor"),
                actor="coordenador",
            )
        self.assertEqual(self.semantic_state(), conflicted_before)

    def test_correction_of_shared_republication_updates_one_operational_version(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        text = "Banca: Banca\nConcurso: Concurso\nAno: 2026\nQuestão 1"
        self.add_document("first", binary=b"shared-one", text=text, metadata=metadata)
        self.add_document("second", binary=b"shared-two", text=text, metadata=metadata)
        first, second = self.resolve("first"), self.resolve("second")
        self.assertEqual(first.document_version_id, second.document_version_id)
        with closing(self.store._connect()) as connection:
            origins_before = connection.execute(
                "SELECT normalized_json FROM document_observation_origins ORDER BY origin_key"
            ).fetchall()

        corrected = self.store.correct_document_identity(
            "second", self.corrected_metadata(role="Auditor"), actor="coordenador"
        )

        self.assertEqual(corrected.document_version_id, first.document_version_id)
        first_view = self.store.semantic_document_view("first")
        second_view = self.store.semantic_document_view("second")
        self.assertEqual(first_view["profile"], second_view["profile"])
        self.assertEqual(
            first_view["profile"]["identity"]["roles"]["normalized_values"], ["auditor"]
        )
        with closing(self.store._connect()) as connection:
            observations = connection.execute(
                "SELECT document_version_id FROM document_observations ORDER BY id"
            ).fetchall()
            origins_after = connection.execute(
                "SELECT normalized_json FROM document_observation_origins ORDER BY origin_key"
            ).fetchall()
        self.assertEqual(
            {row["document_version_id"] for row in observations},
            {first.document_version_id},
        )
        self.assertEqual(
            [row["normalized_json"] for row in origins_after],
            [row["normalized_json"] for row in origins_before],
        )

    def test_correction_repositions_same_operational_version_in_target_identity_lineage(
        self,
    ) -> None:
        original_metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"lineage-first",
            text="Questão 1\nA) Azul\nB) Verde",
            metadata=original_metadata,
        )
        self.add_document(
            "second",
            binary=b"lineage-second",
            text="Questão 1 alterada\nA) Azul\nB) Verde",
            metadata=original_metadata,
        )
        self.resolve("first")
        successor = self.resolve("second")
        self.assertEqual(successor.version_number, 2)
        self.assertIsNotNone(successor.predecessor_version_id)

        corrected = self.store.correct_document_identity(
            "second",
            DesktopImportMetadata(
                document_type="exam",
                board="Outra Banca",
                concurso="Concurso",
                year=2026,
            ),
            actor="coordenador",
        )

        self.assertEqual(corrected.document_version_id, successor.document_version_id)
        self.assertEqual(corrected.version_number, 1)
        self.assertIsNone(corrected.predecessor_version_id)

    def test_moving_non_terminal_version_rebuilds_old_identity_lineage(self) -> None:
        def add_chain(prefix: str, concurso: str) -> list[object]:
            metadata = {"board": "Banca", "concurso": concurso, "year": 2026}
            results = []
            for number in (1, 2, 3):
                document_id = f"{prefix}-{number}"
                self.add_document(
                    document_id,
                    binary=f"{prefix}-binary-{number}".encode(),
                    text=f"Questão {number}\nA) Azul\nB) Verde",
                    metadata=metadata,
                )
                results.append(self.resolve(document_id))
            return results

        first_chain = add_chain("first-chain", "Concurso Primeira")
        self.store.correct_document_identity(
            "first-chain-1",
            DesktopImportMetadata(
                document_type="exam",
                board="Outra Banca",
                concurso="Concurso Primeira",
                year=2026,
            ),
            actor="coordenador",
        )
        with closing(self.store._connect()) as connection:
            remaining = connection.execute(
                "SELECT id, version_number, predecessor_version_id FROM document_versions "
                "WHERE id IN (?, ?) ORDER BY version_number",
                (
                    first_chain[1].document_version_id,
                    first_chain[2].document_version_id,
                ),
            ).fetchall()
        self.assertEqual(
            [
                (row["id"], row["version_number"], row["predecessor_version_id"])
                for row in remaining
            ],
            [
                (first_chain[1].document_version_id, 1, None),
                (
                    first_chain[2].document_version_id,
                    2,
                    first_chain[1].document_version_id,
                ),
            ],
        )

        middle_chain = add_chain("middle-chain", "Concurso Intermediária")
        self.store.correct_document_identity(
            "middle-chain-2",
            DesktopImportMetadata(
                document_type="exam",
                board="Terceira Banca",
                concurso="Concurso Intermediária",
                year=2026,
            ),
            actor="coordenador",
        )
        with closing(self.store._connect()) as connection:
            remaining = connection.execute(
                "SELECT id, version_number, predecessor_version_id FROM document_versions "
                "WHERE id IN (?, ?) ORDER BY version_number",
                (
                    middle_chain[0].document_version_id,
                    middle_chain[2].document_version_id,
                ),
            ).fetchall()
        self.assertEqual(
            [
                (row["id"], row["version_number"], row["predecessor_version_id"])
                for row in remaining
            ],
            [
                (middle_chain[0].document_version_id, 1, None),
                (
                    middle_chain[2].document_version_id,
                    2,
                    middle_chain[0].document_version_id,
                ),
            ],
        )

    def test_exam_correction_switches_key_and_invalidates_only_changed_official_answers(
        self,
    ) -> None:
        core = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "exam",
            binary=b"exam-switch",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista",
            metadata={**core, "role": "Analista"},
            declared_type="exam",
        )
        self.add_document(
            "analyst-key",
            binary=b"analyst-key",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista\n1 - B\n2 - B",
            metadata={**core, "role": "Analista"},
            declared_type="answer_key",
            title="Gabarito Analista",
        )
        self.add_document(
            "auditor-key",
            binary=b"auditor-key",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Auditor\n1 - C\n2 - B",
            metadata={**core, "role": "Auditor"},
            declared_type="answer_key",
            title="Gabarito Auditor",
        )
        exam = self.resolve("exam")
        analyst_key = self.resolve("analyst-key")
        auditor_key = self.resolve("auditor-key")
        question_ids = {
            number: self.store.save_question(
                "exam", self.lineage_question(number), QuestionClassification()
            )
            for number in (1, 2)
        }
        processor = DesktopProcessor(self.store)
        try:
            self.assertEqual(processor._reconcile_answer_key(analyst_key.document_version_id), 1)
        finally:
            processor._executor.shutdown(wait=True)
        for question_id in question_ids.values():
            self.store.decide_question(
                question_id, "approved", actor="revisora", notes="Resposta conferida."
            )

        self.store.correct_document_identity(
            "exam", self.corrected_metadata(role="Auditor"), actor="coordenador"
        )

        first = self.store.question(question_ids[1])
        second = self.store.question(question_ids[2])
        self.assertEqual((first["question"]["correct_answer"], first["status"]), ("C", "pending"))
        self.assertIsNone(first["reviewer"])
        self.assertEqual(
            (second["question"]["correct_answer"], second["status"]),
            ("B", "approved"),
        )
        self.assertEqual(second["reviewer"], "revisora")
        with closing(self.store._connect()) as connection:
            links = connection.execute(
                "SELECT answer_key_version_id, status FROM document_links "
                "WHERE exam_version_id = ? ORDER BY created_at, id",
                (exam.document_version_id,),
            ).fetchall()
        self.assertEqual(
            [(row["answer_key_version_id"], row["status"]) for row in links],
            [
                (analyst_key.document_version_id, "rejected"),
                (auditor_key.document_version_id, "active"),
            ],
        )
        self.assertEqual(
            [entry["action"] for entry in self.store.audit_log(question_ids[1])].count(
                "decision_invalidated"
            ),
            1,
        )
        self.assertNotIn(
            "decision_invalidated",
            [entry["action"] for entry in self.store.audit_log(question_ids[2])],
        )

    def test_scope_correction_reapplies_a_different_grid_from_the_same_answer_key(self) -> None:
        core = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "exam-grid",
            binary=b"exam-grid",
            text=(
                "Banca: Banca\nConcurso: Concurso\nAno: 2026\n"
                "Cargo: Analista\nTipo: 1"
            ),
            metadata={**core, "role": "Analista", "variant": "Tipo 1"},
        )
        self.add_document(
            "shared-grid-key",
            binary=b"shared-grid-key",
            text=(
                "Banca: Banca\nConcurso: Concurso\nAno: 2026\n"
                "Cargos: Analista, Auditor\nTipos: 1\n"
                "Analista - Tipo 1\n1\nA\n"
                "Auditor - Tipo 1\n1\nC"
            ),
            metadata=core,
            declared_type="answer_key",
            title="Gabarito multicargo",
        )
        self.resolve("exam-grid")
        key = self.resolve("shared-grid-key")
        question_id = self.store.save_question(
            "exam-grid",
            self.lineage_question(1, correct_answer="A"),
            QuestionClassification(),
        )
        processor = DesktopProcessor(self.store)
        try:
            self.assertEqual(processor._reconcile_answer_key(key.document_version_id), 1)
        finally:
            processor._executor.shutdown(wait=True)
        self.store.decide_question(
            question_id, "approved", actor="revisora", notes="Grade Analista conferida."
        )

        self.store.correct_document_identity(
            "exam-grid",
            self.corrected_metadata(role="Auditor", variant="Tipo 1"),
            actor="coordenador",
        )

        corrected = self.store.question(question_id)
        self.assertEqual(corrected["question"]["correct_answer"], "C")
        self.assertEqual(corrected["status"], "pending")
        self.assertIsNone(corrected["reviewer"])
        with closing(self.store._connect()) as connection:
            links = connection.execute(
                "SELECT COUNT(*) FROM document_links WHERE status = 'active' "
                "AND answer_key_version_id = ?",
                (key.document_version_id,),
            ).fetchone()[0]
        self.assertEqual(links, 1)
        before_repeat = self.semantic_state()
        self.store.correct_document_identity(
            "exam-grid",
            self.corrected_metadata(role="Auditor", variant="Tipo 1"),
            actor="coordenador",
        )
        self.assertEqual(self.semantic_state(), before_repeat)

    def test_same_key_with_recalculated_evidence_persists_auditable_successor_link(self) -> None:
        core = {"board": "Banca", "concurso": "Concurso", "year": 2026, "role": "Analista"}
        self.add_document(
            "exam-score",
            binary=b"exam-score",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista",
            metadata=core,
        )
        self.add_document(
            "key-score",
            binary=b"key-score",
            text=(
                "Banca: Banca\nConcurso: Concurso\nAno: 2026\n"
                "Cargo: Analista\nÓrgão: Secretaria\n1 - B"
            ),
            metadata={**core, "organization": "Secretaria"},
            declared_type="answer_key",
            title="Gabarito",
        )
        exam = self.resolve("exam-score")
        key = self.resolve("key-score")
        self.store.save_question(
            "exam-score", self.lineage_question(1), QuestionClassification()
        )
        processor = DesktopProcessor(self.store)
        try:
            self.assertEqual(processor._reconcile_answer_key(key.document_version_id), 1)
        finally:
            processor._executor.shutdown(wait=True)

        with closing(self.store._connect()) as connection:
            before_link = connection.execute(
                "SELECT id, decision_json FROM document_links WHERE exam_version_id = ? "
                "AND status = 'active'",
                (exam.document_version_id,),
            ).fetchone()
        before_decision = json.loads(before_link["decision_json"])
        before_score = before_decision["assessments"][0]["score"]

        self.store.correct_document_identity(
            "exam-score",
            self.corrected_metadata(role="Analista", organization="Secretaria"),
            actor="coordenador",
        )

        with closing(self.store._connect()) as connection:
            links = connection.execute(
                "SELECT id, status, predecessor_link_id, decision_json FROM document_links "
                "WHERE exam_version_id = ? ORDER BY created_at, id",
                (exam.document_version_id,),
            ).fetchall()
        self.assertEqual([row["status"] for row in links], ["rejected", "active"])
        active = links[1]
        self.assertEqual(active["predecessor_link_id"], before_link["id"])
        after_score = json.loads(active["decision_json"])["assessments"][0]["score"]
        self.assertGreater(after_score, before_score)

        before_repeat = self.semantic_state()
        self.store.correct_document_identity(
            "exam-score",
            self.corrected_metadata(role="Analista", organization="Secretaria"),
            actor="coordenador",
        )
        self.assertEqual(self.semantic_state(), before_repeat)

    def test_changing_exam_to_answer_key_rejects_its_outgoing_active_link(self) -> None:
        core = {"board": "Banca", "concurso": "Concurso", "year": 2026, "role": "Analista"}
        self.add_document(
            "exam-role-change",
            binary=b"exam-role-change",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista",
            metadata=core,
        )
        self.add_document(
            "key-role-change",
            binary=b"key-role-change",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista\n1 - B",
            metadata=core,
            declared_type="answer_key",
            title="Gabarito",
        )
        exam = self.resolve("exam-role-change")
        key = self.resolve("key-role-change")
        self.store.save_question(
            "exam-role-change", self.lineage_question(1), QuestionClassification()
        )
        processor = DesktopProcessor(self.store)
        try:
            self.assertEqual(processor._reconcile_answer_key(key.document_version_id), 1)
        finally:
            processor._executor.shutdown(wait=True)

        self.store.correct_document_identity(
            "exam-role-change",
            DesktopImportMetadata(
                document_type="answer_key",
                board="Banca",
                concurso="Concurso",
                year=2026,
                role="Analista",
            ),
            actor="coordenador",
        )

        with closing(self.store._connect()) as connection:
            outgoing = connection.execute(
                "SELECT status FROM document_links WHERE exam_version_id = ?",
                (exam.document_version_id,),
            ).fetchall()
        self.assertEqual([row["status"] for row in outgoing], ["rejected"])

    def test_correction_without_sufficient_association_rejects_old_link_without_inventing_answers(
        self,
    ) -> None:
        core = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "exam",
            binary=b"exam-no-candidate",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista",
            metadata={**core, "role": "Analista"},
        )
        self.add_document(
            "key",
            binary=b"key-no-candidate",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista\n1 - B",
            metadata={**core, "role": "Analista"},
            declared_type="answer_key",
            title="Gabarito Analista",
        )
        exam, key = self.resolve("exam"), self.resolve("key")
        first_id = self.store.save_question(
            "exam", self.lineage_question(1), QuestionClassification()
        )
        missing_id = self.store.save_question(
            "exam",
            self.lineage_question(2, answer_status="missing", correct_answer=None),
            QuestionClassification(),
        )
        processor = DesktopProcessor(self.store)
        try:
            self.assertEqual(processor._reconcile_answer_key(key.document_version_id), 1)
        finally:
            processor._executor.shutdown(wait=True)

        self.store.correct_document_identity(
            "exam", self.corrected_metadata(role="Revisor"), actor="coordenador"
        )

        with closing(self.store._connect()) as connection:
            active = connection.execute(
                "SELECT COUNT(*) FROM document_links WHERE exam_version_id = ? "
                "AND status = 'active'",
                (exam.document_version_id,),
            ).fetchone()[0]
            rejected = connection.execute(
                "SELECT COUNT(*) FROM document_links WHERE exam_version_id = ? "
                "AND status = 'rejected'",
                (exam.document_version_id,),
            ).fetchone()[0]
        self.assertEqual((active, rejected), (0, 1))
        self.assertEqual(self.store.question(first_id)["question"]["correct_answer"], "B")
        missing = self.store.question(missing_id)["question"]
        self.assertEqual((missing["answer_status"], missing["correct_answer"]), ("missing", None))

    def test_concurrent_corrections_leave_one_coherent_version_and_auditable_history(self) -> None:
        self.add_document(
            "exam",
            binary=b"concurrent-correction",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nQuestão 1",
            metadata={"board": "Banca", "concurso": "Concurso", "year": 2026},
        )
        original = self.resolve("exam")
        errors: list[Exception] = []

        def correct(role: str, actor: str) -> None:
            try:
                self.store.correct_document_identity(
                    "exam", self.corrected_metadata(role=role), actor=actor
                )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [
            threading.Thread(target=correct, args=("Auditor", "alice")),
            threading.Thread(target=correct, args=("Analista", "bruno")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        view = self.store.semantic_document_view("exam")
        self.assertEqual(view["documentVersionId"], original.document_version_id)
        self.assertIn(
            view["profile"]["identity"]["roles"]["normalized_values"],
            (["auditor"], ["analista"]),
        )
        corrected = [
            event for event in self.store.identity_events("exam")
            if event["action"] == "identity_corrected"
        ]
        self.assertEqual({event["actor"] for event in corrected}, {"alice", "bruno"})
        with closing(self.store._connect()) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM document_versions WHERE id = ?",
                    (original.document_version_id,),
                ).fetchone()[0],
                1,
            )
            self.assertLessEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM document_links WHERE status = 'active'"
                ).fetchone()[0],
                1,
            )

    def test_answer_key_correction_reevaluates_all_and_only_old_and_new_scopes(self) -> None:
        core = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        for role in ("Analista", "Auditor", "Técnico"):
            document_id = role.casefold().replace("é", "e")
            self.add_document(
                document_id,
                binary=f"exam-{document_id}".encode(),
                text=(
                    "Banca: Banca\nConcurso: Concurso\nAno: 2026\n"
                    f"Cargo: {role}"
                ),
                metadata={**core, "role": role},
            )
            self.resolve(document_id)
            self.store.save_question(
                document_id,
                self.lineage_question(1, answer_status="missing", correct_answer=None),
                QuestionClassification(),
            )
        self.add_document(
            "key",
            binary=b"key-scope-change",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista\n1 - B",
            metadata={**core, "role": "Analista"},
            declared_type="answer_key",
            title="Gabarito Analista",
        )
        key = self.resolve("key")
        processor = DesktopProcessor(self.store)
        try:
            self.assertEqual(processor._reconcile_answer_key(key.document_version_id), 1)
        finally:
            processor._executor.shutdown(wait=True)
        with closing(self.store._connect()) as connection:
            technician_before = connection.execute(
                "SELECT COUNT(*) FROM document_identity_events e JOIN documents d "
                "ON d.document_version_id = e.document_version_id WHERE d.id = 'tecnico'"
            ).fetchone()[0]

        self.store.correct_document_identity(
            "key",
            DesktopImportMetadata(
                document_type="answer_key",
                board="Banca",
                concurso="Concurso",
                year=2026,
                role="Auditor",
            ),
            actor="coordenador",
        )

        with closing(self.store._connect()) as connection:
            analyst_version = connection.execute(
                "SELECT document_version_id FROM documents WHERE id = 'analista'"
            ).fetchone()[0]
            auditor_version = connection.execute(
                "SELECT document_version_id FROM documents WHERE id = 'auditor'"
            ).fetchone()[0]
            links = connection.execute(
                "SELECT exam_version_id, answer_key_version_id, status FROM document_links "
                "ORDER BY created_at, id"
            ).fetchall()
            technician_after = connection.execute(
                "SELECT COUNT(*) FROM document_identity_events e JOIN documents d "
                "ON d.document_version_id = e.document_version_id WHERE d.id = 'tecnico'"
            ).fetchone()[0]
        self.assertEqual(
            [
                (row["exam_version_id"], row["answer_key_version_id"], row["status"])
                for row in links
            ],
            [
                (analyst_version, key.document_version_id, "rejected"),
                (auditor_version, key.document_version_id, "active"),
            ],
        )
        self.assertEqual(
            self.store.question_records("auditor")[0][0].correct_answer,
            "B",
        )
        self.assertEqual(
            self.store.question_records("tecnico")[0][0].answer_status,
            "missing",
        )
        self.assertEqual(technician_after, technician_before)

    def test_injected_failure_at_each_correction_phase_rolls_back_the_whole_fact(self) -> None:
        core = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "exam",
            binary=b"atomic-exam",
            text="Banca: Banca\nConcurso: Concurso\nAno: 2026\nCargo: Analista",
            metadata={**core, "role": "Analista"},
        )
        for document_id, role, answer in (
            ("analyst-key", "Analista", "B"),
            ("auditor-key", "Auditor", "C"),
        ):
            self.add_document(
                document_id,
                binary=document_id.encode(),
                text=(
                    "Banca: Banca\nConcurso: Concurso\nAno: 2026\n"
                    f"Cargo: {role}\n1 - {answer}"
                ),
                metadata={**core, "role": role},
                declared_type="answer_key",
                title=f"Gabarito {role}",
            )
        self.resolve("exam")
        analyst_key = self.resolve("analyst-key")
        self.resolve("auditor-key")
        question_id = self.store.save_question(
            "exam", self.lineage_question(1), QuestionClassification()
        )
        processor = DesktopProcessor(self.store)
        try:
            self.assertEqual(processor._reconcile_answer_key(analyst_key.document_version_id), 1)
        finally:
            processor._executor.shutdown(wait=True)
        self.store.decide_question(
            question_id, "approved", actor="revisora", notes="Estado atômico inicial."
        )
        before = self.semantic_state()
        triggers = {
            "identity": (
                "BEFORE INSERT ON semantic_identities "
                "BEGIN SELECT RAISE(ABORT, 'injected identity'); END"
            ),
            "version": (
                "BEFORE UPDATE ON document_versions "
                "BEGIN SELECT RAISE(ABORT, 'injected version'); END"
            ),
            "observation": (
                "BEFORE UPDATE ON document_observations "
                "BEGIN SELECT RAISE(ABORT, 'injected observation'); END"
            ),
            "identity_event": (
                "BEFORE INSERT ON document_identity_events WHEN NEW.action = 'identity_corrected' "
                "BEGIN SELECT RAISE(ABORT, 'injected identity_event'); END"
            ),
            "metadata": (
                "BEFORE UPDATE ON documents WHEN NEW.metadata_json != OLD.metadata_json "
                "BEGIN SELECT RAISE(ABORT, 'injected metadata'); END"
            ),
            "question_metadata": (
                "BEFORE UPDATE ON questions WHEN "
                "json_extract(NEW.payload_json, '$.role') != "
                "json_extract(OLD.payload_json, '$.role') "
                "BEGIN SELECT RAISE(ABORT, 'injected question metadata'); END"
            ),
            "link": (
                "BEFORE UPDATE ON document_links WHEN NEW.status = 'rejected' "
                "BEGIN SELECT RAISE(ABORT, 'injected link'); END"
            ),
            "answer": (
                "BEFORE UPDATE ON questions WHEN "
                "json_extract(NEW.payload_json, '$.correct_answer') != "
                "json_extract(OLD.payload_json, '$.correct_answer') "
                "BEGIN SELECT RAISE(ABORT, 'injected answer'); END"
            ),
        }
        for phase, trigger_body in triggers.items():
            with self.subTest(phase=phase):
                with closing(self.store._connect()) as connection:
                    connection.execute(f"CREATE TRIGGER fail_correction {trigger_body}")
                    connection.commit()
                try:
                    expected = f"injected {phase.split('_')[0]}"
                    with self.assertRaisesRegex(sqlite3.IntegrityError, expected):
                        self.store.correct_document_identity(
                            "exam",
                            self.corrected_metadata(role="Auditor"),
                            actor="coordenador",
                        )
                    self.assertEqual(self.semantic_state(), before)
                finally:
                    with closing(self.store._connect()) as connection:
                        connection.execute("DROP TRIGGER fail_correction")
                        connection.commit()

    def lineage_question(
        self,
        number: int,
        *,
        statement: str | None = None,
        answer_status: str = "matched",
        correct_answer: str | None = "B",
    ) -> QuestionRecord:
        return QuestionRecord.model_validate(
            {
                "number": number,
                "statement": statement or f"Enunciado completo e estável da questão {number}.",
                "alternatives": [
                    Alternative(letter="A", text="Primeira alternativa."),
                    Alternative(letter="B", text="Segunda alternativa."),
                    Alternative(letter="C", text="Terceira alternativa."),
                ],
                "discipline": "Direito",
                "matter": "Direito Administrativo",
                "subject": "Atos administrativos",
                "board": "Banca",
                "organization": "Secretaria Pública",
                "concurso": "Concurso",
                "role": "Analista",
                "year": 2026,
                "level": "Superior",
                "difficulty": "Média",
                "source_pages": [1],
                "explanation": "A alternativa indicada corresponde ao gabarito oficial.",
                "answer_status": answer_status,
                "correct_answer": correct_answer,
            }
        )

    def add_successive_exams(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-lineage-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-lineage-two",
            text="Prova 2026 republicada\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.resolve("first")
        self.resolve("second")

    def reconcile_saved_successor(self) -> None:
        processor = DesktopProcessor(self.store)
        try:
            processor._structure_job("job-second", threading.Event())
        finally:
            processor._executor.shutdown(wait=True)

    def lineage_rows(self) -> list[dict[str, object]]:
        with closing(self.store._connect()) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM question_lineage ORDER BY question_number"
                ).fetchall()
            ]

    def test_human_decision_is_carried_to_identical_successor_question(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="revisora", notes="Decisão humana preservável."
        )
        second_id = self.store.save_question(
            "second", self.lineage_question(1), QuestionClassification()
        )

        self.reconcile_saved_successor()

        successor = self.store.question(second_id)
        self.assertEqual(successor["status"], "approved")
        self.assertEqual(successor["reviewer"], "revisora")
        self.assertEqual(successor["review_notes"], "Decisão humana preservável.")
        self.assertIsNone(successor["exported_at"])
        self.assertEqual(self.lineage_rows()[0]["comparison"], "unchanged")
        self.assertEqual(self.store.audit_log(second_id)[0]["action"], "decision_carried_forward")

    def test_changed_statement_does_not_carry_decision(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="revisora", notes="Decisão da versão anterior."
        )
        second_id = self.store.save_question(
            "second",
            self.lineage_question(1, statement="Enunciado oficialmente alterado na sucessora."),
            QuestionClassification(),
        )

        self.reconcile_saved_successor()

        successor = self.store.question(second_id)
        self.assertEqual(successor["status"], "pending")
        self.assertIsNone(successor["reviewer"])
        rows = self.lineage_rows()
        self.assertEqual(len(rows), 1)
        lineage = rows[0]
        self.assertEqual(lineage["comparison"], "changed")
        self.assertEqual(lineage["content_equal"], 0)
        self.assertEqual(self.store.audit_log(second_id)[0]["action"], "decision_invalidated")

    def test_changed_answer_does_not_carry_decision(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="revisora", notes="Resposta B conferida."
        )
        second_id = self.store.save_question(
            "second",
            self.lineage_question(1, correct_answer="C"),
            QuestionClassification(),
        )

        self.reconcile_saved_successor()

        successor = self.store.question(second_id)
        self.assertEqual(successor["status"], "pending")
        self.assertIsNone(successor["review_notes"])
        rows = self.lineage_rows()
        self.assertEqual(len(rows), 1)
        lineage = rows[0]
        self.assertEqual(lineage["comparison"], "changed")
        self.assertEqual((lineage["content_equal"], lineage["answer_equal"]), (1, 0))
        self.assertEqual(self.store.audit_log(second_id)[0]["action"], "decision_invalidated")
        self.assertIn(
            "decision_invalidated",
            [event["action"] for event in self.store.identity_events("second")],
        )

    def test_added_and_removed_questions_have_lineage(self) -> None:
        self.add_successive_exams()
        first_ids = [
            self.store.save_question(
                "first", self.lineage_question(number), QuestionClassification()
            )
            for number in (1, 2)
        ]
        second_ids = [
            self.store.save_question(
                "second", self.lineage_question(number), QuestionClassification()
            )
            for number in (2, 3)
        ]

        self.reconcile_saved_successor()

        rows = {int(row["question_number"]): row for row in self.lineage_rows()}
        self.assertEqual(set(rows), {1, 2, 3})
        self.assertEqual(rows[1]["comparison"], "removed")
        self.assertEqual(rows[1]["predecessor_question_id"], first_ids[0])
        self.assertIsNone(rows[1]["successor_question_id"])
        self.assertEqual(rows[2]["comparison"], "unchanged")
        self.assertEqual(rows[3]["comparison"], "added")
        self.assertIsNone(rows[3]["predecessor_question_id"])
        self.assertEqual(rows[3]["successor_question_id"], second_ids[1])
        self.assertEqual(len(self.store.question_records("first")), 2)

    def test_only_human_decisions_are_carried_and_exported_becomes_approved(self) -> None:
        self.add_successive_exams()
        first_ids = {
            number: self.store.save_question(
                "first", self.lineage_question(number), QuestionClassification()
            )
            for number in (1, 2, 3, 4)
        }
        self.store.decide_question(
            first_ids[1], "rejected", actor="revisora", notes="Rejeição fundamentada."
        )
        self.store.decide_question(
            first_ids[2], "approved", actor="revisora", notes="Aprovação exportada."
        )
        self.store.mark_exported([first_ids[2]])
        self.store.decide_question(
            first_ids[4], "exception", actor="revisora", notes="Exceção não transportável."
        )
        second_ids = {
            number: self.store.save_question(
                "second", self.lineage_question(number), QuestionClassification()
            )
            for number in (1, 2, 3, 4)
        }

        self.reconcile_saved_successor()

        successors = {number: self.store.question(value) for number, value in second_ids.items()}
        self.assertEqual(successors[1]["status"], "rejected")
        self.assertEqual(successors[2]["status"], "approved")
        self.assertIsNone(successors[2]["exported_at"])
        self.assertEqual(successors[3]["status"], "pending")
        self.assertIsNone(successors[3]["reviewer"])
        self.assertEqual(successors[4]["status"], "pending")
        self.assertIsNone(successors[4]["review_notes"])

    def test_legacy_null_decision_fingerprint_does_not_invent_a_decision(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="revisora", notes="Registro legado."
        )
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET decision_fingerprint = NULL WHERE id = ?", (first_id,)
            )
            connection.commit()
        second_id = self.store.save_question(
            "second", self.lineage_question(1), QuestionClassification()
        )

        self.reconcile_saved_successor()

        successor = self.store.question(second_id)
        self.assertEqual(successor["status"], "pending")
        self.assertIsNone(successor["reviewer"])
        rows = self.lineage_rows()
        self.assertEqual(len(rows), 1)
        lineage = rows[0]
        self.assertEqual(lineage["comparison"], "changed")
        self.assertIn("legad", str(lineage["reason"]).casefold())

    def test_repeated_lineage_reconciliation_is_idempotent(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="revisora", notes="Decisão idempotente."
        )
        second_id = self.store.save_question(
            "second", self.lineage_question(1), QuestionClassification()
        )

        self.reconcile_saved_successor()
        self.reconcile_saved_successor()

        with closing(self.store._connect()) as connection:
            lineage_count = connection.execute(
                "SELECT COUNT(*) FROM question_lineage"
            ).fetchone()[0]
            event_count = connection.execute(
                "SELECT COUNT(*) FROM document_identity_events "
                "WHERE action = 'decision_carried_forward'"
            ).fetchone()[0]
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE question_id = ? AND action = 'decision_carried_forward'",
                (second_id,),
            ).fetchone()[0]
        self.assertEqual((lineage_count, event_count, audit_count), (1, 1, 1))

    def test_concurrent_changed_lineage_is_unique_and_does_not_copy_decision(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="revisora", notes="Resposta B."
        )
        second_id = self.store.save_question(
            "second", self.lineage_question(1, correct_answer="C"), QuestionClassification()
        )
        errors: list[Exception] = []

        def reconcile() -> None:
            try:
                self.reconcile_saved_successor()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=reconcile) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.lineage_rows()), 1)
        successor = self.store.question(second_id)
        self.assertEqual(successor["status"], "pending")
        self.assertIsNone(successor["reviewer"])

    def test_changed_lineage_preserves_successor_human_decision(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="predecessora", notes="Decisão da predecessora."
        )
        second_id = self.store.save_question(
            "second",
            self.lineage_question(1, statement="Enunciado revisado na versão sucessora."),
            QuestionClassification(),
        )
        self.store.decide_question(
            second_id, "approved", actor="sucessora", notes="Decisão própria da sucessora."
        )

        self.reconcile_saved_successor()

        successor = self.store.question(second_id)
        self.assertEqual(successor["status"], "approved")
        self.assertEqual(successor["reviewer"], "sucessora")
        self.assertEqual(successor["review_notes"], "Decisão própria da sucessora.")
        self.assertEqual(self.lineage_rows()[0]["comparison"], "changed")
        self.assertNotIn(
            "decision_invalidated",
            [entry["action"] for entry in self.store.audit_log(second_id)],
        )

    def test_identical_lineage_does_not_overwrite_successor_human_decision(self) -> None:
        self.add_successive_exams()
        first_id = self.store.save_question(
            "first", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            first_id, "approved", actor="predecessora", notes="Aprovação da predecessora."
        )
        second_id = self.store.save_question(
            "second", self.lineage_question(1), QuestionClassification()
        )
        self.store.decide_question(
            second_id, "rejected", actor="sucessora", notes="Rejeição própria da sucessora."
        )

        self.reconcile_saved_successor()

        successor = self.store.question(second_id)
        self.assertEqual(successor["status"], "rejected")
        self.assertEqual(successor["reviewer"], "sucessora")
        self.assertEqual(successor["review_notes"], "Rejeição própria da sucessora.")
        self.assertNotIn(
            "decision_carried_forward",
            [entry["action"] for entry in self.store.audit_log(second_id)],
        )

    def test_equivalent_text_with_different_bytes_is_republication(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\r\nQuestão 1\r\nA) Azul   B) Verde",
            metadata=metadata,
        )
        first, second = self.resolve("first"), self.resolve("second")
        self.assertEqual(second.outcome, "republication")
        self.assertEqual(second.document_version_id, first.document_version_id)
        self.assertEqual(self.store.semantic_summary()["versions"], 1)
        self.assertEqual(self.store.semantic_summary()["observations"], 2)
        self.assertEqual(self.store.semantic_summary()["events"], 4)

    def test_same_identity_with_changed_content_creates_successor(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Vermelho",
            metadata=metadata,
        )
        first, second = self.resolve("first"), self.resolve("second")
        self.assertEqual(second.outcome, "new_version")
        self.assertEqual(second.predecessor_version_id, first.document_version_id)
        self.assertEqual(second.version_number, 2)

    def test_repeating_resolution_for_same_document_is_idempotent(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first", binary=b"pdf-one", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        first = self.resolve("first")
        event_count = self.store.semantic_summary()["events"]
        second = self.resolve("first")
        self.assertEqual(second.outcome, first.outcome)
        self.assertEqual(second.document_version_id, first.document_version_id)
        self.assertEqual(second.version_number, first.version_number)
        self.assertEqual(self.store.semantic_summary()["events"], event_count)

    def test_reprocessed_operational_document_without_final_event_is_republication(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first", binary=b"pdf-one", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        first = self.resolve("first")
        self.add_document(
            "second", binary=b"pdf-two", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE documents SET document_version_id = ?, "
                "semantic_resolution = 'new_identity' "
                "WHERE id = 'second'",
                (first.document_version_id,),
            )
            connection.execute(
                "UPDATE document_observations SET document_version_id = ?, "
                "resolution_status = 'new_identity' "
                "WHERE document_id = 'second'",
                (first.document_version_id,),
            )
            connection.commit()
        second = self.resolve("second")
        self.assertEqual(second.outcome, "republication")
        self.assertEqual(second.document_version_id, first.document_version_id)

    def test_structure_calls_parser_for_new_identity_and_skips_republication(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        text = "Prova 2026\nQuestão 1. Qual é a cor principal?\nA) Azul\nB) Verde"
        self.add_document("first", binary=b"pdf-one", text=text, metadata=metadata)
        self.add_document("second", binary=b"pdf-two", text=text, metadata=metadata)
        self.resolve("first")
        first_processor = DesktopProcessor(self.store)
        try:
            with patch(
                "kad_collector.desktop_processor.parse_question_pages",
                wraps=parse_question_pages,
            ) as parser:
                first_processor._structure_job("job-first", threading.Event())
                self.assertEqual(parser.call_count, 1)
        finally:
            first_processor._executor.shutdown(wait=True)
        self.assertEqual(self.store.semantic_summary()["documents"], 2)
        with closing(self.store._connect()) as connection:
            question_count = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        self.assertEqual(question_count, 1)
        self.resolve("second")
        second_processor = DesktopProcessor(self.store)
        try:
            with patch(
                "kad_collector.desktop_processor.parse_question_pages",
                side_effect=AssertionError("republicação não deve estruturar"),
            ):
                second_processor._structure_job("job-second", threading.Event())
        finally:
            second_processor._executor.shutdown(wait=True)
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)
        self.add_document(
            "third",
            binary=b"pdf-three",
            text="Prova 2026\nQuestão 1. Qual é a cor principal?\nA) Azul\nB) Vermelho",
            metadata=metadata,
        )
        self.assertEqual(self.resolve("third").outcome, "new_version")
        third_processor = DesktopProcessor(self.store)
        try:
            with patch(
                "kad_collector.desktop_processor.parse_question_pages",
                wraps=parse_question_pages,
            ) as parser:
                third_processor._structure_job("job-third", threading.Event())
                self.assertEqual(parser.call_count, 1)
        finally:
            third_processor._executor.shutdown(wait=True)
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 2)

    def test_changed_content_with_question_added_creates_successor(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first", binary=b"pdf-one", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul\nQuestão 2\nA) Verde",
            metadata=metadata,
        )
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.resolve("second").outcome, "new_version")

    def test_changed_content_with_question_removed_creates_successor(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul\nQuestão 2\nA) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second", binary=b"pdf-two", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.resolve("second").outcome, "new_version")

    def test_insufficient_identity_is_uncertain_and_not_structured(self) -> None:
        self.add_document(
            "unknown", binary=b"pdf-one", text="Questão 1\nA) Azul B) Verde", metadata={}
        )
        result = self.resolve("unknown")
        self.assertEqual(result.outcome, "uncertain")
        self.assertIsNone(result.document_version_id)
        self.assertEqual(self.store.semantic_summary()["versions"], 0)
        self.assertIn("identidade semântica insuficiente", result.reason)

    def test_processor_does_not_structure_resolution_failure(self) -> None:
        self.add_document(
            "unknown", binary=b"pdf-one", text="Questão 1\nA) Azul B) Verde", metadata={}
        )
        self.resolve("unknown")
        processor = DesktopProcessor(self.store)
        try:
            with patch(
                "kad_collector.desktop_processor.parse_question_pages",
                side_effect=AssertionError("parser não deveria ser chamado"),
            ):
                processor._structure_job("job-unknown", threading.Event())
        finally:
            processor._executor.shutdown(wait=True)
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 0)

    def test_new_identity_and_changed_content_are_structurable_once(self) -> None:
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata={"board": "Banca", "concurso": "A", "year": 2026},
        )
        self.add_document(
            "other",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata={"board": "Banca", "concurso": "B", "year": 2026},
        )
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.resolve("other").outcome, "new_identity")
        self.assertEqual(self.store.semantic_summary()["versions"], 2)

    def test_republication_adds_origin_without_new_questions(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        first, second = self.resolve("first"), self.resolve("second")
        with closing(self.store._connect()) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM document_observation_origins").fetchone()[
                    0
                ],
                2,
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 0)
        self.assertEqual(first.document_version_id, second.document_version_id)

    def test_concurrent_republications_share_one_version(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        results, errors = [], []

        def run(document_id: str) -> None:
            try:
                results.append(self.resolve(document_id))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(item,)) for item in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len({result.document_version_id for result in results}), 1)
        self.assertEqual(self.store.semantic_summary()["versions"], 1)
        winner = results[0].document_version_id
        with closing(self.store._connect()) as connection:
            documents = connection.execute(
                "SELECT id, document_version_id FROM documents ORDER BY id"
            ).fetchall()
            self.assertEqual(len(documents), 2)
            self.assertEqual({row["document_version_id"] for row in documents}, {winner})
            observations = connection.execute(
                "SELECT document_id, document_version_id, resolution_status "
                "FROM document_observations ORDER BY document_id"
            ).fetchall()
            self.assertEqual(len(observations), 2)
            self.assertEqual({row["document_id"] for row in observations}, {"first", "second"})
            self.assertEqual({row["document_version_id"] for row in observations}, {winner})
            self.assertEqual(
                {row["resolution_status"] for row in observations},
                {"new_identity", "republication"},
            )
            events = connection.execute(
                "SELECT document_id, action FROM document_identity_events "
                "WHERE document_id IS NOT NULL ORDER BY document_id"
            ).fetchall()
            self.assertEqual(len(events), 2)
            self.assertEqual({row["document_id"] for row in events}, {"first", "second"})
            self.assertEqual({row["action"] for row in events}, {"new_identity", "republication"})

    def test_processor_resolution_exception_marks_document_and_skips_parser(self) -> None:
        pdf_buffer = BytesIO()
        pdf = canvas.Canvas(pdf_buffer)
        pdf.drawString(54, 800, "Prova 2026")
        pdf.drawString(54, 778, "Questão 1. Qual é a cor principal?")
        pdf.drawString(54, 756, "A) Azul")
        pdf.drawString(54, 734, "B) Verde")
        pdf.save()
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document("failure", binary=pdf_buffer.getvalue(), text="", metadata=metadata)
        processor = DesktopProcessor(self.store)
        try:
            with (
                patch.object(
                    self.store,
                    "resolve_extracted_document",
                    side_effect=RuntimeError("injected resolution failure"),
                ),
                patch(
                    "kad_collector.desktop_processor.parse_question_pages",
                    side_effect=AssertionError("parser não deveria ser chamado"),
                ),
            ):
                processor.run("job-failure", threading.Event())
        finally:
            processor._executor.shutdown(wait=True)
        document = self.store.document("failure")
        self.assertEqual(document["status"], "exception")
        self.assertIn("resolução semântica falhou", " ".join(document["warnings"]))
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 0)

    def test_reprocessing_resumes_failed_resolution_without_duplicate_event(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        with (
            patch("kad_collector.semantic_resolution._event", side_effect=RuntimeError("injected")),
            self.assertRaises(RuntimeError),
        ):
            self.resolve("first")
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.store.semantic_summary()["events"], 2)


if __name__ == "__main__":
    unittest.main()
