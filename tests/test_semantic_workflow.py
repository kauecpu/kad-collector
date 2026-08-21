from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen import canvas

from kad_collector.desktop_processor import DesktopProcessor, parse_question_pages
from kad_collector.desktop_store import DesktopStore
from kad_collector.document_contract import NormalizedDocument
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
        self, document_id: str, *, binary: bytes, text: str, metadata: dict[str, str | int]
    ) -> None:
        path = Path(self.directory.name) / f"{document_id}.pdf"
        path.write_bytes(binary)
        normalized = NormalizedDocument(
            local_path=str(path),
            sha256=hashlib.sha256(binary).hexdigest(),
            size_bytes=len(binary),
            title="Prova 2026",
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
