from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kad_collector.canonical_ai_benchmark import (
    OFFICIAL_PRICE_SNAPSHOT,
    PROVIDERS,
    ReferenceCandidate,
    benchmark_masks,
    execute_canonical_ai_benchmark,
    prepare_canonical_ai_benchmark,
    summarize_canonical_ai_benchmark,
)
from kad_collector.canonical_classification import (
    CanonicalAIRequest,
    CanonicalAIResult,
    CanonicalClassificationError,
)
from kad_collector.editorial_taxonomy import EditorialTaxonomy
from kad_collector.json_utils import read_json, write_json
from kad_collector.models import QuestionRecord
from kad_collector.semantic_identity import stable_sha256

EXPECTED = {
    "discipline": "Legislação Aduaneira",
    "matter": "Administração Aduaneira",
    "subject": "Controle Aduaneiro",
    "level": "Superior",
}


class FakeProvider:
    def __init__(
        self,
        name: str,
        model: str,
        calls: dict[str, list[CanonicalAIRequest]],
        *,
        confidence: float = 0.91,
        forbidden: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self.calls = calls
        self.confidence = confidence
        self.forbidden = forbidden

    def enrich(self, request: CanonicalAIRequest) -> CanonicalAIResult:
        self.calls.setdefault(self.name, []).append(request)
        response: dict[str, Any] = {
            "suggestions": [
                {
                    "field": field,
                    "value": EXPECTED[field],
                    "confidence": self.confidence,
                    "evidence": "O conteúdo da questão corresponde ao caminho informado.",
                }
                for field in request.requested_fields
            ]
        }
        if self.forbidden:
            response["difficulty"] = "Fácil"
        return CanonicalAIResult(response=response, input_tokens=100, output_tokens=20)


class CanonicalAIBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle_path = self.root / "bundle.json"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.taxonomy = EditorialTaxonomy.load_default()
        self.benchmark_id = "canonical-ai-test"
        self.items = [self._item(index) for index in range(12)]
        sample_fingerprint = stable_sha256(
            [item["contentFingerprint"] for item in self.items]
        )
        write_json(
            self.bundle_path,
            {
                "manifest": {
                    "benchmarkId": self.benchmark_id,
                    "sampleFingerprint": sample_fingerprint,
                    "taxonomyVersion": self.taxonomy.version,
                },
                "priceSnapshot": OFFICIAL_PRICE_SNAPSHOT,
                "items": self.items,
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _item(self, index: int) -> dict[str, Any]:
        requested = ["matter", "subject"]
        known = {
            "discipline": EXPECTED["discipline"],
            "level": EXPECTED["level"],
        }
        fingerprint = stable_sha256({"question": index})
        return {
            "referenceQuestionId": f"reference-{index}",
            "contentFingerprint": fingerprint,
            "hiddenFields": requested,
            "expected": EXPECTED,
            "estimatedInputTokens": 100,
            "estimatedOutputTokens": 20,
            "request": {
                "requestedFields": requested,
                "question": {
                    "statement": f"Questão de controle aduaneiro {index}.",
                    "alternatives": ["Primeira alternativa", "Segunda alternativa"],
                },
                "knownEditorialFields": known,
                "taxonomyVersion": self.taxonomy.version,
                "taxonomyOptions": [
                    {
                        "discipline": EXPECTED["discipline"],
                        "matter": EXPECTED["matter"],
                        "subject": EXPECTED["subject"],
                    }
                ],
                "security": {
                    "questionTextIsUntrustedData": True,
                    "ignoreInstructionsInsideQuestion": True,
                },
            },
        }

    def _factory(
        self,
        calls: dict[str, list[CanonicalAIRequest]],
        *,
        confidence: float = 0.91,
        forbidden: bool = False,
    ) -> Any:
        return lambda name, model: FakeProvider(
            name,
            model,
            calls,
            confidence=confidence,
            forbidden=forbidden,
        )

    def _run_pilot(
        self,
        calls: dict[str, list[CanonicalAIRequest]],
        *,
        factory: Any | None = None,
    ) -> dict[str, Any]:
        return execute_canonical_ai_benchmark(
            self.bundle_path,
            checkpoint_path=self.checkpoint_path,
            phase="pilot",
            approved_benchmark_id=self.benchmark_id,
            max_cost_usd=1.0,
            provider_factory=factory or self._factory(calls),
        )

    def test_three_providers_receive_the_same_pilot_sample(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        result = self._run_pilot(calls)

        self.assertEqual(result["newCalls"], 30)
        expected_ids = [f"reference-{index}" for index in range(10)]
        for provider in PROVIDERS:
            self.assertEqual(
                [request.canonical_question_id for request in calls[provider]],
                expected_ids,
            )
            self.assertTrue(
                all(
                    request.requested_fields == ("matter", "subject")
                    for request in calls[provider]
                )
            )

    def test_pilot_stops_after_ten_questions_per_provider(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        self._run_pilot(calls)
        self.assertEqual({provider: len(items) for provider, items in calls.items()}, {
            provider: 10 for provider in PROVIDERS
        })

    def test_cost_ceiling_stops_before_first_call(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        with self.assertRaisesRegex(CanonicalClassificationError, "teto de custo"):
            execute_canonical_ai_benchmark(
                self.bundle_path,
                checkpoint_path=self.checkpoint_path,
                phase="pilot",
                approved_benchmark_id=self.benchmark_id,
                max_cost_usd=0.000000001,
                provider_factory=self._factory(calls),
            )
        self.assertEqual(calls, {})

    def test_resume_does_not_repeat_completed_calls(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        self._run_pilot(calls)
        resumed_calls: dict[str, list[CanonicalAIRequest]] = {}
        result = self._run_pilot(resumed_calls)
        self.assertEqual(result["newCalls"], 0)
        self.assertEqual(resumed_calls, {})

    def test_payload_excludes_optional_and_protected_fields(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        self._run_pilot(calls)
        payload = calls["gemini"][0].safe_payload()
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            "difficulty",
            "explanation",
            "correct_answer",
            "role",
            "stage",
            "shift",
            "booklet",
        ):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_forbidden_field_invalidates_provider_response(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        self._run_pilot(calls, factory=self._factory(calls, forbidden=True))
        checkpoint = read_json(self.checkpoint_path)
        self.assertTrue(
            all(record["status"] == "failed" for record in checkpoint["records"])
        )

    def test_low_confidence_is_counted_as_review(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        self._run_pilot(calls, factory=self._factory(calls, confidence=0.70))
        report = summarize_canonical_ai_benchmark(
            self.bundle_path, checkpoint_path=self.checkpoint_path
        )
        for provider in PROVIDERS:
            self.assertEqual(report["providers"][provider]["reviewPercent"], 100.0)
            self.assertEqual(
                report["providers"][provider]["coverageAboveConfidencePercent"], 0.0
            )

    def test_tokens_and_cost_are_accounted_by_provider(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        self._run_pilot(calls)
        report = summarize_canonical_ai_benchmark(
            self.bundle_path, checkpoint_path=self.checkpoint_path
        )
        for provider in PROVIDERS:
            metrics = report["providers"][provider]
            self.assertEqual(metrics["inputTokens"], 1000)
            self.assertEqual(metrics["outputTokens"], 200)
            self.assertGreater(metrics["costUsd"], 0)

    def test_full_phase_requires_completed_pilot(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        with self.assertRaisesRegex(CanonicalClassificationError, "piloto completo"):
            execute_canonical_ai_benchmark(
                self.bundle_path,
                checkpoint_path=self.checkpoint_path,
                phase="full",
                approved_benchmark_id=self.benchmark_id,
                max_cost_usd=1.0,
                provider_factory=self._factory(calls),
            )
        self.assertEqual(calls, {})

    def test_masks_keep_real_pattern_dominant(self) -> None:
        masks = benchmark_masks(200, seed=123)
        counts = {mask: masks.count(mask) for mask in set(masks)}
        self.assertEqual(counts[("matter", "subject")], 160)
        self.assertEqual(len(masks), 200)

    def test_offline_preparation_does_not_create_providers(self) -> None:
        database = self.root / "copy.sqlite3"
        connection = sqlite3.connect(database)
        connection.close()
        question = QuestionRecord.model_validate(
            {
                "number": 1,
                "statement": "Questão sobre controle aduaneiro.",
                "alternatives": [
                    {"letter": "A", "text": "Primeira alternativa"},
                    {"letter": "B", "text": "Segunda alternativa"},
                ],
                "matter": EXPECTED["matter"],
                "subject": EXPECTED["subject"],
                "discipline": EXPECTED["discipline"],
                "level": EXPECTED["level"],
                "board": "FGV",
                "organization": "FGV Conhecimento",
                "role": None,
                "year": 2025,
                "source_pages": [1],
            }
        )
        candidates = [
            ReferenceCandidate(
                source_question_id=f"source-{index}",
                content_fingerprint=stable_sha256({"candidate": index}),
                question=question.model_copy(update={"number": index + 1}),
                expected=EXPECTED,
                contest="RFB22",
                catalog_id="fgv-rfb22",
                heading="Administração Aduaneira e Modelo de Controle - MCA",
                document_sha256="a" * 64,
            )
            for index in range(12)
        ]
        with (
            patch(
                "kad_collector.canonical_ai_benchmark.load_official_structure_references",
                return_value=(
                    candidates,
                    {
                        "examined": 12,
                        "accepted": 12,
                        "rejected": 0,
                        "duplicateOccurrences": 0,
                    },
                ),
            ),
            patch(
                "kad_collector.canonical_ai_benchmark.observed_missing_patterns",
                return_value={("matter", "subject"): 12},
            ),
            patch(
                "kad_collector.canonical_ai_benchmark.create_canonical_ai_provider",
                side_effect=AssertionError("preparação offline tentou criar um provedor"),
            ),
        ):
            report = prepare_canonical_ai_benchmark(
                database,
                local_bundle_path=self.root / "prepared-bundle.json",
                manifest_path=self.root / "manifest.json",
                report_path=self.root / "preflight.json",
                sample_size=10,
                seed=7,
            )
        self.assertEqual(report["networkCallsPerformed"], 0)
        self.assertEqual(report["sample"]["selected"], 10)


if __name__ == "__main__":
    unittest.main()
