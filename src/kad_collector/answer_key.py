from __future__ import annotations

import re
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


def parse_answer_key(text: str, *, variant: str | None = None) -> dict[int, AnswerEntry]:
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
