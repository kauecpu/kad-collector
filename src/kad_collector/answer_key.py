from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .json_utils import read_json, write_json
from .models import ExtractionManifest, QuestionBatch, ReviewState
from .validation import validate_questions


@dataclass(frozen=True)
class AnswerEntry:
    number: int
    answer: str | None
    annulled: bool = False


_LINE_PATTERN = re.compile(
    r"^\s*(?P<number>\d{1,3})\s*[.\-):]?\s*(?P<answer>[A-H]|X|ANULAD[AO]|\*)\s*$",
    re.IGNORECASE,
)
_INLINE_PATTERN = re.compile(
    r"(?<!\d)(?P<number>\d{1,3})\s*[.\-):]\s*(?P<answer>[A-H]|X|ANULAD[AO]|\*)(?=\s|$)",
    re.IGNORECASE,
)
_TABULAR_PATTERN = re.compile(
    r"(?<!\d)(?P<number>\d{1,3})\s+(?P<answer>[A-H]|X|\*)(?=\s|$)",
    re.IGNORECASE,
)
_VARIANT_PATTERN = re.compile(r"\bV[1-9]\d*\b", re.IGNORECASE)
_GRID_HEADING = re.compile(
    r"^\s*(?P<label>.+?)\s*[-–—]\s*(?:(?:TIPO|PROVA)\s*)?"
    r"(?P<variant>[1-9]\d*)(?:\s*[-–—]\s*(?P<turn>Turno\s+[^()]+))?"
    r"(?:\s*\((?P<section>[^)]*)\))?\s*$",
    re.IGNORECASE,
)
_GRID_NUMBER = re.compile(r"\d{1,3}(?:ING|ESP)?", re.IGNORECASE)
_GRID_ANSWER = re.compile(r"[A-HX*]", re.IGNORECASE)


@dataclass
class _AnswerGrid:
    label: str
    variant: int
    section: str | None
    entries: dict[int, AnswerEntry]


def _normalized_words(value: str) -> set[str]:
    decomposed = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()
    return {
        word
        for word in re.findall(r"[a-z0-9]+", normalized)
        if len(word) > 1 and word not in {"tipo", "prova", "cargo"}
    }


def _variant_number(value: str | None) -> int | None:
    match = re.search(r"(?:V|TIPO|PROVA)?\s*([1-9]\d*)", value or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_answer_grids(text: str) -> list[_AnswerGrid]:
    grids: list[_AnswerGrid] = []
    active: _AnswerGrid | None = None
    pending_numbers: list[int] = []
    pending_single_number: int | None = None
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        heading = _GRID_HEADING.match(line)
        if heading is not None:
            if active is not None and active.entries:
                grids.append(active)
            active = _AnswerGrid(
                label=heading.group("label"),
                variant=int(heading.group("variant")),
                section=heading.group("section") or heading.group("turn"),
                entries={},
            )
            pending_numbers = []
            pending_single_number = None
            continue
        if active is None:
            continue
        if re.fullmatch(r"\d{1,3}", line):
            pending_single_number = int(line)
            continue
        if pending_single_number is not None and re.fullmatch(r"[A-HX*]", line, re.IGNORECASE):
            answer = line.upper()
            active.entries[pending_single_number] = AnswerEntry(
                number=pending_single_number,
                answer=None if answer in {"X", "*"} else answer,
                annulled=answer in {"X", "*"},
            )
            pending_single_number = None
            continue
        number_tokens = _GRID_NUMBER.findall(line)
        if len(number_tokens) >= 2 and re.fullmatch(
            r"(?:\d{1,3}(?:ING|ESP)?\s*)+", line, re.IGNORECASE
        ):
            pending_numbers = [int(re.sub(r"\D", "", item)) for item in number_tokens]
            continue
        answer_tokens = _GRID_ANSWER.findall(line)
        if pending_numbers and len(answer_tokens) == len(pending_numbers) and re.fullmatch(
            r"(?:[A-HX*]\s*)+", line, re.IGNORECASE
        ):
            for number, raw_answer in zip(pending_numbers, answer_tokens, strict=True):
                answer = raw_answer.upper()
                active.entries[number] = AnswerEntry(
                    number=number,
                    answer=None if answer in {"X", "*"} else answer,
                    annulled=answer in {"X", "*"},
                )
            pending_numbers = []
            pending_single_number = None
    if active is not None and active.entries:
        grids.append(active)
    return grids


def _select_answer_grid(
    grids: list[_AnswerGrid],
    *,
    variant: str | None,
    role: str | None,
    turn: str | None,
) -> dict[int, AnswerEntry] | None:
    if not grids:
        return None
    variant_number = _variant_number(variant)
    candidates = [grid for grid in grids if variant_number in {None, grid.variant}]
    if not candidates:
        return None
    turn_words = _normalized_words(turn or "")
    if turn_words:
        matching_turn = [
            grid for grid in candidates if turn_words & _normalized_words(grid.section or "")
        ]
        if matching_turn:
            candidates = matching_turn
    role_words = _normalized_words(role or "")
    if role_words:
        scored = [
            (len(role_words & _normalized_words(grid.label)), grid) for grid in candidates
        ]
        score, selected = max(scored, key=lambda item: (item[0], len(item[1].entries)))
        if score:
            return selected.entries
    if len(candidates) == 1:
        return candidates[0].entries
    return None


def parse_answer_key(
    text: str,
    *,
    variant: str | None = None,
    role: str | None = None,
    turn: str | None = None,
) -> dict[int, AnswerEntry]:
    grid_entries = _select_answer_grid(
        _parse_answer_grids(text), variant=variant, role=role, turn=turn
    )
    if grid_entries is not None:
        return grid_entries
    entries: dict[int, AnswerEntry] = {}
    variants = list(dict.fromkeys(item.upper() for item in _VARIANT_PATTERN.findall(text)))
    normalized_variant = (variant or "").upper()
    for line in text.splitlines():
        matches = list(_INLINE_PATTERN.finditer(line))
        if not matches:
            single = _LINE_PATTERN.match(line)
            matches = [single] if single else []
        if not matches:
            tabular = list(_TABULAR_PATTERN.finditer(line))
            if variants:
                if normalized_variant not in variants or len(tabular) % len(variants) != 0:
                    tabular = []
                else:
                    width = len(tabular) // len(variants)
                    index = variants.index(normalized_variant)
                    tabular = tabular[index * width : (index + 1) * width]
            matches = tabular
        for match in matches:
            if match is None:
                continue
            number = int(match.group("number"))
            raw_answer = match.group("answer").upper()
            annulled = raw_answer in {"X", "*"} or raw_answer.startswith("ANULAD")
            entries[number] = AnswerEntry(
                number=number,
                answer=None if annulled else raw_answer,
                annulled=annulled,
            )
    return entries


def apply_answer_entries(
    batch: QuestionBatch, entries: dict[int, AnswerEntry]
) -> QuestionBatch:
    if batch.review.status == "approved":
        raise ValueError("nao e permitido alterar um lote ja aprovado")
    updated = batch.model_copy(deep=True)
    for question in updated.questions:
        entry = entries.get(question.number)
        if entry is None:
            note = "resposta nao localizada no gabarito"
            if note not in question.review_notes:
                question.review_notes.append(note)
            continue
        if entry.annulled:
            question.correct_answer = None
            question.answer_status = "annulled"
        else:
            question.correct_answer = entry.answer
            question.answer_status = "matched"
    updated.review = ReviewState()
    updated.validation = validate_questions(updated.questions)
    return updated


def load_answer_key_text(path: Path) -> str:
    if path.suffix.lower() != ".json":
        return path.read_text(encoding="utf-8")
    manifest = ExtractionManifest.model_validate(read_json(path))
    texts = [
        document.text
        for document in manifest.documents
        if document.document.document_type == "answer_key"
    ]
    if not texts:
        raise ValueError("o manifesto nao contem documento do tipo answer_key")
    return "\n\n".join(texts)


def match_answer_key(
    batch_path: Path, answer_key_path: Path, output_path: Path | None = None
) -> tuple[QuestionBatch, Path]:
    batch = QuestionBatch.model_validate(read_json(batch_path))
    if batch.review.status == "approved":
        raise ValueError("nao e permitido alterar um lote ja aprovado")
    entries = parse_answer_key(load_answer_key_text(answer_key_path))
    if not entries:
        raise ValueError("nenhuma resposta reconhecida no gabarito")

    updated = apply_answer_entries(batch, entries)
    if output_path is None:
        output_path = Path("data/reviewed") / batch_path.name
    write_json(output_path, updated.model_dump(mode="json"))
    return updated, output_path
