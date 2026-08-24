from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kad_collector.canonical_ai_benchmark import BENCHMARK_ALGORITHM_VERSION
from kad_collector.canonical_classification import (
    CANONICAL_ENRICHMENT_PROMPT_VERSION,
    CanonicalAIRequest,
    CanonicalAIResult,
    CanonicalClassificationError,
)
from kad_collector.cli import build_parser
from kad_collector.editorial_taxonomy import EditorialTaxonomy
from kad_collector.json_utils import read_json, write_json
from kad_collector.ollama_ai_benchmark import (
    LOCAL_BENCHMARK_ALGORITHM_VERSION,
    execute_ollama_ai_benchmark,
    prepare_ollama_ai_benchmark,
    summarize_ollama_ai_benchmark,
)
from kad_collector.ollama_ai_provider import OllamaUnavailableError
from kad_collector.ollama_preflight import OLLAMA_BENCHMARK_TARGETS
from kad_collector.semantic_identity import stable_sha256

EXPECTED = {
    "discipline": "Legislação Aduaneira",
    "matter": "Administração Aduaneira",
    "subject": "Controle Aduaneiro",
    "level": "Superior",
}


class FakeBenchmarkAdmin:
    base_url = "http://127.0.0.1:11434"

    def __init__(self) -> None:
        self.active_model: str | None = None
        self.unloaded: list[str] = []

    def version(self) -> str:
        return "0.11.10"

    def tags(self) -> list[dict[str, Any]]:
        return []

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("benchmark deve usar o provedor, não o cliente administrativo")

    def running_models(self) -> list[dict[str, Any]]:
        assert self.active_model is not None
        return [
            {
                "name": self.active_model,
                "model": self.active_model,
                "size": 8_000_000_000,
                "size_vram": 8_000_000_000,
                "context_length": 4096,
            }
        ]

    def unload(self, model: str) -> None:
        self.unloaded.append(model)
        self.active_model = None


class FakeLocalProvider:
    name = "ollama"

    def __init__(
        self,
        model: str,
        calls: dict[str, list[CanonicalAIRequest]],
        admin: FakeBenchmarkAdmin,
        *,
        unavailable_after: int | None = None,
        forbidden: bool = False,
    ) -> None:
        self.model = model
        self.calls = calls
        self.admin = admin
        self.unavailable_after = unavailable_after
        self.forbidden = forbidden

    def enrich(self, request: CanonicalAIRequest) -> CanonicalAIResult:
        model_calls = self.calls.setdefault(self.model, [])
        if self.unavailable_after is not None and len(model_calls) >= self.unavailable_after:
            raise OllamaUnavailableError("serviço encerrado")
        model_calls.append(request)
        self.admin.active_model = self.model
        response: dict[str, Any] = {
            "suggestions": [
                {
                    "field": field,
                    "value": EXPECTED[field],
                    "confidence": 0.91,
                    "evidence": "O conteúdo sustenta o caminho taxonômico.",
                }
                for field in request.requested_fields
            ]
        }
        if self.forbidden:
            response["difficulty"] = "Fácil"
        return CanonicalAIResult(
            response=response,
            input_tokens=100,
            output_tokens=20,
            estimated_cost=0,
            provider_metrics={
                "totalDurationNs": 1_000_000_000,
                "loadDurationNs": 100_000_000,
                "promptEvalDurationNs": 200_000_000,
                "evalDurationNs": 200_000_000,
                "doneReason": "stop",
            },
        )


class OllamaAIBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_bundle = self.root / "canonical-bundle.json"
        self.preflight = self.root / "ollama-preflight.json"
        self.local_bundle = self.root / "ollama-bundle.json"
        self.manifest = self.root / "ollama-manifest.json"
        self.checkpoint = self.root / "ollama-checkpoint.json"
        self.report = self.root / "ollama-report.json"
        self.taxonomy = EditorialTaxonomy.load_default()
        self.items = [self._item(index) for index in range(200)]
        manifest_items = [self._safe_item(item) for item in self.items]
        write_json(
            self.source_bundle,
            {
                "manifest": {
                    "schemaVersion": 1,
                    "algorithmVersion": BENCHMARK_ALGORITHM_VERSION,
                    "benchmarkId": "canonical-source-test",
                    "sampleFingerprint": stable_sha256(manifest_items),
                    "taxonomyVersion": self.taxonomy.version,
                    "promptVersion": CANONICAL_ENRICHMENT_PROMPT_VERSION,
                    "items": manifest_items,
                },
                "items": self.items,
            },
        )
        models = [
            {
                "tag": target.tag,
                "digest": f"sha256:{index:064x}",
                "quantization": target.expected_quantization,
                "processor": "100% GPU",
                "sizeBytes": (6 + index) * 1024**3,
                "sizeVramBytes": (6 + index) * 1024**3,
                "contextLength": 4096,
                "structuredOutput": True,
            }
            for index, target in enumerate(OLLAMA_BENCHMARK_TARGETS, 1)
        ]
        write_json(
            self.preflight,
            {
                "schemaVersion": 1,
                "kind": "ollama-local-preflight-probe",
                "probeId": "probe-test",
                "probeReportId": "probe-report-test",
                "networkScope": "loopback",
                "ollama": {
                    "baseUrl": "http://127.0.0.1:11434",
                    "version": "0.11.10",
                },
                "models": models,
                "readyForBenchmark": True,
            },
        )
        prepared = prepare_ollama_ai_benchmark(
            self.source_bundle,
            preflight_path=self.preflight,
            local_bundle_path=self.local_bundle,
            manifest_path=self.manifest,
        )
        self.benchmark_id = str(prepared["benchmarkId"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _safe_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item[key]
            for key in (
                "referenceQuestionId",
                "sourceQuestionId",
                "contentFingerprint",
                "hiddenFields",
                "expected",
                "contest",
                "taxonomyVersion",
                "referenceKind",
            )
        }

    def _item(self, index: int) -> dict[str, Any]:
        fingerprint = stable_sha256({"question": index})
        hidden = ["matter", "subject"]
        return {
            "referenceQuestionId": f"reference-{index:03d}",
            "sourceQuestionId": f"source-{index:03d}",
            "contentFingerprint": fingerprint,
            "hiddenFields": hidden,
            "expected": EXPECTED,
            "contest": "RFB22",
            "taxonomyVersion": self.taxonomy.version,
            "referenceKind": "official_structure_reference",
            "estimatedInputTokens": 100,
            "estimatedOutputTokens": 20,
            "request": {
                "requestedFields": hidden,
                "question": {
                    "statement": f"Questão sintética número {index}.",
                    "alternatives": ["Primeira alternativa", "Segunda alternativa"],
                },
                "knownEditorialFields": {
                    "discipline": EXPECTED["discipline"],
                    "level": EXPECTED["level"],
                },
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

    def _run_smoke(
        self,
        calls: dict[str, list[CanonicalAIRequest]],
        admin: FakeBenchmarkAdmin,
        *,
        unavailable_after: int | None = None,
        forbidden: bool = False,
    ) -> dict[str, Any]:
        return execute_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            preflight_path=self.preflight,
            checkpoint_path=self.checkpoint,
            phase="smoke",
            approved_benchmark_id=self.benchmark_id,
            max_new_calls=30,
            provider_factory=lambda model: FakeLocalProvider(
                model,
                calls,
                admin,
                unavailable_after=(
                    unavailable_after
                    if model == OLLAMA_BENCHMARK_TARGETS[0].tag
                    else None
                ),
                forbidden=forbidden,
            ),
            admin_client=admin,
        )

    def test_preparation_freezes_exact_models_without_inference(self) -> None:
        manifest = read_json(self.manifest)
        bundle = read_json(self.local_bundle)

        self.assertEqual(manifest["algorithmVersion"], LOCAL_BENCHMARK_ALGORITHM_VERSION)
        self.assertEqual(
            [item["tag"] for item in manifest["models"]],
            [target.tag for target in OLLAMA_BENCHMARK_TARGETS],
        )
        self.assertEqual(manifest["parameters"]["concurrency"], 1)
        self.assertEqual(manifest["parameters"]["numCtx"], 4096)
        self.assertEqual(manifest["sampleSize"], 200)
        self.assertEqual(bundle["manifest"], manifest)

    def test_cli_exposes_prepare_run_and_aggregate_report_commands(self) -> None:
        parser = build_parser()
        prepared = parser.parse_args(
            [
                "prepare-ollama-ai-benchmark",
                "--canonical-bundle",
                "canonical.json",
                "--preflight",
                "preflight.json",
            ]
        )
        run = parser.parse_args(
            [
                "run-ollama-ai-benchmark",
                "--local-bundle",
                "bundle.json",
                "--manifest",
                "manifest.json",
                "--preflight",
                "preflight.json",
                "--checkpoint",
                "checkpoint.json",
                "--phase",
                "smoke",
                "--approved-benchmark-id",
                "benchmark-1",
                "--max-new-calls",
                "30",
            ]
        )
        report = parser.parse_args(
            [
                "report-ollama-ai-benchmark",
                "--local-bundle",
                "bundle.json",
                "--manifest",
                "manifest.json",
                "--checkpoint",
                "checkpoint.json",
                "--report",
                "report.json",
            ]
        )

        self.assertEqual(prepared.command, "prepare-ollama-ai-benchmark")
        self.assertEqual(run.phase, "smoke")
        self.assertEqual(run.max_new_calls, 30)
        self.assertEqual(report.command, "report-ollama-ai-benchmark")

    def test_smoke_uses_same_ten_items_for_each_model_and_stops_at_thirty(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        admin = FakeBenchmarkAdmin()

        result = self._run_smoke(calls, admin)

        expected_ids = [f"reference-{index:03d}" for index in range(10)]
        self.assertEqual(result["newCalls"], 30)
        self.assertEqual(result["status"], "completed")
        for target in OLLAMA_BENCHMARK_TARGETS:
            self.assertEqual(
                [request.canonical_question_id for request in calls[target.tag][1:]],
                expected_ids,
            )
        self.assertEqual(admin.unloaded, [target.tag for target in OLLAMA_BENCHMARK_TARGETS])

        with self.assertRaisesRegex(CanonicalClassificationError, "30"):
            execute_ollama_ai_benchmark(
                self.local_bundle,
                manifest_path=self.manifest,
                preflight_path=self.preflight,
                checkpoint_path=self.root / "other-checkpoint.json",
                phase="smoke",
                approved_benchmark_id=self.benchmark_id,
                max_new_calls=31,
                provider_factory=lambda _: (_ for _ in ()).throw(AssertionError()),
                admin_client=FakeBenchmarkAdmin(),
            )

    def test_resume_skips_checkpointed_pairs(self) -> None:
        self._run_smoke({}, FakeBenchmarkAdmin())
        resumed_calls: dict[str, list[CanonicalAIRequest]] = {}

        result = self._run_smoke(resumed_calls, FakeBenchmarkAdmin())

        self.assertEqual(result["newCalls"], 0)
        self.assertEqual(resumed_calls, {})

    def test_unavailable_model_pauses_without_completing_current_pair(self) -> None:
        first = self._run_smoke({}, FakeBenchmarkAdmin(), unavailable_after=2)
        checkpoint = read_json(self.checkpoint)

        self.assertEqual(first["status"], "paused")
        self.assertEqual(first["newCalls"], 1)
        self.assertEqual(len(checkpoint["records"]), 1)
        self.assertEqual(len(checkpoint["interruptions"]), 1)

        resumed = self._run_smoke({}, FakeBenchmarkAdmin())
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["newCalls"], 29)
        self.assertEqual(len(read_json(self.checkpoint)["records"]), 30)

    def test_full_requires_successful_smoke_and_a_new_phase(self) -> None:
        with self.assertRaisesRegex(CanonicalClassificationError, "smoke"):
            execute_ollama_ai_benchmark(
                self.local_bundle,
                manifest_path=self.manifest,
                preflight_path=self.preflight,
                checkpoint_path=self.checkpoint,
                phase="full",
                approved_benchmark_id=self.benchmark_id,
                max_new_calls=570,
                provider_factory=lambda _: (_ for _ in ()).throw(AssertionError()),
                admin_client=FakeBenchmarkAdmin(),
            )

    def test_aggregate_report_excludes_raw_content_and_local_paths(self) -> None:
        self._run_smoke({}, FakeBenchmarkAdmin(), forbidden=True)

        report = summarize_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            checkpoint_path=self.checkpoint,
            report_path=self.report,
        )
        serialized = json.dumps(report, ensure_ascii=False)

        self.assertEqual(report["records"], 30)
        self.assertNotIn("Questão sintética", serialized)
        self.assertNotIn("Primeira alternativa", serialized)
        self.assertNotIn("rawResponse", serialized)
        self.assertNotIn(str(self.root), serialized)
        for target in OLLAMA_BENCHMARK_TARGETS:
            metrics = report["models"][target.tag]
            self.assertEqual(metrics["schemaFailures"], 10)
            self.assertEqual(metrics["prohibitedFieldAttempts"], 10)

    def test_provider_failure_is_not_misreported_as_schema_failure(self) -> None:
        manifest = read_json(self.manifest)
        model = manifest["models"][0]
        item = self.items[0]
        write_json(
            self.checkpoint,
            {
                "schemaVersion": 1,
                "benchmarkId": manifest["benchmarkId"],
                "manifestFingerprint": manifest["manifestFingerprint"],
                "sampleFingerprint": manifest["sampleFingerprint"],
                "records": [
                    {
                        "key": "provider-failure",
                        "phase": "smoke",
                        "model": model["tag"],
                        "digest": model["digest"],
                        "referenceQuestionId": item["referenceQuestionId"],
                        "contentFingerprint": item["contentFingerprint"],
                        "requestedFields": item["hiddenFields"],
                        "status": "failed",
                        "schemaValid": None,
                        "errorType": "RuntimeError",
                        "errorCategory": "provider_failure",
                        "wallLatencyMs": 1,
                    }
                ],
                "warmups": [],
                "interruptions": [],
            },
        )

        report = summarize_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            checkpoint_path=self.checkpoint,
        )

        metrics = report["models"][model["tag"]]
        self.assertEqual(metrics["failures"], 1)
        self.assertEqual(metrics["schemaFailures"], 0)


if __name__ == "__main__":
    unittest.main()
