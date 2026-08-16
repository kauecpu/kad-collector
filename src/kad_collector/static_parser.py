from __future__ import annotations

import bisect
import re

from .models import AIChunkResult, AIQuestion, Alternative

_PAGE_PATTERN = re.compile(r"(?m)^--- Pagina (?P<page>\d+) ---[ \t\r]*$")
_QUESTION_PATTERN = re.compile(r"(?m)^\{(?P<number>\d{2,3})\}[ \t\r]*$")
_ALTERNATIVE_PATTERN = re.compile(r"(?m)^\((?P<letter>[A-H])\)[ \t\r]*")
_CONTEXT_PATTERN = re.compile(
    r"(?im)^Texto para as quest(?:ões|oes) "
    r"(?:(?P<first>\d{1,3}) e (?P<second>\d{1,3})|"
    r"de (?P<range_start>\d{1,3}) a (?P<range_end>\d{1,3}))[ \t\r]*$"
)
_HEADER_PATTERN = re.compile(r"(?i)^Concurso Vestibular FUVEST .* Prova V\d+\s*$")
_FIGURE_LABEL_PATTERN = re.compile(r"(?i)\bFigura\s+([A-H])\b")


def _normalize_pdf_text(text: str) -> str:
    return text.replace("\u00ac", " ").replace("\u00a0", " ").replace("\u200b", "")


def _clean_fragment(fragment: str) -> str:
    lines: list[str] = []
    for raw_line in fragment.splitlines():
        line = " ".join(raw_line.split())
        if not line or line == "#####" or _HEADER_PATTERN.match(line):
            continue
        if _PAGE_PATTERN.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _supporting_contexts(
    text: str, question_matches: list[re.Match[str]]
) -> dict[int, str]:
    question_positions = {
        int(match.group("number")): match.start() for match in question_matches
    }
    contexts: dict[int, str] = {}
    for match in _CONTEXT_PATTERN.finditer(text):
        first_value = match.group("first") or match.group("range_start")
        last_value = match.group("second") or match.group("range_end")
        if first_value is None or last_value is None:
            continue
        first = int(first_value)
        last = int(last_value)
        context_end = question_positions.get(first)
        if context_end is None or context_end <= match.end():
            continue
        context = _clean_fragment(text[match.end() : context_end])
        if context:
            for number in range(first, last + 1):
                contexts[number] = context
    return contexts


class FuvestStaticExtractor:
    """Deterministic parser for the stable markers used by FUVEST first-phase PDFs."""

    model = "local-fuvest-markers-v1"

    def extract(self, text: str, metadata: dict[str, object]) -> AIChunkResult:
        del metadata
        normalized = _normalize_pdf_text(text)
        question_matches = list(_QUESTION_PATTERN.finditer(normalized))
        page_matches = list(_PAGE_PATTERN.finditer(normalized))
        page_positions = [match.start() for match in page_matches]
        page_numbers = [int(match.group("page")) for match in page_matches]
        contexts = _supporting_contexts(normalized, question_matches)
        questions: list[AIQuestion] = []
        warnings: list[str] = []
        incomplete = False

        for index, question_match in enumerate(question_matches):
            block_end = (
                question_matches[index + 1].start()
                if index + 1 < len(question_matches)
                else len(normalized)
            )
            block = normalized[question_match.end() : block_end]
            terminator = block.find("#####")
            number = int(question_match.group("number"))
            if terminator < 0:
                incomplete = True
                warnings.append(f"questao {number}: marcador final ausente; questao ignorada")
                continue
            block = block[:terminator]
            alternative_matches = list(_ALTERNATIVE_PATTERN.finditer(block))
            if len(alternative_matches) < 2:
                warnings.append(f"questao {number}: menos de duas alternativas reconhecidas")
                continue

            statement = _clean_fragment(block[: alternative_matches[0].start()])
            context = contexts.get(number)
            if context:
                statement = f"Texto de apoio:\n{context}\n\nQuestao:\n{statement}"
            if not statement:
                warnings.append(f"questao {number}: enunciado vazio; questao ignorada")
                continue

            alternatives: list[Alternative] = []
            visual_alternative = False
            extracted_letters = [match.group("letter") for match in alternative_matches]
            for alternative_index, alternative_match in enumerate(alternative_matches):
                alternative_end = (
                    alternative_matches[alternative_index + 1].start()
                    if alternative_index + 1 < len(alternative_matches)
                    else len(block)
                )
                letter = alternative_match.group("letter")
                alternative_text = _clean_fragment(
                    block[alternative_match.end() : alternative_end]
                )
                if not alternative_text:
                    visual_alternative = True
                    alternative_text = (
                        f"Alternativa visual {letter}; conferir a figura na pagina original."
                    )
                alternatives.append(Alternative(letter=letter, text=alternative_text))

            if extracted_letters != sorted(extracted_letters):
                visual_alternative = True
                figure_labels = {
                    letter.upper() for letter in _FIGURE_LABEL_PATTERN.findall(block)
                }
                if set(extracted_letters).issubset(figure_labels):
                    alternatives = [
                        Alternative(
                            letter=alternative.letter,
                            text=(
                                f"Alternativa visual {alternative.letter} "
                                f"(Figura {alternative.letter}); conferir na pagina original."
                            ),
                        )
                        for alternative in alternatives
                    ]
            if visual_alternative:
                warnings.append(
                    f"questao {number}: alternativas visuais ou em colunas exigem conferencia"
                )
            alternatives.sort(key=lambda item: item.letter)

            page_index = bisect.bisect_right(page_positions, question_match.start()) - 1
            source_pages = [page_numbers[page_index]] if page_index >= 0 else []
            questions.append(
                AIQuestion(
                    number=number,
                    statement=statement,
                    alternatives=alternatives,
                    matter=None,
                    subject=None,
                    board=None,
                    organization=None,
                    role=None,
                    year=None,
                    source_pages=source_pages,
                )
            )

        numbers = [question.number for question in questions]
        if numbers:
            expected = set(range(min(numbers), max(numbers) + 1))
            missing = sorted(expected - set(numbers))
            if missing:
                warnings.append(
                    "numeracao incompleta; questoes nao reconhecidas: "
                    + ", ".join(str(number) for number in missing)
                )
        return AIChunkResult(
            questions=questions,
            chunk_has_continuation=incomplete,
            warnings=list(dict.fromkeys(warnings)),
        )
