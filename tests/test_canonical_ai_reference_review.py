from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kad_collector.canonical_ai_reference_review import (
    ReferenceReviewError,
    load_reference_reviews,
)
from kad_collector.editorial_taxonomy import EditorialTaxonomy
from kad_collector.json_utils import write_json


class CanonicalAIReferenceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "review.json"
        self.taxonomy = EditorialTaxonomy.load_default()
        path = self.taxonomy.candidate_paths(catalog_ids=("fgv-rfb22",))[0]
        self.expected = {
            "discipline": path.discipline,
            "matter": str(path.matter),
            "subject": str(path.subject),
            "level": "Superior",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, records: list[dict[str, object]], **overrides: object) -> None:
        payload: dict[str, object] = {
            "schemaVersion": 2,
            "kind": "canonical-ai-reference-review",
            "taxonomyVersion": self.taxonomy.version,
            "records": records,
        }
        payload.update(overrides)
        write_json(self.path, payload)

    def _record(self, **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "sourceQuestionId": "question-1",
            "contentFingerprint": "a" * 64,
            "status": "agent_reviewed_reference",
            "structuralExpected": self.expected,
            "reviewedExpected": self.expected,
            "reasonCode": "content_matches_taxonomy_path",
        }
        record.update(overrides)
        return record

    def test_loads_agent_review_without_calling_it_human_review(self) -> None:
        self._write([self._record()])

        reviews = load_reference_reviews(self.path, taxonomy=self.taxonomy)

        self.assertEqual(reviews["question-1"].status, "agent_reviewed_reference")
        self.assertNotIn("human_review", self.path.read_text(encoding="utf-8"))

    def test_rejects_unknown_review_state(self) -> None:
        self._write([self._record(status="approved")])
        with self.assertRaisesRegex(ReferenceReviewError, "status"):
            load_reference_reviews(self.path, taxonomy=self.taxonomy)

    def test_rejects_human_review_state(self) -> None:
        self._write([self._record(status="human_review")])
        with self.assertRaisesRegex(ReferenceReviewError, "human_review"):
            load_reference_reviews(self.path, taxonomy=self.taxonomy)

    def test_rejects_duplicate_source_question_id(self) -> None:
        self._write([self._record(), self._record()])
        with self.assertRaisesRegex(ReferenceReviewError, "duplicad"):
            load_reference_reviews(self.path, taxonomy=self.taxonomy)

    def test_rejects_taxonomy_version_drift(self) -> None:
        self._write([self._record()], taxonomyVersion="old")
        with self.assertRaisesRegex(ReferenceReviewError, "taxonomia"):
            load_reference_reviews(self.path, taxonomy=self.taxonomy)

    def test_rejects_reviewed_labels_outside_taxonomy(self) -> None:
        invalid = {**self.expected, "subject": "Assunto inventado"}
        self._write([self._record(reviewedExpected=invalid)])
        with self.assertRaisesRegex(ReferenceReviewError, "caminho taxonômico"):
            load_reference_reviews(self.path, taxonomy=self.taxonomy)

    def test_ambiguous_reference_must_not_have_reviewed_expected(self) -> None:
        self._write(
            [
                self._record(
                    status="ambiguous_reference",
                    reviewedExpected=self.expected,
                    reasonCode="multiple_plausible_paths",
                )
            ]
        )
        with self.assertRaisesRegex(ReferenceReviewError, "reviewedExpected"):
            load_reference_reviews(self.path, taxonomy=self.taxonomy)

    def test_versioned_review_is_safe_and_keeps_non_usable_references_separate(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        review_path = (
            repository_root / "docs" / "benchmarks" / "canonical-ai-reference-review.v2.json"
        )
        audit_path = (
            repository_root / "docs" / "benchmarks" / "canonical-ai-reference-audit.v2.json"
        )

        reviews = load_reference_reviews(review_path, taxonomy=self.taxonomy)
        serialized = json.dumps(
            json.loads(review_path.read_text(encoding="utf-8")),
            ensure_ascii=False,
        )

        self.assertEqual(len(reviews), 200)
        self.assertEqual(
            sum(review.status == "agent_reviewed_reference" for review in reviews.values()),
            175,
        )
        self.assertNotIn("statement", serialized)
        self.assertNotIn("alternatives", serialized)
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertFalse(audit["readyForPreparation"])
        self.assertEqual(audit["usableReferences"], 175)
        self.assertEqual(audit["sanitization"]["cleaned"], 56)
        self.assertEqual(audit["sanitization"]["residuesAfterCleaning"], 0)
        self.assertEqual(audit["networkCallsPerformed"], 0)
        self.assertEqual(audit["modelInferencesPerformed"], 0)


if __name__ == "__main__":
    unittest.main()
