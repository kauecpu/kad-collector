from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kad_collector.canonical_ai_benchmark import (
    BENCHMARK_ALGORITHM_VERSION,
    BENCHMARK_SCHEMA_VERSION,
    canonical_benchmark_sample_fingerprint,
)
from kad_collector.canonical_ai_input import CANONICAL_AI_INPUT_SANITIZER_VERSION
from kad_collector.canonical_classification import (
    CANONICAL_AI_RESPONSE_CONTRACT_VERSION,
    CANONICAL_ENRICHMENT_PROMPT_VERSION,
    CanonicalAIRequest,
    CanonicalAIResult,
    CanonicalClassificationError,
    canonical_taxonomy_path_id,
)
from kad_collector.cli import build_parser
from kad_collector.editorial_taxonomy import EditorialTaxonomy
from kad_collector.json_utils import read_json, write_json
from kad_collector.ollama_ai_benchmark import (
    FULL_MAX_CALLS,
    LOCAL_BENCHMARK_ALGORITHM_VERSION,
    LOCAL_BENCHMARK_SAMPLE_SIZE,
    LOCAL_BENCHMARK_SCHEMA_VERSION,
    SMOKE_MAX_CALLS,
    execute_ollama_ai_benchmark,
    prepare_ollama_ai_benchmark,
    summarize_ollama_ai_benchmark,
)
from kad_collector.ollama_ai_provider import OllamaUnavailableError
from kad_collector.ollama_preflight import (
    OLLAMA_BENCHMARK_TARGETS,
    ollama_probe_report_id,
)
from kad_collector.semantic_identity import stable_sha256

EXPECTED = {
    "discipline": "Legislação Aduaneira",
    "matter": "Administração Aduaneira",
    "subject": "Controle Aduaneiro",
    "level": "Superior",
}


class FakeBenchmarkAdmin:
    base_url = "http://127.0.0.1:11434"

    def __init__(self, models: list[dict[str, Any]] | None = None) -> None:
        self.active_model: str | None = None
        self.unloaded: list[str] = []
        self.models = models or []
        self.command_environments: list[dict[str, str]] = []

    def version(self) -> str:
        return "0.11.10"

    def tags(self) -> list[dict[str, Any]]:
        return self.models

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
        response: dict[str, Any] = {}
        if any(field in request.requested_fields for field in ("discipline", "matter", "subject")):
            option = next(
                option
                for option in request.taxonomy_options
                if all(
                    option[field] == EXPECTED[field]
                    for field in ("discipline", "matter", "subject")
                )
            )
            response["taxonomy"] = {
                "pathId": option["pathId"],
                "confidence": 0.91,
                "evidence": "O conteúdo sustenta o caminho taxonômico.",
            }
        if "level" in request.requested_fields:
            response["level"] = {
                "value": EXPECTED["level"],
                "confidence": 0.91,
                "evidence": "O nível corresponde à referência.",
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
        self.taxonomy_path = next(
            path
            for path in self.taxonomy.candidate_paths()
            if path.discipline == EXPECTED["discipline"]
            and path.matter == EXPECTED["matter"]
            and path.subject == EXPECTED["subject"]
        )
        self.items = [self._item(index) for index in range(175)]
        manifest_items = [self._safe_item(item) for item in self.items]
        write_json(
            self.source_bundle,
            {
                "manifest": {
                    "schemaVersion": BENCHMARK_SCHEMA_VERSION,
                    "algorithmVersion": BENCHMARK_ALGORITHM_VERSION,
                    "benchmarkId": "canonical-source-test",
                    "sampleFingerprint": canonical_benchmark_sample_fingerprint(
                        manifest_items, taxonomy_version=self.taxonomy.version
                    ),
                    "taxonomyVersion": self.taxonomy.version,
                    "promptVersion": CANONICAL_ENRICHMENT_PROMPT_VERSION,
                    "responseContractVersion": CANONICAL_AI_RESPONSE_CONTRACT_VERSION,
                    "sanitizerVersion": CANONICAL_AI_INPUT_SANITIZER_VERSION,
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
        preflight_report = {
            "schemaVersion": 1,
            "kind": "ollama-local-preflight-probe",
            "probeId": "probe-test",
            "networkScope": "loopback",
            "ollama": {
                "baseUrl": "http://127.0.0.1:11434",
                "version": "0.11.10",
            },
            "models": models,
            "readyForBenchmark": True,
        }
        preflight_report["probeReportId"] = ollama_probe_report_id(preflight_report)
        write_json(self.preflight, preflight_report)
        self.installed_models = [
            {
                "name": model["tag"],
                "model": model["tag"],
                "digest": model["digest"],
                "details": {"quantization_level": model["quantization"]},
            }
            for model in models
        ]
        prepared = prepare_ollama_ai_benchmark(
            self.source_bundle,
            preflight_path=self.preflight,
            local_bundle_path=self.local_bundle,
            manifest_path=self.manifest,
            local_artifact_root=self.root,
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
                "structuralExpected",
                "reviewedExpected",
                "referenceStatus",
                "reasonCode",
                "contest",
                "taxonomyVersion",
                "referenceKind",
                "promptContentFingerprint",
                "removedArtifacts",
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
            "structuralExpected": EXPECTED,
            "reviewedExpected": EXPECTED,
            "referenceStatus": "agent_reviewed_reference",
            "reasonCode": "content_matches_taxonomy_path",
            "contest": "RFB22",
            "taxonomyVersion": self.taxonomy.version,
            "referenceKind": "agent_reviewed_reference",
            "promptContentFingerprint": stable_sha256({"prompt": index}),
            "removedArtifacts": [],
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
                        "pathId": canonical_taxonomy_path_id(self.taxonomy_path),
                        "discipline": EXPECTED["discipline"],
                        "matter": EXPECTED["matter"],
                        "subject": EXPECTED["subject"],
                        "keywords": list(self.taxonomy.keywords_for_path(self.taxonomy_path)),
                    }
                ],
                "promptContentFingerprint": stable_sha256({"prompt": index}),
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
        processor: str = "100% GPU",
    ) -> dict[str, Any]:
        admin.models = self.installed_models

        def command_runner(command: tuple[str, ...], environment: Mapping[str, str]) -> str:
            self.assertEqual(command, ("ollama", "ps"))
            admin.command_environments.append(dict(environment))
            return f"{admin.active_model} abc 9 GB {processor}"

        return execute_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            preflight_path=self.preflight,
            checkpoint_path=self.checkpoint,
            phase="smoke",
            approved_benchmark_id=self.benchmark_id,
            max_new_calls=20,
            provider_factory=lambda model: FakeLocalProvider(
                model,
                calls,
                admin,
                unavailable_after=(
                    unavailable_after if model == OLLAMA_BENCHMARK_TARGETS[0].tag else None
                ),
                forbidden=forbidden,
            ),
            admin_client=admin,
            command_runner=command_runner,
            local_artifact_root=self.root,
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
        self.assertEqual(LOCAL_BENCHMARK_SAMPLE_SIZE, 175)
        self.assertEqual(SMOKE_MAX_CALLS, 20)
        self.assertEqual(FULL_MAX_CALLS, 330)
        self.assertEqual(manifest["sampleSize"], LOCAL_BENCHMARK_SAMPLE_SIZE)
        self.assertEqual(manifest["phases"]["smokeMeasuredCalls"], 20)
        self.assertEqual(manifest["phases"]["fullRemainderMeasuredCalls"], 330)
        self.assertEqual(manifest["phases"]["maximumMeasuredCalls"], 350)
        self.assertEqual(manifest["localBundleFingerprint"], stable_sha256(bundle["items"]))
        self.assertEqual(bundle["manifest"], manifest)

    def test_raw_question_change_invalidates_prepared_bundle(self) -> None:
        bundle = read_json(self.local_bundle)
        bundle["items"][0]["request"]["question"]["statement"] = "conteúdo alterado"
        write_json(self.local_bundle, bundle)

        with self.assertRaisesRegex(CanonicalClassificationError, "conteúdo bruto"):
            self._run_smoke({}, FakeBenchmarkAdmin())

    def test_preparation_rejects_requested_fields_that_diverge_from_hidden_fields(
        self,
    ) -> None:
        source = read_json(self.source_bundle)
        source["items"][0]["request"]["requestedFields"] = ["subject"]
        write_json(self.source_bundle, source)

        with self.assertRaisesRegex(CanonicalClassificationError, "campos solicitados"):
            prepare_ollama_ai_benchmark(
                self.source_bundle,
                preflight_path=self.preflight,
                local_bundle_path=self.root / "other-bundle.json",
                manifest_path=self.root / "other-manifest.json",
                local_artifact_root=self.root,
            )

    def test_tampered_preflight_report_is_rejected_before_inference(self) -> None:
        preflight = read_json(self.preflight)
        preflight["models"][0]["processor"] = "80% GPU 20% CPU"
        write_json(self.preflight, preflight)

        with self.assertRaisesRegex(CanonicalClassificationError, "alterado"):
            self._run_smoke({}, FakeBenchmarkAdmin())

    def test_live_digest_drift_is_rejected_before_inference(self) -> None:
        admin = FakeBenchmarkAdmin(self.installed_models)
        admin.models[0] = {**admin.models[0], "digest": "sha256:changed"}

        with self.assertRaisesRegex(CanonicalClassificationError, "digest vivo"):
            self._run_smoke({}, admin)

    def test_missing_live_model_stops_before_inference_with_pull_instruction(self) -> None:
        admin = FakeBenchmarkAdmin(self.installed_models[:-1])

        with self.assertRaisesRegex(
            CanonicalClassificationError,
            r"não está instalado.*ollama pull qwen3:14b",
        ):
            execute_ollama_ai_benchmark(
                self.local_bundle,
                manifest_path=self.manifest,
                preflight_path=self.preflight,
                checkpoint_path=self.checkpoint,
                phase="smoke",
                approved_benchmark_id=self.benchmark_id,
                max_new_calls=20,
                provider_factory=lambda _: (_ for _ in ()).throw(AssertionError()),
                admin_client=admin,
                command_runner=lambda _, __: "unused",
                local_artifact_root=self.root,
            )

        self.assertFalse(self.checkpoint.exists())

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
                "20",
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
        self.assertEqual(run.max_new_calls, 20)
        self.assertEqual(report.command, "report-ollama-ai-benchmark")

    def test_smoke_uses_same_ten_items_for_each_model_and_stops_at_twenty(self) -> None:
        calls: dict[str, list[CanonicalAIRequest]] = {}
        admin = FakeBenchmarkAdmin()

        result = self._run_smoke(calls, admin)

        expected_ids = [f"reference-{index:03d}" for index in range(10)]
        self.assertEqual(result["newCalls"], 20)
        self.assertEqual(result["status"], "completed")
        for target in OLLAMA_BENCHMARK_TARGETS:
            self.assertEqual(
                [request.canonical_question_id for request in calls[target.tag][1:]],
                expected_ids,
            )
        self.assertEqual(admin.unloaded, [target.tag for target in OLLAMA_BENCHMARK_TARGETS])
        self.assertEqual(
            admin.command_environments,
            [{"OLLAMA_HOST": admin.base_url}] * len(OLLAMA_BENCHMARK_TARGETS),
        )

        with self.assertRaisesRegex(CanonicalClassificationError, "20"):
            execute_ollama_ai_benchmark(
                self.local_bundle,
                manifest_path=self.manifest,
                preflight_path=self.preflight,
                checkpoint_path=self.root / "other-checkpoint.json",
                phase="smoke",
                approved_benchmark_id=self.benchmark_id,
                max_new_calls=21,
                provider_factory=lambda _: (_ for _ in ()).throw(AssertionError()),
                admin_client=FakeBenchmarkAdmin(self.installed_models),
                command_runner=lambda _, __: "unused",
                local_artifact_root=self.root,
            )

    def test_resume_skips_checkpointed_pairs(self) -> None:
        self._run_smoke({}, FakeBenchmarkAdmin())
        resumed_calls: dict[str, list[CanonicalAIRequest]] = {}

        result = self._run_smoke(resumed_calls, FakeBenchmarkAdmin())

        self.assertEqual(result["newCalls"], 0)
        self.assertEqual(resumed_calls, {})

    def test_tampered_checkpoint_record_is_rejected(self) -> None:
        self._run_smoke({}, FakeBenchmarkAdmin())
        checkpoint = read_json(self.checkpoint)
        checkpoint["records"][0]["key"] = "tampered"
        write_json(self.checkpoint, checkpoint)

        with self.assertRaisesRegex(CanonicalClassificationError, "chave inválida"):
            summarize_ollama_ai_benchmark(
                self.local_bundle,
                manifest_path=self.manifest,
                checkpoint_path=self.checkpoint,
                local_artifact_root=self.root,
            )

    def test_checkpoint_rejects_unknown_record_status(self) -> None:
        self._run_smoke({}, FakeBenchmarkAdmin())
        checkpoint = read_json(self.checkpoint)
        checkpoint["records"][0]["status"] = "mystery"
        write_json(self.checkpoint, checkpoint)

        with self.assertRaisesRegex(CanonicalClassificationError, "status desconhecido"):
            summarize_ollama_ai_benchmark(
                self.local_bundle,
                manifest_path=self.manifest,
                checkpoint_path=self.checkpoint,
                local_artifact_root=self.root,
            )

    def test_unavailable_model_pauses_without_completing_current_pair(self) -> None:
        first = self._run_smoke({}, FakeBenchmarkAdmin(), unavailable_after=2)
        checkpoint = read_json(self.checkpoint)

        self.assertEqual(first["status"], "paused")
        self.assertEqual(first["newCalls"], 1)
        self.assertEqual(len(checkpoint["records"]), 1)
        self.assertEqual(len(checkpoint["interruptions"]), 1)

        resumed = self._run_smoke({}, FakeBenchmarkAdmin())
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["newCalls"], 19)
        self.assertEqual(len(read_json(self.checkpoint)["records"]), 20)

    def test_hardware_gate_pauses_during_warmup_before_measured_calls(self) -> None:
        admin = FakeBenchmarkAdmin(self.installed_models)
        result = self._run_smoke({}, admin, processor="80% GPU 20% CPU")
        checkpoint = read_json(self.checkpoint)

        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["newCalls"], 0)
        self.assertEqual(checkpoint["records"], [])
        self.assertEqual(checkpoint["interruptions"][0]["stage"], "warmup")

    def test_cleanup_failure_is_checkpointed_without_masking_completed_calls(self) -> None:
        class CleanupFailingAdmin(FakeBenchmarkAdmin):
            def unload(self, model: str) -> None:
                raise RuntimeError("detalhe sensível que não deve ser persistido")

        admin = CleanupFailingAdmin(self.installed_models)
        result = self._run_smoke({}, admin)
        checkpoint = read_json(self.checkpoint)

        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["newCalls"], 10)
        self.assertEqual(len(checkpoint["cleanupFailures"]), 1)
        self.assertEqual(checkpoint["cleanupFailures"][0]["status"], "unresolved")
        self.assertEqual(checkpoint["cleanupFailures"][0]["errorType"], "RuntimeError")
        self.assertNotIn("detalhe sensível", json.dumps(checkpoint))

        resumed = self._run_smoke({}, FakeBenchmarkAdmin())
        resumed_checkpoint = read_json(self.checkpoint)

        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["newCalls"], 10)
        self.assertEqual(resumed_checkpoint["cleanupFailures"][0]["status"], "resolved")
        self.assertIsInstance(resumed_checkpoint["cleanupFailures"][0]["resolvedAt"], str)

    def test_unresolved_final_cleanup_blocks_full_and_positive_recommendation(self) -> None:
        final_tag = OLLAMA_BENCHMARK_TARGETS[-1].tag

        class FinalCleanupFailingAdmin(FakeBenchmarkAdmin):
            def unload(self, model: str) -> None:
                if model == final_tag:
                    raise RuntimeError("unload failed")
                super().unload(model)

        failing_admin = FinalCleanupFailingAdmin(self.installed_models)
        smoke = self._run_smoke({}, failing_admin)
        report = summarize_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            checkpoint_path=self.checkpoint,
            local_artifact_root=self.root,
        )

        self.assertEqual(smoke["newCalls"], 20)
        self.assertEqual(smoke["status"], "paused")
        self.assertEqual(report["cleanupFailures"]["unresolved"], 1)
        self.assertIn("Smoke incompleto", report["recommendation"])

        full = execute_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            preflight_path=self.preflight,
            checkpoint_path=self.checkpoint,
            phase="full",
            approved_benchmark_id=self.benchmark_id,
            max_new_calls=330,
            provider_factory=lambda _: (_ for _ in ()).throw(AssertionError()),
            admin_client=failing_admin,
            command_runner=lambda _, __: "unused",
            local_artifact_root=self.root,
        )

        self.assertEqual(full["status"], "paused")
        self.assertEqual(full["newCalls"], 0)

        resolved = self._run_smoke({}, FakeBenchmarkAdmin())
        resolved_report = summarize_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            checkpoint_path=self.checkpoint,
            local_artifact_root=self.root,
        )
        self.assertEqual(resolved["status"], "completed")
        self.assertEqual(resolved["newCalls"], 0)
        self.assertEqual(resolved_report["cleanupFailures"]["unresolved"], 0)
        self.assertNotIn("Smoke incompleto", resolved_report["recommendation"])

    def test_full_requires_successful_smoke_and_a_new_phase(self) -> None:
        with self.assertRaisesRegex(CanonicalClassificationError, "smoke"):
            execute_ollama_ai_benchmark(
                self.local_bundle,
                manifest_path=self.manifest,
                preflight_path=self.preflight,
                checkpoint_path=self.checkpoint,
                phase="full",
                approved_benchmark_id=self.benchmark_id,
                max_new_calls=330,
                provider_factory=lambda _: (_ for _ in ()).throw(AssertionError()),
                admin_client=FakeBenchmarkAdmin(self.installed_models),
                command_runner=lambda _, __: "unused",
                local_artifact_root=self.root,
            )

    def test_aggregate_report_excludes_raw_content_and_local_paths(self) -> None:
        self._run_smoke({}, FakeBenchmarkAdmin(), forbidden=True)

        report = summarize_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            checkpoint_path=self.checkpoint,
            report_path=self.report,
            local_artifact_root=self.root,
        )
        serialized = json.dumps(report, ensure_ascii=False)

        self.assertEqual(report["records"], 20)
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
                "schemaVersion": LOCAL_BENCHMARK_SCHEMA_VERSION,
                "benchmarkId": manifest["benchmarkId"],
                "manifestFingerprint": manifest["manifestFingerprint"],
                "sampleFingerprint": manifest["sampleFingerprint"],
                "localBundleFingerprint": manifest["localBundleFingerprint"],
                "records": [
                    {
                        "key": stable_sha256(
                            {
                                "benchmark": manifest["benchmarkId"],
                                "model": model["tag"],
                                "digest": model["digest"],
                                "question": item["referenceQuestionId"],
                                "fingerprint": item["contentFingerprint"],
                                "fields": item["hiddenFields"],
                            }
                        ),
                        "phase": "smoke",
                        "model": model["tag"],
                        "digest": model["digest"],
                        "referenceQuestionId": item["referenceQuestionId"],
                        "contentFingerprint": item["contentFingerprint"],
                        "requestedFields": item["hiddenFields"],
                        "status": "failed",
                        "schemaValid": None,
                        "errorType": "RuntimeError",
                        "validationCode": "provider_transport_failure",
                        "errorCategory": "provider_transport_failure",
                        "wallLatencyMs": 1,
                    }
                ],
                "warmups": [],
                "interruptions": [],
                "cleanupFailures": [],
            },
        )

        report = summarize_ollama_ai_benchmark(
            self.local_bundle,
            manifest_path=self.manifest,
            checkpoint_path=self.checkpoint,
            local_artifact_root=self.root,
        )

        metrics = report["models"][model["tag"]]
        self.assertEqual(metrics["failures"], 1)
        self.assertEqual(metrics["schemaFailures"], 0)


if __name__ == "__main__":
    unittest.main()
