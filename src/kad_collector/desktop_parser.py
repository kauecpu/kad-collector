from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .editorial_taxonomy import EditorialTaxonomy, TaxonomyPath
from .fgv_parser import (
    BankParsingContext,
    BankParsingResult,
    FgvDocumentIdentity,
    FgvSectionAdapter,
)
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
_CEBRASPE_ITEM_LINE = re.compile(r"^\s*(?P<number>\d{1,3})\s+(?P<text>\S.*)$")
_CEBRASPE_OBJECTIVE_HEADING = re.compile(
    r"(?i)^\s*[\-–—]*\s*(?:PROVA\s+OBJETIVA|"
    r"CONHECIMENTOS\s+(?:BÁSICOS|BASICOS|ESPECÍFICOS|ESPECIFICOS)"
    r"(?:\s*[\-–—]+\s*BLOCO\s+[IVX]+)?)\s*[\-–—]*\s*$"
)
_CEBRASPE_NON_OBJECTIVE_HEADING = re.compile(
    r"(?i)^\s*[\-–—]*\s*(?:PROVA\s+DISCURSIVA|REDAÇÃO)\s*[\-–—]*\s*$"
)
_CEBRASPE_TRUE_FALSE_NOTE = (
    "item CERTO/ERRADO; A representa Certo e B representa Errado no formato interno"
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


@dataclass(frozen=True)
class CebraspeQuestionContext:
    block: str | None
    supporting_text: str | None


@dataclass(frozen=True)
class _CebraspeLine:
    page_number: int
    text: str


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
        page_lines = str(page["text"]).splitlines()
        has_pdf_header = any(
            line.strip().casefold() == "www.pciconcursos.com.br"
            or line.strip().casefold().startswith("pcimarkpci ")
            or re.fullmatch(r"GABARITO(?:\s+[1-9]\d*)?", line.strip(), re.IGNORECASE)
            for line in page_lines[:8]
        )
        for line_index, raw_line in enumerate(page_lines):
            line = raw_line.strip()
            if not line:
                continue
            if line_index < 8 and (
                (has_pdf_header and line == str(page_number))
                or re.fullmatch(r"GABARITO(?:\s+[1-9]\d*)?", line, re.IGNORECASE)
                or line.casefold() == "www.pciconcursos.com.br"
                or line.casefold().startswith("pcimarkpci ")
            ):
                continue
            question_match = _QUESTION_LINE.match(line)
            if question_match is not None:
                number = int(question_match.group("number"))
                if not 1 <= number <= 200:
                    continue
                explicit = _EXPLICIT_QUESTION.match(line) is not None
                punctuation = bool(re.search(rf"{number}\s*[).:\-]", line))
                nested_number = (
                    current is not None
                    and not explicit
                    and number <= current.number
                    and punctuation
                )
                standalone = not question_match.group("text").strip()
                standalone_allowed = (
                    allow_standalone_numbers
                    and standalone
                    and number <= 200
                    and (current is None or current.collecting_content)
                )
                if not nested_number and (
                    explicit or (allow_punctuated_numbers and punctuation) or standalone_allowed
                ):
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


def _legacy_parse_question_pages(
    pages: list[dict[str, Any]],
) -> tuple[list[QuestionRecord], list[str]]:
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


def parse_fgv_objective_pages(
    pages: list[dict[str, Any]],
) -> tuple[list[QuestionRecord], list[str]]:
    return _generic_parse(
        pages,
        allow_standalone_numbers=True,
        allow_punctuated_numbers=False,
    )


def _supports_cebraspe_true_false(
    pages: list[dict[str, Any]], context: BankParsingContext
) -> bool:
    owner = " ".join(filter(None, (context.board, context.provider))).casefold()
    if "cebraspe" not in owner and "cespe" not in owner:
        return False
    if context.question_format == "true_false":
        return True
    if context.question_format == "multiple_choice":
        return False
    opening = " ".join(
        " ".join(str(page["text"]).split()) for page in pages[:3]
    ).casefold()
    return "caso julgue o item certo" in opening and "caso julgue o item errado" in opening


def _cebraspe_objective_lines(
    pages: list[dict[str, Any]],
) -> tuple[list[_CebraspeLine], bool]:
    lines: list[_CebraspeLine] = []
    all_lines: list[_CebraspeLine] = []
    in_objective_section = False
    objective_heading_found = False
    for page in pages:
        page_number = int(page["page_number"])
        for raw_line in str(page["text"]).splitlines():
            line = " ".join(raw_line.split())
            all_lines.append(_CebraspeLine(page_number=page_number, text=line))
            if _CEBRASPE_OBJECTIVE_HEADING.search(line):
                in_objective_section = True
                objective_heading_found = True
                block_match = re.search(r"(?i)\bBLOCO\s+([IVX]+)\b", line)
                if block_match is not None:
                    lines.append(
                        _CebraspeLine(
                            page_number=page_number,
                            text=f"BLOCO {block_match.group(1).upper()}",
                        )
                    )
                continue
            if in_objective_section and _CEBRASPE_NON_OBJECTIVE_HEADING.search(line):
                return lines, objective_heading_found
            if in_objective_section:
                lines.append(_CebraspeLine(page_number=page_number, text=line))
    if not objective_heading_found:
        for start in range(len(all_lines)):
            instruction = ""
            for end in range(start, min(len(all_lines), start + 5)):
                instruction = f"{instruction} {all_lines[end].text}".casefold()
                if (
                    "caso julgue o item certo" in instruction
                    and "caso julgue o item errado" in instruction
                ):
                    return all_lines[end + 1 :], True
    return lines, objective_heading_found


def _cebraspe_question_starts(
    lines: list[_CebraspeLine], expected_numbers: tuple[int, ...] | None
) -> list[tuple[int, int, str]]:
    sequence = tuple(sorted(set(expected_numbers or ())))
    cursor = 0
    expected = sequence[0] if sequence else 1
    starts: list[tuple[int, int, str]] = []
    for index, item in enumerate(lines):
        match = _CEBRASPE_ITEM_LINE.match(item.text)
        if match is None or int(match.group("number")) != expected:
            continue
        number = int(match.group("number"))
        starts.append((index, number, match.group("text").strip()))
        if sequence:
            cursor += 1
            if cursor >= len(sequence):
                break
            expected = sequence[cursor]
        else:
            expected += 1
    return starts


def _cebraspe_statement_end(
    lines: list[_CebraspeLine], line_index: int, end: int, inline: str
) -> int:
    terminal = re.compile(r"[.!?][\"'\u00bb)]?$")
    if terminal.search(inline):
        return line_index + 1
    for index in range(line_index + 1, end):
        if lines[index].text and terminal.search(lines[index].text):
            return index + 1
    return end


def _cebraspe_context_lines(lines: list[_CebraspeLine]) -> tuple[list[str], str | None]:
    content: list[str] = []
    block: str | None = None
    for item in lines:
        line = item.text.strip()
        block_match = re.fullmatch(r"(?i)BLOCO\s+([IVX]+)", line)
        if block_match is not None:
            block = f"Bloco {block_match.group(1).upper()}"
            continue
        if (
            not line
            or re.fullmatch(r"(?i)ESPAÇO\s+LIVRE", line)
            or re.match(r"(?i)^(?:CESPE\s*\|\s*)?CEBRASPE\s*[–—-]", line)
            or re.match(r"(?i)^(?:MATRIZ_)?\d*_[A-Z0-9_]*PF[A-Z0-9_]*", line)
        ):
            continue
        content.append(line)
    return content, block


def cebraspe_question_contexts(
    pages: list[dict[str, Any]], *, expected_numbers: tuple[int, ...] | None = None
) -> dict[int, CebraspeQuestionContext]:
    """Return shared text and block evidence for Cebraspe objective items."""
    lines, _heading_found = _cebraspe_objective_lines(pages)
    starts = _cebraspe_question_starts(lines, expected_numbers)
    contexts: dict[int, CebraspeQuestionContext] = {}
    active_support: str | None = None
    active_block: str | None = None
    previous_statement_end = 0
    for position, (line_index, number, inline) in enumerate(starts):
        pending, block = _cebraspe_context_lines(lines[previous_statement_end:line_index])
        if block is not None:
            active_block = block
        if pending:
            active_support = _clean(pending)
        contexts[number] = CebraspeQuestionContext(
            block=active_block,
            supporting_text=active_support,
        )
        next_start = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        previous_statement_end = _cebraspe_statement_end(
            lines, line_index, next_start, inline
        )
    return contexts


def _parse_cebraspe_true_false_pages(
    pages: list[dict[str, Any]], context: BankParsingContext
) -> BankParsingResult:
    questions: list[QuestionRecord] = []
    warnings: list[str] = []
    lines, objective_heading_found = _cebraspe_objective_lines(pages)
    starts = _cebraspe_question_starts(lines, context.expected_numbers)
    for position, (line_index, number, inline) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        statement_end = _cebraspe_statement_end(lines, line_index, end, inline)
        statement_lines = [
            inline,
            *(item.text for item in lines[line_index + 1 : statement_end]),
        ]
        statement = _clean(statement_lines)
        if len(statement) < 5:
            warnings.append(f"questao {number}: enunciado incompleto")
            continue
        source_pages = sorted(
            {item.page_number for item in lines[line_index:statement_end]}
        )
        questions.append(
            QuestionRecord(
                number=number,
                statement=statement,
                alternatives=[
                    Alternative(letter="A", text="Certo"),
                    Alternative(letter="B", text="Errado"),
                ],
                matter=None,
                subject=None,
                board=context.board or "Cebraspe",
                organization=None,
                role=context.role,
                year=None,
                source_pages=source_pages,
                answer_status="missing",
                correct_answer=None,
                review_notes=[_CEBRASPE_TRUE_FALSE_NOTE],
            )
        )
    if not objective_heading_found:
        warnings.append("seção de prova objetiva não identificada")
    expected = list(context.expected_numbers or ())
    found = [question.number for question in questions]
    if expected and found != expected:
        warnings.append("sequencia CERTO/ERRADO diverge do gabarito associado")
    elif found and found != list(range(found[0], found[-1] + 1)):
        warnings.append("sequencia CERTO/ERRADO incompleta; conferir o PDF original")
    return BankParsingResult(
        adapter_id="cebraspe-true-false",
        adapter_version="2.0",
        profile_id=None,
        identity=FgvDocumentIdentity(
            role=context.role,
            shift=context.shift,
            booklet_type=context.booklet_type,
            evidence=("instrução oficial de marcação CERTO/ERRADO",),
        ),
        sections=(),
        objective_questions=tuple(questions),
        discursive_numbers=(),
        expected_intervals=(),
        exceptions=(),
        warnings=tuple(warnings),
        status="completed" if questions and not warnings else "incomplete",
        summary={
            "objectiveFound": len(questions),
            "discursiveFound": 0,
            "exceptions": 0,
            "numberingClosed": bool(questions) and not warnings,
            "status": "cebraspe_true_false",
        },
    )


def parse_question_document(
    pages: list[dict[str, Any]], context: BankParsingContext
) -> BankParsingResult:
    if _supports_cebraspe_true_false(pages, context):
        return _parse_cebraspe_true_false_pages(pages, context)
    adapter = FgvSectionAdapter()
    if adapter.supports(context):
        return adapter.parse(
            pages,
            context,
            parse_fgv_objective_pages,
        )
    questions, warnings = _legacy_parse_question_pages(pages)
    return BankParsingResult(
        adapter_id="generic",
        adapter_version="1.0",
        profile_id=None,
        identity=FgvDocumentIdentity(
            role=context.role,
            shift=context.shift,
            booklet_type=context.booklet_type,
            evidence=(),
        ),
        sections=(),
        objective_questions=tuple(questions),
        discursive_numbers=(),
        expected_intervals=(),
        exceptions=(),
        warnings=tuple(warnings),
        status="completed",
        summary={
            "objectiveFound": len(questions),
            "discursiveFound": 0,
            "exceptions": 0,
            "numberingClosed": False,
            "status": "legacy_unbounded",
        },
    )


def parse_question_pages(
    pages: list[dict[str, Any]], context: BankParsingContext | None = None
) -> tuple[list[QuestionRecord], list[str]]:
    if context is None:
        return _legacy_parse_question_pages(pages)
    result = parse_question_document(pages, context)
    return list(result.objective_questions), result.warning_messages()
