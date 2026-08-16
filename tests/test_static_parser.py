from __future__ import annotations

import unittest

from kad_collector.static_parser import FuvestStaticExtractor


class FuvestStaticExtractorTests(unittest.TestCase):
    def test_extracts_real_markers_context_alternatives_and_pages(self) -> None:
        text = """
--- Pagina 2 ---
Concurso Vestibular FUVEST 2026 – Prova V1 ¬
Texto para as questões 01 e 02
Texto¬de¬apoio¬compartilhado.
{01}
Primeiro¬enunciado.
(A)¬Primeira alternativa.
(B)¬Segunda alternativa.
#####
{02}
Segundo enunciado.
(A)¬
(B)¬Alternativa em figura.
#####
"""

        result = FuvestStaticExtractor().extract(text, {})

        self.assertEqual([question.number for question in result.questions], [1, 2])
        self.assertIn("Texto de apoio compartilhado", result.questions[0].statement)
        self.assertIn("Texto de apoio compartilhado", result.questions[1].statement)
        self.assertEqual(result.questions[0].source_pages, [2])
        self.assertEqual(
            [alternative.letter for alternative in result.questions[0].alternatives],
            ["A", "B"],
        )
        self.assertIn("Alternativa visual A", result.questions[1].alternatives[0].text)
        self.assertTrue(any("alternativas visuais" in item for item in result.warnings))

    def test_ignores_question_without_final_marker(self) -> None:
        text = """
--- Pagina 1 ---
{01}
Questao cortada.
(A) Uma alternativa.
(B) Outra alternativa.
"""

        result = FuvestStaticExtractor().extract(text, {})

        self.assertEqual(result.questions, [])
        self.assertTrue(result.chunk_has_continuation)


if __name__ == "__main__":
    unittest.main()
