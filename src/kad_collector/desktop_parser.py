from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .editorial_taxonomy import EditorialTaxonomy, TaxonomyPath
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
_SECTION_RESET = re.compile(
    r"^\s*(?:prova\s+discursiva|quest(?:ões|oes)\s+discursivas|"
    r"redaç(?:ão|ao)|estudo\s+de\s+caso)\s*$",
    re.IGNORECASE,
)
_RFB22_HEADER = re.compile(
    r"(?i)CONCURSO P[ÚU]BLICO DA RECEITA FEDERAL DO BRASIL"
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


@dataclass(frozen=True)
class QuestionSectionContext:
    section_title: str
    block_id: str
    page_number: int
    path: TaxonomyPath


def map_question_sections(
    pages: list[dict[str, Any]],
    taxonomy: EditorialTaxonomy,
    *,
    catalog_ids: Iterable[str] | None = None,
) -> dict[tuple[int, int], QuestionSectionContext]:
    """Associate each numbered question with the nearest controlled heading."""
    sections: dict[tuple[int, int], QuestionSectionContext] = {}
    active_title: str | None = None
    active_path: TaxonomyPath | None = None
    active_block: str | None = None
    block_number = 0
    for page in pages:
        page_number = int(page["page_number"])
        recent_lines: list[str] = []
        for raw_line in str(page["text"]).splitlines():
            line = " ".join(raw_line.split())
            if not line:
                recent_lines.clear()
                continue
            if _SECTION_RESET.match(line):
                active_title = None
                active_path = None
                active_block = None
                recent_lines.clear()
                continue
            question_match = _QUESTION_LINE.match(line)
            if question_match is not None:
                number = int(question_match.group("number"))
                explicit = _EXPLICIT_QUESTION.match(line) is not None
                punctuation = bool(re.search(rf"{number}\s*[).:\-]", line))
                bare_number = line.isdigit()
                if (
                    1 <= number <= 200
                    and (explicit or punctuation or bare_number)
                    and active_title is not None
                    and active_path is not None
                    and active_block is not None
                ):
                    sections[(number, page_number)] = QuestionSectionContext(
                        section_title=active_title,
                        block_id=active_block,
                        page_number=page_number,
                        path=active_path,
                    )
                    recent_lines.clear()
                    continue

            recent_lines.append(line)
            recent_lines = recent_lines[-3:]
            heading_path: TaxonomyPath | None = None
            heading_title: str | None = None
            for width in range(1, len(recent_lines) + 1):
                candidate = " ".join(recent_lines[-width:])
                candidate_path = taxonomy.match_context_heading(
                    candidate, catalog_ids=catalog_ids
                )
                if candidate_path is not None:
                    heading_path = candidate_path
                    heading_title = candidate
                    break
            if heading_path is not None:
                canonical = (
                    heading_path.discipline,
                    heading_path.matter,
                    heading_path.subject,
                )
                previous = (
                    active_path.discipline,
                    active_path.matter,
                    active_path.subject,
                ) if active_path is not None else None
                if canonical != previous:
                    block_number += 1
                    active_block = f"section-{block_number}"
                active_title = str(heading_title).split(":", 1)[0].strip()
                active_path = heading_path
                continue
    return sections


def question_section_context(
    sections: dict[tuple[int, int], QuestionSectionContext],
    question: QuestionRecord,
) -> QuestionSectionContext | None:
    for page_number in question.source_pages:
        if context := sections.get((question.number, page_number)):
            return context
    return None


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
    pages: list[dict[str, Any]],
    *,
    allow_standalone_numbers: bool,
    allow_punctuated_numbers: bool = True,
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
                if explicit or (allow_punctuated_numbers and punctuation) or standalone_allowed:
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


def _rfb22_objective_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the objective section of RFB22 mixed afternoon booklets."""
    objective_pages: list[dict[str, Any]] = []
    discursive_started = False
    for page in pages:
        text = str(page["text"])
        if discursive_started:
            continue
        split = re.split(
            r"(?im)^\s*Prova\s+Discursiva\s*$",
            text,
            maxsplit=1,
        )
        objective_text = split[0]
        if objective_text.strip():
            objective_pages.append(
                {"page_number": int(page["page_number"]), "text": objective_text}
            )
        if len(split) == 2:
            discursive_started = True
    return objective_pages


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
    if _RFB22_HEADER.search(combined):
        return _generic_parse(
            _rfb22_objective_pages(pages),
            allow_standalone_numbers=True,
            allow_punctuated_numbers=False,
        )
    has_explicit_questions = any(
        _EXPLICIT_QUESTION.match(line)
        for page in pages
        for line in str(page["text"]).splitlines()
    )
    return _generic_parse(pages, allow_standalone_numbers=not has_explicit_questions)
