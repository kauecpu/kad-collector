from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_PERIOD_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*/\s*([12])(?!\d)")
_DATE_PATTERN = re.compile(r"(?<!\d)\d{1,2}/(\d{1,2})/((?:19|20)\d{2})(?!\d)")
_TURN_PATTERN = re.compile(r"\b(?:manha|tarde)\b", re.IGNORECASE)
_VARIANT_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?P<label>V|TIPO)[-_ ]*(?P<number>[1-9]\d*)(?!\d)",
    re.IGNORECASE,
)
_STOP_TOKENS = {
    "arquivo",
    "caderno",
    "com",
    "concurso",
    "concursos",
    "da",
    "das",
    "de",
    "definitivo",
    "definitiva",
    "do",
    "dos",
    "em",
    "exame",
    "exames",
    "fase",
    "gabarito",
    "gabaritos",
    "oficial",
    "pdf",
    "para",
    "por",
    "processo",
    "processos",
    "prova",
    "provas",
    "questao",
    "questoes",
    "resposta",
    "respostas",
    "selecao",
    "selecoes",
    "seletiva",
    "seletivas",
    "seletivo",
    "seletivos",
    "sem",
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


def _variants(value: str) -> set[tuple[str, int]]:
    return {
        (match.group("label").casefold(), int(match.group("number")))
        for match in _VARIANT_PATTERN.finditer(value)
    }


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


def _turns(value: str) -> set[str]:
    return {
        match.group(0).casefold()
        for match in _TURN_PATTERN.finditer(normalize_text(value))
    }


@dataclass(frozen=True)
class DocumentEvidence:
    title: str
    content: str = ""
    concurso: str | None = None
    year: int | None = None
    role: str | None = None
    organization: str | None = None
    variant: str | None = None
    turn: str | None = None

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
                self.turn,
            )
            if value
        )

    @property
    def known_turns(self) -> set[str]:
        for value in (self.turn or "", self.title, self.content):
            if turns := _turns(value):
                return turns
        return set()


def has_known_conflict(exam: DocumentEvidence, candidate: DocumentEvidence) -> bool:
    for exam_value, candidate_value in (
        (exam.concurso, candidate.concurso),
        (exam.role, candidate.role),
        (exam.organization, candidate.organization),
    ):
        if (
            exam_value
            and candidate_value
            and normalize_text(exam_value) != normalize_text(candidate_value)
        ):
            return True

    if (
        exam.year is not None
        and candidate.year is not None
        and exam.year != candidate.year
    ):
        return True

    explicit_exam_variant = _variant(exam.variant or "")
    explicit_candidate_variant = _variant(candidate.variant or "")
    if (
        explicit_exam_variant is not None
        and explicit_candidate_variant is not None
        and explicit_exam_variant != explicit_candidate_variant
    ):
        return True

    exam_text = exam.searchable_text
    candidate_text = candidate.searchable_text
    exam_years = _years(exam_text)
    candidate_years = _years(candidate_text)
    if exam.year is not None:
        exam_years.add(exam.year)
    if candidate.year is not None:
        candidate_years.add(candidate.year)
    if exam_years and candidate_years and not exam_years & candidate_years:
        return True

    exam_turns = exam.known_turns
    candidate_turns = candidate.known_turns
    if exam_turns and candidate_turns and not exam_turns & candidate_turns:
        return True

    exam_variants = _variants(f"{exam.variant or ''} {exam.title}")
    candidate_variants = _variants(
        f"{candidate.variant or ''} {candidate.title} {candidate.content}"
    )
    return bool(
        exam_variants and candidate_variants and not exam_variants & candidate_variants
    )


def _meaningful_title_evidence(common_tokens: set[str]) -> int:
    identifier_tokens = {
        token for token in common_tokens if any(character.isdigit() for character in token)
    }
    if identifier_tokens:
        return min(8, len(identifier_tokens))
    return min(8, len(common_tokens)) if len(common_tokens) >= 2 else 0


def _evidence_rank(
    exam: DocumentEvidence, candidate: DocumentEvidence
) -> tuple[int, int] | None:
    if has_known_conflict(exam, candidate):
        return None

    evidence = 0
    for exam_value, candidate_value in (
        (exam.concurso, candidate.concurso),
        (exam.organization, candidate.organization),
    ):
        if exam_value and candidate_value:
            evidence += 12

    if exam.year is not None and candidate.year is not None:
        evidence += 10

    explicit_exam_variant = _variant(exam.variant or "")
    explicit_candidate_variant = _variant(candidate.variant or "")
    if explicit_exam_variant is not None and explicit_candidate_variant is not None:
        evidence += 9

    exam_text = exam.searchable_text
    candidate_text = candidate.searchable_text
    exam_years = _years(exam_text)
    candidate_years = _years(candidate_text)
    if exam.year is not None:
        exam_years.add(exam.year)
    if candidate.year is not None:
        candidate_years.add(candidate.year)
    if exam_years & candidate_years and (exam.year is None or candidate.year is None):
        evidence += 10

    exam_periods = _periods(exam_text)
    candidate_periods = _periods(candidate_text)
    if exam_periods & candidate_periods:
        evidence += 14

    if exam.role:
        role = normalize_text(exam.role)
        candidate_haystack = normalize_text(candidate_text)
        if candidate.role and role == normalize_text(candidate.role):
            evidence += 12
        else:
            evidence += 2 * sum(
                word in candidate_haystack
                for word in _tokens(exam.role)
                if len(word) > 2
            )

    exam_variants = _variants(f"{exam.variant or ''} {exam.title}")
    candidate_variants = _variants(
        f"{candidate.variant or ''} {candidate.title} {candidate.content}"
    )
    if exam_variants & candidate_variants and (
        explicit_exam_variant is None or explicit_candidate_variant is None
    ):
        evidence += 9

    common_title_tokens = _tokens(exam.title) & _tokens(candidate.title)
    evidence += _meaningful_title_evidence(common_title_tokens)
    if evidence <= 0:
        return None

    tie_break = min(2, len(common_title_tokens))
    if "definitiv" in normalize_text(candidate_text):
        tie_break += 1
    if candidate.content.strip():
        tie_break += 1
    return evidence, tie_break


def select_evidence_match(
    exam: DocumentEvidence, candidates: list[DocumentEvidence]
) -> tuple[int | None, str | None]:
    if not candidates:
        return None, "missing"
    ranked = [
        (rank, index)
        for index, candidate in enumerate(candidates)
        if (rank := _evidence_rank(exam, candidate)) is not None
    ]
    if not ranked:
        return None, "no_evidence"
    best_rank = max(rank for rank, _index in ranked)
    best = [index for rank, index in ranked if rank == best_rank]
    if len(best) != 1:
        return None, "ambiguous"
    return best[0], None
