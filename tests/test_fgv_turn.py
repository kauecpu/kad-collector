import unittest

from kad_collector.fgv_turn import extract_fgv_turn_evidence


class FgvTurnExtractionTests(unittest.TestCase):
    def test_exam_header_normalizes_accented_and_ascii_morning(self) -> None:
        for raw in ("MANHÃ", "MANHA"):
            with self.subTest(raw=raw):
                evidence = extract_fgv_turn_evidence(
                    [(1, f"FUNDAÇÃO GETULIO VARGAS\n{raw}\nPROVA OBJETIVA")],
                    document_role="exam",
                )

                self.assertEqual(tuple(item.normalized for item in evidence), ("manhã",))

    def test_exam_header_normalizes_afternoon(self) -> None:
        evidence = extract_fgv_turn_evidence(
            [(1, "FUNDAÇÃO GETULIO VARGAS\nTARDE\nPROVA OBJETIVA")],
            document_role="exam",
        )

        self.assertEqual(tuple(item.normalized for item in evidence), ("tarde",))

    def test_answer_key_collects_morning_and_afternoon_as_coverage(self) -> None:
        evidence = extract_fgv_turn_evidence(
            [
                (1, "Auditor Fiscal - TIPO 1 (Manhã)\n1 2\nA B"),
                (2, "Auditor Fiscal - 1 - Turno Tarde\n1 2\nC D"),
            ],
            document_role="answer_key",
        )

        self.assertEqual(
            tuple(item.normalized for item in evidence),
            ("manhã", "tarde"),
        )

    def test_question_body_words_are_not_exam_shift_evidence(self) -> None:
        evidence = extract_fgv_turn_evidence(
            [
                (
                    1,
                    "PROVA OBJETIVA\nQUESTÃO 1\n"
                    "O atendimento ocorreu pela manhã e terminou à tarde.",
                )
            ],
            document_role="exam",
        )

        self.assertEqual(evidence, ())

    def test_standalone_shift_after_question_start_is_not_evidence(self) -> None:
        evidence = extract_fgv_turn_evidence(
            [(1, "PROVA OBJETIVA\n{01}\nConsidere o período:\nMANHÃ")],
            document_role="exam",
        )

        self.assertEqual(evidence, ())

    def test_exam_extraction_is_limited_to_initial_pages(self) -> None:
        evidence = extract_fgv_turn_evidence(
            [(number, "TARDE" if number == 5 else "CAPA") for number in range(1, 6)],
            document_role="exam",
        )

        self.assertEqual(evidence, ())


if __name__ == "__main__":
    unittest.main()
