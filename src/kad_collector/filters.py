from __future__ import annotations

import re
import unicodedata

from .models import CollectionFilters, QuestionRecord


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())


def _text_matches(value: str | None, requested: list[str]) -> bool:
    if not requested:
        return True
    if not value:
        return False
    normalized = _normalize(value)
    return any(_normalize(item) in normalized for item in requested)


def _metadata_value(metadata: dict[str, str], key: str) -> str | None:
    value = metadata.get(key, "").strip()
    return value or None


def document_might_match_filters(
    title: str,
    url: str,
    metadata: dict[str, str],
    filters: CollectionFilters,
) -> bool:
    """Descarta cedo somente quando um metadado conhecido contradiz o pedido.

    Campos desconhecidos permanecem elegiveis para que a filtragem definitiva seja
    feita depois da extracao estruturada da questao.
    """

    searchable = f"{title}\n{url}"
    known_year = _metadata_value(metadata, "ano")
    if filters.years:
        if known_year and known_year.isdigit():
            if int(known_year) not in filters.years:
                return False
        else:
            discovered_years = {
                int(value) for value in re.findall(r"\b(?:19|20|21)\d{2}\b", searchable)
            }
            if discovered_years and discovered_years.isdisjoint(filters.years):
                return False

    text_fields = (
        ("banca", filters.boards),
        ("orgao", filters.organizations),
        ("cargo", filters.roles),
        ("materia", filters.matters),
        ("assunto", filters.subjects),
    )
    for metadata_key, requested in text_fields:
        if not requested:
            continue
        known_value = _metadata_value(metadata, metadata_key)
        if known_value and not _text_matches(known_value, requested):
            return False
        if known_value is None and _text_matches(searchable, requested):
            continue
    return True


def question_matches_filters(question: QuestionRecord, filters: CollectionFilters) -> bool:
    if filters.years and question.year not in filters.years:
        return False
    return all(
        (
            _text_matches(question.board, filters.boards),
            _text_matches(question.organization, filters.organizations),
            _text_matches(question.role, filters.roles),
            _text_matches(question.matter, filters.matters),
            _text_matches(question.subject, filters.subjects),
        )
    )


def filter_questions(
    questions: list[QuestionRecord], filters: CollectionFilters
) -> tuple[list[QuestionRecord], int]:
    if filters.is_empty():
        return questions, 0
    selected = [question for question in questions if question_matches_filters(question, filters)]
    return selected, len(questions) - len(selected)
