from copy import deepcopy

from kad_collector.desktop_parser import parse_question_pages
from kad_collector.pdf_text import repair_page_hyphenation


def test_repairs_corroborated_words_before_segmentation_and_preserves_pages() -> None:
    pages = [
        {
            "page_number": 1,
            "text": (
                "Texto de referência: investir no desenvolvedor.\n"
                "Questão 18\nQual o valor disponível para inves-\ntir?\n"
                "(A) O desenvolve-\ndor recebe dez reais.\n(B) Vinte reais."
            ),
        }
    ]
    original = deepcopy(pages)
    questions, _warnings = parse_question_pages(pages)
    assert "investir" in questions[0].statement
    assert "desenvolvedor" in questions[0].alternatives[0].text
    assert questions[0].source_pages == [1]
    assert pages == original


def test_soft_hyphen_and_unverified_hard_hyphen_are_different() -> None:
    pages = [{"page_number": 1, "text": "inves\u00ad\ntir e desconhe-\ncida"}]
    assert repair_page_hyphenation(pages)[0]["text"] == "investir e desconhe-\ncida"


def test_discretionary_spelling_corroborates_hard_break_without_second_pass() -> None:
    pages = [{"page_number": 1, "text": "inves\u00ad\ntir e inves-\ntir"}]
    result = repair_page_hyphenation(pages)
    assert result[0]["text"] == "investir e investir"
    assert repair_page_hyphenation(result) == result


def test_preserves_lexical_hyphens_operators_urls_and_unknown_words() -> None:
    text = (
        "segunda-feira guarda-chuva micro-ondas torna-se anti-inflamatório\n"
        "segunda-\nfeira guarda-\nchuva micro-\nondas torna-\nse anti-\ninflamatório\n"
        "x -\ny\nabc-\nXYZ\nhttps://example.test/a-b\nfantas-\npalavra"
    )
    assert repair_page_hyphenation([{"page_number": 1, "text": text}])[0]["text"] == text


def test_compound_spelling_vetoes_join_even_when_joined_form_also_occurs() -> None:
    text = "recriação re-criação re-\ncriação"
    assert repair_page_hyphenation([{"page_number": 1, "text": text}])[0]["text"] == text


def test_uses_same_document_evidence_but_never_merges_pages() -> None:
    pages = [
        {"page_number": 1, "text": "Investir. inves-"},
        {"page_number": 2, "text": "tir e inves-\ntir."},
    ]
    repaired = repair_page_hyphenation(pages)
    assert repaired[0]["text"] == pages[0]["text"]
    assert repaired[1]["text"] == "tir e investir."
    assert repair_page_hyphenation(repaired) == repaired
