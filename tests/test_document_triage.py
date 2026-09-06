from __future__ import annotations

import unittest

from kad_collector.document_triage import TRIAGE_ALGORITHM_VERSION, classify_document


class DocumentTriageTests(unittest.TestCase):
    def test_fgv_standalone_numbers_and_parenthesized_alternatives(self) -> None:
        result = classify_document(
            filename="caderno-01.pdf", title=None, declared_type="auto",
            text=(
                "1\nQual opção descreve a situação fictícia?\n(A) Primeira\n(B) Segunda\n"
                "2\nQual procedimento atende ao caso?\n(A) Primeiro\n(B) Segundo\n"
            ),
        )
        self.assertEqual(result.decision, "exam")

    def test_numbered_prose_is_not_an_answer_key(self) -> None:
        result = classify_document(
            filename="caderno.pdf", title=None, declared_type="auto",
            text="1. A organização define regras.\n2. A unidade recebe pedidos.\n3. A lei regula.",
        )
        self.assertEqual(result.decision, "review")

    def test_bare_numbered_list_without_alternatives_stays_in_review(self) -> None:
        result = classify_document(
            filename="lista.pdf", title=None, declared_type="auto",
            text="1\nPrimeiro assunto\n2\nSegundo assunto\n3\nTerceiro assunto",
        )
        self.assertEqual(result.decision, "review")

    def test_simple_answer_rows_remain_a_key(self) -> None:
        result = classify_document(
            filename="001.pdf", title=None, declared_type="auto",
            text="1 - A\n2 - C\n3 - D\n",
        )
        self.assertEqual(result.decision, "answer_key")

    def test_manual_other_is_preserved_as_an_explicit_decision(self) -> None:
        result = classify_document(
            filename="arquivo.pdf", title=None, text="", declared_type="other"
        )

        self.assertEqual(result.decision, "other")
        self.assertEqual(result.source, "manual")
        self.assertEqual(result.confidence, 1)

    def test_detects_a_synthetic_edital_with_strong_evidence(self) -> None:
        result = classify_document(
            filename="edital-de-abertura.pdf",
            title="Edital do concurso",
            text="O órgão torna público o cronograma e o prazo para recurso das inscrições.",
            declared_type="auto",
        )

        self.assertEqual(result.decision, "other")
        self.assertGreaterEqual(result.confidence, 0.9)
        self.assertEqual(result.algorithm_version, TRIAGE_ALGORITHM_VERSION)

    def test_detects_a_synthetic_comunicado(self) -> None:
        result = classify_document(
            filename="comunicado-aos-candidatos.pdf",
            title=None,
            text="COMUNICADO. Candidatos convocados devem observar o cronograma.",
            declared_type="auto",
        )

        self.assertEqual(result.decision, "other")

    def test_question_structure_prevents_a_valid_exam_from_being_rejected(self) -> None:
        result = classify_document(
            filename="edital-prova.pdf",
            title="Prova objetiva",
            text=(
                "QUESTÃO 1\nQual alternativa está correta?\n"
                "A) Primeira opção\nB) Segunda opção\nC) Terceira opção"
            ),
            declared_type="auto",
        )

        self.assertEqual(result.decision, "exam")

    def test_ambiguous_document_requires_human_review(self) -> None:
        result = classify_document(
            filename="arquivo-2026.pdf",
            title=None,
            text="Material informativo sem estrutura conclusiva.",
            declared_type="auto",
        )

        self.assertEqual(result.decision, "review")
        self.assertIn("decidido por uma pessoa", result.reason)

    def test_ocr_text_uses_the_same_local_rules(self) -> None:
        result = classify_document(
            filename="digitalizado.pdf",
            title=None,
            text=(
                "QUESTÃO 7\nTexto recuperado por OCR com conteúdo suficiente.\n"
                "A) Um\nB) Dois\nC) Três"
            ),
            declared_type="auto",
        )

        self.assertEqual(result.decision, "exam")


if __name__ == "__main__":
    unittest.main()
