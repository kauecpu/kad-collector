from __future__ import annotations

import re
import tomllib
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fgv_turn import extract_fgv_turn_evidence, is_fgv_source, normalize_fgv_turn
from .models import QuestionRecord

SectionKind = Literal[
    "instructions",
    "objective",
    "discursive",
    "answer_sheet",
    "non_question",
]
QuestionSectionKind = Literal["objective", "discursive"]
ParsingStatus = Literal["completed", "incomplete"]
Confidence = Literal["high", "medium", "low"]
ObjectiveParser = Callable[
    [list[dict[str, Any]]], tuple[list[QuestionRecord], list[str]]
]

_DISCURSIVE_HEADING = re.compile(
    r"(?im)^\s*(?:prova\s+discursiva|quest(?:ões|oes)\s+discursivas|"
    r"redaç(?:ão|ao)|estudo\s+de\s+caso)\s*$"
)
_OBJECTIVE_HEADING = re.compile(
    r"(?im)^\s*(?:prova\s+objetiva|quest(?:ões|oes)\s+objetivas)\s*$"
)
_ANSWER_SHEET_HEADING = re.compile(
    r"(?im)^\s*(?:cart[aã]o|folha)\s+(?:de\s+)?respostas?\s*$"
)
_QUESTION_MARKER = re.compile(
    r"^\s*(?:(?:QUEST(?:ÃO|AO)|QUEST[.])\s*)?(?P<number>\d{1,3})\s*$",
    re.IGNORECASE,
)
_DISCURSIVE_QUESTION = re.compile(
    r"(?im)^\s*Quest(?:ão|ao)\s+(?P<number>\d{1,3})\s*$"
)
_BOOKLET_TYPE = re.compile(r"(?i)\bTIPO\s+(?P<number>[1-9]\d*)\b")
_NON_QUESTION_PAGE = re.compile(r"(?i)^\s*(?:realizaç(?:ão|ao)|fim)\s*$")
_RESPONSE_LINE_NUMBER = re.compile(r"^\s*\d{1,3}\s*$")
_RESPONSE_RULE = re.compile(r"-{20,}")


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", plain).casefold().split())


class ExpectedSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QuestionSectionKind
    first: int = Field(ge=1)
    last: int = Field(ge=1)
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> ExpectedSection:
        if self.last < self.first:
            raise ValueError("section last must be greater than or equal to first")
        if self.count != self.last - self.first + 1:
            raise ValueError("section count must match its inclusive interval")
        return self


class FgvSectionProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    application_id: str = Field(min_length=1)
    contest_aliases: tuple[str, ...] = Field(min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)
    shift: str = Field(min_length=1)
    booklet_types: tuple[int, ...] = Field(min_length=1)
    sections: tuple[ExpectedSection, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profile(self) -> FgvSectionProfile:
        kinds = [section.kind for section in self.sections]
        if kinds.count("objective") != 1:
            raise ValueError("FGV profile must declare exactly one objective section")
        if len(kinds) != len(set(kinds)):
            raise ValueError("FGV profile section kinds must be unique")
        if len(self.booklet_types) != len(set(self.booklet_types)):
            raise ValueError("FGV profile booklet types must be unique")
        return self


class FgvProfileCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    profiles: tuple[FgvSectionProfile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ids(self) -> FgvProfileCatalog:
        ids = [profile.id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("FGV profile ids must be unique")
        return self


@dataclass(frozen=True)
class BankParsingContext:
    document_id: str
    board: str | None = None
    provider: str | None = None
    contest: str | None = None
    application_id: str | None = None
    role: str | None = None
    shift: str | None = None
    booklet_type: int | None = None


@dataclass(frozen=True)
class FgvDocumentIdentity:
    role: str | None
    shift: str | None
    booklet_type: int | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class DetectedSection:
    kind: SectionKind
    page_start: int
    page_end: int
    expected_first: int | None
    expected_last: int | None
    found_numbers: tuple[int, ...]
    evidence: tuple[str, ...]
    confidence: Confidence
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsingException:
    contest: str | None
    application_id: str | None
    document_id: str
    role: str | None
    shift: str | None
    booklet_type: int | None
    section: QuestionSectionKind
    expected_number: int | None
    nearby_pages: tuple[int, ...]
    reason: str
    evidence: tuple[str, ...]
    recommended_action: str


@dataclass(frozen=True)
class BankParsingResult:
    adapter_id: str
    adapter_version: str
    profile_id: str | None
    identity: FgvDocumentIdentity
    sections: tuple[DetectedSection, ...]
    objective_questions: tuple[QuestionRecord, ...]
    discursive_numbers: tuple[int, ...]
    expected_intervals: tuple[ExpectedSection, ...]
    exceptions: tuple[ParsingException, ...]
    warnings: tuple[str, ...]
    status: ParsingStatus
    summary: dict[str, int | bool | str]

    def to_payload(self) -> dict[str, object]:
        return {
            "adapterId": self.adapter_id,
            "adapterVersion": self.adapter_version,
            "profileId": self.profile_id,
            "identity": asdict(self.identity),
            "sections": [asdict(section) for section in self.sections],
            "objectiveNumbers": [question.number for question in self.objective_questions],
            "discursiveNumbers": list(self.discursive_numbers),
            "expectedIntervals": [
                section.model_dump(mode="json") for section in self.expected_intervals
            ],
            "exceptions": [asdict(exception) for exception in self.exceptions],
            "warnings": list(self.warnings),
            "status": self.status,
            "summary": self.summary,
        }

    def warning_messages(self) -> list[str]:
        messages = list(self.warnings)
        for exception in self.exceptions:
            label = (
                f"questão {exception.expected_number}"
                if exception.expected_number is not None
                else "documento"
            )
            messages.append(f"{exception.section}: {label}: {exception.reason}")
        return list(dict.fromkeys(messages))


def load_fgv_profiles(text: str | None = None) -> FgvProfileCatalog:
    if text is None:
        source = resources.files("kad_collector").joinpath("fgv_section_profiles.v1.toml")
        text = source.read_text(encoding="utf-8")
    return FgvProfileCatalog.model_validate(tomllib.loads(text))


def _page_number(page: dict[str, Any]) -> int:
    return int(page["page_number"])


def _page_text(page: dict[str, Any]) -> str:
    return str(page.get("text") or "")


def _first_lines(text: str, limit: int = 4) -> tuple[str, ...]:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    return tuple(lines[:limit])


def _is_response_sheet_page(text: str) -> bool:
    lines = text.splitlines()
    numbered_lines = sum(_RESPONSE_LINE_NUMBER.match(line) is not None for line in lines)
    ruled_lines = sum(_RESPONSE_RULE.search(line) is not None for line in lines)
    return numbered_lines >= 3 and ruled_lines >= 3


def infer_fgv_identity(
    pages: list[dict[str, Any]],
    context: BankParsingContext,
    profiles: tuple[FgvSectionProfile, ...],
) -> FgvDocumentIdentity:
    header_text = "\n".join(_page_text(page) for page in pages[:4])
    normalized_header = _normalize(header_text)
    evidence: list[str] = []

    turn_evidence = extract_fgv_turn_evidence(
        [(_page_number(page), _page_text(page)) for page in pages],
        document_role="exam",
    )
    shift = (
        turn_evidence[0].normalized
        if len(turn_evidence) == 1
        else normalize_fgv_turn(context.shift or "")
        if not turn_evidence
        else None
    )
    evidence.extend(item.raw for item in turn_evidence)

    type_match = _BOOKLET_TYPE.search(header_text)
    booklet_type = int(type_match.group("number")) if type_match else context.booklet_type
    if type_match:
        evidence.append(" ".join(type_match.group(0).split()))

    matched_roles = {
        role
        for profile in profiles
        for role in profile.roles
        if _normalize(role) and _normalize(role) in normalized_header
    }
    role = next(iter(matched_roles)) if len(matched_roles) == 1 else context.role
    if role and _normalize(role) in normalized_header:
        evidence.append(role)

    return FgvDocumentIdentity(
        role=role,
        shift=shift,
        booklet_type=booklet_type,
        evidence=tuple(dict.fromkeys(evidence)),
    )


def _is_fgv(context: BankParsingContext) -> bool:
    return is_fgv_source(board=context.board, provider=context.provider)


def _profile_matches_contest(profile: FgvSectionProfile, contest: str | None) -> bool:
    normalized_contest = _normalize(contest)
    return bool(normalized_contest) and normalized_contest in {
        _normalize(alias) for alias in profile.contest_aliases
    }


def resolve_fgv_profile(
    catalog: FgvProfileCatalog,
    context: BankParsingContext,
    identity: FgvDocumentIdentity,
) -> FgvSectionProfile | None:
    candidates = [
        profile
        for profile in catalog.profiles
        if _profile_matches_contest(profile, context.contest)
    ]
    if identity.role:
        role = _normalize(identity.role)
        candidates = [
            profile
            for profile in candidates
            if role in {_normalize(candidate) for candidate in profile.roles}
        ]
    if identity.shift:
        shift = _normalize(identity.shift)
        candidates = [profile for profile in candidates if _normalize(profile.shift) == shift]
    if identity.booklet_type is not None:
        candidates = [
            profile
            for profile in candidates
            if identity.booklet_type in profile.booklet_types
        ]
    return candidates[0] if len(candidates) == 1 else None


def _split_discursive_pages(
    pages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...]]:
    objective_pages: list[dict[str, Any]] = []
    discursive_pages: list[dict[str, Any]] = []
    evidence: list[str] = []
    discursive_started = False
    for page in pages:
        text = _page_text(page)
        page_number = _page_number(page)
        if discursive_started:
            discursive_pages.append({"page_number": page_number, "text": text})
            continue
        split = _DISCURSIVE_HEADING.split(text, maxsplit=1)
        before = split[0]
        if before.strip():
            objective_pages.append({"page_number": page_number, "text": before})
        if len(split) == 2:
            match = _DISCURSIVE_HEADING.search(text)
            if match:
                evidence.append(" ".join(match.group(0).split()))
            discursive_started = True
            discursive_pages.append({"page_number": page_number, "text": split[1]})
    return objective_pages, discursive_pages, tuple(evidence)


def _marker_occurrences(pages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    occurrences: list[tuple[int, int]] = []
    for page in pages:
        page_number = _page_number(page)
        for raw_line in _page_text(page).splitlines():
            match = _QUESTION_MARKER.match(raw_line)
            if match:
                occurrences.append((int(match.group("number")), page_number))
    return occurrences


def _objective_parser_pages(
    pages: list[dict[str, Any]], expected: ExpectedSection | None
) -> list[dict[str, Any]]:
    if expected is None:
        return pages
    output: list[dict[str, Any]] = []
    for page in pages:
        kept: list[str] = []
        for line in _page_text(page).splitlines():
            match = _QUESTION_MARKER.match(line)
            if match and not expected.first <= int(match.group("number")) <= expected.last:
                continue
            kept.append(line)
        output.append({"page_number": _page_number(page), "text": "\n".join(kept)})
    return output


def _nearby_pages(
    number: int, marker_pages: dict[int, set[int]], all_pages: list[int]
) -> tuple[int, ...]:
    pages = set(marker_pages.get(number, set()))
    for distance in range(1, 4):
        pages.update(marker_pages.get(number - distance, set()))
        pages.update(marker_pages.get(number + distance, set()))
        if pages:
            break
    if not pages and all_pages:
        pages.add(all_pages[0])
    return tuple(sorted(pages)[:6])


def _section_exceptions(
    *,
    context: BankParsingContext,
    identity: FgvDocumentIdentity,
    application_id: str | None,
    expected: ExpectedSection,
    occurrences: list[tuple[int, int]],
    extracted_numbers: list[int],
    evidence: tuple[str, ...],
    all_pages: list[int],
) -> list[ParsingException]:
    exceptions: list[ParsingException] = []
    marker_pages: dict[int, set[int]] = defaultdict(set)
    for number, page in occurrences:
        marker_pages[number].add(page)
    occurrence_numbers = [number for number, _ in occurrences]
    counts = Counter(occurrence_numbers)
    extracted = set(extracted_numbers)
    expected_numbers = set(range(expected.first, expected.last + 1))

    def add(number: int | None, reason: str, pages: tuple[int, ...]) -> None:
        exceptions.append(
            ParsingException(
                contest=context.contest,
                application_id=application_id,
                document_id=context.document_id,
                role=identity.role,
                shift=identity.shift,
                booklet_type=identity.booklet_type,
                section=expected.kind,
                expected_number=number,
                nearby_pages=pages,
                reason=reason,
                evidence=evidence,
                recommended_action=(
                    "Revisar o PDF original e corrigir o marcador ou a configuração."
                ),
            )
        )

    for number in sorted(expected_numbers - extracted):
        add(
            number,
            "questão esperada não extraída",
            _nearby_pages(number, marker_pages, all_pages),
        )
    for number in sorted(value for value, count in counts.items() if count > 1):
        add(number, "numeração duplicada", tuple(sorted(marker_pages[number])))
    for number in sorted(set(occurrence_numbers) - expected_numbers):
        add(number, "número fora do intervalo esperado", tuple(sorted(marker_pages[number])))
    in_range = [number for number in occurrence_numbers if number in expected_numbers]
    if any(
        current <= previous
        for previous, current in zip(in_range, in_range[1:], strict=False)
    ):
        add(None, "quebra de ordem na numeração", tuple(sorted(set(all_pages))))
    return exceptions


def _detected_sections(
    *,
    pages: list[dict[str, Any]],
    objective_occurrences: list[tuple[int, int]],
    discursive_occurrences: list[tuple[int, int]],
    expected_sections: tuple[ExpectedSection, ...],
    discursive_evidence: tuple[str, ...],
) -> tuple[DetectedSection, ...]:
    sections: list[DetectedSection] = []
    all_page_numbers = [_page_number(page) for page in pages]
    objective_pages = [page for _, page in objective_occurrences]
    first_objective = min(objective_pages) if objective_pages else None
    if all_page_numbers and first_objective is not None:
        sections.append(
            DetectedSection(
                kind="instructions",
                page_start=min(all_page_numbers),
                page_end=first_objective,
                expected_first=None,
                expected_last=None,
                found_numbers=(),
                evidence=_first_lines(_page_text(pages[0])),
                confidence="medium",
            )
        )
    for expected in expected_sections:
        occurrences = (
            objective_occurrences if expected.kind == "objective" else discursive_occurrences
        )
        occurrence_pages = [page for _, page in occurrences]
        if not occurrence_pages:
            continue
        evidence = (
            (f"marcador de questão {occurrences[0][0]}",)
            if expected.kind == "objective"
            else discursive_evidence
        )
        sections.append(
            DetectedSection(
                kind=expected.kind,
                page_start=min(occurrence_pages),
                page_end=max(occurrence_pages),
                expected_first=expected.first,
                expected_last=expected.last,
                found_numbers=tuple(number for number, _ in occurrences),
                evidence=evidence,
                confidence="high",
            )
        )
    for page in pages:
        text = _page_text(page)
        page_number = _page_number(page)
        if _ANSWER_SHEET_HEADING.search(text) or _is_response_sheet_page(text):
            sections.append(
                DetectedSection(
                    kind="answer_sheet",
                    page_start=page_number,
                    page_end=page_number,
                    expected_first=None,
                    expected_last=None,
                    found_numbers=(),
                    evidence=(
                        (
                            "folha ou cartão de respostas"
                            if _ANSWER_SHEET_HEADING.search(text)
                            else "linhas numeradas de resposta"
                        ),
                    ),
                    confidence="high",
                )
            )
        sparse_lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        if sparse_lines and len(sparse_lines) <= 3 and any(
            _NON_QUESTION_PAGE.match(line) for line in sparse_lines
        ):
            sections.append(
                DetectedSection(
                    kind="non_question",
                    page_start=page_number,
                    page_end=page_number,
                    expected_first=None,
                    expected_last=None,
                    found_numbers=(),
                    evidence=tuple(sparse_lines),
                    confidence="high",
                )
            )
    return tuple(sections)


class FgvSectionAdapter:
    adapter_id = "fgv-sections"
    adapter_version = "1.1"

    def __init__(self, catalog: FgvProfileCatalog | None = None) -> None:
        self.catalog = catalog or load_fgv_profiles()

    def supports(self, context: BankParsingContext) -> bool:
        return _is_fgv(context)

    def parse(
        self,
        pages: list[dict[str, Any]],
        context: BankParsingContext,
        objective_parser: ObjectiveParser,
    ) -> BankParsingResult:
        identity = infer_fgv_identity(pages, context, self.catalog.profiles)
        profile = resolve_fgv_profile(self.catalog, context, identity)
        objective_pages, discursive_pages, discursive_evidence = _split_discursive_pages(pages)
        objective_expected = (
            next(section for section in profile.sections if section.kind == "objective")
            if profile
            else None
        )
        parser_pages = _objective_parser_pages(objective_pages, objective_expected)
        objective_questions, parser_warnings = objective_parser(parser_pages)
        objective_occurrences = _marker_occurrences(objective_pages)
        discursive_occurrences = [
            (int(match.group("number")), _page_number(page))
            for page in discursive_pages
            for match in _DISCURSIVE_QUESTION.finditer(_page_text(page))
        ]
        expected_sections = profile.sections if profile else ()
        exceptions: list[ParsingException] = []
        all_pages = [_page_number(page) for page in pages]

        if profile is None:
            exceptions.append(
                ParsingException(
                    contest=context.contest,
                    application_id=context.application_id,
                    document_id=context.document_id,
                    role=identity.role,
                    shift=identity.shift,
                    booklet_type=identity.booklet_type,
                    section="objective",
                    expected_number=None,
                    nearby_pages=tuple(all_pages[:4]),
                    reason="perfil oficial de intervalos não localizado",
                    evidence=identity.evidence,
                    recommended_action="Cadastrar o concurso no catálogo versionado da FGV.",
                )
            )
        else:
            for expected in expected_sections:
                occurrences = (
                    objective_occurrences
                    if expected.kind == "objective"
                    else discursive_occurrences
                )
                extracted_numbers = (
                    [question.number for question in objective_questions]
                    if expected.kind == "objective"
                    else [number for number, _ in discursive_occurrences]
                )
                evidence = (
                    (f"perfil {profile.id}: {expected.first}-{expected.last}",)
                    + (discursive_evidence if expected.kind == "discursive" else ())
                )
                exceptions.extend(
                    _section_exceptions(
                        context=context,
                        identity=identity,
                        application_id=profile.application_id,
                        expected=expected,
                        occurrences=occurrences,
                        extracted_numbers=extracted_numbers,
                        evidence=evidence,
                        all_pages=all_pages,
                    )
                )

        sections = _detected_sections(
            pages=pages,
            objective_occurrences=objective_occurrences,
            discursive_occurrences=discursive_occurrences,
            expected_sections=expected_sections,
            discursive_evidence=discursive_evidence,
        )
        status: ParsingStatus = "incomplete" if exceptions else "completed"
        return BankParsingResult(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            profile_id=profile.id if profile else None,
            identity=identity,
            sections=sections,
            objective_questions=tuple(objective_questions),
            discursive_numbers=tuple(number for number, _ in discursive_occurrences),
            expected_intervals=expected_sections,
            exceptions=tuple(exceptions),
            warnings=tuple(parser_warnings),
            status=status,
            summary={
                "objectiveFound": len(objective_questions),
                "discursiveFound": len(discursive_occurrences),
                "exceptions": len(exceptions),
                "numberingClosed": not exceptions,
                "status": status,
            },
        )


def objective_heading_present(pages: list[dict[str, Any]]) -> bool:
    return any(_OBJECTIVE_HEADING.search(_page_text(page)) for page in pages)
