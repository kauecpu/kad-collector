from __future__ import annotations

import unittest

from kad_collector.desktop_parser import parse_fgv_objective_pages, parse_question_document
from kad_collector.fgv_parser import (
    BankParsingContext,
    FgvSectionAdapter,
    load_fgv_profiles,
)

PROFILE_TOML = '''
schema_version = 1

[[profiles]]
id = "test-analyst-morning"
application_id = "test-main"
contest_aliases = ["TEST"]
roles = ["Analista de Teste"]
shift = "Manhã"
booklet_types = [1, 2]
sections = [{ kind = "objective", first = 1, last = 3, count = 3 }]

[[profiles]]
id = "test-analyst-afternoon"
application_id = "test-main"
contest_aliases = ["TEST"]
roles = ["Analista de Teste"]
shift = "Tarde"
booklet_types = [1, 2]
sections = [
  { kind = "objective", first = 1, last = 2, count = 2 },
  { kind = "discursive", first = 1, last = 1, count = 1 },
]
'''


def _question(number: int, label: str | None = None) -> str:
    description = label or f"Enunciado completo da questão {number}."
    return f"""{number}
{description}
(A) Primeira alternativa da questão {number}.
(B) Segunda alternativa da questão {number}.
"""


def _page(number: int, text: str) -> dict[str, object]:
    return {"page_number": number, "text": text}


def _context(*, shift: str = "Manhã", board: str = "FGV") -> BankParsingContext:
    return BankParsingContext(
        document_id="synthetic-fgv-exam",
        board=board,
        provider="fgv_conhecimento" if board == "FGV" else "other",
        contest="TEST",
        role="Analista de Teste",
        shift=shift,
        booklet_type=1,
    )


def _parse(
    pages: list[dict[str, object]], *, shift: str = "Manhã"
):
    adapter = FgvSectionAdapter(load_fgv_profiles(PROFILE_TOML))
    return adapter.parse(  # type: ignore[arg-type]
        pages,
        _context(shift=shift),
        parse_fgv_objective_pages,
    )


class FgvSectionAdapterTests(unittest.TestCase):
    def test_recognizes_shift_objective_discursive_and_non_question_sections(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    """TARDE
Analista de Teste
TIPO 1
Instruções ao candidato.
"""
                    + _question(1),
                ),
                _page(2, _question(2) + "\nProva Discursiva\nQuestão 1\nTexto discursivo."),
                _page(
                    3,
                    "1\n--------------------\n2\n--------------------\n"
                    "3\n--------------------",
                ),
                _page(4, "Realização"),
            ],
            shift="Tarde",
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.identity.shift, "Tarde")
        self.assertEqual(result.discursive_numbers, (1,))
        self.assertTrue(
            {"instructions", "objective", "discursive", "answer_sheet", "non_question"}
            <= {section.kind for section in result.sections}
        )

    def test_accepts_isolated_numbers_inside_the_expected_objective_interval(self) -> None:
        result = _parse([_page(1, "MANHÃ\nAnalista de Teste\nTIPO 1\n" + "".join(
            _question(number) for number in range(1, 4)
        ))])

        self.assertEqual([question.number for question in result.objective_questions], [1, 2, 3])
        self.assertEqual(result.status, "completed")

    def test_number_outside_the_interval_is_not_extracted(self) -> None:
        result = _parse(
            [_page(1, "MANHÃ\nAnalista de Teste\nTIPO 1\n" + "".join(
                _question(number) for number in range(1, 5)
            ))]
        )

        self.assertEqual([question.number for question in result.objective_questions], [1, 2, 3])
        self.assertTrue(
            any(
                exception.expected_number == 4
                and exception.reason == "número fora do intervalo esperado"
                for exception in result.exceptions
            )
        )

    def test_numbered_list_stays_inside_the_statement(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    "MANHÃ\nAnalista de Teste\nTIPO 1\n"
                    + _question(1, "Considere a lista:\n1. item interno\n2. outro item")
                    + _question(2)
                    + _question(3),
                )
            ]
        )

        self.assertEqual(result.status, "completed")
        self.assertIn("1. item interno", result.objective_questions[0].statement)

    def test_discursive_subitems_never_become_objective_alternatives(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    "TARDE\nAnalista de Teste\nTIPO 1\n"
                    + _question(1)
                    + _question(2)
                    + """Prova Discursiva
Questão 1
a) Primeiro item discursivo.
b) Segundo item discursivo.
c) Terceiro item discursivo.
""",
                )
            ],
            shift="Tarde",
        )

        self.assertEqual(len(result.objective_questions), 2)
        self.assertEqual(result.discursive_numbers, (1,))
        self.assertNotIn("item discursivo", result.objective_questions[-1].alternatives[-1].text)

    def test_transition_on_the_same_page_cuts_objective_content(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    "TARDE\nAnalista de Teste\nTIPO 1\n"
                    + _question(1)
                    + _question(2)
                    + "Prova Discursiva\nQuestão 1\nTexto suficiente.",
                )
            ],
            shift="Tarde",
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.objective_questions[-1].number, 2)
        self.assertEqual(result.discursive_numbers, (1,))

    def test_numbering_can_restart_in_the_discursive_namespace(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    "TARDE\nAnalista de Teste\nTIPO 1\n"
                    + _question(1)
                    + _question(2)
                    + "Prova Discursiva\nQuestão 1\nTexto suficiente.",
                )
            ],
            shift="Tarde",
        )

        self.assertEqual(result.status, "completed")
        self.assertFalse(result.exceptions)

    def test_question_split_between_pages_is_preserved(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    """MANHÃ
Analista de Teste
TIPO 1
1
Enunciado da questão dividido entre páginas.
""",
                ),
                _page(
                    2,
                    """(A) Primeira alternativa.
(B) Segunda alternativa.
"""
                    + _question(2)
                    + _question(3),
                ),
            ]
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.objective_questions[0].source_pages, [1, 2])

    def test_every_missing_question_creates_its_own_exception(self) -> None:
        result = _parse([_page(1, "MANHÃ\nAnalista de Teste\nTIPO 1\n" + _question(1))])

        missing = [
            exception.expected_number
            for exception in result.exceptions
            if exception.reason == "questão esperada não extraída"
        ]
        self.assertEqual(missing, [2, 3])
        self.assertTrue(all(exception.nearby_pages for exception in result.exceptions))

    def test_duplicate_number_blocks_completion(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    "MANHÃ\nAnalista de Teste\nTIPO 1\n"
                    + _question(1)
                    + _question(2, "Primeira ocorrência da questão dois.")
                    + _question(2, "Segunda ocorrência da questão dois.")
                    + _question(3),
                )
            ]
        )

        self.assertEqual(result.status, "incomplete")
        self.assertTrue(any(item.reason == "numeração duplicada" for item in result.exceptions))

    def test_order_break_blocks_completion(self) -> None:
        result = _parse(
            [
                _page(
                    1,
                    "MANHÃ\nAnalista de Teste\nTIPO 1\n"
                    + _question(1)
                    + _question(3)
                    + _question(2),
                )
            ]
        )

        self.assertEqual(result.status, "incomplete")
        self.assertTrue(
            any(
                item.reason == "quebra de ordem na numeração"
                for item in result.exceptions
            )
        )

    def test_missing_interval_never_returns_completed(self) -> None:
        result = _parse([_page(1, "MANHÃ\nAnalista de Teste\nTIPO 1\n" + _question(1))])

        self.assertEqual(result.status, "incomplete")
        self.assertFalse(result.summary["numberingClosed"])

    def test_non_fgv_document_uses_generic_fallback(self) -> None:
        result = parse_question_document(
            [_page(1, "QUESTÃO 1\nEnunciado suficiente.\n(A) Uma.\n(B) Duas.")],
            _context(board="FCC"),  # type: ignore[arg-type]
        )

        self.assertEqual(result.adapter_id, "generic")
        self.assertEqual(result.status, "completed")
        self.assertEqual([question.number for question in result.objective_questions], [1])

    def test_adapter_selection_uses_normalized_board_identity(self) -> None:
        result = _parse([_page(1, "MANHÃ\nAnalista de Teste\nTIPO 1\n" + _question(1))])

        self.assertEqual(result.adapter_id, "fgv-sections")
        self.assertEqual(result.profile_id, "test-analyst-morning")

    def test_unknown_fgv_contest_is_incomplete_instead_of_guessing_ranges(self) -> None:
        adapter = FgvSectionAdapter(load_fgv_profiles(PROFILE_TOML))
        context = BankParsingContext(
            document_id="unknown",
            board="FGV",
            provider="fgv_conhecimento",
            contest="OUTRO",
            role="Analista de Teste",
            shift="Manhã",
            booklet_type=1,
        )
        result = adapter.parse(
            [_page(1, "MANHÃ\nAnalista de Teste\nTIPO 1\n" + _question(1))],  # type: ignore[arg-type]
            context,
            parse_fgv_objective_pages,
        )

        self.assertEqual(result.status, "incomplete")
        self.assertTrue(
            any(
                item.reason == "perfil oficial de intervalos não localizado"
                for item in result.exceptions
            )
        )

    def test_same_pages_produce_the_same_structured_payload(self) -> None:
        pages = [_page(1, "MANHÃ\nAnalista de Teste\nTIPO 1\n" + "".join(
            _question(number) for number in range(1, 4)
        ))]

        first = _parse(pages).to_payload()
        second = _parse(pages).to_payload()

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
