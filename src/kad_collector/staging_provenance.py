"""Canonical staging records from already approved, verified occurrences."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from .editorial_export import (
    EditorialImportRecordV2,
    build_editorial_record,
    stable_question_id,
)
from .models import QuestionBatch, QuestionRecord
from .question_equivalence import question_fingerprints


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _text(value: str) -> str:
    # Unlike the discovery fingerprint, this must preserve operators and punctuation.
    return " ".join(unicodedata.normalize("NFC", value).split())


@dataclass(frozen=True)
class StagingOccurrence:
    stable_id: str
    batch: QuestionBatch
    question: QuestionRecord
    audit: dict[str, Any]

    @property
    def equivalence_key(self) -> str:
        return question_fingerprints(self.question.model_dump(mode="json")).invariant


def build_canonical_staging_record(
    occurrences: list[StagingOccurrence],
) -> tuple[EditorialImportRecordV2, dict[str, Any]]:
    ordered = sorted(occurrences, key=lambda item: item.stable_id)
    representative = ordered[0]
    question = representative.question
    canonical_id = representative.equivalence_key
    reference_texts = {_text(item.text): item.letter for item in question.alternatives}
    if len(reference_texts) != len(question.alternatives):
        raise ValueError("alternativas com texto repetido; equivalência ambígua")
    taxonomy = (question.discipline, question.matter, question.subject, question.level)
    provenances: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    occurrence_ids: set[str] = set()
    for item in ordered:
        source = item.batch.source_document
        answer_key = item.batch.answer_key_document
        current = item.question
        if answer_key is None or current.answer_status != "matched":
            raise ValueError("ocorrência sem associação de gabarito compatível")
        current_texts = {_text(option.text): option.letter for option in current.alternatives}
        if (
            _text(current.statement) != _text(question.statement)
            or set(current_texts) != set(reference_texts)
            or len(current_texts) != len(current.alternatives)
        ):
            raise ValueError("colisão de equivalência; conteúdo não é idêntico")
        if (current.discipline, current.matter, current.subject, current.level) != taxonomy:
            raise ValueError("classificações divergentes no grupo de equivalência")
        correct_text = next(
            (
                _text(option.text)
                for option in current.alternatives
                if option.letter == current.correct_answer
            ),
            None,
        )
        canonical_answer = reference_texts.get(correct_text or "")
        if canonical_answer is None or canonical_answer != question.correct_answer:
            raise ValueError("respostas oficiais divergentes no grupo de equivalência")
        occurrence_id = "occ-" + _sha([source.sha256, current.number])
        if occurrence_id in occurrence_ids:
            raise ValueError("ocorrência repetida na campanha de aprovação")
        occurrence_ids.add(occurrence_id)
        association = next(
            note for note in current.review_notes if note.startswith("Associação de gabarito:")
        )
        link_id = "akl-" + _sha([source.sha256, answer_key.sha256, association])
        provenances.append(
            {
                "occurrenceId": occurrence_id,
                "questionId": item.stable_id,
                "questionNumber": current.number,
                "pages": sorted(set(current.source_pages)),
                "sha256": source.sha256,
                "url": source.resolved_url,
                "role": current.role,
                "shift": source.metadata.get("shift"),
                "booklet": source.metadata.get("variant"),
                # KAD compares every provenance answer to the canonical alternative order.
                "answer": canonical_answer,
                "answerStatus": "matched",
                "answerKeyLinkId": link_id,
            }
        )
        evidence.append(
            {
                "stableId": item.stable_id,
                "occurrenceId": occurrence_id,
                "answerKeyLinkId": link_id,
                "examUrl": source.resolved_url,
                "examSha256": source.sha256,
                "answerKeyUrl": answer_key.resolved_url,
                "answerKeySha256": answer_key.sha256,
                "questionNumber": current.number,
                "pages": sorted(set(current.source_pages)),
                "originalAnswer": current.correct_answer,
                "canonicalAnswer": canonical_answer,
                "alternativeMapping": {
                    letter: reference_texts[text] for text, letter in sorted(current_texts.items())
                },
                "association": association,
                "audit": item.audit,
            }
        )
    record = build_editorial_record(
        representative.batch,
        question,
        canonical_question={
            "questionId": canonical_id,
            "groupId": "eq-" + canonical_id,
            "occurrenceCount": len(provenances),
            "provenances": provenances,
        },
    )
    # Enrich an existing approval export without creating a second KAD question.
    # The source fingerprint deliberately excludes data.id.
    record.data.id = stable_question_id(representative.batch, question)
    return record, {
        "stableId": representative.stable_id,
        "canonicalQuestionId": canonical_id,
        "equivalenceGroupId": "eq-" + canonical_id,
        "groupId": representative.audit.get("groupId"),
        "examUrl": evidence[0]["examUrl"],
        "examSha256": evidence[0]["examSha256"],
        "answerKeyUrl": evidence[0]["answerKeyUrl"],
        "answerKeySha256": evidence[0]["answerKeySha256"],
        "audit": representative.audit,
        "occurrences": evidence,
    }
