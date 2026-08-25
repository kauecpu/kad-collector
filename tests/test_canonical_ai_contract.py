from __future__ import annotations

import unittest

from kad_collector.canonical_classification import (
    CanonicalAIHTTPError,
    CanonicalAIInvalidJSONError,
    CanonicalAIProviderUnavailableError,
    CanonicalAIRequest,
    CanonicalAIValidationError,
    _validate_ai_response,
    canonical_ai_error_code,
    canonical_ai_response_schema,
)
from kad_collector.editorial_taxonomy import EditorialTaxonomy

PATH_ID = "generic-public-exam:direito:normas:aplicacao-da-lei"


def _taxonomy() -> EditorialTaxonomy:
    return EditorialTaxonomy(
        {
            "id": "generic-public-exam",
            "version": "1.0.0",
            "sources": ["https://example.test/programa"],
            "disciplines": [
                {
                    "name": "Direito",
                    "topics": [
                        {
                            "matter": "Normas",
                            "subject": "Aplicação da lei",
                            "keywords": ["norma apresentada"],
                        }
                    ],
                },
                {
                    "name": "Administração",
                    "topics": [
                        {
                            "matter": "Gestão",
                            "subject": "Planejamento",
                            "keywords": ["planejamento"],
                        }
                    ],
                },
            ],
        }
    )


def _request(
    *,
    requested_fields: tuple[str, ...] = ("matter", "subject", "level"),
    known_fields: dict[str, str] | None = None,
    options: tuple[dict[str, object], ...] | None = None,
) -> CanonicalAIRequest:
    return CanonicalAIRequest(
        canonical_question_id="canonical-1",
        content_fingerprint="raw-sha256",
        requested_fields=requested_fields,
        statement="A norma apresentada resolve a situação.",
        alternatives=("Alternativa A", "Alternativa B"),
        known_fields=known_fields or {"discipline": "Direito"},
        taxonomy_version="1.0.0",
        taxonomy_options=options
        or (
            {
                "pathId": PATH_ID,
                "discipline": "Direito",
                "matter": "Normas",
                "subject": "Aplicação da lei",
                "keywords": ["norma apresentada"],
            },
        ),
    )


def _decision() -> dict[str, object]:
    return {
        "taxonomy": {
            "pathId": PATH_ID,
            "confidence": 0.91,
            "evidence": "A questão pede a aplicação da norma apresentada.",
        },
        "level": {
            "value": "Superior",
            "confidence": 0.88,
            "evidence": "O cargo exige nível superior.",
        },
    }


class CanonicalAIContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = _taxonomy()

    def test_schema_enumerates_path_and_level_without_legacy_suggestions(self) -> None:
        schema = canonical_ai_response_schema(_request())

        self.assertNotIn("suggestions", schema["properties"])
        self.assertEqual(
            schema["properties"]["taxonomy"]["properties"]["pathId"]["enum"],
            [PATH_ID],
        )
        self.assertEqual(
            schema["properties"]["level"]["properties"]["value"]["enum"],
            ["Fundamental", "Médio", "Superior"],
        )
        self.assertFalse(schema["additionalProperties"])

    def test_valid_path_populates_only_requested_taxonomy_fields(self) -> None:
        request = _request(requested_fields=("subject",))

        accepted, low_confidence = _validate_ai_response(
            {"taxonomy": _decision()["taxonomy"]},
            request=request,
            taxonomy=self.taxonomy,
        )

        self.assertEqual(set(accepted), {"subject"})
        self.assertEqual(accepted["subject"][0], "Aplicação da lei")
        self.assertEqual(low_confidence, [])

    def test_unknown_path_has_an_explicit_validation_code(self) -> None:
        response = _decision()
        response["taxonomy"] = {
            "pathId": "unknown:path",
            "confidence": 0.91,
            "evidence": "Evidência suficiente.",
        }

        with self.assertRaises(CanonicalAIValidationError) as raised:
            _validate_ai_response(
                response,
                request=_request(),
                taxonomy=self.taxonomy,
            )

        self.assertEqual(raised.exception.code, "unknown_taxonomy_path")

    def test_path_conflicting_with_known_fields_is_rejected(self) -> None:
        administration = {
            "pathId": "generic-public-exam:administracao:gestao:planejamento",
            "discipline": "Administração",
            "matter": "Gestão",
            "subject": "Planejamento",
            "keywords": ["planejamento"],
        }
        response = {
            "taxonomy": {
                "pathId": administration["pathId"],
                "confidence": 0.91,
                "evidence": "Evidência suficiente.",
            }
        }

        with self.assertRaises(CanonicalAIValidationError) as raised:
            _validate_ai_response(
                response,
                request=_request(
                    requested_fields=("subject",),
                    options=(administration,),
                ),
                taxonomy=self.taxonomy,
            )

        self.assertEqual(raised.exception.code, "incompatible_taxonomy_path")

    def test_invalid_level_and_prohibited_field_have_distinct_codes(self) -> None:
        with self.assertRaises(CanonicalAIValidationError) as invalid_level:
            _validate_ai_response(
                {
                    "level": {
                        "value": "Fiscal Federal",
                        "confidence": 0.91,
                        "evidence": "O cargo é fiscal.",
                    }
                },
                request=_request(requested_fields=("level",)),
                taxonomy=self.taxonomy,
            )
        self.assertEqual(invalid_level.exception.code, "invalid_level")

        with self.assertRaises(CanonicalAIValidationError) as prohibited:
            _validate_ai_response(
                {"difficulty": "Difícil"},
                request=_request(requested_fields=("level",)),
                taxonomy=self.taxonomy,
            )
        self.assertEqual(prohibited.exception.code, "prohibited_field")

    def test_error_codes_use_exception_types_instead_of_message_words(self) -> None:
        self.assertEqual(
            canonical_ai_error_code(
                CanonicalAIProviderUnavailableError("qualquer mensagem")
            ),
            "provider_transport_failure",
        )
        self.assertEqual(
            canonical_ai_error_code(CanonicalAIHTTPError("HTTP recusado")),
            "provider_http_failure",
        )
        self.assertEqual(
            canonical_ai_error_code(CanonicalAIInvalidJSONError("conteúdo inválido")),
            "invalid_json",
        )
        self.assertEqual(
            canonical_ai_error_code(
                CanonicalAIValidationError("invalid_level", "texto sem palavra nível")
            ),
            "invalid_level",
        )


if __name__ == "__main__":
    unittest.main()
