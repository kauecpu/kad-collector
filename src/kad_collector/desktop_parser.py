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
_ALTERNATIVE_LINE = re.compile(r"^\s*(?P<letter>[A-H])\s*[).:\-]\s*(?P<text>.*)$")


@dataclass
class _QuestionBuilder:
    number: int
    pages: set[int] = field(default_factory=set)
    statement_lines: list[str] = field(default_factory=list)
    alternatives: dict[str, list[str]] = field(default_factory=dict)
    active_alternative: str | None = None


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
            answer_status="missing",
            review_notes=notes,
        )
    )


def _generic_parse(pages: list[dict[str, Any]]) -> tuple[list[QuestionRecord], list[str]]:
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
                explicit = _EXPLICIT_QUESTION.match(line) is not None
                punctuation = bool(re.search(rf"{number}\s*[).:\-]", line))
                if explicit or punctuation:
                    _flush(current, questions, warnings)
                    current = _QuestionBuilder(number=number, pages={page_number})
                    inline = question_match.group("text").strip()
                    if inline:
                        current.statement_lines.append(inline)
                    continue
            alternative_match = _ALTERNATIVE_LINE.match(line)
            if alternative_match is not None and current is not None:
                letter = alternative_match.group("letter").upper()
                current.active_alternative = letter
                current.alternatives.setdefault(letter, [])
                inline = alternative_match.group("text").strip()
                if inline:
                    current.alternatives[letter].append(inline)
                current.pages.add(page_number)
                continue
            if current is None:
                continue
            current.pages.add(page_number)
            if current.active_alternative is None:
                current.statement_lines.append(line)
            else:
                current.alternatives[current.active_alternative].append(line)
    _flush(current, questions, warnings)
    return questions, warnings


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
    return _generic_parse(pages)
