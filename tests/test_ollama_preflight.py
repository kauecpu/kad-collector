from __future__ import annotations

import unittest
from typing import Any

from kad_collector.canonical_classification import CanonicalClassificationError
from kad_collector.cli import build_parser
from kad_collector.ollama_preflight import (
    GIB,
    MINIMUM_FREE_BYTES,
    OLLAMA_BENCHMARK_TARGETS,
    inspect_ollama_environment,
    probe_ollama_models,
)


def _installed_model(tag: str, index: int) -> dict[str, Any]:
    quantization = "Q4_0" if "qat" in tag else "Q4_K_M"
    return {
        "name": tag,
        "model": tag,
        "size": (6 + index) * GIB,
        "digest": f"sha256:{index:064x}",
        "details": {
            "format": "gguf",
            "family": "gemma3" if "gemma" in tag else "qwen3",
            "parameter_size": "12.2B" if "gemma" in tag else "14.8B",
            "quantization_level": quantization,
        },
    }


class FakeAdminClient:
    base_url = "http://127.0.0.1:11434"

    def __init__(self, models: list[dict[str, Any]]) -> None:
        self.models = models
        self.chat_requests: list[dict[str, Any]] = []
        self.unloaded: list[str] = []
        self.active_model: str | None = None

    def version(self) -> str:
        return "0.11.10"

    def tags(self) -> list[dict[str, Any]]:
        return self.models

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.chat_requests.append(payload)
        self.active_model = str(payload["model"])
        return {
            "message": {"role": "assistant", "content": '{"ok":true}'},
            "done": True,
            "done_reason": "stop",
            "total_duration": 800_000_000,
            "load_duration": 600_000_000,
            "prompt_eval_count": 30,
            "prompt_eval_duration": 80_000_000,
            "eval_count": 5,
            "eval_duration": 120_000_000,
        }

    def running_models(self) -> list[dict[str, Any]]:
        assert self.active_model is not None
        installed = next(item for item in self.models if item["name"] == self.active_model)
        return [
            {
                **installed,
                "size_vram": installed["size"],
                "context_length": 4096,
            }
        ]

    def unload(self, model: str) -> None:
        self.unloaded.append(model)
        self.active_model = None


class OllamaPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tags = [target.tag for target in OLLAMA_BENCHMARK_TARGETS]
        self.models = [_installed_model(tag, index) for index, tag in enumerate(self.tags, 1)]

    def test_inspection_never_pulls_or_generates_and_lists_missing_models(self) -> None:
        client = FakeAdminClient(self.models[:1])

        report = inspect_ollama_environment(client=client, free_bytes=20 * GIB)

        self.assertEqual(client.chat_requests, [])
        self.assertEqual(client.unloaded, [])
        self.assertEqual(report["networkScope"], "loopback")
        self.assertEqual(report["missingModels"], self.tags[1:])
        self.assertEqual(report["pullCommands"], [])
        self.assertEqual(report["downloadBlockedReason"], "insufficient_free_space")
        self.assertEqual(report["disk"]["minimumFreeBytes"], MINIMUM_FREE_BYTES)
        self.assertFalse(report["disk"]["sufficient"])
        self.assertFalse(report["readyForProbe"])
        self.assertTrue(report["probeId"].startswith("ollama-probe-"))

        enough_space = inspect_ollama_environment(
            client=FakeAdminClient(self.models[:1]), free_bytes=40 * GIB
        )
        self.assertEqual(
            enough_space["pullCommands"],
            [f"ollama pull {tag}" for tag in self.tags[1:]],
        )
        self.assertIsNone(enough_space["downloadBlockedReason"])

    def test_probe_requires_matching_approval_before_any_generation(self) -> None:
        client = FakeAdminClient(self.models)
        preflight = inspect_ollama_environment(client=client, free_bytes=40 * GIB)

        with self.assertRaisesRegex(CanonicalClassificationError, "aprovação"):
            probe_ollama_models(
                preflight=preflight,
                approved_probe_id="wrong",
                client=client,
                command_runner=lambda _: "",
                windows_log_reader=lambda: "",
            )

        self.assertEqual(client.chat_requests, [])

    def test_probe_id_ignores_harmless_free_space_fluctuation(self) -> None:
        first = inspect_ollama_environment(
            client=FakeAdminClient(self.models), free_bytes=40 * GIB
        )
        second = inspect_ollama_environment(
            client=FakeAdminClient(self.models), free_bytes=39 * GIB
        )

        self.assertEqual(first["probeId"], second["probeId"])

    def test_probe_uses_structured_output_and_requires_full_gpu(self) -> None:
        client = FakeAdminClient(self.models)
        preflight = inspect_ollama_environment(client=client, free_bytes=40 * GIB)

        report = probe_ollama_models(
            preflight=preflight,
            approved_probe_id=str(preflight["probeId"]),
            client=client,
            command_runner=lambda command: (
                "NAME ID SIZE PROCESSOR UNTIL\n"
                f"{client.active_model} abc 9 GB 100% GPU 4 minutes"
            ),
            windows_log_reader=lambda: (
                "time=... msg=\"offloaded 41/41 layers to GPU\"\n"
                "time=... msg=\"offloaded 49/49 layers to GPU\""
            ),
        )

        self.assertTrue(report["readyForBenchmark"])
        self.assertEqual(len(client.chat_requests), 3)
        self.assertEqual(client.unloaded, self.tags)
        for request in client.chat_requests:
            self.assertFalse(request["stream"])
            self.assertFalse(request["think"])
            self.assertFalse(request["format"]["additionalProperties"])
            self.assertEqual(request["options"]["num_ctx"], 4096)
            self.assertEqual(request["options"]["temperature"], 0)
        for result in report["models"]:
            self.assertEqual(result["processor"], "100% GPU")
            self.assertEqual(result["sizeBytes"], result["sizeVramBytes"])
            self.assertEqual(result["loadedLayers"], 49)
            self.assertEqual(result["totalLayers"], 49)

    def test_probe_rejects_partial_gpu_and_still_unloads_model(self) -> None:
        client = FakeAdminClient(self.models)
        preflight = inspect_ollama_environment(client=client, free_bytes=40 * GIB)

        with self.assertRaisesRegex(CanonicalClassificationError, "100% GPU"):
            probe_ollama_models(
                preflight=preflight,
                approved_probe_id=str(preflight["probeId"]),
                client=client,
                command_runner=lambda _: (
                    f"{client.active_model} abc 9 GB 80% GPU 20% CPU 4 minutes"
                ),
                windows_log_reader=lambda: "",
            )

        self.assertEqual(client.unloaded, [self.tags[0]])

    def test_cli_exposes_inspection_and_explicit_probe_gate(self) -> None:
        parser = build_parser()
        inspection = parser.parse_args(
            ["preflight-ollama-ai", "--report", "preflight.json"]
        )
        probe = parser.parse_args(
            [
                "preflight-ollama-ai",
                "--report",
                "preflight.json",
                "--probe-models",
                "--approved-probe-id",
                "probe-123",
            ]
        )

        self.assertFalse(inspection.probe_models)
        self.assertIsNone(inspection.approved_probe_id)
        self.assertTrue(probe.probe_models)
        self.assertEqual(probe.approved_probe_id, "probe-123")


if __name__ == "__main__":
    unittest.main()
