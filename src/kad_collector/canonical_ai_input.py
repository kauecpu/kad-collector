from __future__ import annotations

import re
from dataclasses import dataclass

from .editorial_taxonomy import normalize_taxonomy_text
from .semantic_identity import stable_sha256

CANONICAL_AI_INPUT_SANITIZER_VERSION = "canonical-ai-input-v1"

_FGV_FOOTER = re.compile(r"\bFGV\s+CONHECIMENTO\b", re.IGNORECASE)
_PAGE_FOOTER = re.compile(r"\bP[ÁA]GINA\s+\d+\b", re.IGNORECASE)
_EXAM_HEADER = re.compile(
    r"\b(?:CONCURSO\s+P[ÚU]BLICO\s+DA\s+)?RECEITA\s+FEDERAL\s+DO\s+BRASIL\b",
    re.IGNORECASE,
)
_CALENDAR_WEEK = re.compile(
    r"^\s*Se\s+Te\s+Qua\s+Qui\s+Sex\s+Sab\s+Do\s*$",
    re.IGNORECASE,
)


class CanonicalAIInputError(ValueError):
    """The derived AI input cannot be sanitized without losing question content."""


@dataclass(frozen=True)
class SanitizedAIContent:
    statement: str
    alternatives: tuple[str, ...]
    prompt_content_fingerprint: str
    removed_artifacts: tuple[str, ...]


def _line_artifacts(line: str) -> tuple[str, ...]:
    codes: list[str] = []
    if _CALENDAR_WEEK.fullmatch(line):
        codes.append("calendar")
    if _FGV_FOOTER.search(line):
        codes.append("fgv_footer")
    if _PAGE_FOOTER.search(line):
        codes.append("page_footer")
    if _EXAM_HEADER.search(line):
        codes.append("exam_header")
    return tuple(codes)


def find_canonical_ai_artifacts(
    statement: str,
    alternatives: tuple[str, ...],
    *,
    official_headings: tuple[str, ...] = (),
) -> tuple[str, ...]:
    codes: list[str] = []
    for text in (statement, *alternatives):
        for line in text.splitlines():
            codes.extend(_line_artifacts(line))
    if alternatives and official_headings:
        normalized_headings = {
            normalize_taxonomy_text(value) for value in official_headings
        }
        last_lines = alternatives[-1].splitlines()
        if len(last_lines) > 1 and normalize_taxonomy_text(last_lines[-1]) in normalized_headings:
            codes.append("section_heading_bleed")
    return tuple(dict.fromkeys(codes))


def _strip_artifact_suffix(text: str) -> tuple[str, tuple[str, ...]]:
    lines = text.splitlines()
    cut_at: int | None = None
    for index, line in enumerate(lines):
        if _line_artifacts(line):
            cut_at = index
            break
    if cut_at is None:
        return text.strip(), ()
    removed = tuple(
        dict.fromkeys(
            code for line in lines[cut_at:] for code in _line_artifacts(line)
        )
    )
    return "\n".join(lines[:cut_at]).strip(), removed


def sanitize_canonical_ai_content(
    statement: str,
    alternatives: tuple[str, ...],
    *,
    official_headings: tuple[str, ...] = (),
) -> SanitizedAIContent:
    sanitized_statement, statement_codes = _strip_artifact_suffix(statement)
    sanitized_alternatives: list[str] = []
    removed: list[str] = list(statement_codes)
    for alternative in alternatives:
        sanitized, codes = _strip_artifact_suffix(alternative)
        sanitized_alternatives.append(sanitized)
        removed.extend(codes)

    if sanitized_alternatives and official_headings:
        normalized_headings = {
            normalize_taxonomy_text(value) for value in official_headings
        }
        last_lines = sanitized_alternatives[-1].splitlines()
        if (
            len(last_lines) > 1
            and normalize_taxonomy_text(last_lines[-1]) in normalized_headings
        ):
            sanitized_alternatives[-1] = "\n".join(last_lines[:-1]).strip()
            removed.append("section_heading_bleed")

    if not sanitized_statement or any(not value for value in sanitized_alternatives):
        raise CanonicalAIInputError("sanitização removeu conteúdo obrigatório da questão")

    alternatives_tuple = tuple(sanitized_alternatives)
    fingerprint = stable_sha256(
        {
            "sanitizerVersion": CANONICAL_AI_INPUT_SANITIZER_VERSION,
            "statement": sanitized_statement,
            "alternatives": list(alternatives_tuple),
        }
    )
    return SanitizedAIContent(
        statement=sanitized_statement,
        alternatives=alternatives_tuple,
        prompt_content_fingerprint=fingerprint,
        removed_artifacts=tuple(dict.fromkeys(removed)),
    )
