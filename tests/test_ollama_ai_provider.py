from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest.mock import patch

import httpx

from kad_collector.canonical_classification import (
    CanonicalAIHTTPError,
    CanonicalAIInvalidJSONError,
    CanonicalAIRequest,
    CanonicalClassificationError,
)
from kad_collector.ollama_ai_provider import (
    OllamaCanonicalEnrichmentProvider,
    OllamaUnavailableError,
    validate_ollama_base_url,
)

MODEL = "qwen3:14b"
PATH_ID = "generic-public-exam:direito:normas:aplicacao-da-lei"


def _request() -> CanonicalAIRequest:
    return CanonicalAIRequest(
        canonical_question_id="canonical-1",
        content_fingerprint="sha256",
        requested_fields=("matter", "subject"),
        statement="A norma apresentada resolve a situação descrita.",
        alternatives=("Primeira alternativa", "Segunda alternativa"),
        known_fields={"discipline": "Direito", "level": "Superior"},
        taxonomy_version="2.0.1",
        taxonomy_options=(
            {
                "pathId": PATH_ID,
                "discipline": "Direito",
                "matter": "Normas",
                "subject": "Aplicação da lei",
                "keywords": ["norma apresentada"],
            },
        ),
    )


def _decision() -> dict[str, Any]:
    return {
        "taxonomy": {
            "pathId": PATH_ID,
            "confidence": 0.91,
            "evidence": "A questão cobra a norma aplicável.",
        }
    }


def _ollama_response() -> dict[str, Any]:
    return {
        "model": MODEL,
        "created_at": "2026-08-24T12:00:00Z",
        "message": {
            "role": "assistant",
            "content": json.dumps(_decision(), ensure_ascii=False),
            "thinking": "",
        },
        "done": True,
        "done_reason": "stop",
        "total_duration": 1_000_000_000,
        "load_duration": 200_000_000,
        "prompt_eval_count": 321,
        "prompt_eval_duration": 100_000_000,
        "eval_count": 24,
        "eval_duration": 200_000_000,
    }


class OllamaAIProviderTests(unittest.TestCase):
    def test_defaults_to_loopback_without_api_key_or_background_call(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("kad_collector.ollama_ai_provider.httpx.Client") as client_factory,
        ):
            provider = OllamaCanonicalEnrichmentProvider(MODEL)

        self.assertEqual(provider.model, MODEL)
        client_factory.assert_called_once_with(
            base_url="http://127.0.0.1:11434",
            timeout=180.0,
            trust_env=False,
        )
        client_factory.return_value.post.assert_not_called()

    def test_posts_native_schema_request_and_returns_usage_metrics(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json=_ollama_response())

        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:11434",
        )
        provider = OllamaCanonicalEnrichmentProvider(MODEL, client=client)

        result = provider.enrich(_request())

        self.assertEqual(result.response, _decision())
        self.assertEqual((result.input_tokens, result.output_tokens), (321, 24))
        self.assertEqual(
            result.provider_metrics,
            {
                "totalDurationNs": 1_000_000_000,
                "loadDurationNs": 200_000_000,
                "promptEvalDurationNs": 100_000_000,
                "evalDurationNs": 200_000_000,
                "doneReason": "stop",
            },
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/api/chat")
        sent = json.loads(calls[0].content)
        self.assertEqual(sent["model"], MODEL)
        self.assertFalse(sent["stream"])
        self.assertFalse(sent["think"])
        self.assertEqual(sent["keep_alive"], "5m")
        self.assertEqual(
            sent["options"],
            {"temperature": 0, "num_ctx": 4096, "num_predict": 512, "seed": 0},
        )
        self.assertFalse(sent["format"]["additionalProperties"])
        path_schema = sent["format"]["properties"]["taxonomy"]["properties"][
            "pathId"
        ]
        self.assertEqual(path_schema["enum"], [PATH_ID])
        serialized = json.dumps(sent, ensure_ascii=False)
        self.assertNotIn("difficulty", serialized)
        self.assertNotIn("explanation", serialized)
        self.assertNotIn("correct_answer", serialized)

    def test_rejects_non_loopback_credentials_query_and_path(self) -> None:
        invalid = (
            "http://192.168.0.10:11434",
            "https://example.com",
            "http://user:password@localhost:11434",
            "http://localhost:11434?key=value",
            "http://localhost:11434/api",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(CanonicalClassificationError):
                validate_ollama_base_url(value)

        self.assertEqual(
            validate_ollama_base_url("http://localhost:11434/"),
            "http://localhost:11434",
        )
        self.assertEqual(
            validate_ollama_base_url("http://[::1]:11434"),
            "http://[::1]:11434",
        )

    def test_rejects_injected_client_with_remote_base_url(self) -> None:
        remote_client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=_ollama_response())
            ),
            base_url="https://example.com",
        )

        with self.assertRaisesRegex(CanonicalClassificationError, "loopback"):
            OllamaCanonicalEnrichmentProvider(MODEL, client=remote_client)

    def test_connection_failure_raises_availability_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:11434",
        )
        provider = OllamaCanonicalEnrichmentProvider(MODEL, client=client)

        with self.assertRaisesRegex(OllamaUnavailableError, "indisponível"):
            provider.enrich(_request())

    def test_http_and_invalid_json_failures_keep_distinct_types(self) -> None:
        for response, expected in (
            (httpx.Response(500, json={"error": "offline"}), CanonicalAIHTTPError),
            (
                httpx.Response(
                    200,
                    json={
                        **_ollama_response(),
                        "message": {"role": "assistant", "content": "not-json"},
                    },
                ),
                CanonicalAIInvalidJSONError,
            ),
        ):
            with self.subTest(expected=expected.__name__):
                client = httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, current=response: current
                    ),
                    base_url="http://127.0.0.1:11434",
                )
                provider = OllamaCanonicalEnrichmentProvider(MODEL, client=client)

                with self.assertRaises(expected):
                    provider.enrich(_request())

    def test_missing_model_is_a_configuration_error_without_retry(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(404, json={"error": "model not found"})

        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:11434",
        )
        provider = OllamaCanonicalEnrichmentProvider(MODEL, client=client)

        with self.assertRaisesRegex(CanonicalClassificationError, "modelo.*não está instalado"):
            provider.enrich(_request())
        self.assertEqual(calls, 1)

    def test_model_must_be_explicit_or_configured_in_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            provider = OllamaCanonicalEnrichmentProvider()
        with self.assertRaisesRegex(CanonicalClassificationError, "OLLAMA_MODEL"):
            provider.enrich(_request())


if __name__ == "__main__":
    unittest.main()
