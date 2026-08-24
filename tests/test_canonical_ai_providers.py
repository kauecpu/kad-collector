from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from kad_collector.canonical_ai_providers import (
    DeepSeekCanonicalEnrichmentProvider,
    GeminiCanonicalEnrichmentProvider,
    QwenCanonicalEnrichmentProvider,
    create_canonical_ai_provider,
)
from kad_collector.canonical_classification import (
    AISuggestion,
    CanonicalAIRequest,
    CanonicalAIResponse,
    CanonicalClassificationError,
)
from kad_collector.cli import build_parser


def _request() -> CanonicalAIRequest:
    return CanonicalAIRequest(
        canonical_question_id="canonical-1",
        content_fingerprint="sha256",
        requested_fields=("difficulty",),
        statement="A norma apresentada resolve a situação descrita.",
        alternatives=("Primeira alternativa", "Segunda alternativa"),
        known_fields={"discipline": "Direito"},
        taxonomy_version="1.0.0",
        taxonomy_options=(
            {
                "discipline": "Direito",
                "matter": "Normas",
                "subject": "Aplicação da lei",
            },
        ),
    )


def _response_payload() -> dict[str, object]:
    return {
        "suggestions": [
            {
                "field": "difficulty",
                "value": "Média",
                "confidence": 0.91,
                "evidence": "O enunciado exige interpretação da regra apresentada.",
            }
        ]
    }


class _Recorder:
    def __init__(self, completion: Any) -> None:
        self.completion = completion
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.completion

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.completion


def _completion(*, parsed: CanonicalAIResponse | None = None) -> Any:
    message = SimpleNamespace(
        content=json.dumps(_response_payload(), ensure_ascii=False),
        parsed=parsed,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=35),
    )


def _client(completion: Any) -> tuple[Any, _Recorder, _Recorder]:
    chat_recorder = _Recorder(completion)
    beta_recorder = _Recorder(completion)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=chat_recorder),
        beta=SimpleNamespace(chat=SimpleNamespace(completions=beta_recorder)),
    )
    return client, chat_recorder, beta_recorder


class CanonicalAIProvidersTests(unittest.TestCase):
    def test_cli_accepts_all_dormant_providers(self) -> None:
        parser = build_parser()
        for provider in ("openai", "gemini", "qwen", "deepseek"):
            with self.subTest(provider=provider):
                args = parser.parse_args(
                    [
                        "classify-canonical-questions",
                        "--database",
                        "collector.sqlite3",
                        "--provider",
                        provider,
                    ]
                )
                self.assertEqual(args.provider, provider)
                self.assertFalse(args.enable_ai)

    def test_qwen_uses_json_mode_and_disables_thinking(self) -> None:
        client, recorder, _ = _client(_completion())
        provider = QwenCanonicalEnrichmentProvider(client=client)

        result = provider.enrich(_request())

        self.assertEqual(provider.model, "qwen3.7-plus")
        self.assertEqual(result.response, _response_payload())
        self.assertEqual((result.input_tokens, result.output_tokens), (120, 35))
        call = recorder.calls[0]
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertEqual(call["extra_body"], {"enable_thinking": False})
        self.assertIn("JSON", call["messages"][0]["content"])
        sent = json.loads(call["messages"][1]["content"])
        self.assertEqual(sent["outputSchema"]["properties"]["suggestions"]["items"]
                         ["properties"]["field"]["enum"], ["difficulty"])
        self.assertNotIn("correct_answer", json.dumps(sent))

    def test_deepseek_uses_json_mode_and_disables_thinking(self) -> None:
        client, recorder, _ = _client(_completion())
        provider = DeepSeekCanonicalEnrichmentProvider(client=client)

        result = provider.enrich(_request())

        self.assertEqual(provider.model, "deepseek-v4-pro")
        self.assertEqual(result.response, _response_payload())
        call = recorder.calls[0]
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertEqual(call["extra_body"], {"thinking": {"type": "disabled"}})

    def test_gemini_uses_structured_parse_with_low_reasoning(self) -> None:
        parsed = CanonicalAIResponse(
            suggestions=[
                AISuggestion(
                    field="difficulty",
                    value="Média",
                    confidence=0.91,
                    evidence="O enunciado exige interpretação da regra apresentada.",
                )
            ]
        )
        client, _, recorder = _client(_completion(parsed=parsed))
        provider = GeminiCanonicalEnrichmentProvider(client=client)

        result = provider.enrich(_request())

        self.assertEqual(provider.model, "gemini-3.7-flash")
        self.assertEqual(result.response, _response_payload())
        call = recorder.calls[0]
        self.assertIs(call["response_format"], CanonicalAIResponse)
        self.assertEqual(call["reasoning_effort"], "low")

    def test_missing_keys_do_not_activate_external_calls(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            providers = (
                GeminiCanonicalEnrichmentProvider(),
                QwenCanonicalEnrichmentProvider(),
                DeepSeekCanonicalEnrichmentProvider(),
            )
        for provider in providers:
            with (
                self.subTest(provider=provider.name),
                self.assertRaisesRegex(CanonicalClassificationError, "configure"),
            ):
                provider.enrich(_request())

    def test_factory_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(CanonicalClassificationError, "desconhecido"):
            create_canonical_ai_provider("unknown")


if __name__ == "__main__":
    unittest.main()
