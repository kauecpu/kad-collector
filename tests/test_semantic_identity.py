import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from kad_collector.models import DocumentRecord
from kad_collector.semantic_identity import (
    SemanticEvidence,
    SemanticField,
    build_content_fingerprint,
    extract_semantic_profile,
    profile_from_document_record,
    semantic_identity_key,
)

try:
    from .semantic_helpers import identity, normalized_document
except ImportError:
    from semantic_helpers import identity, normalized_document


class SemanticContractTests(unittest.TestCase):
    def test_extracts_labeled_pdf_fields_without_source_rules(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf"), metadata={}),
            [(1, "Banca: Instituto Exemplo\nConcurso: Auditoria 2026\nAno: 2026\nCargo: Auditor")],
        )
        self.assertEqual(profile.identity.board.normalized_values, ("instituto exemplo",))
        self.assertEqual(profile.identity.year.normalized_values, (2026,))
        self.assertIsNotNone(profile.identity_key)

    def test_declared_year_conflicting_with_pdf_is_not_resolved(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(
                Path("prova.pdf"), metadata={"board": "X", "concurso": "Y", "year": 2025}
            ),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026")],
        )
        self.assertEqual(profile.identity.year.status, "conflict")
        self.assertIsNone(profile.identity_key)

    def test_weak_title_does_not_invent_minimum_identity(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf"), title="prova-fiscal-2026.pdf"),
            [(1, "Assinale a alternativa correta.")],
        )
        self.assertEqual(profile.identity.board.status, "unknown")
        self.assertIsNone(profile.identity_key)

    def test_answer_key_coverage_supports_multiple_roles_and_types(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("key.pdf"), declared_type="answer_key"),
            [(
                1,
                "Banca: X\nConcurso: Y\nAno: 2026\nCargos: Auditor; Analista\n"
                "Tipos: 1 a 4\nGabarito definitivo",
            )],
        )
        self.assertEqual(profile.answer_key_state, "definitive")
        self.assertEqual(profile.coverage.roles.status, "known")
        self.assertFalse(profile.has_conflict)
        self.assertEqual(profile.coverage.roles.normalized_values, ("analista", "auditor"))
        self.assertEqual(
            profile.coverage.variants.normalized_values,
            ("tipo 1", "tipo 2", "tipo 3", "tipo 4"),
        )

    def test_oversized_variant_interval_fails_closed_without_expansion(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("key.pdf"), declared_type="answer_key"),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026\nTipos: 1 a 10000")],
        )

        self.assertEqual(profile.coverage.variants.status, "unknown")
        self.assertIn("limite", profile.coverage.variants.reason)

    def test_oversized_variant_list_and_value_fail_closed(self) -> None:
        oversized_list = "; ".join(str(number) for number in range(1, 2001))
        list_profile = extract_semantic_profile(
            normalized_document(Path("list.pdf"), declared_type="answer_key"),
            [(1, f"Banca: X\nConcurso: Y\nAno: 2026\nTipos: {oversized_list}")],
        )
        value_profile = extract_semantic_profile(
            normalized_document(Path("value.pdf"), declared_type="answer_key"),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026\nTipo: 100000000")],
        )

        self.assertEqual(list_profile.coverage.variants.status, "unknown")
        self.assertEqual(value_profile.coverage.variants.status, "unknown")

    def test_oversized_labeled_line_is_not_preserved_as_evidence(self) -> None:
        secret = "sensitive-value-" * 400
        profile = extract_semantic_profile(
            normalized_document(Path("exam.pdf"), declared_type="exam"),
            [(1, f"Banca: {secret}\nConcurso: Y\nAno: 2026")],
        )

        self.assertEqual(profile.identity.board.status, "unknown")
        self.assertNotIn(secret, profile.model_dump_json())

    def test_conflicting_human_overrides_remain_unresolved(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf")),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026")],
            human_overrides={"year": (2025, 2026)},
        )
        self.assertEqual(profile.identity.year.status, "conflict")
        self.assertIsNone(profile.identity_key)

    def test_incompatible_strong_set_assertions_conflict(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf")),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026\nCargo: Auditor\nCargos: Analista")],
        )
        self.assertEqual(profile.identity.roles.status, "conflict")
        self.assertTrue(profile.has_conflict)

    def test_single_human_override_sets_effective_value_and_preserves_evidence(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf"), metadata={"year": 2025}),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026")],
            human_overrides={"year": 2027},
        )
        self.assertEqual(profile.identity.year.status, "known")
        self.assertEqual(profile.identity.year.normalized_values, (2027,))
        self.assertEqual(
            tuple(evidence.normalized_value for evidence in profile.identity.year.evidence),
            (2025, 2026, 2027),
        )
        self.assertEqual(profile.identity.year.method, "human_override")

    def test_divergent_scalar_human_overrides_conflict(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf")),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026")],
            human_overrides={"year": (2025, 2026)},
        )
        self.assertEqual(profile.identity.year.status, "conflict")

    def test_extracts_accented_organization_labels_preserving_raw_value(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf")),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026\nÓrgão: Órgão Exemplo\nOrganização: Instituto")],
        )
        self.assertEqual(profile.identity.organization.status, "conflict")
        self.assertIn("Órgão Exemplo", profile.identity.organization.raw_values)

    def test_unique_body_year_beats_weak_title_year(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("prova.pdf"), title="prova-2025.pdf"),
            [(1, "Banca: X\nConcurso: Y\nAplicação em 2026")],
        )
        self.assertEqual(profile.identity.year.normalized_values, (2026,))

    def test_role_and_state_markers_require_word_boundaries(self) -> None:
        false_positive = extract_semantic_profile(
            normalized_document(
                Path("documento.pdf"), title="comprovante-finalidade.pdf", declared_type="auto"
            ),
            [(1, "Texto")],
        )
        positive = extract_semantic_profile(
            normalized_document(
                Path("documento.pdf"), title="gabarito-definitivo.pdf", declared_type="auto"
            ),
            [(1, "Texto")],
        )
        self.assertEqual(false_positive.document_role, "unknown")
        self.assertEqual(positive.document_role, "answer_key")
        self.assertEqual(positive.answer_key_state, "definitive")

    def test_page_boundaries_do_not_join_role_or_state_markers(self) -> None:
        split_role = extract_semantic_profile(
            normalized_document(Path("documento.pdf"), title="documento.pdf", declared_type="auto"),
            [(1, "gaba"), (2, "rito")],
        )
        split_state = extract_semantic_profile(
            normalized_document(
                Path("documento.pdf"), title="documento.pdf", declared_type="answer_key"
            ),
            [(1, "defini"), (2, "tivo")],
        )
        positive = extract_semantic_profile(
            normalized_document(Path("documento.pdf"), title="documento.pdf", declared_type="auto"),
            [(1, "gabarito definitivo")],
        )
        self.assertEqual(split_role.document_role, "unknown")
        self.assertEqual(split_state.answer_key_state, "unknown")
        self.assertEqual(positive.document_role, "answer_key")
        self.assertEqual(positive.answer_key_state, "definitive")

    def test_ambiguous_answer_key_state_is_unknown(self) -> None:
        profile = extract_semantic_profile(
            normalized_document(Path("key.pdf"), declared_type="answer_key"),
            [(1, "Banca: X\nConcurso: Y\nAno: 2026\nGabarito preliminar e definitivo")],
        )
        self.assertEqual(profile.answer_key_state, "unknown")

    def test_document_record_adapter_uses_only_declared_record_fields(self) -> None:
        record = DocumentRecord(
            source_id="not-identity", source_name="not-identity", document_type="exam",
            title="prova.pdf", original_url="https://example.test/prova.pdf",
            resolved_url="https://example.test/prova.pdf", local_path="data/prova.pdf",
            sha256="b" * 64, content_type="application/pdf", size_bytes=100,
            downloaded_at=datetime(2026, 1, 1, tzinfo=UTC), authorization_basis="test",
            metadata={"banca": "X", "concurso": "Y", "ano": "2026"},
        )
        profile = profile_from_document_record(record, [(1, "Assinale a alternativa correta.")])
        self.assertIsNotNone(profile.identity_key)

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

    def test_identity_normalization_keeps_distinct_accents(self) -> None:
        accented = identity(board="Órgão", concurso="Contest", year=2026)
        plain = identity(board="Orgao", concurso="Contest", year=2026)
        self.assertNotEqual(semantic_identity_key(accented), semantic_identity_key(plain))

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
