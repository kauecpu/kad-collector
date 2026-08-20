import unittest

from kad_collector.semantic_identity import (
    SemanticEvidence,
    SemanticField,
    build_content_fingerprint,
    semantic_identity_key,
)

from .semantic_helpers import identity


class SemanticContractTests(unittest.TestCase):
    def test_unknown_field_has_no_value_or_confidence(self) -> None:
        field = SemanticField.unknown("ano não localizado")
        self.assertEqual(field.status, "unknown")
        self.assertEqual(field.normalized_values, ())
        self.assertIsNone(field.confidence)

    def test_conflicting_field_preserves_both_evidences(self) -> None:
        field = SemanticField.from_evidence(
            "year", (
                SemanticEvidence.metadata("year", 2025),
                SemanticEvidence.pdf_text("page:1", 2026),
            )
        )
        self.assertEqual(field.status, "conflict")
        self.assertEqual(field.normalized_values, (2025, 2026))

    def test_identity_key_ignores_path_dates_and_evidence_order(self) -> None:
        first = identity(board="FGV", concurso="Receita Federal", year=2026)
        second = identity(board=" fgv ", concurso="  Receita   Federal ", year=2026)
        second = second.model_copy(update={"board": SemanticField.from_evidence(
            "board", (
                SemanticEvidence.pdf_text("page:2", "FGV"),
                SemanticEvidence.metadata("board", "fgv"),
            )
        )})
        first = first.model_copy(update={"board": SemanticField.from_evidence(
            "board", (
                SemanticEvidence.metadata("board", "FGV"),
                SemanticEvidence.pdf_text("page:1", "FGV"),
            )
        )})
        self.assertEqual(semantic_identity_key(first), semantic_identity_key(second))

    def test_identity_key_requires_board_contest_and_year(self) -> None:
        self.assertIsNone(semantic_identity_key(identity(board=None, concurso="RF", year=2026)))

    def test_content_fingerprint_tolerates_only_layout_whitespace(self) -> None:
        first = build_content_fingerprint([(1, "Questão 1\nA) azul  B) verde")])
        second = build_content_fingerprint([(1, "Questão 1\r\nA) azul   B) verde  ")])
        changed = build_content_fingerprint([(1, "Questão 1\nA) azul B) vermelho")])
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotEqual(first.sha256, changed.sha256)
