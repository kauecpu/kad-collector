from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from .canonical_classification import canonical_classification_coverage
from .canonical_identity import initialize_canonical_identity_schema
from .question_equivalence import (
    initialize_question_equivalence_schema,
    run_question_equivalence_migration,
)
from .semantic_identity import canonical_json

DESKTOP_PREPARATION_ALGORITHM_VERSION = "desktop-preparation-v3"

_REQUIRED_CONTEXT_FIELDS = (
    "board",
    "concurso",
    "year",
    "role",
    "stage",
    "turn",
    "variant",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stable_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kad:{kind}:{key}"))


def _token(value: object) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


@dataclass(frozen=True)
class _ExamContext:
    exam_document_id: str
    exam_version_id: str
    key_document_id: str
    key_version_id: str
    link_id: str
    filename: str
    key_filename: str
    source_url: str
    key_source_url: str
    exam_sha256: str
    key_sha256: str
    board: str
    contest: str
    year: int
    role: str
    stage: str
    turn: str
    variant: str
    first_question: int
    last_question: int
    answer_key_state: str
    evidence: dict[str, Any]


def _comparison_values(decision: Mapping[str, Any], version_id: str) -> dict[str, list[Any]]:
    assessments = decision.get("assessments")
    if not isinstance(assessments, list):
        return {}
    selected = next(
        (
            item
            for item in assessments
            if isinstance(item, Mapping) and item.get("version_id") == version_id
        ),
        None,
    )
    if not isinstance(selected, Mapping):
        return {}
    comparisons = selected.get("comparisons")
    if not isinstance(comparisons, list):
        return {}
    values: dict[str, list[Any]] = {}
    for comparison in comparisons:
        if not isinstance(comparison, Mapping) or comparison.get("status") != "matched":
            continue
        field = comparison.get("field")
        exam_values = comparison.get("exam_values")
        if isinstance(field, str) and isinstance(exam_values, list):
            values[field] = exam_values
    return values


def _single(values: Mapping[str, list[Any]], field: str) -> Any | None:
    items = values.get(field, [])
    return items[0] if len(items) == 1 else None


def _year_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        year = value
    elif isinstance(value, str) and value.strip().isdigit():
        year = int(value.strip())
    else:
        return None
    return year if 1900 <= year <= 2100 else None


def _exam_context(row: sqlite3.Row) -> tuple[_ExamContext | None, list[str]]:
    decision = json.loads(cast(str, row["decision_json"]))
    values = _comparison_values(decision, cast(str, row["key_version_id"]))
    year = _year_or_none(_single(values, "year"))
    missing = [
        field
        for field in _REQUIRED_CONTEXT_FIELDS
        if field != "year" and _single(values, field) is None
    ]
    if year is None:
        missing.append("year")
    interval = values.get("interval", [])
    if len(interval) != 2 or not all(isinstance(value, int) for value in interval):
        missing.append("interval")
    if missing:
        return None, missing
    exam_metadata = json.loads(cast(str, row["exam_metadata_json"]))
    key_metadata = json.loads(cast(str, row["key_metadata_json"]))
    return (
        _ExamContext(
            exam_document_id=cast(str, row["exam_document_id"]),
            exam_version_id=cast(str, row["exam_version_id"]),
            key_document_id=cast(str, row["key_document_id"]),
            key_version_id=cast(str, row["key_version_id"]),
            link_id=cast(str, row["link_id"]),
            filename=cast(str, row["exam_filename"]),
            key_filename=cast(str, row["key_filename"]),
            source_url=str(
                exam_metadata.get("source_url")
                or exam_metadata.get("canonical_url")
                or f"kad-local:{row['exam_document_id']}"
            ),
            key_source_url=str(
                key_metadata.get("source_url")
                or key_metadata.get("canonical_url")
                or f"kad-local:{row['key_document_id']}"
            ),
            exam_sha256=cast(str, row["exam_sha256"]),
            key_sha256=cast(str, row["key_sha256"]),
            board=str(_single(values, "board")),
            contest=str(_single(values, "concurso")),
            year=cast(int, year),
            role=str(_single(values, "role")),
            stage=str(_single(values, "stage")),
            turn=str(_single(values, "turn")),
            variant=str(_single(values, "variant")),
            first_question=int(interval[0]),
            last_question=int(interval[1]),
            answer_key_state=cast(str, row["answer_key_state"]),
            evidence={
                "source": "active_answer_key_link",
                "linkId": row["link_id"],
                "algorithmVersion": row["link_algorithm_version"],
                "decision": decision,
            },
        ),
        [],
    )


def _linked_exam_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT exam.id AS exam_document_id, exam.filename AS exam_filename,
                   exam.document_version_id AS exam_version_id,
                   exam.metadata_json AS exam_metadata_json, exam.sha256 AS exam_sha256,
                   key_doc.id AS key_document_id, key_doc.filename AS key_filename,
                   key_doc.document_version_id AS key_version_id,
                   key_doc.metadata_json AS key_metadata_json, key_doc.sha256 AS key_sha256,
                   key_version.answer_key_state, link.id AS link_id,
                   link.decision_json, link.algorithm_version AS link_algorithm_version,
                   (SELECT COUNT(*) FROM questions q WHERE q.document_id = exam.id)
                       AS question_count
            FROM documents exam
            JOIN document_versions exam_version
              ON exam_version.id = exam.document_version_id
             AND exam_version.document_role = 'exam'
            JOIN document_links link
              ON link.exam_version_id = exam_version.id
             AND link.status = 'active'
             AND link.algorithm_version = 'semantic-association-v3'
            JOIN document_versions key_version
              ON key_version.id = link.answer_key_version_id
             AND key_version.document_role = 'answer_key'
            JOIN documents key_doc ON key_doc.document_version_id = key_version.id
            ORDER BY exam.id, key_doc.id
            """
        ).fetchall()
    )


def _upsert_catalog(connection: sqlite3.Connection, context: _ExamContext, changed_at: str) -> None:
    contest_key = f"desktop:{_token(context.board)}:{_token(context.contest)}"
    contest_id = _stable_id("canonical-contest", contest_key)
    application_key = f"{contest_key}:application:{context.year}"
    application_id = _stable_id("exam-application", application_key)
    role_key = f"{contest_key}:role:{_token(context.role)}"
    role_id = _stable_id("role", role_key)
    stage_key = f"{application_key}:stage:{_token(context.stage)}"
    stage_id = _stable_id("stage", stage_key)
    shift_key = f"{application_key}:shift:{_token(context.turn)}"
    shift_id = _stable_id("shift", shift_key)
    booklet_key = f"{application_key}:booklet:{_token(context.variant)}"
    booklet_id = _stable_id("booklet", booklet_key)
    scope_key = ":".join((application_key, role_id, stage_id, shift_id, booklet_id))
    scope_id = _stable_id("application-scope", scope_key)
    evidence_json = canonical_json(context.evidence)
    connection.execute(
        "INSERT INTO canonical_contests (id,canonical_key,official_name,display_name,"
        "notice_year,board,organization,official_code,source_url,evidence_json,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "updated_at=excluded.updated_at,evidence_json=excluded.evidence_json",
        (
            contest_id,
            contest_key,
            context.contest,
            context.contest,
            context.year,
            context.board,
            context.board,
            context.contest,
            context.source_url,
            evidence_json,
            changed_at,
            changed_at,
        ),
    )
    alias_key = f"{contest_id}:{_token(context.contest)}:desktop"
    connection.execute(
        "INSERT INTO contest_aliases (id,contest_id,raw_value,normalized_value,alias_type,"
        "source_context,source_url,evidence_json,status,created_at,updated_at) "
        "VALUES (?,?,?,?,?,'desktop',?,?,'active',?,?) ON CONFLICT(id) DO UPDATE SET "
        "updated_at=excluded.updated_at",
        (
            _stable_id("contest-alias", alias_key),
            contest_id,
            context.contest,
            _token(context.contest),
            "collected_metadata",
            context.source_url,
            evidence_json,
            changed_at,
            changed_at,
        ),
    )
    connection.execute(
        "INSERT INTO exam_applications (id,canonical_key,contest_id,official_title,"
        "display_name,application_date,support_status,source_url,evidence_json,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "updated_at=excluded.updated_at,evidence_json=excluded.evidence_json",
        (
            application_id,
            application_key,
            contest_id,
            f"{context.contest} {context.year}",
            str(context.year),
            f"{context.year:04d}-01-01",
            "collected",
            context.source_url,
            evidence_json,
            changed_at,
            changed_at,
        ),
    )
    connection.execute(
        "INSERT INTO contest_roles (id,canonical_key,contest_id,official_name,display_name,"
        "official_code,normalized_name,evidence_json,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "updated_at=excluded.updated_at,evidence_json=excluded.evidence_json",
        (
            role_id,
            role_key,
            contest_id,
            context.role,
            context.role,
            None,
            _token(context.role),
            evidence_json,
            changed_at,
            changed_at,
        ),
    )
    connection.execute(
        "INSERT INTO application_stages (id,canonical_key,application_id,official_name,"
        "category,normalized_name,evidence_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,"
        "evidence_json=excluded.evidence_json",
        (
            stage_id,
            stage_key,
            application_id,
            context.stage,
            "objective",
            _token(context.stage),
            evidence_json,
            changed_at,
            changed_at,
        ),
    )
    connection.execute(
        "INSERT INTO application_shifts (id,canonical_key,application_id,official_name,"
        "normalized_name,sort_order,evidence_json,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,"
        "evidence_json=excluded.evidence_json",
        (
            shift_id,
            shift_key,
            application_id,
            context.turn,
            _token(context.turn),
            0,
            evidence_json,
            changed_at,
            changed_at,
        ),
    )
    connection.execute(
        "INSERT INTO application_booklets (id,canonical_key,application_id,official_code,"
        "display_name,normalized_code,evidence_json,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,"
        "evidence_json=excluded.evidence_json",
        (
            booklet_id,
            booklet_key,
            application_id,
            context.variant,
            context.variant,
            _token(context.variant),
            evidence_json,
            changed_at,
            changed_at,
        ),
    )
    connection.execute(
        "INSERT INTO application_scopes (id,canonical_key,application_id,role_id,stage_id,"
        "shift_id,booklet_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
        (
            scope_id,
            scope_key,
            application_id,
            role_id,
            stage_id,
            shift_id,
            booklet_id,
            changed_at,
            changed_at,
        ),
    )
    for document_id, version_id, filename, source_url, sha256, kind, key_state in (
        (
            context.exam_document_id,
            context.exam_version_id,
            context.filename,
            context.source_url,
            context.exam_sha256,
            "exam",
            None,
        ),
        (
            context.key_document_id,
            context.key_version_id,
            context.key_filename,
            context.key_source_url,
            context.key_sha256,
            "answer_key",
            context.answer_key_state,
        ),
    ):
        canonical_key = f"{application_key}:document:{version_id}"
        canonical_document_id = _stable_id("canonical-document", canonical_key)
        connection.execute(
            "INSERT INTO canonical_documents (id,canonical_key,source_document_key,contest_id,"
            "application_id,document_kind,official_title,display_name,answer_key_state,"
            "source_url,sha256,evidence_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "updated_at=excluded.updated_at,evidence_json=excluded.evidence_json",
            (
                canonical_document_id,
                canonical_key,
                version_id,
                contest_id,
                application_id,
                kind,
                filename,
                filename,
                key_state,
                source_url,
                sha256,
                evidence_json,
                changed_at,
                changed_at,
            ),
        )
        connection.execute(
            "INSERT OR IGNORE INTO canonical_document_scopes "
            "(document_id,scope_id,content_kind,first_question,last_question,created_at) "
            "VALUES (?,?, 'objective',?,?,?)",
            (
                canonical_document_id,
                scope_id,
                context.first_question,
                context.last_question,
                changed_at,
            ),
        )
        connection.execute(
            "UPDATE documents SET canonical_contest_id=?,canonical_application_id=?,"
            "canonical_document_id=? WHERE id=?",
            (contest_id, application_id, canonical_document_id, document_id),
        )
        connection.execute(
            "UPDATE document_versions SET canonical_contest_id=?,canonical_application_id=?,"
            "canonical_document_id=? WHERE id=?",
            (contest_id, application_id, canonical_document_id, version_id),
        )


def _preparation_reviews(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    rows = connection.execute(
        """
        SELECT queue.exam_version_id, queue.reason, queue.candidates_json,
               exam.id AS document_id, exam.filename,
               (SELECT q.id FROM questions q WHERE q.document_id=exam.id
                ORDER BY q.question_number LIMIT 1) AS question_id,
               (SELECT COUNT(*) FROM questions q WHERE q.document_id=exam.id) AS question_count,
               version.profile_json
        FROM association_review_queue queue
        JOIN document_versions version ON version.id=queue.exam_version_id
        LEFT JOIN documents exam ON exam.document_version_id=version.id
        WHERE queue.status='pending'
        ORDER BY exam.filename, queue.exam_version_id
        """
    ).fetchall()
    labels = {
        "role": "cargo",
        "stage": "etapa",
        "turn": "turno",
        "variant": "tipo de prova",
        "interval": "intervalo de questões",
    }
    for row in rows:
        profile = json.loads(cast(str, row["profile_json"]))
        identity = profile.get("identity", {})
        fields: dict[str, list[Any]] = {}
        for name in ("roles", "stage", "turns", "variants"):
            value = identity.get(name, {})
            fields[name] = value.get("normalized_values", []) if isinstance(value, dict) else []
        candidates = json.loads(cast(str, row["candidates_json"]))
        candidate_items = []
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, Mapping):
                continue
            version_id = candidate.get("version_id")
            key = connection.execute(
                "SELECT d.filename,v.answer_key_state FROM document_versions v "
                "LEFT JOIN documents d ON d.document_version_id=v.id WHERE v.id=? LIMIT 1",
                (version_id,),
            ).fetchone()
            candidate_items.append(
                {
                    "versionId": version_id,
                    "filename": key["filename"] if key is not None else "Gabarito não localizado",
                    "answerKeyState": key["answer_key_state"] if key is not None else None,
                    "score": candidate.get("score"),
                    "conflicts": candidate.get("conflicts", []),
                    "incompleteFields": candidate.get("incomplete_fields", []),
                }
            )
        reason = cast(str, row["reason"])
        missing_labels = [label for key, label in labels.items() if key in reason]
        reviews.append(
            {
                "type": "answer_key_association",
                "examVersionId": row["exam_version_id"],
                "documentId": row["document_id"],
                "questionId": row["question_id"],
                "filename": row["filename"],
                "questionCount": int(row["question_count"] or 0),
                "reason": reason,
                "missingLabels": missing_labels,
                "currentScope": fields,
                "candidates": candidate_items,
                "action": "Abra uma questão, corrija os dados da prova e salve.",
            }
        )
    return reviews


def _summary(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM documents d JOIN document_versions v
             ON v.id=d.document_version_id WHERE v.document_role='exam') AS exams,
          (SELECT COUNT(*) FROM documents d JOIN document_versions v
             ON v.id=d.document_version_id WHERE v.document_role='answer_key') AS answer_keys,
          (SELECT COUNT(*) FROM questions) AS raw_questions,
          (SELECT COUNT(*) FROM question_occurrences WHERE scope_id IS NOT NULL) AS occurrences,
          (SELECT COUNT(*) FROM question_equivalence_groups
             WHERE status='confirmed') AS confirmed_groups,
          (SELECT COUNT(*) FROM canonical_questions cq
             JOIN question_equivalence_groups g ON g.id=cq.group_id
             WHERE g.status='confirmed' AND cq.editorial_status!='blocked') AS canonical_questions,
          (SELECT COALESCE(SUM(g.occurrence_count - 1),0)
             FROM question_equivalence_groups g
             WHERE g.status='confirmed') AS duplicate_occurrences,
          (SELECT COUNT(*) FROM question_equivalence_groups
             WHERE status IN ('conflict','needs_review')
                OR (status='confirmed' AND has_statement_variants=1)) AS conflict_groups,
          (SELECT COALESCE(SUM(occurrence_count),0) FROM question_equivalence_groups
             WHERE status IN ('conflict','needs_review')
                OR (status='confirmed' AND has_statement_variants=1)) AS conflict_occurrences,
          (SELECT COUNT(*) FROM question_equivalence_review_queue
             WHERE status='pending') AS group_reviews
        """
    ).fetchone()
    assert row is not None
    reviews = _preparation_reviews(connection)
    canonical = int(row["canonical_questions"])
    occurrences = int(row["occurrences"])
    raw = int(row["raw_questions"])
    prepared_question_ids = int(
        connection.execute(
            "SELECT COUNT(DISTINCT o.question_id) FROM question_occurrences o "
            "JOIN question_group_occurrences go ON go.occurrence_id=o.id AND go.status='active' "
            "JOIN question_equivalence_groups g ON g.id=go.group_id "
            "WHERE g.status='confirmed'"
        ).fetchone()[0]
    )
    classification_coverage = canonical_classification_coverage(
        connection, eligibility_scope="answered"
    )
    return {
        "algorithmVersion": DESKTOP_PREPARATION_ALGORITHM_VERSION,
        "exams": int(row["exams"]),
        "answerKeys": int(row["answer_keys"]),
        "rawQuestions": raw,
        "occurrences": occurrences,
        "confirmedGroups": int(row["confirmed_groups"]),
        "canonicalQuestions": canonical,
        "mainQuestions": canonical,
        "readyQuestions": prepared_question_ids,
        "duplicateQuestions": int(row["duplicate_occurrences"]),
        "conflictGroups": int(row["conflict_groups"]),
        "conflictQuestions": int(row["conflict_occurrences"]),
        "pendingQuestions": max(raw - prepared_question_ids, 0),
        "qwenEligible": classification_coverage["classificationUnits"],
        "qwenEligibleQuestions": classification_coverage["eligibleQuestions"],
        "qwenInheritedCopies": classification_coverage["inheritedCopies"],
        "qwenBlockedAnswered": classification_coverage["blockedAnswered"],
        "pendingCases": len(reviews) + int(row["group_reviews"]),
        "reviews": reviews,
    }


def apply_desktop_preparation(
    connection: sqlite3.Connection, *, run_id: str
) -> dict[str, Any]:
    initialize_canonical_identity_schema(connection)
    initialize_question_equivalence_schema(connection)
    changed_at = _now()
    rows = _linked_exam_rows(connection)
    contexts: list[_ExamContext] = []
    skipped: list[dict[str, Any]] = []
    seen_exams: set[str] = set()
    for row in rows:
        exam_version_id = cast(str, row["exam_version_id"])
        if exam_version_id in seen_exams:
            skipped.append(
                {
                    "examVersionId": exam_version_id,
                    "reason": "mais de um documento local representa o gabarito selecionado",
                }
            )
            continue
        context, missing = _exam_context(row)
        if context is None:
            skipped.append({"examVersionId": exam_version_id, "missingFields": missing})
            continue
        seen_exams.add(exam_version_id)
        contexts.append(context)
    connection.execute("BEGIN IMMEDIATE")
    try:
        for context in contexts:
            _upsert_catalog(connection, context, changed_at)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    equivalence = run_question_equivalence_migration(
        connection,
        apply=True,
        run_id=f"{run_id}-equivalence",
    )
    result = _summary(connection)
    result.update(
        {
            "runId": run_id,
            "identifiedExams": len(contexts),
            "activeAnswerLinks": len(contexts),
            "skipped": skipped,
            "equivalence": equivalence.as_dict(),
        }
    )
    return result


class DesktopPreparationManager:
    def __init__(self, store: Any) -> None:
        self.store = store
        with closing(self.store._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS desktop_preparation_runs (
                    id TEXT PRIMARY KEY,
                    algorithm_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            connection.commit()

    def summary(self) -> dict[str, Any]:
        with closing(self.store._connect()) as connection:
            return _summary(connection)

    def preview(self) -> dict[str, Any]:
        source = sqlite3.connect(self.store.path, timeout=30)
        memory = sqlite3.connect(":memory:")
        memory.row_factory = sqlite3.Row
        try:
            source.backup(memory)
            report = apply_desktop_preparation(memory, run_id=f"preview-{uuid.uuid4().hex}")
            report["mode"] = "preview"
            return report
        finally:
            memory.close()
            source.close()

    def run(self) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        started_at = _now()
        with closing(self.store._connect()) as connection:
            connection.execute(
                "INSERT INTO desktop_preparation_runs "
                "(id,algorithm_version,status,report_json,created_at) "
                "VALUES (?,?, 'running','{}',?)",
                (run_id, DESKTOP_PREPARATION_ALGORITHM_VERSION, started_at),
            )
            connection.commit()
            try:
                report = apply_desktop_preparation(connection, run_id=run_id)
                finished_at = _now()
                connection.execute(
                    "UPDATE desktop_preparation_runs SET status='completed',report_json=?,"
                    "finished_at=? WHERE id=?",
                    (canonical_json(report), finished_at, run_id),
                )
                connection.commit()
                return report
            except Exception as exc:
                connection.execute(
                    "UPDATE desktop_preparation_runs SET status='failed',report_json=?,"
                    "finished_at=? WHERE id=?",
                    (canonical_json({"error": str(exc)}), _now(), run_id),
                )
                connection.commit()
                raise
