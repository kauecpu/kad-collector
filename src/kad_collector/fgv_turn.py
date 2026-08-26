from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

FgvDocumentRole = Literal["exam", "answer_key"]

_FGV_NAMES = frozenset(
    {
        "fgv",
        "fgv conhecimento",
        "fundacao getulio vargas",
        "fundacao getulio vargas fgv",
    }
)

_SHIFT_TOKEN = r"MANH[ÃA]|TARDE"
_STANDALONE_SHIFT = re.compile(rf"^\s*(?P<shift>{_SHIFT_TOKEN})\s*$", re.IGNORECASE)
_LABELED_SHIFT = re.compile(
    rf"^\s*Turno\s*[:\-–—]?\s*(?P<shift>{_SHIFT_TOKEN})\s*$",
    re.IGNORECASE,
)
_GRID_SHIFT = re.compile(
    rf"(?:\(\s*(?P<parenthetical>{_SHIFT_TOKEN})\s*\)|"
    rf"\bTurno\s*[:\-–—]?\s*(?P<labeled>{_SHIFT_TOKEN})\b)",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(
    r"^\s*(?:\{\s*\d{1,3}\s*\}|"
    r"(?:(?:QUEST(?:ÃO|AO)|QUEST[.])\s*)?\d{1,3}\s*)$",
    re.IGNORECASE,
)
_GRID_HEADING_MARKER = re.compile(
    r"(?:\b(?:TIPO|PROVA)\s*[1-9]\d*\b|[-–—]\s*[1-9]\d*\s*[-–—]\s*Turno\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FgvTurnEvidence:
    raw: str
    normalized: Literal["manhã", "tarde"]
    locator: str


def normalize_fgv_turn(value: str) -> Literal["manhã", "tarde"] | None:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    normalized = " ".join(plain.casefold().split())
    labeled = re.fullmatch(r"turno\s*[:\-–—]?\s*(manha|tarde)", normalized)
    if labeled is not None:
        normalized = labeled.group(1)
    if normalized == "manha":
        return "manhã"
    if normalized == "tarde":
        return "tarde"
    return None


def is_fgv_source(*, board: str | None, provider: str | None) -> bool:
    normalized_board = normalize_fgv_name(board or "")
    return normalized_board in _FGV_NAMES or provider == "fgv_conhecimento"


def normalize_fgv_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", plain).casefold().split())


def _line_shift(line: str, *, document_role: FgvDocumentRole) -> str | None:
    direct = _STANDALONE_SHIFT.fullmatch(line) or _LABELED_SHIFT.fullmatch(line)
    if direct is not None:
        return direct.group("shift")
    if document_role != "answer_key" or _GRID_HEADING_MARKER.search(line) is None:
        return None
    grid = _GRID_SHIFT.search(line)
    if grid is None:
        return None
    return grid.group("parenthetical") or grid.group("labeled")


def extract_fgv_turn_evidence(
    pages: Sequence[tuple[int, str]],
    *,
    document_role: FgvDocumentRole,
) -> tuple[FgvTurnEvidence, ...]:
    """Extract FGV shifts only from bounded structural PDF regions."""
    evidence: list[FgvTurnEvidence] = []
    seen: set[str] = set()
    question_started = False
    selected_pages = pages[:4] if document_role == "exam" else pages
    for page_number, text in selected_pages:
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = " ".join(raw_line.split())
            if not line:
                continue
            if document_role == "exam" and _QUESTION_START.fullmatch(line):
                question_started = True
                break
            raw_shift = _line_shift(line, document_role=document_role)
            if raw_shift is None:
                continue
            normalized = normalize_fgv_turn(raw_shift)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            evidence.append(
                FgvTurnEvidence(
                    raw=raw_shift,
                    normalized=normalized,
                    locator=f"page:{page_number}:line:{line_number}:fgv-turn",
                )
            )
        if question_started:
            break
    return tuple(evidence)
