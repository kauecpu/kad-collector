from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import Alternative, QuestionRecord
from .static_parser import FuvestStaticExtractor

_QUESTION_LINE = re.compile(
    r"^\s*(?:(?:QUEST(?:ÃO|AO)|QUEST[.])\s*)?(?P<number>\d{1,3})\s*"
    r"(?:(?:[).:\-])|(?=\s*$))\s*(?P<text>.*)$",
    re.IGNORECASE,
)
_EXPLICIT_QUESTION = re.compile(r"^\s*(?:QUEST(?:ÃO|AO)|QUEST[.])", re.IGNORECASE)
_ALTERNATIVE_LINE = re.compile(
    r"^\s*(?:\((?P<parenthesized>[A-H])\)|(?P<plain>[A-H])\s*[).:\-])\s*"
    r"(?P<text>.*)$",
    re.IGNORECASE,
)
_ANSWER_LINE = re.compile(
    r"^\s*(?:alternativa\s+correta|resposta\s+correta|gabarito)\s*[:\-]\s*"
    r"(?P<letter>[A-H])\b",
    re.IGNORECASE,
)
_COMMENTARY_BOUNDARY = re.compile(
    r"^\s*(?:objetivo\s+da\s+quest[aã]o|coment[aá]rios?\s+gerais|"
    r"desempenho\s+dos\s+candidatos|alternativa\s+correta|resposta\s+correta|"
    r"resposta\s+esperada)\b",
    re.IGNORECASE,
)


@dataclass
class _QuestionBuilder:
    number: int
    pages: set[int] = field(default_factory=set)
    statement_lines: list[str] = field(default_factory=list)
    alternatives: dict[str, list[str]] = field(default_factory=dict)
    active_alternative: str | None = None
    correct_answer: str | None = None
    collecting_content: bool = True


def _clean(lines: list[str]) -> str:
    return "\n".join(" ".join(line.split()) for line in lines if line.strip()).strip()


def _flush(
    builder: _QuestionBuilder | None, output: list[QuestionRecord], warnings: list[str]
) -> None:
    if builder is None:
        return
    alternatives: list[Alternative] = []
    visual = False
    for letter, lines in builder.alternatives.items():
        text = _clean(lines)
        if not text:
            visual = True
            text = f"Alternativa visual {letter}; conferir no PDF original."
        alternatives.append(Alternative(letter=letter, text=text))
    alternatives.sort(key=lambda item: item.letter)
    if len(alternatives) < 2:
        warnings.append(f"questao {builder.number}: menos de duas alternativas reconhecidas")
        return
    statement = _clean(builder.statement_lines)
    if len(statement) < 5:
        warnings.append(f"questao {builder.number}: enunciado incompleto")
        return
    notes = []
    if visual:
        notes.append("alternativa visual; conferir no PDF original")
    if len(alternatives) > 5:
        notes.append("mais de cinco alternativas; item nao exportavel sem revisao")
    output.append(
        QuestionRecord(
            number=builder.number,
            statement=statement,
            alternatives=alternatives,
            matter=None,
            subject=None,
            board=None,
            organization=None,
            role=None,
            year=None,
            source_pages=sorted(builder.pages),
            answer_status="matched" if builder.correct_answer else "missing",
            correct_answer=builder.correct_answer,
            review_notes=notes,
        )
    )


def _generic_parse(
    pages: list[dict[str, Any]], *, allow_standalone_numbers: bool
) -> tuple[list[QuestionRecord], list[str]]:
    questions: list[QuestionRecord] = []
    warnings: list[str] = []
    current: _QuestionBuilder | None = None
    for page in pages:
        page_number = int(page["page_number"])
        for raw_line in str(page["text"]).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            question_match = _QUESTION_LINE.match(line)
            if question_match is not None:
                number = int(question_match.group("number"))
                if not 1 <= number <= 200:
                    continue
                explicit = _EXPLICIT_QUESTION.match(line) is not None
                punctuation = bool(re.search(rf"{number}\s*[).:\-]", line))
                standalone = not question_match.group("text").strip()
                standalone_allowed = (
                    allow_standalone_numbers
                    and standalone
                    and number <= 200
                    and (current is None or current.collecting_content)
                )
                if explicit or punctuation or standalone_allowed:
                    _flush(current, questions, warnings)
                    current = _QuestionBuilder(number=number, pages={page_number})
                    inline = question_match.group("text").strip()
                    if inline:
                        current.statement_lines.append(inline)
                    continue
            alternative_match = _ALTERNATIVE_LINE.match(line)
            if (
                alternative_match is not None
                and current is not None
                and current.collecting_content
            ):
                letter = (
                    alternative_match.group("parenthesized")
                    or alternative_match.group("plain")
                ).upper()
                current.active_alternative = letter
                current.alternatives.setdefault(letter, [])
                inline = alternative_match.group("text").strip()
                if inline:
                    current.alternatives[letter].append(inline)
                current.pages.add(page_number)
                continue
            if current is not None:
                answer_match = _ANSWER_LINE.match(line)
                if answer_match is not None:
                    current.correct_answer = answer_match.group("letter").upper()
                    current.active_alternative = None
                    current.collecting_content = False
                    current.pages.add(page_number)
                    continue
                if _COMMENTARY_BOUNDARY.match(line):
                    current.active_alternative = None
                    current.collecting_content = False
                    continue
            if current is None:
                continue
            if not current.collecting_content:
                continue
            current.pages.add(page_number)
            if current.active_alternative is None:
                current.statement_lines.append(line)
            else:
                current.alternatives[current.active_alternative].append(line)
    _flush(current, questions, warnings)
    selected: dict[int, QuestionRecord] = {}
    for question in questions:
        existing = selected.get(question.number)
        score = (
            int(question.answer_status == "matched"),
            len(question.alternatives),
            len(question.statement),
        )
        existing_score = (
            (
                int(existing.answer_status == "matched"),
                len(existing.alternatives),
                len(existing.statement),
            )
            if existing is not None
            else (-1, -1, -1)
        )
        if score > existing_score:
            selected[question.number] = question
        if existing is not None:
            warnings.append(
                f"questao {question.number}: ocorrencia duplicada; preservada a mais completa"
            )
    return [selected[number] for number in sorted(selected)], warnings


def parse_question_pages(pages: list[dict[str, Any]]) -> tuple[list[QuestionRecord], list[str]]:
    combined = "\n\n".join(
        f"--- Pagina {page['page_number']} ---\n{page['text']}" for page in pages if page["text"]
    )
    if re.search(r"(?m)^\{\d{2,3}\}\s*$", combined):
        result = FuvestStaticExtractor().extract(combined, {})
        questions = [
            QuestionRecord(
                **item.model_dump(),
                answer_status="missing",
                review_notes=[],
            )
            for item in result.questions
        ]
        return questions, result.warnings
    has_explicit_questions = any(
        _EXPLICIT_QUESTION.match(line)
        for page in pages
        for line in str(page["text"]).splitlines()
    )
    return _generic_parse(pages, allow_standalone_numbers=not has_explicit_questions)
