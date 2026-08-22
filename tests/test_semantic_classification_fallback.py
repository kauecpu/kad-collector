from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kad_collector.desktop_classifier import (
    LocalRuleClassifier,
    OpenAIClassificationProvider,
)
from kad_collector.desktop_models import ClassificationRequest, DesktopImportMetadata
from kad_collector.editorial_taxonomy import EditorialTaxonomy


def _neutral_question(number: int = 1) -> ClassificationRequest:
    return ClassificationRequest(
        question_number=number,
        statement="Considere as informações e assinale a alternativa correta.",
        alternatives=["Primeira opção.", "Segunda opção.", "Terceira opção."],
    )


class _FakeResponses:
    def __init__(self, responder: object) -> None:
        self.responder = responder
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        responder = self.responder
        output = responder(kwargs) if callable(responder) else responder
        return SimpleNamespace(output_text=output)


class _FakeClient:
    def __init__(self, responder: object) -> None:
        self.responses = _FakeResponses(responder)


def _valid_choice(kwargs: dict[str, object]) -> str:
    payload = json.loads(str(kwargs["input"]))
    option = next(
        item
        for item in payload["taxonomy_options"]
        if item["discipline"] == "Direito Tributário"
    )
    return json.dumps(
        {
            "items": [
                {
                    "question_number": 1,
                    "option_id": option["id"],
                    "confidence": 0.91,
                    "evidence": "O enunciado trata expressamente de competência tributária.",
                }
            ]
        }
    )


class SemanticClassificationFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = EditorialTaxonomy.load_default()

    def test_without_api_key_returns_the_same_offline_local_result(self) -> None:
        questions = [
            ClassificationRequest(
                question_number=1,
                statement=(
                    "O controle aduaneiro e a administração aduaneira fiscalizam "
                    "a aduana."
                ),
                alternatives=["A", "B", "C"],
            )
        ]
        expected = LocalRuleClassifier(self.taxonomy).classify_many(
            questions, DesktopImportMetadata()
        )

        with patch.dict(os.environ, {}, clear=True):
            actual = OpenAIClassificationProvider(
                taxonomy=self.taxonomy
            ).classify_many(questions, DesktopImportMetadata())

        self.assertEqual(actual, expected)

    def test_ai_can_only_select_a_closed_taxonomy_option(self) -> None:
        client = _FakeClient(_valid_choice)
        provider = OpenAIClassificationProvider(
            model="modelo-teste", client=client, taxonomy=self.taxonomy
        )

        result = provider.classify_many(
            [_neutral_question()], DesktopImportMetadata()
        )[0].classification

        call = client.responses.calls[0]
        payload = json.loads(str(call["input"]))
        option_ids = {item["id"] for item in payload["taxonomy_options"]}
        schema = call["text"]["format"]["schema"]
        schema_ids = set(
            schema["properties"]["items"]["items"]["properties"]["option_id"][
                "enum"
            ]
        )
        self.assertEqual(schema_ids, option_ids)
        self.assertEqual(result.discipline.value, "Direito Tributário")
        self.assertEqual(result.discipline.source, "openai_taxonomy_choice")
        self.assertIn("modelo-teste", result.discipline.reason or "")
        self.assertIn("model:modelo-teste", result.discipline.provenance)

    def test_unknown_ai_option_is_ignored(self) -> None:
        client = _FakeClient(
            json.dumps(
                {
                    "items": [
                        {
                            "question_number": 1,
                            "option_id": "disciplina-inventada",
                            "confidence": 0.99,
                            "evidence": "Resposta inválida.",
                        }
                    ]
                }
            )
        )
        result = OpenAIClassificationProvider(
            client=client, taxonomy=self.taxonomy
        ).classify_many([_neutral_question()], DesktopImportMetadata())[0]

        self.assertIsNone(result.classification.discipline.value)
        self.assertEqual(result.classification.discipline.source, "unresolved")

    def test_incomplete_or_low_confidence_ai_response_is_ignored(self) -> None:
        def incomplete(kwargs: dict[str, object]) -> str:
            payload = json.loads(str(kwargs["input"]))
            return json.dumps(
                {
                    "items": [
                        {
                            "question_number": 1,
                            "option_id": payload["taxonomy_options"][0]["id"],
                            "confidence": 0.95,
                        }
                    ]
                }
            )

        def low_confidence(kwargs: dict[str, object]) -> str:
            payload = json.loads(str(kwargs["input"]))
            return json.dumps(
                {
                    "items": [
                        {
                            "question_number": 1,
                            "option_id": payload["taxonomy_options"][0]["id"],
                            "confidence": 0.5,
                            "evidence": "Sinal insuficiente.",
                        }
                    ]
                }
            )

        for responder in (incomplete, low_confidence):
            with self.subTest(responder=responder.__name__):
                result = OpenAIClassificationProvider(
                    client=_FakeClient(responder), taxonomy=self.taxonomy
                ).classify_many([_neutral_question()], DesktopImportMetadata())[0]
                self.assertIsNone(result.classification.discipline.value)
                self.assertEqual(result.classification.discipline.source, "unresolved")

    def test_ai_is_not_called_when_local_evidence_completed_the_path(self) -> None:
        client = _FakeClient(_valid_choice)
        question = ClassificationRequest(
            question_number=1,
            statement=(
                "O controle aduaneiro e a administração aduaneira fiscalizam "
                "a aduana."
            ),
            alternatives=["A", "B", "C"],
        )

        result = OpenAIClassificationProvider(
            client=client, taxonomy=self.taxonomy
        ).classify_many([question], DesktopImportMetadata())[0].classification

        self.assertEqual(result.discipline.value, "Legislação Aduaneira")
        self.assertEqual(client.responses.calls, [])

    def test_repeated_fallback_is_idempotent(self) -> None:
        client = _FakeClient(_valid_choice)
        provider = OpenAIClassificationProvider(
            model="modelo-teste", client=client, taxonomy=self.taxonomy
        )

        first = provider.classify_many([_neutral_question()], DesktopImportMetadata())
        second = provider.classify_many([_neutral_question()], DesktopImportMetadata())

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
