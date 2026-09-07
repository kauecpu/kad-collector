from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .fgv_turn import normalize_fgv_turn
from .json_utils import read_json, write_json
from .models import ExtractionManifest, QuestionBatch, QuestionRecord, ReviewState
from .validation import validate_questions


@dataclass(frozen=True)
class AnswerEntry:
    number: int
    answer: str | None
    annulled: bool = False


def questions_use_true_false(questions: list[QuestionRecord]) -> bool:
    if not questions:
        return False
    return all(
        [(item.letter, item.text.casefold()) for item in question.alternatives]
        == [("A", "certo"), ("B", "errado")]
        for question in questions
    )


def adapt_true_false_entries(
    entries: dict[int, AnswerEntry],
) -> dict[int, AnswerEntry]:
    """Translate Cebraspe's C/E alphabet to the internal A/B representation."""
    translated: dict[int, AnswerEntry] = {}
    for number, entry in entries.items():
        if entry.annulled:
            translated[number] = entry
            continue
        answer = {"C": "A", "E": "B"}.get(entry.answer or "")
        if answer is None:
            return {}
        translated[number] = AnswerEntry(number=number, answer=answer)
    return translated


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
_GABARITO_HEADING = re.compile(
    r"^\s*GABARITO\s+(?P<number>[1-9]\d*)\s*$", re.IGNORECASE | re.MULTILINE
)
_GRID_HEADING = re.compile(
    r"^\s*(?P<label>.+?)\s*[-–—]\s*(?:(?:TIPO|PROVA)\s*)?"
    r"(?P<variant>[1-9]\d*)(?:\s*[-–—]\s*(?P<turn>Turno\s+[^()]+))?"
    r"(?:\s*\((?P<section>[^)]*)\))?\s*$",
    re.IGNORECASE,
)
_GRID_NUMBER = re.compile(r"\d{1,3}(?:ING|ESP)?", re.IGNORECASE)
_GRID_ANSWER = re.compile(r"[A-HX*]", re.IGNORECASE)
_CEBRASPE_GRID_LINE = re.compile(r"[CEX* ]{10,}", re.IGNORECASE)

# FCC publishes several booklet types in a single annex. A recognized annex
# must never fall back to the unscoped parser, which overwrites equal numbers.
FCC_BLOCK_HEADING = re.compile(
    r"(?im)^[ \t]*(?P<code>[A-Z]\d{2,})[ \t]*[-–—][ \t]*"
    r"(?P<role>[^\r\n]+?)[ \t]*[-–—][ \t]*Tipo[ \t]+(?P<variant>\d+)"
    r"[ \t]+Folha[ \t]*:[ \t]*\d+[ \t]*\r?$"
)


def _fcc_role(value: str) -> str:
    return " ".join(sorted(_normalized_words(re.sub(r"\([aA]\)", "", value))))


def _parse_fcc_blocks(
    text: str, *, variant: str | None, role: str | None,
) -> dict[int, AnswerEntry] | None:
    headings = list(FCC_BLOCK_HEADING.finditer(text))
    if not headings:
        return None
    if len(headings) > 128 or any(
        len(heading["variant"]) > 5 or not 1 <= int(heading["variant"]) <= 10_000
        for heading in headings
    ):
        return {}
    requested = _variant_number(variant)
    if variant and requested is None:
        return {}
    blocks: dict[tuple[str, str, int], dict[int, AnswerEntry]] = {}
    for index, heading in enumerate(headings):
        key = (heading["code"].upper(), _fcc_role(heading["role"]), int(heading["variant"]))
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        entries = blocks.setdefault(key, {})
        for line in text[heading.end():end].splitlines():
            # Reject prose and footer numbers: only complete rows of pairs count.
            matches = list(_INLINE_PATTERN.finditer(line))
            if not matches or _INLINE_PATTERN.sub("", line).strip():
                continue
            for match in matches:
                number = int(match["number"])
                raw = match["answer"].upper()
                annulled = raw in {"X", "*"} or raw.startswith("ANULAD")
                entry = AnswerEntry(number, None if annulled else raw, annulled)
                if number in entries and entries[number] != entry:
                    return {}
                entries[number] = entry
    candidates = [entries for (_, label, kind), entries in blocks.items()
                  if (requested is None or kind == requested)
                  and (not role or label == _fcc_role(role))]
    return candidates[0] if len(candidates) == 1 else {}


def _parse_cebraspe_grid(text: str) -> dict[int, AnswerEntry] | None:
    normalized = text.casefold()
    if (
        "gabarito" not in normalized
        or not (
            any(owner in normalized for owner in ("cebraspe", "cespe", "polícia federal"))
            or ("gabaritos oficiais" in normalized and "cargo:" in normalized)
        )
    ):
        return None
    lines = [" ".join(line.split()) for line in text.splitlines()]
    entries: dict[int, AnswerEntry] = {}
    last_number = 0
    for index, line in enumerate(lines[:-1]):
        answer_line = lines[index + 1]
        if not re.fullmatch(r"[CEX*0 ]+", answer_line, re.IGNORECASE):
            continue
        answers = re.findall(r"[CEX*]", answer_line.upper())
        raw_numbers = [int(value) for value in re.findall(r"\d+", line)]
        nonzero_numbers = [number for number in raw_numbers if number > 0]
        if not nonzero_numbers or not answers:
            continue
        start = nonzero_numbers[0]
        if start <= last_number:
            continue
        inferred_numbers = list(range(start, start + len(answers)))
        visible_numbers = [number for number in nonzero_numbers if number <= inferred_numbers[-1]]
        if any(number not in inferred_numbers for number in visible_numbers):
            return {}
        for number, answer in zip(inferred_numbers, answers, strict=True):
            entry = AnswerEntry(
                number=number,
                answer=None if answer in "X*" else answer,
                annulled=answer in "X*",
            )
            if number in entries and entries[number] != entry:
                return {}
            entries[number] = entry
        last_number = inferred_numbers[-1]
    return entries if len(entries) >= 10 else {}


@dataclass
class _AnswerGrid:
    label: str
    variant: int | None
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
    active_turn: str | None = None
    lines = [" ".join(raw_line.split()) for raw_line in text.splitlines()]
    for index, line in enumerate(lines):
        structural_turn = normalize_fgv_turn(line)
        if structural_turn is not None:
            if active is not None and active.entries:
                grids.append(active)
            active = None
            active_turn = structural_turn
            pending_numbers = []
            pending_single_number = None
            continue
        heading = _GRID_HEADING.match(line)
        if heading is not None:
            if active is not None and active.entries:
                grids.append(active)
            active = _AnswerGrid(
                label=heading.group("label"),
                variant=int(heading.group("variant")),
                section=heading.group("section") or heading.group("turn") or active_turn,
                entries={},
            )
            pending_numbers = []
            pending_single_number = None
            continue
        next_line = next((item for item in lines[index + 1 :] if item), "")
        next_numbers = _GRID_NUMBER.findall(next_line)
        untyped_heading = (
            bool(re.search(r"[A-Za-zÀ-ÿ]", line))
            and re.fullmatch(r"(?:[A-HX*][.\s]*)+", line, re.IGNORECASE) is None
            and len(next_numbers) >= 2
            and re.fullmatch(
                r"(?:\d{1,3}(?:ING|ESP)?\s*)+", next_line, re.IGNORECASE
            )
            is not None
        )
        if untyped_heading:
            if active is not None and active.entries:
                grids.append(active)
            active = _AnswerGrid(
                label=line, variant=None, section=active_turn, entries={}
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
            r"(?:[A-HX*][.\s]*)+", line, re.IGNORECASE
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
    candidates = [
        grid
        for grid in grids
        if variant_number is None or grid.variant in {None, variant_number}
    ]
    if not candidates:
        return None
    turn_words = _normalized_words(turn or "")
    if turn_words:
        sectioned = [grid for grid in candidates if _normalized_words(grid.section or "")]
        matching_turn = [
            grid for grid in sectioned if turn_words & _normalized_words(grid.section or "")
        ]
        if sectioned:
            if not matching_turn:
                return {}
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
    fcc_entries = _parse_fcc_blocks(text, variant=variant, role=role)
    if fcc_entries is not None:
        return fcc_entries
    cebraspe_entries = _parse_cebraspe_grid(text)
    if cebraspe_entries is not None:
        return cebraspe_entries
    grid_entries = _select_answer_grid(
        _parse_answer_grids(text), variant=variant, role=role, turn=turn
    )
    if grid_entries is not None:
        return grid_entries
    entries: dict[int, AnswerEntry] = {}
    requested_variant_number = _variant_number(variant)
    if requested_variant_number is not None:
        headings = list(_GABARITO_HEADING.finditer(text))
        selected_heading = next(
            (
                heading
                for heading in headings
                if int(heading.group("number")) == requested_variant_number
            ),
            None,
        )
        if selected_heading is not None:
            next_heading = next(
                (heading for heading in headings if heading.start() > selected_heading.start()),
                None,
            )
            text = text[
                selected_heading.end() :
                next_heading.start() if next_heading is not None else None
            ]
    variants = list(dict.fromkeys(item.upper() for item in _VARIANT_PATTERN.findall(text)))
    normalized_variant = (
        f"V{requested_variant_number}"
        if requested_variant_number is not None
        else (variant or "").upper()
    )
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
