from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_PERIOD_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*/\s*([12])(?!\d)")
_DATE_PATTERN = re.compile(r"(?<!\d)\d{1,2}/(\d{1,2})/((?:19|20)\d{2})(?!\d)")
_VARIANT_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?P<label>V|TIPO)[-_ ]*(?P<number>[1-9]\d*)(?!\d)",
    re.IGNORECASE,
)
_STOP_TOKENS = {
    "arquivo",
    "caderno",
    "definitivo",
    "definitiva",
    "fase",
    "gabarito",
    "gabaritos",
    "oficial",
    "pdf",
    "prova",
    "provas",
    "resposta",
    "respostas",
}


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def structural_v_number(value: str) -> int | None:
    match = re.search(r"(?<![A-Z0-9])V[-_ ]*([1-9]\d*)(?!\d)", value, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _variant(value: str) -> tuple[str, int] | None:
    match = _VARIANT_PATTERN.search(value)
    if match is None:
        return None
    label = match.group("label").casefold()
    return label, int(match.group("number"))


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(normalize_text(value))
        if len(token) > 1 and token not in _STOP_TOKENS
    }


def _years(value: str) -> set[int]:
    return {int(item) for item in _YEAR_PATTERN.findall(value)}


def _periods(value: str) -> set[tuple[int, int]]:
    periods = {
        (int(year), int(term)) for year, term in _PERIOD_PATTERN.findall(value)
    }
    for month, year in _DATE_PATTERN.findall(value):
        periods.add((int(year), 1 if int(month) <= 6 else 2))
    return periods


@dataclass(frozen=True)
class DocumentEvidence:
    title: str
    content: str = ""
    concurso: str | None = None
    year: int | None = None
    role: str | None = None
    organization: str | None = None
    variant: str | None = None

    @property
    def searchable_text(self) -> str:
        return " ".join(
            value
            for value in (
                self.title,
                self.content[:20_000],
                self.concurso,
                str(self.year) if self.year is not None else None,
                self.role,
                self.organization,
                self.variant,
            )
            if value
        )


def evidence_score(exam: DocumentEvidence, candidate: DocumentEvidence) -> int:
    score = 0
    for exam_value, candidate_value in (
        (exam.concurso, candidate.concurso),
        (exam.organization, candidate.organization),
    ):
        if exam_value and candidate_value:
            if normalize_text(exam_value) == normalize_text(candidate_value):
                score += 12
            else:
                score -= 4

    exam_text = exam.searchable_text
    candidate_text = candidate.searchable_text
    exam_years = _years(exam_text)
    candidate_years = _years(candidate_text)
    if exam.year is not None:
        exam_years.add(exam.year)
    if candidate.year is not None:
        candidate_years.add(candidate.year)
    if exam_years and candidate_years:
        score += 10 if exam_years & candidate_years else -10

    exam_periods = _periods(exam_text)
    candidate_periods = _periods(candidate_text)
    if exam_periods and candidate_periods:
        score += 14 if exam_periods & candidate_periods else -8

    if exam.role:
        role = normalize_text(exam.role)
        candidate_haystack = normalize_text(candidate_text)
        if candidate.role and role == normalize_text(candidate.role):
            score += 12
        else:
            score += 2 * sum(
                word in candidate_haystack
                for word in _tokens(exam.role)
                if len(word) > 2
            )

    exam_variant = _variant(f"{exam.variant or ''} {exam.title}")
    candidate_variant = _variant(f"{candidate.variant or ''} {candidate.title} {candidate.content}")
    if exam_variant is not None and candidate_variant is not None:
        score += 9 if exam_variant == candidate_variant else -9

    common_title_tokens = _tokens(exam.title) & _tokens(candidate.title)
    score += min(8, len(common_title_tokens))
    if "definitiv" in normalize_text(candidate_text):
        score += 1
    if candidate.content.strip():
        score += 1
    return score


def select_evidence_match(
    exam: DocumentEvidence, candidates: list[DocumentEvidence]
) -> tuple[int | None, str | None]:
    if not candidates:
        return None, "missing"
    if len(candidates) == 1:
        return 0, None

    scores = [evidence_score(exam, candidate) for candidate in candidates]
    best_score = max(scores)
    if best_score <= 0:
        return None, "no_evidence"
    best = [index for index, score in enumerate(scores) if score == best_score]
    if len(best) != 1:
        return None, "ambiguous"
    return best[0], None
