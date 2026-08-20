import unittest

from pydantic import ValidationError

from kad_collector.semantic_identity import (
    SemanticEvidence,
    SemanticField,
    build_content_fingerprint,
    semantic_identity_key,
)

try:
    from .semantic_helpers import identity
except ImportError:
    from semantic_helpers import identity


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

    def test_semantic_models_are_strict_and_immutable(self) -> None:
        field = SemanticField.unknown("missing")
        with self.assertRaises(ValidationError):
            SemanticField(status="known", method="test", reason="x", confidence="1")
        with self.assertRaises(ValidationError):
            SemanticField(status="known", method="test", reason="x", extra="nope")
        with self.assertRaises(ValidationError):
            field.status = "known"

    def test_unknown_field_rejects_values_evidence_and_confidence(self) -> None:
        evidence = (SemanticEvidence.metadata("year", 2026),)
        for kwargs in (
            {"raw_values": (2026,)},
            {"normalized_values": (2026,)},
            {"evidence": evidence},
            {"confidence": 0.0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                SemanticField(status="unknown", method="test", reason="missing", **kwargs)

    def test_semantic_field_states_require_coherent_values(self) -> None:
        with self.assertRaises(ValidationError):
            SemanticField(status="known", method="test", reason="missing")
        with self.assertRaises(ValidationError):
            SemanticField(status="conflict", method="test", reason="x", normalized_values=(1,))

    def test_conflicting_field_rejects_confidence(self) -> None:
        with self.assertRaises(ValidationError):
            SemanticField(
                status="conflict", method="test", reason="x",
                normalized_values=(1, "1"), confidence=1.0,
            )

    def test_value_sorting_discriminates_types(self) -> None:
        first = SemanticField.from_evidence(
            "value", (SemanticEvidence.metadata("a", 1), SemanticEvidence.metadata("b", "1"))
        )
        second = SemanticField.from_evidence(
            "value", (SemanticEvidence.metadata("b", "1"), SemanticEvidence.metadata("a", 1))
        )
        self.assertEqual(first.normalized_values, (1, "1"))
        self.assertEqual(first, second)

    def test_identity_normalization_preserves_compatibility_characters(self) -> None:
        ascii_identity = identity(board="A", concurso="Contest", year=2026)
        compatibility_identity = identity(board="Ａ", concurso="Contest", year=2026)
        spaced_case_identity = identity(board=" a ", concurso="  CONTEST ", year=2026)
        self.assertNotEqual(
            semantic_identity_key(ascii_identity), semantic_identity_key(compatibility_identity)
        )
        self.assertEqual(
            semantic_identity_key(ascii_identity), semantic_identity_key(spaced_case_identity)
        )

    def test_evidence_sorting_discriminates_types_with_equal_location(self) -> None:
        first = SemanticField.from_evidence(
            "value", (
                SemanticEvidence.metadata("same", 1),
                SemanticEvidence.metadata("same", "1"),
            )
        )
        second = SemanticField.from_evidence(
            "value", (
                SemanticEvidence.metadata("same", "1"),
                SemanticEvidence.metadata("same", 1),
            )
        )
        self.assertEqual(first, second)

    def test_content_fingerprint_frames_literal_page_marker_structurally(self) -> None:
        literal = build_content_fingerprint([(1, "x\n--- PAGE 2 ---\ny")])
        segmented = build_content_fingerprint([(1, "x"), (2, "y")])
        self.assertNotEqual(literal.sha256, segmented.sha256)
