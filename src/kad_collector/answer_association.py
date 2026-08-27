from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from .answer_key import parse_answer_key
from .canonical_identity import canonicalize_profile_for_version
from .document_contract import NormalizedDocument
from .fgv_turn import is_fgv_source
from .question_equivalence import (
    sync_canonical_editorial_from_question,
    sync_question_occurrence,
)
from .semantic_identity import (
    AssociationCandidate,
    AssociationFieldComparison,
    DocumentAssociationDecision,
    DocumentSemanticProfile,
    QuestionInterval,
    SemanticField,
    canonical_json,
    extract_semantic_profile,
    stable_sha256,
)
from .semantic_registry import (
    active_answer_key_candidates,
    record_corrected_document_link,
    record_document_link,
)
from .semantic_resolution import ASSOCIATION_ALGORITHM_VERSION, select_answer_key

RevalidationResult = Literal["maintained", "changed", "invalidated", "ambiguous", "incomplete"]
AnswerKeyAuditStatus = Literal["confirmed", "uncertain", "incorrect", "missing"]
ANSWER_KEY_AUDIT_ALGORITHM_VERSION = "answer-key-audit-v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _single_value(field: SemanticField) -> str | None:
    if field.status != "known" or len(field.normalized_values) != 1:
        return None
    return str(field.normalized_values[0])


def _display_semantic_value(
    connection: sqlite3.Connection,
    field: SemanticField,
    *,
    table: str,
    label_column: str,
) -> str | None:
    value = _single_value(field)
    if value is None:
        return None
    allowed = {
        ("contest_roles", "display_name"),
        ("application_shifts", "official_name"),
        ("application_booklets", "display_name"),
    }
    if (table, label_column) not in allowed:
        raise ValueError("catálogo semântico não permitido")
    row = connection.execute(
        f"SELECT {label_column} AS label FROM {table} WHERE id=?",  # noqa: S608
        (value,),
    ).fetchone()
    return cast(str, row["label"]) if row is not None else value


def _closed_interval(numbers: list[int]) -> QuestionInterval | None:
    unique = sorted(set(numbers))
    if not unique or len(unique) != unique[-1] - unique[0] + 1:
        return None
    return QuestionInterval(first=unique[0], last=unique[-1])


@dataclass(frozen=True)
class RuntimeAssociationContext:
    exam_profile: DocumentSemanticProfile
    exam_interval: QuestionInterval | None
    candidates: tuple[AssociationCandidate, ...]
    answer_updates: dict[str, dict[int, tuple[str, str | None]]]


def _effective_profile_for_version(
    connection: sqlite3.Connection,
    version_id: str,
    stored_profile: DocumentSemanticProfile,
) -> DocumentSemanticProfile:
    """Enrich only missing turn evidence from immutable local FGV pages."""
    row = connection.execute(
        "SELECT d.id, d.normalized_json FROM documents d "
        "WHERE d.document_version_id = ? AND d.normalized_json IS NOT NULL "
        "ORDER BY (SELECT COALESCE(SUM(p.character_count), 0) FROM pages p "
        "WHERE p.document_id = d.id) DESC, d.created_at, d.id LIMIT 1",
        (version_id,),
    ).fetchone()
    if row is None:
        return stored_profile
    pages = [
        (int(item["page_number"]), cast(str, item["text"]))
        for item in connection.execute(
            "SELECT page_number, text FROM pages WHERE document_id = ? ORDER BY page_number",
            (row["id"],),
        ).fetchall()
    ]
    if not pages:
        return stored_profile
    normalized = NormalizedDocument.model_validate_json(cast(str, row["normalized_json"]))
    extracted = extract_semantic_profile(normalized, pages)

    def enrich(stored: SemanticField, fresh: SemanticField) -> SemanticField:
        return fresh if stored.status == "unknown" and fresh.status != "unknown" else stored

    identity_turns = enrich(stored_profile.identity.turns, extracted.identity.turns)
    coverage_turns = enrich(stored_profile.coverage.turns, extracted.coverage.turns)
    if (
        identity_turns == stored_profile.identity.turns
        and coverage_turns == stored_profile.coverage.turns
    ):
        return stored_profile
    identity = stored_profile.identity.model_copy(update={"turns": identity_turns})
    coverage = stored_profile.coverage.model_copy(update={"turns": coverage_turns})
    return stored_profile.model_copy(
        update={
            "identity": identity,
            "coverage": coverage,
            "has_conflict": stored_profile.has_conflict
            or identity_turns.status == "conflict"
            or coverage_turns.status == "conflict",
        }
    )


def build_runtime_context(
    connection: sqlite3.Connection, exam_version_id: str
) -> RuntimeAssociationContext:
    row = connection.execute(
        "SELECT profile_json FROM document_versions WHERE id = ? AND document_role = 'exam'",
        (exam_version_id,),
    ).fetchone()
    if row is None:
        raise ValueError("versão de prova não encontrada")
    exam_profile = canonicalize_profile_for_version(
        connection,
        exam_version_id,
        DocumentSemanticProfile.model_validate_json(cast(str, row["profile_json"])),
    )
    exam_profile = _effective_profile_for_version(
        connection, exam_version_id, exam_profile
    )
    question_rows = connection.execute(
        "SELECT q.question_number FROM questions q JOIN documents d ON d.id = q.document_id "
        "WHERE d.document_version_id = ? ORDER BY q.question_number",
        (exam_version_id,),
    ).fetchall()
    exam_interval = _closed_interval([int(item["question_number"]) for item in question_rows])
    role = _display_semantic_value(
        connection,
        exam_profile.identity.roles,
        table="contest_roles",
        label_column="display_name",
    )
    turn = _display_semantic_value(
        connection,
        exam_profile.identity.turns,
        table="application_shifts",
        label_column="official_name",
    )
    variant = _display_semantic_value(
        connection,
        exam_profile.identity.variants,
        table="application_booklets",
        label_column="display_name",
    )
    candidates: list[AssociationCandidate] = []
    answer_updates: dict[str, dict[int, tuple[str, str | None]]] = {}
    for item in active_answer_key_candidates(
        connection, exam_version_id, include_scope_conflicts=True
    ):
        version_id = cast(str, item["answer_key_version_id"])
        text_rows = connection.execute(
            "SELECT p.text FROM documents d JOIN pages p ON p.document_id = d.id "
            "WHERE d.document_version_id = ? ORDER BY d.created_at, p.page_number",
            (version_id,),
        ).fetchall()
        text = "\n".join(cast(str, text_row["text"]) for text_row in text_rows)
        entries = parse_answer_key(text, role=role, variant=variant, turn=turn)
        updates = {
            number: (
                "annulled" if entry.annulled else "matched",
                None if entry.annulled else entry.answer,
            )
            for number, entry in entries.items()
        }
        answer_updates[version_id] = updates
        candidate_profile = canonicalize_profile_for_version(
            connection,
            version_id,
            DocumentSemanticProfile.model_validate_json(canonical_json(item["profile"])),
        )
        candidate_profile = _effective_profile_for_version(
            connection, version_id, candidate_profile
        )
        candidates.append(
            AssociationCandidate(
                version_id=version_id,
                profile=candidate_profile,
                predecessor_version_id=cast(str | None, item["predecessor_version_id"]),
                question_interval=_closed_interval(list(updates)),
            )
        )
    return RuntimeAssociationContext(
        exam_profile=exam_profile,
        exam_interval=exam_interval,
        candidates=tuple(candidates),
        answer_updates=answer_updates,
    )


def decide_runtime_association(
    connection: sqlite3.Connection, exam_version_id: str
) -> tuple[RuntimeAssociationContext, DocumentAssociationDecision]:
    context = build_runtime_context(connection, exam_version_id)
    decision = select_answer_key(
        context.exam_profile,
        context.candidates,
        exam_interval=context.exam_interval,
    )
    if decision.selected_version_id is not None:
        validation = _answer_update_validation(
            connection,
            exam_version_id=exam_version_id,
            updates=context.answer_updates[decision.selected_version_id],
        )
        if not validation["compatible"]:
            selected = decision.selected_version_id
            assessments = tuple(
                item.model_copy(
                    update={
                        "compatible": False,
                        "conflicts": tuple(
                            dict.fromkeys(
                                (*item.conflicts, *cast(list[str], validation["conflicts"]))
                            )
                        ),
                        "incomplete_fields": tuple(
                            dict.fromkeys(
                                (
                                    *item.incomplete_fields,
                                    *cast(list[str], validation["incompleteFields"]),
                                )
                            )
                        ),
                        "comparisons": (
                            *item.comparisons,
                            AssociationFieldComparison(
                                field="answer_grid",
                                status=(
                                    "incompatible"
                                    if validation["conflicts"]
                                    else "incomplete"
                                ),
                                exam_values=tuple(validation["questionNumbers"]),
                                candidate_values=tuple(validation["answerNumbers"]),
                                reason=cast(str, validation["reason"]),
                            ),
                        ),
                        "reasons": (*item.reasons, cast(str, validation["reason"])),
                    }
                )
                if item.version_id == selected
                else item
                for item in decision.assessments
            )
            decision = decision.model_copy(
                update={
                    "outcome": "conflict" if validation["conflicts"] else "incomplete",
                    "selected_version_id": None,
                    "assessments": assessments,
                    "reason": validation["reason"],
                }
            )
    return context, decision


def _answer_update_validation(
    connection: sqlite3.Connection,
    *,
    exam_version_id: str,
    updates: dict[int, tuple[str, str | None]],
) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT q.question_number,q.payload_json FROM questions q "
        "JOIN documents d ON d.id=q.document_id WHERE d.document_version_id=? "
        "ORDER BY q.question_number,q.id",
        (exam_version_id,),
    ).fetchall()
    alternatives_by_number: dict[int, set[str]] = {}
    for row in rows:
        payload = json.loads(cast(str, row["payload_json"]))
        alternatives_by_number.setdefault(int(row["question_number"]), set()).update(
            str(item.get("letter") or "").strip().upper()
            for item in payload.get("alternatives", [])
            if str(item.get("letter") or "").strip()
        )
    question_numbers = sorted(alternatives_by_number)
    answer_numbers = sorted(updates)
    missing = sorted(set(question_numbers) - set(answer_numbers))
    extra = sorted(set(answer_numbers) - set(question_numbers))
    invalid_letters = [
        {"question": number, "answer": answer}
        for number, (status, answer) in sorted(updates.items())
        if status == "matched"
        and answer is not None
        and number in alternatives_by_number
        and answer.upper() not in alternatives_by_number[number]
    ]
    conflicts: list[str] = []
    incomplete: list[str] = []
    details: list[str] = []
    if extra:
        conflicts.append("answer_grid: questões extras no gabarito")
        details.append("questões extras: " + ", ".join(map(str, extra)))
    if invalid_letters:
        conflicts.append("answer_grid: resposta não existe nas alternativas")
        details.append(
            "respostas fora das alternativas: "
            + ", ".join(
                f"{item['question']}={item['answer']}" for item in invalid_letters
            )
        )
    if missing:
        incomplete.append("answer_grid")
        details.append("questões sem resposta: " + ", ".join(map(str, missing)))
    reason = (
        "Gabarito incompatível com as questões e alternativas da prova: "
        + "; ".join(details)
        if details
        else "Quantidade de questões e alternativas conferidas."
    )
    return {
        "compatible": not conflicts and not incomplete,
        "questionCount": len(question_numbers),
        "answerCount": len(answer_numbers),
        "questionNumbers": question_numbers,
        "answerNumbers": answer_numbers,
        "missingNumbers": missing,
        "extraNumbers": extra,
        "invalidLetters": invalid_letters,
        "conflicts": conflicts,
        "incompleteFields": incomplete,
        "reason": reason,
    }


def _question_decision_fingerprint(payload: dict[str, Any]) -> str:
    content = {
        "statement": " ".join(str(payload.get("statement", "")).split()).casefold(),
        "alternatives": [
            [item.get("letter"), " ".join(str(item.get("text", "")).split()).casefold()]
            for item in payload.get("alternatives", [])
        ],
    }
    return stable_sha256(
        {
            "content": stable_sha256(content),
            "answer_status": payload.get("answer_status"),
            "correct_answer": payload.get("correct_answer"),
        }
    )


def _update_question_answers(
    connection: sqlite3.Connection,
    *,
    exam_version_id: str,
    link_id: str | None,
    updates: dict[int, tuple[str, str | None]],
    reason: str,
    changed_at: str,
) -> int:
    rows = connection.execute(
        "SELECT q.*, d.id AS source_document_id FROM questions q "
        "JOIN documents d ON d.id = q.document_id WHERE d.document_version_id = ? "
        "ORDER BY d.id, q.question_number",
        (exam_version_id,),
    ).fetchall()
    changed = 0
    for row in rows:
        number = int(row["question_number"])
        old_payload = json.loads(cast(str, row["payload_json"]))
        update = updates.get(number)
        answer_status, correct_answer = update if update is not None else ("missing", None)
        provenance_changed = row["answer_key_link_id"] != link_id
        answer_changed = (
            old_payload.get("answer_status") != answer_status
            or old_payload.get("correct_answer") != correct_answer
        )
        if not answer_changed and not provenance_changed:
            continue
        new_payload = dict(old_payload)
        new_payload["answer_status"] = answer_status
        new_payload["correct_answer"] = correct_answer
        flags = [
            value
            for value in json.loads(cast(str, row["flags_json"]))
            if value not in {"without_answer", "annulled"}
        ]
        official_flag: str | None = None
        if answer_status == "missing":
            official_flag = "without_answer"
        elif answer_status == "annulled":
            official_flag = "annulled"
        if official_flag is not None:
            downstream = {"visual", "missing_fields", "low_confidence", "duplicate"}
            position = next(
                (index for index, value in enumerate(flags) if value in downstream),
                len(flags),
            )
            flags.insert(position, official_flag)
        old_status = cast(str, row["status"])
        next_status = old_status
        reviewer = row["reviewer"]
        review_notes = row["review_notes"]
        exported_at = row["exported_at"]
        if answer_changed and old_status in {"approved", "rejected", "exported"}:
            next_status = "exception" if answer_status != "matched" else "pending"
            reviewer = None
            review_notes = None
            exported_at = None
        elif answer_status != "matched" and old_status not in {"rejected"}:
            next_status = "exception"
        decision_fingerprint = _question_decision_fingerprint(new_payload)
        invalidated = link_id is None and (
            old_payload.get("answer_status") != "missing" or row["answer_key_link_id"] is not None
        )
        connection.execute(
            "UPDATE questions SET payload_json = ?, decision_fingerprint = ?, flags_json = ?, "
            "status = ?, reviewer = ?, review_notes = ?, exported_at = ?, answer_key_link_id = ?, "
            "answer_invalidated_at = ?, answer_invalidation_reason = ?, updated_at = ? "
            "WHERE id = ?",
            (
                canonical_json(new_payload), decision_fingerprint,
                canonical_json(list(dict.fromkeys(flags))), next_status, reviewer,
                review_notes, exported_at, link_id,
                changed_at if invalidated else None, reason if invalidated else None,
                changed_at, row["id"],
            ),
        )
        sync_question_occurrence(
            connection, cast(str, row["id"]), changed_at=changed_at
        )
        sync_canonical_editorial_from_question(
            connection, cast(str, row["id"]), changed_at=changed_at
        )
        action = "answer_association_invalidated" if invalidated else "answer_revalidated"
        before = {
            "status": old_status,
            "answerKeyLinkId": row["answer_key_link_id"],
            "question": old_payload,
        }
        after = {
            "status": next_status,
            "answerKeyLinkId": link_id,
            "question": new_payload,
        }
        connection.execute(
            "INSERT INTO audit_log (question_id, action, actor, created_at, before_json, "
            "after_json, notes) VALUES (?, ?, 'system', ?, ?, ?, ?)",
            (
                row["id"], action, changed_at, canonical_json(before),
                canonical_json(after), reason,
            ),
        )
        changed += 1
    return changed


def invalidate_answer_association(
    connection: sqlite3.Connection,
    *,
    exam_version_id: str,
    reason: str,
    changed_at: str,
) -> int:
    return _update_question_answers(
        connection,
        exam_version_id=exam_version_id,
        link_id=None,
        updates={},
        reason=reason,
        changed_at=changed_at,
    )


@dataclass
class AnswerKeyAuditReport:
    run_id: str
    mode: Literal["preview", "apply"]
    confirmed: int = 0
    uncertain: int = 0
    incorrect: int = 0
    missing: int = 0
    corrected: int = 0
    questions_affected: int = 0
    sent_to_review: int = 0
    cases: list[dict[str, Any]] = field(default_factory=list)

    @property
    def examined(self) -> int:
        return self.confirmed + self.uncertain + self.incorrect + self.missing

    def record(self, payload: dict[str, Any]) -> None:
        status = cast(AnswerKeyAuditStatus, payload["status"])
        setattr(self, status, int(getattr(self, status)) + 1)
        self.corrected += int(bool(payload.get("corrected")))
        self.questions_affected += int(payload.get("questionsAffected", 0))
        self.sent_to_review += int(bool(payload.get("sentToReview")))
        self.cases.append(payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "algorithmVersion": ANSWER_KEY_AUDIT_ALGORITHM_VERSION,
            "mode": self.mode,
            "examined": self.examined,
            "confirmed": self.confirmed,
            "uncertain": self.uncertain,
            "incorrect": self.incorrect,
            "missing": self.missing,
            "corrected": self.corrected,
            "questionsAffected": self.questions_affected,
            "sentToReview": self.sent_to_review,
            "cases": self.cases,
        }


def _active_answer_key_link(
    connection: sqlite3.Connection, exam_version_id: str
) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT id,answer_key_version_id,decision_json,algorithm_version "
        "FROM document_links WHERE exam_version_id=? AND status='active'",
        (exam_version_id,),
    ).fetchone()
    return cast(sqlite3.Row | None, row)


def _answer_key_audit_status(
    current: sqlite3.Row | None, decision: DocumentAssociationDecision
) -> AnswerKeyAuditStatus:
    selected = decision.selected_version_id
    if current is None:
        if selected is not None or decision.outcome in {
            "missing",
            "conflict",
            "insufficient_evidence",
        }:
            return "missing"
        return "uncertain"
    current_key = cast(str, current["answer_key_version_id"])
    if selected == current_key:
        return "confirmed"
    if selected is not None:
        return "incorrect"
    current_assessment = next(
        (item for item in decision.assessments if item.version_id == current_key),
        None,
    )
    if current_assessment is not None and current_assessment.conflicts:
        return "incorrect"
    return "uncertain"


def _version_public_label(
    connection: sqlite3.Connection, version_id: str | None
) -> dict[str, Any] | None:
    if version_id is None:
        return None
    row = connection.execute(
        "SELECT v.id,v.answer_key_state,d.filename,d.id AS document_id "
        "FROM document_versions v LEFT JOIN documents d ON d.document_version_id=v.id "
        "WHERE v.id=? ORDER BY d.created_at,d.id LIMIT 1",
        (version_id,),
    ).fetchone()
    if row is None:
        return {"versionId": version_id, "filename": "Documento não localizado"}
    return {
        "versionId": row["id"],
        "documentId": row["document_id"],
        "filename": row["filename"],
        "answerKeyState": row["answer_key_state"],
    }


def _queue_answer_key_review(
    connection: sqlite3.Connection,
    *,
    exam_version_id: str,
    decision: DocumentAssociationDecision,
    reason: str,
    changed_at: str,
) -> None:
    connection.execute(
        "INSERT INTO association_review_queue "
        "(exam_version_id,run_id,status,reason,candidates_json,created_at,updated_at) "
        "VALUES (?,NULL,'pending',?,?,?,?) "
        "ON CONFLICT(exam_version_id) DO UPDATE SET run_id=NULL,status='pending',"
        "reason=excluded.reason,candidates_json=excluded.candidates_json,"
        "updated_at=excluded.updated_at",
        (
            exam_version_id,
            reason,
            canonical_json(
                [item.model_dump(mode="json") for item in decision.assessments]
            ),
            changed_at,
            changed_at,
        ),
    )


def audit_answer_key_associations(
    connection: sqlite3.Connection,
    *,
    apply: bool = False,
    run_id: str | None = None,
) -> AnswerKeyAuditReport:
    effective_run_id = run_id or str(uuid.uuid4())
    mode: Literal["preview", "apply"] = "apply" if apply else "preview"
    report = AnswerKeyAuditReport(run_id=effective_run_id, mode=mode)
    started_at = _now()
    if apply:
        connection.execute(
            "INSERT INTO answer_key_audit_runs "
            "(id,algorithm_version,mode,status,totals_json,started_at) "
            "VALUES (?,?,?,'running','{}',?)",
            (
                effective_run_id,
                ANSWER_KEY_AUDIT_ALGORITHM_VERSION,
                mode,
                started_at,
            ),
        )
        connection.commit()
    exams = connection.execute(
        "SELECT v.id,d.id AS document_id,d.filename FROM document_versions v "
        "LEFT JOIN documents d ON d.document_version_id=v.id "
        "WHERE v.document_role='exam' "
        "GROUP BY v.id ORDER BY COALESCE(d.filename,''),v.id"
    ).fetchall()
    try:
        for exam in exams:
            exam_version_id = cast(str, exam["id"])
            current = _active_answer_key_link(connection, exam_version_id)
            context, decision = decide_runtime_association(connection, exam_version_id)
            status = _answer_key_audit_status(current, decision)
            current_key = cast(
                str | None,
                current["answer_key_version_id"] if current is not None else None,
            )
            question_count = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT q.question_number) FROM questions q "
                    "JOIN documents d ON d.id=q.document_id "
                    "WHERE d.document_version_id=?",
                    (exam_version_id,),
                ).fetchone()[0]
            )
            action = "maintained" if status == "confirmed" else "none"
            questions_affected = 0
            corrected = False
            sent_to_review = False
            changed_at = _now()
            if apply:
                connection.execute("BEGIN IMMEDIATE")
                if decision.selected_version_id is not None and status != "confirmed":
                    new_link = record_corrected_document_link(
                        connection, exam_version_id, decision, changed_at
                    )
                    if new_link is None:
                        raise RuntimeError("a auditoria não produziu vínculo ativo")
                    questions_affected = _update_question_answers(
                        connection,
                        exam_version_id=exam_version_id,
                        link_id=new_link,
                        updates=context.answer_updates[decision.selected_version_id],
                        reason="Respostas recalculadas pela auditoria prova-gabarito.",
                        changed_at=changed_at,
                    )
                    connection.execute(
                        "DELETE FROM association_review_queue WHERE exam_version_id=?",
                        (exam_version_id,),
                    )
                    action = "linked" if current is None else "replaced"
                    corrected = True
                elif current is not None and status in {"incorrect", "uncertain"}:
                    record_corrected_document_link(
                        connection, exam_version_id, decision, changed_at
                    )
                    questions_affected = invalidate_answer_association(
                        connection,
                        exam_version_id=exam_version_id,
                        reason=(
                            "Vínculo removido porque a auditoria não confirmou um "
                            "gabarito único e compatível."
                        ),
                        changed_at=changed_at,
                    )
                    _queue_answer_key_review(
                        connection,
                        exam_version_id=exam_version_id,
                        decision=decision,
                        reason=decision.reason,
                        changed_at=changed_at,
                    )
                    action = "invalidated"
                    sent_to_review = True
                elif status == "uncertain":
                    _queue_answer_key_review(
                        connection,
                        exam_version_id=exam_version_id,
                        decision=decision,
                        reason=decision.reason,
                        changed_at=changed_at,
                    )
                    action = "review"
                    sent_to_review = True

            evidence = {
                "examInterval": (
                    context.exam_interval.model_dump(mode="json")
                    if context.exam_interval is not None
                    else None
                ),
                "currentAnswerKey": _version_public_label(connection, current_key),
                "recommendedAnswerKey": _version_public_label(
                    connection, decision.selected_version_id
                ),
                "candidates": [
                    {
                        **item.model_dump(mode="json"),
                        "document": _version_public_label(connection, item.version_id),
                    }
                    for item in decision.assessments
                ],
            }
            case = {
                "examVersionId": exam_version_id,
                "documentId": exam["document_id"],
                "filename": exam["filename"],
                "status": status,
                "action": action,
                "reason": decision.reason,
                "questionCount": question_count,
                "questionsAffected": questions_affected,
                "corrected": corrected,
                "sentToReview": sent_to_review,
                "currentAnswerKey": evidence["currentAnswerKey"],
                "recommendedAnswerKey": evidence["recommendedAnswerKey"],
                "candidates": evidence["candidates"],
            }
            if apply:
                case_id = stable_sha256(
                    {"runId": effective_run_id, "examVersionId": exam_version_id}
                )
                connection.execute(
                    "INSERT INTO answer_key_audit_cases "
                    "(id,run_id,exam_version_id,current_link_id,"
                    "current_answer_key_version_id,recommended_answer_key_version_id,"
                    "audit_status,action,question_count,questions_affected,evidence_json,"
                    "decision_json,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        case_id,
                        effective_run_id,
                        exam_version_id,
                        current["id"] if current is not None else None,
                        current_key,
                        decision.selected_version_id,
                        status,
                        action,
                        question_count,
                        questions_affected,
                        canonical_json(evidence),
                        canonical_json(decision.model_dump(mode="json")),
                        decision.reason,
                        changed_at,
                    ),
                )
                connection.commit()
            report.record(case)
        if apply:
            connection.execute(
                "UPDATE answer_key_audit_runs SET status='completed',totals_json=?,"
                "finished_at=? WHERE id=?",
                (canonical_json(report.as_dict()), _now(), effective_run_id),
            )
            connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        if apply:
            connection.execute(
                "UPDATE answer_key_audit_runs SET status='failed',finished_at=? WHERE id=?",
                (_now(), effective_run_id),
            )
            connection.commit()
        raise
    return report


def _manual_association_event(
    connection: sqlite3.Connection,
    *,
    action: str,
    actor: str,
    exam_version_id: str,
    answer_key_version_id: str | None,
    questions_affected: int,
    reason: str,
    changed_at: str,
) -> None:
    payload = {
        "examVersionId": exam_version_id,
        "answerKeyVersionId": answer_key_version_id,
        "questionsAffected": questions_affected,
        "reason": reason,
    }
    connection.execute(
        "INSERT INTO document_identity_events "
        "(event_key,document_version_id,action,actor,algorithm_version,payload_json,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            stable_sha256({"action": action, "payload": payload, "at": changed_at}),
            exam_version_id,
            action,
            actor,
            ANSWER_KEY_AUDIT_ALGORITHM_VERSION,
            canonical_json(payload),
            changed_at,
        ),
    )


def replace_answer_key_association(
    connection: sqlite3.Connection,
    *,
    exam_version_id: str,
    answer_key_version_id: str,
    actor: str,
) -> dict[str, Any]:
    context, decision = decide_runtime_association(connection, exam_version_id)
    candidate = next(
        (item for item in decision.assessments if item.version_id == answer_key_version_id),
        None,
    )
    if candidate is None or answer_key_version_id not in context.answer_updates:
        raise ValueError("gabarito não pertence aos candidatos desta prova")
    if candidate.conflicts:
        raise ValueError("gabarito possui conflito conhecido com a prova")
    validation = _answer_update_validation(
        connection,
        exam_version_id=exam_version_id,
        updates=context.answer_updates[answer_key_version_id],
    )
    if not validation["compatible"]:
        raise ValueError(cast(str, validation["reason"]))
    changed_at = _now()
    reason = "Gabarito escolhido pelo operador após revisão das evidências."
    manual_decision = decision.model_copy(
        update={
            "outcome": "selected",
            "selected_version_id": answer_key_version_id,
            "reason": reason,
            "algorithm_version": ASSOCIATION_ALGORITHM_VERSION,
        }
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        link_id = record_corrected_document_link(
            connection, exam_version_id, manual_decision, changed_at
        )
        if link_id is None:
            raise RuntimeError("a escolha manual não produziu vínculo ativo")
        affected = _update_question_answers(
            connection,
            exam_version_id=exam_version_id,
            link_id=link_id,
            updates=context.answer_updates[answer_key_version_id],
            reason=reason,
            changed_at=changed_at,
        )
        connection.execute(
            "DELETE FROM association_review_queue WHERE exam_version_id=?",
            (exam_version_id,),
        )
        _manual_association_event(
            connection,
            action="association_manual_replaced",
            actor=actor,
            exam_version_id=exam_version_id,
            answer_key_version_id=answer_key_version_id,
            questions_affected=affected,
            reason=reason,
            changed_at=changed_at,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "examVersionId": exam_version_id,
        "answerKeyVersionId": answer_key_version_id,
        "questionsAffected": affected,
    }


def remove_answer_key_association(
    connection: sqlite3.Connection,
    *,
    exam_version_id: str,
    actor: str,
) -> dict[str, Any]:
    _, decision = decide_runtime_association(connection, exam_version_id)
    changed_at = _now()
    reason = "Vínculo removido pelo operador para revisão do lote."
    removal_decision = decision.model_copy(
        update={"outcome": "missing", "selected_version_id": None, "reason": reason}
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        record_corrected_document_link(
            connection, exam_version_id, removal_decision, changed_at
        )
        affected = invalidate_answer_association(
            connection,
            exam_version_id=exam_version_id,
            reason=reason,
            changed_at=changed_at,
        )
        _queue_answer_key_review(
            connection,
            exam_version_id=exam_version_id,
            decision=decision,
            reason=reason,
            changed_at=changed_at,
        )
        _manual_association_event(
            connection,
            action="association_manual_removed",
            actor=actor,
            exam_version_id=exam_version_id,
            answer_key_version_id=None,
            questions_affected=affected,
            reason=reason,
            changed_at=changed_at,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"examVersionId": exam_version_id, "questionsAffected": affected}


@dataclass
class RevalidationReport:
    run_id: str
    mode: Literal["dry-run", "apply"]
    status: str = "completed"
    examined: int = 0
    maintained: int = 0
    changed: int = 0
    invalidated: int = 0
    ambiguous: int = 0
    incomplete: int = 0
    answers_invalidated: int = 0
    sent_to_review: int = 0
    cases: list[dict[str, Any]] = field(default_factory=list)
    by_contest: Counter[str] = field(default_factory=Counter)
    by_document: Counter[str] = field(default_factory=Counter)

    def record(self, payload: dict[str, Any]) -> None:
        result = cast(RevalidationResult, payload["result"])
        self.examined += 1
        setattr(self, result, int(getattr(self, result)) + 1)
        self.answers_invalidated += int(payload.get("answersInvalidated", 0))
        if bool(payload.get("sentToReview", result in {"ambiguous", "incomplete"})):
            self.sent_to_review += 1
        contest = str(payload.get("contest") or "[concurso desconhecido]")
        document = str(payload.get("documentId") or payload["examVersionId"])
        self.by_contest[contest] += 1
        self.by_document[document] += 1
        self.cases.append(payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "algorithmVersion": ASSOCIATION_ALGORITHM_VERSION,
            "mode": self.mode,
            "status": self.status,
            "associationsExamined": self.examined,
            "maintained": self.maintained,
            "changed": self.changed,
            "invalidated": self.invalidated,
            "ambiguous": self.ambiguous,
            "incomplete": self.incomplete,
            "answersInvalidated": self.answers_invalidated,
            "sentToReview": self.sent_to_review,
            "byContest": dict(sorted(self.by_contest.items())),
            "byDocument": dict(sorted(self.by_document.items())),
            "cases": self.cases,
        }


def _result_for(
    old_answer_key_version_id: str | None, decision: DocumentAssociationDecision
) -> RevalidationResult:
    if decision.outcome == "selected":
        return (
            "maintained"
            if decision.selected_version_id == old_answer_key_version_id
            else "changed"
        )
    if decision.outcome == "ambiguous":
        return "ambiguous"
    if decision.outcome in {"incomplete", "insufficient_evidence"}:
        return "incomplete"
    return "invalidated"


def _pending_exam_versions(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT v.id, EXISTS (SELECT 1 FROM document_links legacy "
        "WHERE legacy.exam_version_id = v.id AND legacy.algorithm_version != ?) "
        "AS has_legacy_link, EXISTS (SELECT 1 FROM document_links linked "
        "WHERE linked.exam_version_id = v.id) AS has_any_link "
        "FROM document_versions v "
        "WHERE v.document_role = 'exam' AND NOT EXISTS ("
        "SELECT 1 FROM document_links current "
        "WHERE current.exam_version_id = v.id AND current.status = 'active' "
        "AND current.algorithm_version = ?) AND NOT EXISTS ("
        "SELECT 1 FROM association_revalidation_audit a "
        "JOIN association_revalidation_runs r ON r.id = a.run_id "
        "WHERE a.exam_version_id = v.id AND r.algorithm_version = ?) "
        "AND NOT EXISTS (SELECT 1 FROM association_review_queue q "
        "WHERE q.exam_version_id = v.id AND q.status != 'obsolete') "
        "ORDER BY v.id",
        (
            ASSOCIATION_ALGORITHM_VERSION,
            ASSOCIATION_ALGORITHM_VERSION,
            ASSOCIATION_ALGORITHM_VERSION,
        ),
    ).fetchall()
    return [
        cast(str, row["id"])
        for row in rows
        if bool(row["has_legacy_link"])
        or (not bool(row["has_any_link"]) and _version_is_fgv(connection, row["id"]))
    ]


def _version_is_fgv(connection: sqlite3.Connection, version_id: str) -> bool:
    version = connection.execute(
        "SELECT profile_json FROM document_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if version is not None:
        profile = DocumentSemanticProfile.model_validate_json(
            cast(str, version["profile_json"])
        )
        if any(
            is_fgv_source(board=str(value), provider=None)
            for value in profile.identity.board.normalized_values
        ):
            return True
    document = connection.execute(
        "SELECT normalized_json FROM documents WHERE document_version_id = ? "
        "AND normalized_json IS NOT NULL ORDER BY created_at, id LIMIT 1",
        (version_id,),
    ).fetchone()
    if document is None:
        return False
    normalized = NormalizedDocument.model_validate_json(
        cast(str, document["normalized_json"])
    )
    return is_fgv_source(
        board=str(normalized.metadata.get("board") or normalized.metadata.get("banca") or ""),
        provider=str(normalized.metadata.get("provider") or normalized.source_id or ""),
    )


def _review_reason(decision: DocumentAssociationDecision) -> str:
    incomplete = sorted(
        {
            field
            for assessment in decision.assessments
            for field in assessment.incomplete_fields
        }
    )
    conflicts = sorted(
        {
            conflict.split(":", 1)[0]
            for assessment in decision.assessments
            for conflict in assessment.conflicts
        }
    )
    details: list[str] = []
    if incomplete:
        details.append("campos incompletos: " + ", ".join(incomplete))
    if conflicts:
        details.append("campos conflitantes: " + ", ".join(conflicts))
    return decision.reason + ("; " + "; ".join(details) if details else "")


def _case_identity(
    connection: sqlite3.Connection, exam_version_id: str
) -> tuple[str | None, str | None]:
    row = connection.execute(
        "SELECT d.id AS document_id, v.profile_json FROM document_versions v "
        "LEFT JOIN documents d ON d.document_version_id = v.id "
        "WHERE v.id = ? ORDER BY d.created_at, d.id LIMIT 1",
        (exam_version_id,),
    ).fetchone()
    if row is None:
        return None, None
    profile = DocumentSemanticProfile.model_validate_json(cast(str, row["profile_json"]))
    contest = _single_value(profile.identity.concurso)
    return cast(str | None, row["document_id"]), contest


def revalidate_answer_key_associations(
    connection: sqlite3.Connection,
    *,
    apply: bool = False,
    run_id: str | None = None,
    limit: int | None = None,
) -> RevalidationReport:
    mode: Literal["dry-run", "apply"] = "apply" if apply else "dry-run"
    effective_run_id = run_id or str(uuid.uuid4())
    report = RevalidationReport(run_id=effective_run_id, mode=mode)
    started_at = _now()
    if apply:
        existing = connection.execute(
            "SELECT mode, status, totals_json FROM association_revalidation_runs WHERE id = ?",
            (effective_run_id,),
        ).fetchone()
        if existing is not None and existing["mode"] != "apply":
            raise ValueError("o identificador pertence a uma simulação")
        connection.execute(
            "INSERT OR IGNORE INTO association_revalidation_runs "
            "(id, algorithm_version, mode, status, totals_json, started_at) "
            "VALUES (?, ?, 'apply', 'running', '{}', ?)",
            (effective_run_id, ASSOCIATION_ALGORITHM_VERSION, started_at),
        )
        connection.execute(
            "UPDATE association_revalidation_runs SET status = 'running', finished_at = NULL "
            "WHERE id = ? AND status != 'completed'",
            (effective_run_id,),
        )
        connection.commit()
    pending = _pending_exam_versions(connection)
    if limit is not None:
        pending = pending[:limit]
    for exam_version_id in pending:
        if apply:
            already = connection.execute(
                "SELECT 1 FROM association_revalidation_audit WHERE run_id = ? "
                "AND exam_version_id = ?",
                (effective_run_id, exam_version_id),
            ).fetchone()
            if already is not None:
                continue
        legacy = connection.execute(
            "SELECT id, answer_key_version_id, status, algorithm_version, decision_json "
            "FROM document_links WHERE exam_version_id = ? AND algorithm_version != ? "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            (exam_version_id, ASSOCIATION_ALGORITHM_VERSION),
        ).fetchone()
        legacy_history = connection.execute(
            "SELECT id, answer_key_version_id, status, algorithm_version, decision_json "
            "FROM document_links WHERE exam_version_id = ? AND algorithm_version != ? "
            "ORDER BY created_at, id",
            (exam_version_id, ASSOCIATION_ALGORITHM_VERSION),
        ).fetchall()
        old_link_id = cast(str | None, legacy["id"] if legacy is not None else None)
        old_key_id = cast(
            str | None, legacy["answer_key_version_id"] if legacy is not None else None
        )
        context, decision = decide_runtime_association(connection, exam_version_id)
        result = _result_for(old_key_id, decision)
        document_id, contest = _case_identity(connection, exam_version_id)
        answers_invalidated = 0
        new_link_id: str | None = None
        changed_at = _now()
        if not apply and decision.selected_version_id is None:
            answers_invalidated = int(
                connection.execute(
                    "SELECT COUNT(*) FROM questions q JOIN documents d ON d.id = q.document_id "
                    "WHERE d.document_version_id = ? AND "
                    "json_extract(q.payload_json, '$.answer_status') != 'missing'",
                    (exam_version_id,),
                ).fetchone()[0]
            )
        if apply:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if decision.selected_version_id is not None:
                    new_link_id = record_document_link(
                        connection,
                        exam_version_id,
                        decision.selected_version_id,
                        decision,
                        changed_at,
                    )
                    if new_link_id is None:
                        raise RuntimeError("a decisão selecionada não gerou vínculo ativo")
                    _update_question_answers(
                        connection,
                        exam_version_id=exam_version_id,
                        link_id=new_link_id,
                        updates=context.answer_updates[decision.selected_version_id],
                        reason="Respostas recalculadas por semantic-association-v2.",
                        changed_at=changed_at,
                    )
                    connection.execute(
                        "DELETE FROM association_review_queue WHERE exam_version_id = ?",
                        (exam_version_id,),
                    )
                else:
                    record_corrected_document_link(
                        connection, exam_version_id, decision, changed_at
                    )
                    answers_invalidated = _update_question_answers(
                        connection,
                        exam_version_id=exam_version_id,
                        link_id=None,
                        updates={},
                        reason=(
                            "Resposta invalidada porque semantic-association-v2 não confirmou "
                            "um gabarito único e compatível."
                        ),
                        changed_at=changed_at,
                    )
                    review_reason = _review_reason(decision)
                    connection.execute(
                        "INSERT INTO association_review_queue "
                        "(exam_version_id, run_id, status, reason, candidates_json, "
                        "created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?, ?) "
                        "ON CONFLICT(exam_version_id) DO UPDATE SET run_id=excluded.run_id, "
                        "status='pending', reason=excluded.reason, "
                        "candidates_json=excluded.candidates_json, "
                        "updated_at=excluded.updated_at "
                        "WHERE association_review_queue.status IN ('pending', 'obsolete')",
                        (
                            exam_version_id, effective_run_id, review_reason,
                            canonical_json([
                                item.model_dump(mode="json") for item in decision.assessments
                            ]), changed_at, changed_at,
                        ),
                    )
                old_status = cast(str, legacy["status"] if legacy is not None else "missing")
                new_status = "active" if new_link_id is not None else "review"
                comparison = {
                    "examInterval": (
                        context.exam_interval.model_dump(mode="json")
                        if context.exam_interval is not None else None
                    ),
                    "oldAssociation": (
                        {
                            "linkId": old_link_id,
                            "answerKeyVersionId": old_key_id,
                            "algorithmVersion": legacy["algorithm_version"],
                            "decision": json.loads(cast(str, legacy["decision_json"])),
                        }
                        if legacy is not None else None
                    ),
                    "oldAssociationHistory": [
                        {
                            "linkId": item["id"],
                            "answerKeyVersionId": item["answer_key_version_id"],
                            "status": item["status"],
                            "algorithmVersion": item["algorithm_version"],
                            "decision": json.loads(cast(str, item["decision_json"])),
                        }
                        for item in legacy_history
                    ],
                    "candidates": [
                        item.model_dump(mode="json") for item in decision.assessments
                    ],
                    "answersInvalidated": answers_invalidated,
                }
                audit_id = stable_sha256(
                    {"run_id": effective_run_id, "exam_version_id": exam_version_id}
                )
                connection.execute(
                    "INSERT INTO association_revalidation_audit "
                    "(id, run_id, exam_version_id, old_link_id, old_answer_key_version_id, "
                    "new_link_id, new_answer_key_version_id, result_status, old_status, "
                    "new_status, comparison_json, decision_json, reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        audit_id, effective_run_id, exam_version_id, old_link_id, old_key_id,
                        new_link_id, decision.selected_version_id, result, old_status, new_status,
                        canonical_json(comparison),
                        canonical_json(decision.model_dump(mode="json")), decision.reason,
                        changed_at,
                    ),
                )
                connection.execute(
                    "UPDATE association_revalidation_runs SET cursor_exam_version_id = ? "
                    "WHERE id = ?",
                    (exam_version_id, effective_run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                connection.execute(
                    "UPDATE association_revalidation_runs SET status = 'interrupted' WHERE id = ?",
                    (effective_run_id,),
                )
                connection.commit()
                raise
        case = {
            "examVersionId": exam_version_id,
            "documentId": document_id,
            "contest": contest,
            "oldLinkId": old_link_id,
            "oldAnswerKeyVersionId": old_key_id,
            "newLinkId": new_link_id,
            "newAnswerKeyVersionId": decision.selected_version_id,
            "result": result,
            "decisionOutcome": decision.outcome,
            "reason": decision.reason,
            "answersInvalidated": answers_invalidated,
            "sentToReview": decision.selected_version_id is None,
        }
        report.record(case)
    remaining = _pending_exam_versions(connection) if apply else []
    if apply:
        aggregate = RevalidationReport(run_id=effective_run_id, mode="apply")
        rows = connection.execute(
            "SELECT * FROM association_revalidation_audit WHERE run_id = ? "
            "ORDER BY created_at, exam_version_id",
            (effective_run_id,),
        ).fetchall()
        for row in rows:
            document_id, contest = _case_identity(
                connection, cast(str, row["exam_version_id"])
            )
            comparison = json.loads(cast(str, row["comparison_json"]))
            aggregate.record(
                {
                    "examVersionId": row["exam_version_id"],
                    "documentId": document_id,
                    "contest": contest,
                    "oldLinkId": row["old_link_id"],
                    "oldAnswerKeyVersionId": row["old_answer_key_version_id"],
                    "newLinkId": row["new_link_id"],
                    "newAnswerKeyVersionId": row["new_answer_key_version_id"],
                    "result": row["result_status"],
                    "decisionOutcome": json.loads(cast(str, row["decision_json"]))[
                        "outcome"
                    ],
                    "reason": row["reason"],
                    "answersInvalidated": int(
                        comparison.get("answersInvalidated", 0)
                    ),
                    "sentToReview": row["new_link_id"] is None,
                }
            )
        aggregate.status = "paused" if remaining else "completed"
        report = aggregate
        finished_at = _now() if not remaining else None
        connection.execute(
            "UPDATE association_revalidation_runs SET status = ?, totals_json = ?, "
            "finished_at = ? WHERE id = ?",
            (report.status, canonical_json(report.as_dict()), finished_at, effective_run_id),
        )
        connection.commit()
    return report
