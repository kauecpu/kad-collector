from __future__ import annotations

import unittest

from kad_collector.canonical_ai_input import (
    find_canonical_ai_artifacts,
    sanitize_canonical_ai_content,
)


class CanonicalAIInputTests(unittest.TestCase):
    def test_removes_fgv_footer_without_mutating_raw_content(self) -> None:
        statement = "Assinale a alternativa correta."
        alternatives = (
            "Resposta A.",
            "Resposta E.\nCONCURSO PÚBLICO DA RECEITA FEDERAL DO BRASIL "
            "FGV CONHECIMENTO\nANALISTA TRIBUTÁRIO TIPO BRANCA – PÁGINA 13",
        )
        original = tuple(alternatives)

        sanitized = sanitize_canonical_ai_content(statement, alternatives)

        self.assertEqual(sanitized.statement, statement)
        self.assertEqual(sanitized.alternatives, ("Resposta A.", "Resposta E."))
        self.assertEqual(alternatives, original)
        self.assertIn("fgv_footer", sanitized.removed_artifacts)
        self.assertIn("page_footer", sanitized.removed_artifacts)
        self.assertEqual(
            find_canonical_ai_artifacts(
                sanitized.statement,
                sanitized.alternatives,
            ),
            (),
        )

    def test_removes_calendar_and_page_block_from_last_alternative(self) -> None:
        alternatives = (
            "Resposta A.",
            "Resposta E.\nSe Te Qua Qui Sex Sab Do\n1 2 3 4 5 6 7\nOUTUBRO\n"
            "FGV CONHECIMENTO\nTIPO BRANCA – PÁGINA 8",
        )

        sanitized = sanitize_canonical_ai_content("Questão.", alternatives)

        self.assertEqual(sanitized.alternatives[-1], "Resposta E.")
        self.assertEqual(
            set(sanitized.removed_artifacts),
            {"calendar", "fgv_footer", "page_footer"},
        )

    def test_removes_official_heading_only_when_appended_to_last_alternative(self) -> None:
        alternatives = (
            "Sigilo Fiscal é o tema desta alternativa.",
            "Resposta final.\nSIGILO FISCAL",
        )

        sanitized = sanitize_canonical_ai_content(
            "Questão.",
            alternatives,
            official_headings=("Sigilo Fiscal", "Tributação e Contencioso"),
        )

        self.assertEqual(
            sanitized.alternatives,
            ("Sigilo Fiscal é o tema desta alternativa.", "Resposta final."),
        )
        self.assertEqual(sanitized.removed_artifacts, ("section_heading_bleed",))

    def test_cleaned_content_has_its_own_stable_fingerprint(self) -> None:
        clean = sanitize_canonical_ai_content("Questão.", ("A", "B"))
        changed = sanitize_canonical_ai_content("Questão alterada.", ("A", "B"))

        self.assertEqual(len(clean.prompt_content_fingerprint), 64)
        self.assertNotEqual(
            clean.prompt_content_fingerprint,
            changed.prompt_content_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
