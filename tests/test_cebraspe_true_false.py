from __future__ import annotations

from kad_collector.answer_key import (
    AnswerEntry,
    adapt_true_false_entries,
    parse_answer_key,
    questions_use_true_false,
)
from kad_collector.desktop_parser import parse_question_document
from kad_collector.document_triage import classify_document
from kad_collector.fgv_parser import BankParsingContext

PAGES = [
    {
        "page_number": 1,
        "text": (
            "De acordo com o comando, marque o campo designado com o código C, "
            "caso julgue o item CERTO; ou o campo designado com o código E, "
            "caso julgue o item ERRADO.\n"
            "Julgue os itens a seguir.\n"
            "1 A primeira afirmação deve ser analisada.\n"
            "2 A segunda afirmação continua\nna linha seguinte.\n"
        ),
    },
    {
        "page_number": 2,
        "text": "3 A terceira afirmação encerra a sequência.\nPROVA DISCURSIVA\n1\n2\n3",
    },
]


def test_cebraspe_parser_creates_compatible_true_false_questions() -> None:
    result = parse_question_document(
        PAGES,
        BankParsingContext(
            document_id="pcdf",
            board="Cebraspe",
            provider="cebraspe",
            role="Agente de Polícia",
        ),
    )

    assert result.adapter_id == "cebraspe-true-false"
    assert [question.number for question in result.objective_questions] == [1, 2, 3]
    assert result.objective_questions[1].statement.endswith("na linha seguinte.")
    assert questions_use_true_false(list(result.objective_questions))
    alternatives = [
        [item.text for item in question.alternatives]
        for question in result.objective_questions
    ]
    assert alternatives == [
        ["Certo", "Errado"],
        ["Certo", "Errado"],
        ["Certo", "Errado"],
    ]


def test_cebraspe_parser_is_scoped_to_the_declared_bank() -> None:
    result = parse_question_document(
        PAGES,
        BankParsingContext(document_id="other", board="Outra banca"),
    )

    assert result.adapter_id == "generic"
    assert result.objective_questions == ()


def test_true_false_answer_mapping_is_fail_closed() -> None:
    mapped = adapt_true_false_entries(
        {
            1: AnswerEntry(number=1, answer="C"),
            2: AnswerEntry(number=2, answer="E"),
            3: AnswerEntry(number=3, answer=None, annulled=True),
        }
    )

    assert mapped[1].answer == "A"
    assert mapped[2].answer == "B"
    assert mapped[3].annulled
    assert adapt_true_false_entries({1: AnswerEntry(number=1, answer="D")}) == {}


def test_cebraspe_grid_uses_row_order_when_pdf_numbers_are_fragmented() -> None:
    text = (
        "1 23456789 1 0 1 1 1 2 1 3 1 4 1 5 1 6 1 7 1 8 1 9 2 0\n"
        "E CCCEEEEECCCEE C EE E EE\n"
        "21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40\n"
        "E CEECECCCECXXE C EE C CE\n"
        "Obs.: ( X ) item anulado.\n"
        "GABARITOS OFICIAIS DEFINITIVOS\n"
        "CARGO: AGENTE DE POLÍCIA\n"
    )

    entries = parse_answer_key(text)

    assert len(entries) == 40
    assert entries[1].answer == "E"
    assert entries[33].annulled
    assert entries[34].answer == "E"


def test_true_false_exam_is_detected_without_multiple_choice_alternatives() -> None:
    result = classify_document(
        filename="caderno.pdf",
        title="Prova objetiva",
        text="\n".join(str(page["text"]) for page in PAGES),
        declared_type="auto",
    )

    assert result.decision == "exam"


def test_cebraspe_section_pdf_uses_answer_format_and_block_heading() -> None:
    pages = [
        {
            "page_number": 1,
            "text": (
                "CEBRASPE – PF – Edital: 2025\n"
                "-- CONHECIMENTOS ESPECÍFICOS – BLOCO III --\n"
                "Julgue os itens subsequentes.\n"
                "97 A primeira afirmação está completa.\n"
                "98 A segunda afirmação também está completa.\n"
            ),
        }
    ]

    result = parse_question_document(
        pages,
        BankParsingContext(
            document_id="section",
            board="Cebraspe",
            expected_numbers=(97, 98),
            question_format="true_false",
        ),
    )

    assert [question.number for question in result.objective_questions] == [97, 98]
    assert result.status == "completed"


def test_cebraspe_grid_keeps_last_partial_row_with_zero_padding() -> None:
    text = (
        "CEBRASPE POLÍCIA FEDERAL GABARITO DEFINITIVO\n"
        "51 52 53 54 55 56 57 58 59 60\n"
        "C E C E C E C E C E\n"
        "61 62 63 64 65 0 0 0 0 0\n"
        "E C X C E 0 0 0 0 0\n"
        "Obs.: ( X ) item anulado.\n"
    )

    entries = parse_answer_key(text)

    assert list(entries) == list(range(51, 66))
    assert entries[63].annulled
