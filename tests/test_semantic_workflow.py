# ruff: noqa: E501
from __future__ import annotations

import unittest

from kad_collector.semantic_identity import (
    AnswerKeyCoverage,
    ContentFingerprint,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    KnownDocumentVersion,
    SemanticField,
)
from kad_collector.semantic_resolution import decide_document_version


def profile(*, key: str | None = "identity", sha: str = "sha", conflict: bool = False) -> DocumentSemanticProfile:
    known = SemanticField(status="known", normalized_values=("x",), method="test", reason="test", confidence=1.0)
    identity = ExamSemanticIdentity(
        board=known, concurso=known, organization=SemanticField.unknown("test"),
        year=known, roles=SemanticField.unknown("test"), stage=SemanticField.unknown("test"),
        turns=SemanticField.unknown("test"), variants=SemanticField.unknown("test"),
    )
    return DocumentSemanticProfile(
        identity=identity, identity_key=key, document_role="exam",
        coverage=AnswerKeyCoverage(
            roles=identity.roles, stage=identity.stage, turns=identity.turns, variants=identity.variants,
        ), content_fingerprint=ContentFingerprint(
            sha256=sha, page_sha256s=(), page_count=1, character_count=1,
        ), has_conflict=conflict,
    )


class SemanticResolutionDecisionTests(unittest.TestCase):
    def test_decision_has_the_five_expected_outcomes(self) -> None:
        current = KnownDocumentVersion(version_id="v1", identity_key="identity", document_role="exam", content_sha256="sha-1", version_number=1)
        self.assertEqual(decide_document_version(profile(key=None), ()).outcome, "uncertain")
        self.assertEqual(decide_document_version(profile(sha="sha-1"), (current,)).outcome, "republication")
        self.assertEqual(decide_document_version(profile(sha="sha-2"), (current,)).outcome, "new_version")
        self.assertEqual(decide_document_version(profile(key="other"), (current,)).outcome, "new_identity")
        self.assertEqual(decide_document_version(profile(conflict=True), (current,)).outcome, "uncertain")

    def test_changed_content_can_add_or_remove_questions_without_changing_outcome(self) -> None:
        current = KnownDocumentVersion(version_id="v1", identity_key="identity", document_role="exam", content_sha256="sha-1", version_number=1)
        for sha in ("added-question", "removed-question"):
            self.assertEqual(decide_document_version(profile(sha=sha), (current,)).outcome, "new_version")


if __name__ == "__main__":
    unittest.main()
