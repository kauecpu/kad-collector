from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from .answer_key import parse_answer_key
from .semantic_identity import (
    AssociationCandidate,
    DocumentAssociationDecision,
    DocumentSemanticProfile,
    QuestionInterval,
    SemanticField,
    canonical_json,
    stable_sha256,
)
from .semantic_registry import (
    active_answer_key_candidates,
    record_corrected_document_link,
    record_document_link,
)
from .semantic_resolution import ASSOCIATION_ALGORITHM_VERSION, select_answer_key

RevalidationResult = Literal["maintained", "changed", "invalidated", "ambiguous", "incomplete"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _single_value(field: SemanticField) -> str | None:
    if field.status != "known" or len(field.normalized_values) != 1:
        return None
    return str(field.normalized_values[0])


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


def build_runtime_context(
    connection: sqlite3.Connection, exam_version_id: str
) -> RuntimeAssociationContext:
    row = connection.execute(
        "SELECT profile_json FROM document_versions WHERE id = ? AND document_role = 'exam'",
        (exam_version_id,),
    ).fetchone()
    if row is None:
        raise ValueError("versão de prova não encontrada")
    exam_profile = DocumentSemanticProfile.model_validate_json(cast(str, row["profile_json"]))
    question_rows = connection.execute(
        "SELECT q.question_number FROM questions q JOIN documents d ON d.id = q.document_id "
        "WHERE d.document_version_id = ? ORDER BY q.question_number",
        (exam_version_id,),
    ).fetchall()
    exam_interval = _closed_interval([int(item["question_number"]) for item in question_rows])
    role = _single_value(exam_profile.identity.roles)
    turn = _single_value(exam_profile.identity.turns)
    variant = _single_value(exam_profile.identity.variants)
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
        candidate_profile = DocumentSemanticProfile.model_validate_json(
            canonical_json(item["profile"])
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
    return context, decision


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
        if result in {"ambiguous", "incomplete"}:
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
        "SELECT l.exam_version_id FROM document_links l "
        "WHERE l.algorithm_version != ? AND l.id = ("
        "SELECT latest.id FROM document_links latest "
        "WHERE latest.exam_version_id = l.exam_version_id "
        "AND latest.algorithm_version != ? "
        "ORDER BY latest.updated_at DESC, latest.id DESC LIMIT 1) AND NOT EXISTS ("
        "SELECT 1 FROM association_revalidation_audit a "
        "WHERE a.old_link_id = l.id) "
        "ORDER BY l.exam_version_id",
        (ASSOCIATION_ALGORITHM_VERSION, ASSOCIATION_ALGORITHM_VERSION),
    ).fetchall()
    return [cast(str, row["exam_version_id"]) for row in rows]


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
                    if result in {"ambiguous", "incomplete"}:
                        connection.execute(
                            "INSERT INTO association_review_queue "
                            "(exam_version_id, run_id, status, reason, candidates_json, "
                            "created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?, ?) "
                            "ON CONFLICT(exam_version_id) DO UPDATE SET run_id=excluded.run_id, "
                            "status='pending', reason=excluded.reason, "
                            "candidates_json=excluded.candidates_json, "
                            "updated_at=excluded.updated_at",
                            (
                                exam_version_id, effective_run_id, decision.reason,
                                canonical_json([
                                    item.model_dump(mode="json") for item in decision.assessments
                                ]), changed_at, changed_at,
                            ),
                        )
                old_status = cast(str, legacy["status"] if legacy is not None else "missing")
                new_status = "active" if new_link_id is not None else (
                    "review" if result in {"ambiguous", "incomplete"} else "invalidated"
                )
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
