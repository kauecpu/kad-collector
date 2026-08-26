from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, Literal, cast

from .canonical_identity import resolve_contest_alias
from .semantic_identity import canonical_json, stable_sha256

QUESTION_EQUIVALENCE_SCHEMA_VERSION = 1
QUESTION_EQUIVALENCE_ALGORITHM_VERSION = "question-equivalence-v2"
MigrationMode = Literal["dry-run", "apply"]
GroupStatus = Literal[
    "candidate", "confirmed", "incomplete", "conflict", "needs_review", "rejected"
]

_ALTERNATIVE_PREFIX = re.compile(r"^\s*[A-H]\s*[\)\.\-:]\s*", re.IGNORECASE)
_PARSER_HEADER = re.compile(
    r"(?i)^\s*(?:concurso\s+p[uú]blico.*|.*\btipo\s+[1-9]\d*\s*[-–—]?\s*"
    r"p[aá]gina\s+\d+\s*)$"
)


class QuestionEquivalenceError(ValueError):
    """The collector cannot create a proved canonical question."""


@dataclass(frozen=True)
class QuestionFingerprints:
    exact: str
    invariant: str
    statement: str
    normalized_statement: str
    normalized_alternatives: tuple[str, ...]


@dataclass
class QuestionEquivalenceReport:
    run_id: str
    mode: MigrationMode
    status: Literal["completed", "paused", "failed"] = "completed"
    requested_contest: str | None = None
    contest_id: str | None = None
    questions_before: int = 0
    duplicate_flags_before: int = 0
    occurrences_analyzed: int = 0
    occurrences_total: int = 0
    candidate_groups: int = 0
    confirmed_groups: int = 0
    canonical_questions: int = 0
    ungrouped_occurrences: int = 0
    incomplete_groups: int = 0
    conflicting_groups: int = 0
    answer_conflicts: int = 0
    classification_conflicts: int = 0
    sent_to_review: int = 0
    remaining: int = 0
    by_context: dict[str, dict[str, int]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        reduction = max(self.occurrences_total - self.canonical_questions, 0)
        return {
            "schemaVersion": QUESTION_EQUIVALENCE_SCHEMA_VERSION,
            "algorithmVersion": QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
            "runId": self.run_id,
            "mode": self.mode,
            "status": self.status,
            "requestedContest": self.requested_contest,
            "contestId": self.contest_id,
            "questionsBefore": self.questions_before,
            "duplicateFlagsBefore": self.duplicate_flags_before,
            "occurrencesAnalyzed": self.occurrences_analyzed,
            "occurrencesTotal": self.occurrences_total,
            "candidateGroups": self.candidate_groups,
            "confirmedGroups": self.confirmed_groups,
            "canonicalQuestions": self.canonical_questions,
            "ungroupedOccurrences": self.ungrouped_occurrences,
            "incompleteGroups": self.incomplete_groups,
            "conflictingGroups": self.conflicting_groups,
            "answerConflicts": self.answer_conflicts,
            "classificationConflicts": self.classification_conflicts,
            "sentToReview": self.sent_to_review,
            "remaining": self.remaining,
            "reduction": reduction,
            "byContext": dict(sorted(self.by_context.items())),
            "conflicts": self.conflicts,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stable_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kad:{kind}:{key}"))


def normalize_question_text(value: str, *, alternative: bool = False) -> str:
    text = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in text.split("\n"):
        line = " ".join(raw_line.split())
        if not line or _PARSER_HEADER.fullmatch(line):
            continue
        lines.append(line)
    normalized = " ".join(lines).casefold()
    if alternative:
        normalized = _ALTERNATIVE_PREFIX.sub("", normalized)
    return normalized


def question_fingerprints(payload: dict[str, Any]) -> QuestionFingerprints:
    statement = normalize_question_text(str(payload.get("statement") or ""))
    alternatives_payload = payload.get("alternatives")
    alternatives = alternatives_payload if isinstance(alternatives_payload, list) else []
    ordered = tuple(
        normalize_question_text(str(item.get("text") or ""), alternative=True)
        for item in alternatives
        if isinstance(item, dict)
    )
    exact = stable_sha256(
        {
            "algorithm": QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
            "statement": statement,
            "alternatives": ordered,
        }
    )
    invariant = stable_sha256(
        {
            "algorithm": QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
            "statement": statement,
            "alternatives": sorted(ordered),
        }
    )
    return QuestionFingerprints(
        exact=exact,
        invariant=invariant,
        statement=stable_sha256(
            {"algorithm": QUESTION_EQUIVALENCE_ALGORITHM_VERSION, "statement": statement}
        ),
        normalized_statement=statement,
        normalized_alternatives=ordered,
    )


def initialize_question_equivalence_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS question_equivalence_runs (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            algorithm_version TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            contest_id TEXT REFERENCES canonical_contests(id),
            cursor_question_id TEXT,
            report_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS question_occurrences (
            id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL UNIQUE REFERENCES questions(id),
            document_id TEXT NOT NULL REFERENCES documents(id),
            document_version_id TEXT REFERENCES document_versions(id),
            canonical_document_id TEXT REFERENCES canonical_documents(id),
            contest_id TEXT REFERENCES canonical_contests(id),
            application_id TEXT REFERENCES exam_applications(id),
            scope_id TEXT REFERENCES application_scopes(id),
            role_id TEXT REFERENCES contest_roles(id),
            stage_id TEXT REFERENCES application_stages(id),
            shift_id TEXT REFERENCES application_shifts(id),
            booklet_id TEXT REFERENCES application_booklets(id),
            content_kind TEXT NOT NULL,
            original_number INTEGER NOT NULL,
            pages_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            classification_json TEXT NOT NULL,
            exact_fingerprint TEXT NOT NULL,
            equivalence_fingerprint TEXT NOT NULL,
            statement_fingerprint TEXT NOT NULL,
            extraction_fingerprint TEXT NOT NULL,
            answer_status TEXT NOT NULL,
            answer_letter TEXT,
            answer_text TEXT,
            normalized_answer_text TEXT,
            answer_key_link_id TEXT REFERENCES document_links(id),
            answer_link_valid INTEGER NOT NULL,
            source_sha256 TEXT,
            source_url TEXT,
            legacy_duplicate INTEGER NOT NULL,
            occurrence_status TEXT NOT NULL,
            source_updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS question_occurrences_equivalence_idx
            ON question_occurrences(contest_id, application_id, role_id, stage_id, shift_id,
                                    equivalence_fingerprint);
        CREATE TABLE IF NOT EXISTS question_equivalence_groups (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            algorithm_version TEXT NOT NULL,
            equivalence_fingerprint TEXT NOT NULL,
            statement_fingerprint TEXT NOT NULL,
            contest_id TEXT NOT NULL REFERENCES canonical_contests(id),
            application_id TEXT NOT NULL REFERENCES exam_applications(id),
            role_id TEXT NOT NULL REFERENCES contest_roles(id),
            stage_id TEXT NOT NULL REFERENCES application_stages(id),
            shift_id TEXT NOT NULL REFERENCES application_shifts(id),
            content_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            expected_occurrences INTEGER NOT NULL,
            occurrence_count INTEGER NOT NULL,
            representative_occurrence_id TEXT REFERENCES question_occurrences(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS question_equivalence_groups_context_idx
            ON question_equivalence_groups(contest_id, application_id, role_id, stage_id,
                                           shift_id, content_kind);
        CREATE TABLE IF NOT EXISTS question_group_occurrences (
            group_id TEXT NOT NULL REFERENCES question_equivalence_groups(id),
            occurrence_id TEXT NOT NULL REFERENCES question_occurrences(id),
            relation_type TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(group_id, occurrence_id)
        );
        CREATE TABLE IF NOT EXISTS canonical_questions (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL UNIQUE REFERENCES question_equivalence_groups(id),
            representative_occurrence_id TEXT NOT NULL REFERENCES question_occurrences(id),
            payload_json TEXT NOT NULL,
            classification_json TEXT NOT NULL,
            content_fingerprint TEXT NOT NULL,
            canonical_answer_text TEXT,
            editorial_status TEXT NOT NULL,
            editorial_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS question_equivalence_review_queue (
            group_id TEXT PRIMARY KEY REFERENCES question_equivalence_groups(id),
            run_id TEXT REFERENCES question_equivalence_runs(id),
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            occurrence_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS question_equivalence_events (
            event_key TEXT PRIMARY KEY,
            run_id TEXT REFERENCES question_equivalence_runs(id),
            group_id TEXT REFERENCES question_equivalence_groups(id),
            canonical_question_id TEXT REFERENCES canonical_questions(id),
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS question_equivalence_events_append_only_update
        BEFORE UPDATE ON question_equivalence_events
        BEGIN SELECT RAISE(ABORT, 'question equivalence events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS question_equivalence_events_append_only_delete
        BEFORE DELETE ON question_equivalence_events
        BEGIN SELECT RAISE(ABORT, 'question equivalence events are append-only'); END;
        """
    )


def _answer_content(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    letter = payload.get("correct_answer")
    if not isinstance(letter, str):
        return None, None
    alternatives = payload.get("alternatives")
    if not isinstance(alternatives, list):
        return letter, None
    for item in alternatives:
        if not isinstance(item, dict) or item.get("letter") != letter:
            continue
        raw = str(item.get("text") or "").strip()
        return letter, raw or None
    return letter, None


def _objective_scope(connection: sqlite3.Connection, document_id: str) -> sqlite3.Row | None:
    rows = connection.execute(
        """
        SELECT s.id AS scope_id, s.role_id, s.stage_id, s.shift_id, s.booklet_id,
               d.canonical_contest_id AS contest_id,
               d.canonical_application_id AS application_id,
               d.canonical_document_id
        FROM documents d
        JOIN canonical_document_scopes cds
          ON cds.document_id = d.canonical_document_id AND cds.content_kind = 'objective'
        JOIN application_scopes s ON s.id = cds.scope_id
        WHERE d.id = ?
        ORDER BY s.id
        """,
        (document_id,),
    ).fetchall()
    scope_ids = {cast(str, row["scope_id"]) for row in rows}
    return rows[0] if len(scope_ids) == 1 else None


def _valid_answer_link(connection: sqlite3.Connection, link_id: str | None) -> bool:
    if not link_id:
        return False
    row = connection.execute(
        "SELECT 1 FROM document_links WHERE id = ? AND status = 'active' "
        "AND algorithm_version = 'semantic-association-v2'",
        (link_id,),
    ).fetchone()
    return row is not None


def sync_question_occurrence(
    connection: sqlite3.Connection,
    question_id: str,
    *,
    changed_at: str,
) -> str:
    row = connection.execute(
        """
        SELECT q.*, d.document_version_id, d.canonical_document_id,
               d.canonical_contest_id, d.canonical_application_id,
               d.sha256, d.metadata_json
        FROM questions q JOIN documents d ON d.id = q.document_id
        WHERE q.id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        raise QuestionEquivalenceError("questão legada não encontrada")
    payload = json.loads(cast(str, row["payload_json"]))
    classification = json.loads(cast(str, row["classification_json"]))
    metadata = json.loads(cast(str, row["metadata_json"]))
    fingerprints = question_fingerprints(payload)
    scope = _objective_scope(connection, cast(str, row["document_id"]))
    flags = set(json.loads(cast(str, row["flags_json"])))
    alternatives = payload.get("alternatives")
    alternatives_valid = (
        isinstance(alternatives, list)
        and len(alternatives) >= 2
        and all(
            isinstance(item, dict) and normalize_question_text(str(item.get("text") or ""))
            for item in alternatives
        )
    )
    occurrence_status = "ready"
    if scope is None:
        occurrence_status = "unresolved_scope"
    elif not fingerprints.normalized_statement or not alternatives_valid:
        occurrence_status = "incomplete_content"
    elif "visual" in flags:
        occurrence_status = "needs_review"
    answer_letter, answer_text = _answer_content(payload)
    answer_status = str(payload.get("answer_status") or "missing")
    answer_valid = _valid_answer_link(connection, cast(str | None, row["answer_key_link_id"]))
    occurrence_id = _stable_id("question-occurrence", question_id)
    source_url = str(metadata.get("source_url") or metadata.get("canonical_url") or "").strip()
    values = {
        "id": occurrence_id,
        "question_id": question_id,
        "document_id": row["document_id"],
        "document_version_id": row["document_version_id"],
        "canonical_document_id": row["canonical_document_id"],
        "contest_id": scope["contest_id"] if scope is not None else row["canonical_contest_id"],
        "application_id": (
            scope["application_id"] if scope is not None else row["canonical_application_id"]
        ),
        "scope_id": scope["scope_id"] if scope is not None else None,
        "role_id": scope["role_id"] if scope is not None else None,
        "stage_id": scope["stage_id"] if scope is not None else None,
        "shift_id": scope["shift_id"] if scope is not None else None,
        "booklet_id": scope["booklet_id"] if scope is not None else None,
        "content_kind": "objective",
        "original_number": row["question_number"],
        "pages_json": canonical_json(payload.get("source_pages") or []),
        "payload_json": canonical_json(payload),
        "classification_json": canonical_json(classification),
        "exact_fingerprint": fingerprints.exact,
        "equivalence_fingerprint": fingerprints.invariant,
        "statement_fingerprint": fingerprints.statement,
        "extraction_fingerprint": cast(str, row["fingerprint"]),
        "answer_status": answer_status,
        "answer_letter": answer_letter,
        "answer_text": answer_text,
        "normalized_answer_text": (
            normalize_question_text(answer_text, alternative=True) if answer_text else None
        ),
        "answer_key_link_id": row["answer_key_link_id"],
        "answer_link_valid": int(answer_valid),
        "source_sha256": row["sha256"],
        "source_url": source_url or None,
        "legacy_duplicate": int("duplicate" in flags),
        "occurrence_status": occurrence_status,
        "source_updated_at": row["updated_at"],
        "created_at": changed_at,
        "updated_at": changed_at,
    }
    names = tuple(values)
    updates = ", ".join(
        f"{name}=excluded.{name}" for name in names if name not in {"id", "created_at"}
    )
    connection.execute(
        f"INSERT INTO question_occurrences ({', '.join(names)}) "  # noqa: S608
        f"VALUES ({', '.join('?' for _ in names)}) "
        f"ON CONFLICT(question_id) DO UPDATE SET {updates}",
        tuple(values[name] for name in names),
    )
    return occurrence_id


def _boundary(row: sqlite3.Row) -> tuple[str, str, str, str, str, str]:
    return (
        cast(str, row["contest_id"]),
        cast(str, row["application_id"]),
        cast(str, row["role_id"]),
        cast(str, row["stage_id"]),
        cast(str, row["shift_id"]),
        cast(str, row["content_kind"]),
    )


def _expected_booklets(
    connection: sqlite3.Connection, boundary: tuple[str, str, str, str, str, str]
) -> tuple[str, ...]:
    _, application_id, role_id, stage_id, shift_id, _ = boundary
    rows = connection.execute(
        "SELECT DISTINCT booklet_id FROM application_scopes "
        "WHERE application_id = ? AND role_id = ? AND stage_id = ? AND shift_id = ? "
        "ORDER BY booklet_id",
        (application_id, role_id, stage_id, shift_id),
    ).fetchall()
    return tuple(cast(str, row["booklet_id"]) for row in rows)


def _editorial_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(cast(str, row["payload_json"]))
    return {
        name: payload.get(name)
        for name in ("discipline", "matter", "subject", "level", "difficulty", "explanation")
        if payload.get(name) not in {None, ""}
    }


def _representative(
    connection: sqlite3.Connection, occurrences: Sequence[sqlite3.Row]
) -> sqlite3.Row:
    def rank(item: sqlite3.Row) -> tuple[int, int, int, int, int, str]:
        details = connection.execute(
            """
            SELECT q.status AS question_status, d.status AS document_status,
                   d.warnings_json, d.canonical_document_id,
                   COALESCE(cd.canonical_key, d.id) AS canonical_key,
                   key_version.answer_key_state
            FROM questions q
            JOIN documents d ON d.id = q.document_id
            LEFT JOIN canonical_documents cd ON cd.id = d.canonical_document_id
            LEFT JOIN document_links l ON l.id = q.answer_key_link_id
            LEFT JOIN document_versions key_version ON key_version.id = l.answer_key_version_id
            WHERE q.id = ?
            """,
            (item["question_id"],),
        ).fetchone()
        warnings = json.loads(cast(str, details["warnings_json"]))
        return (
            0 if details["question_status"] in {"approved", "exported"} else 1,
            0 if details["answer_key_state"] == "definitive" else 1,
            0 if details["document_status"] in {"extracted", "processed"} else 1,
            len(warnings),
            0 if details["canonical_document_id"] else 1,
            cast(str, details["canonical_key"]),
        )

    return sorted(occurrences, key=rank)[0]


def _event(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    group_id: str,
    canonical_question_id: str | None,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    reason: str,
    changed_at: str,
) -> None:
    event_key = stable_sha256(
        {
            "runId": run_id,
            "groupId": group_id,
            "action": action,
            "after": after,
        }
    )
    connection.execute(
        "INSERT OR IGNORE INTO question_equivalence_events "
        "(event_key, run_id, group_id, canonical_question_id, action, actor, "
        "algorithm_version, before_json, after_json, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'system', ?, ?, ?, ?, ?)",
        (
            event_key,
            run_id,
            group_id,
            canonical_question_id,
            action,
            QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
            canonical_json(before) if before is not None else None,
            canonical_json(after),
            reason,
            changed_at,
        ),
    )


def _upsert_group(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    occurrences: Sequence[sqlite3.Row],
    statement_collision: bool,
    grouping_fingerprint: str,
    relation_type: str,
    changed_at: str,
) -> tuple[str, GroupStatus, bool, bool]:
    first = occurrences[0]
    boundary = _boundary(first)
    expected_booklets = _expected_booklets(connection, boundary)
    present_booklets = tuple(
        sorted(cast(str, item["booklet_id"]) for item in occurrences if item["booklet_id"])
    )
    duplicate_booklet = len(present_booklets) != len(set(present_booklets))
    content_problem = any(item["occurrence_status"] != "ready" for item in occurrences)
    answer_statuses = {cast(str, item["answer_status"]) for item in occurrences}
    valid_answers = all(bool(item["answer_link_valid"]) for item in occurrences)
    answer_values = {
        cast(str, item["normalized_answer_text"])
        for item in occurrences
        if item["normalized_answer_text"]
    }
    answer_conflict = len(answer_values) > 1 or (
        len(answer_statuses) > 1 and "missing" not in answer_statuses
    )
    matched_consensus = answer_statuses == {"matched"} and len(answer_values) == 1
    annulled_consensus = answer_statuses == {"annulled"} and not answer_values
    answer_incomplete = not valid_answers or not (matched_consensus or annulled_consensus)
    snapshots = {
        canonical_json(snapshot) for item in occurrences if (snapshot := _editorial_snapshot(item))
    }
    classification_conflict = len(snapshots) > 1
    coverage_incomplete = set(present_booklets) != set(expected_booklets)
    status: GroupStatus = "confirmed"
    reason = "ocorrências cobrem os cadernos esperados com conteúdo e resposta compatíveis"
    if statement_collision or duplicate_booklet or answer_conflict or classification_conflict:
        status = "conflict"
        reasons = []
        if statement_collision:
            reasons.append("mesmo enunciado possui conjuntos de alternativas diferentes")
        if duplicate_booklet:
            reasons.append("mais de uma ocorrência pertence ao mesmo caderno")
        if answer_conflict:
            reasons.append("respostas oficiais apontam para conteúdos diferentes")
        if classification_conflict:
            reasons.append("classificações editoriais divergem")
        reason = "; ".join(reasons)
    elif content_problem:
        status = "needs_review"
        reason = "uma ocorrência possui escopo, conteúdo ou recurso visual incompleto"
    elif coverage_incomplete or answer_incomplete:
        status = "incomplete"
        reasons = []
        if coverage_incomplete:
            reasons.append("grupo não cobre todos os cadernos esperados")
        if answer_incomplete:
            reasons.append("resposta oficial ativa está ausente ou incompleta")
        reason = "; ".join(reasons)
    canonical_key = ":".join((*boundary, grouping_fingerprint))
    group_id = _stable_id("question-equivalence-group", canonical_key)
    old = connection.execute(
        "SELECT status, representative_occurrence_id, reason FROM question_equivalence_groups "
        "WHERE id = ?",
        (group_id,),
    ).fetchone()
    representative = _representative(connection, occurrences)
    connection.execute(
        """
        INSERT INTO question_equivalence_groups (
            id, canonical_key, algorithm_version, equivalence_fingerprint,
            statement_fingerprint, contest_id, application_id, role_id, stage_id,
            shift_id, content_kind, status, reason, expected_occurrences,
            occurrence_count, representative_occurrence_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET status=excluded.status, reason=excluded.reason,
            expected_occurrences=excluded.expected_occurrences,
            occurrence_count=excluded.occurrence_count,
            representative_occurrence_id=excluded.representative_occurrence_id,
            updated_at=excluded.updated_at
        """,
        (
            group_id,
            canonical_key,
            QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
            grouping_fingerprint,
            first["statement_fingerprint"],
            *boundary,
            status,
            reason,
            len(expected_booklets),
            len(occurrences),
            representative["id"],
            changed_at,
            changed_at,
        ),
    )
    for occurrence in occurrences:
        connection.execute(
            "INSERT INTO question_group_occurrences "
            "(group_id, occurrence_id, relation_type, evidence_json, algorithm_version, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?) "
            "ON CONFLICT(group_id, occurrence_id) DO UPDATE SET "
            "relation_type=excluded.relation_type, evidence_json=excluded.evidence_json, "
            "algorithm_version=excluded.algorithm_version, status='active', "
            "updated_at=excluded.updated_at",
            (
                group_id,
                occurrence["id"],
                relation_type,
                canonical_json(
                    {
                        "exactFingerprint": occurrence["exact_fingerprint"],
                        "equivalenceFingerprint": occurrence["equivalence_fingerprint"],
                        "scopeId": occurrence["scope_id"],
                        "bookletId": occurrence["booklet_id"],
                        "legacyDuplicate": bool(occurrence["legacy_duplicate"]),
                        "relationType": relation_type,
                    }
                ),
                QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
                changed_at,
                changed_at,
            ),
        )
    canonical_question_id = _stable_id("canonical-question", group_id)
    if status == "confirmed":
        payload = json.loads(cast(str, representative["payload_json"]))
        classification = json.loads(cast(str, representative["classification_json"]))
        canonical_answer_text = cast(str | None, representative["answer_text"])
        representative_question = connection.execute(
            "SELECT status FROM questions WHERE id = ?", (representative["question_id"],)
        ).fetchone()
        editorial_status = (
            "approved"
            if representative_question is not None
            and representative_question["status"] in {"approved", "exported"}
            else "pending"
        )
        existing_canonical = connection.execute(
            "SELECT representative_occurrence_id, payload_json, classification_json, "
            "editorial_version FROM canonical_questions WHERE id = ?",
            (canonical_question_id,),
        ).fetchone()
        changed = existing_canonical is not None and (
            existing_canonical["representative_occurrence_id"] != representative["id"]
            or existing_canonical["payload_json"] != canonical_json(payload)
            or existing_canonical["classification_json"] != canonical_json(classification)
        )
        version = (
            int(existing_canonical["editorial_version"]) + 1
            if existing_canonical is not None and changed
            else int(existing_canonical["editorial_version"])
            if existing_canonical is not None
            else 1
        )
        connection.execute(
            "INSERT INTO canonical_questions "
            "(id, group_id, representative_occurrence_id, payload_json, classification_json, "
            "content_fingerprint, canonical_answer_text, editorial_status, editorial_version, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(group_id) DO UPDATE SET "
            "representative_occurrence_id=excluded.representative_occurrence_id, "
            "payload_json=excluded.payload_json, classification_json=excluded.classification_json, "
            "content_fingerprint=excluded.content_fingerprint, "
            "canonical_answer_text=excluded.canonical_answer_text, "
            "editorial_status=excluded.editorial_status, "
            "editorial_version=excluded.editorial_version, updated_at=excluded.updated_at",
            (
                canonical_question_id,
                group_id,
                representative["id"],
                canonical_json(payload),
                canonical_json(classification),
                representative["equivalence_fingerprint"],
                canonical_answer_text,
                editorial_status,
                version,
                changed_at,
                changed_at,
            ),
        )
        connection.execute(
            "DELETE FROM question_equivalence_review_queue WHERE group_id = ?", (group_id,)
        )
    else:
        connection.execute(
            "UPDATE canonical_questions SET editorial_status = 'blocked', updated_at = ? "
            "WHERE group_id = ?",
            (changed_at, group_id),
        )
        connection.execute(
            "INSERT INTO question_equivalence_review_queue "
            "(group_id, run_id, status, reason, occurrence_ids_json, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET "
            "run_id=excluded.run_id, status='pending', reason=excluded.reason, "
            "occurrence_ids_json=excluded.occurrence_ids_json, updated_at=excluded.updated_at",
            (
                group_id,
                run_id,
                reason,
                canonical_json([cast(str, item["id"]) for item in occurrences]),
                changed_at,
                changed_at,
            ),
        )
    after = {
        "status": status,
        "representativeOccurrenceId": representative["id"],
        "occurrenceIds": [cast(str, item["id"]) for item in occurrences],
        "expectedOccurrences": len(expected_booklets),
        "reason": reason,
    }
    before = dict(old) if old is not None else None
    _event(
        connection,
        run_id=run_id,
        group_id=group_id,
        canonical_question_id=(canonical_question_id if status == "confirmed" else None),
        action=("group_created" if old is None else "group_revalidated"),
        before=before,
        after=after,
        reason=reason,
        changed_at=changed_at,
    )
    return group_id, status, answer_conflict, classification_conflict


def _answer_consensus_fingerprints(
    connection: sqlite3.Connection,
    occurrences: Sequence[sqlite3.Row],
) -> dict[str, str]:
    def alternatives_compatible(left: sqlite3.Row, right: sqlite3.Row) -> bool:
        def alternatives(row: sqlite3.Row) -> list[tuple[str, bool]]:
            payload = json.loads(cast(str, row["payload_json"]))
            return [
                (
                    normalize_question_text(str(item.get("text") or ""), alternative=True),
                    "\n" in str(item.get("text") or ""),
                )
                for item in payload.get("alternatives", [])
                if isinstance(item, dict)
            ]

        left_items = alternatives(left)
        right_items = alternatives(right)
        if len(left_items) != len(right_items) or not left_items:
            return False
        common = {item[0] for item in left_items} & {item[0] for item in right_items}
        if len(common) == len(left_items):
            return True
        if len(common) != len(left_items) - 1:
            return False
        left_only = [item for item in left_items if item[0] not in common]
        right_only = [item for item in right_items if item[0] not in common]
        return bool(
            len(left_only) == len(right_only) == 1
            and (left_only[0][1] or right_only[0][1])
            and (left_only[0][0] in right_only[0][0] or right_only[0][0] in left_only[0][0])
        )

    candidates: dict[tuple[tuple[str, str, str, str, str, str], str], list[sqlite3.Row]] = (
        defaultdict(list)
    )
    for occurrence in occurrences:
        answer_text = cast(str | None, occurrence["normalized_answer_text"])
        if occurrence["answer_status"] != "matched" or not answer_text:
            continue
        candidates[(_boundary(occurrence), answer_text)].append(occurrence)
    consensus: dict[str, str] = {}
    for (boundary, answer_text), members in candidates.items():
        expected_booklets = set(_expected_booklets(connection, boundary))
        present_booklets = [cast(str, item["booklet_id"]) for item in members]
        if (
            len(members) < 2
            or len(members) != len(expected_booklets)
            or set(present_booklets) != expected_booklets
            or len(present_booklets) != len(set(present_booklets))
            or not all(item["occurrence_status"] == "ready" for item in members)
            or not all(bool(item["answer_link_valid"]) for item in members)
        ):
            continue
        statements = [
            normalize_question_text(
                str(json.loads(cast(str, item["payload_json"])).get("statement") or "")
            )
            for item in members
        ]
        minimum_similarity = min(
            SequenceMatcher(None, left, right).ratio()
            for index, left in enumerate(statements)
            for right in statements[index + 1 :]
        )
        if minimum_similarity < 0.90:
            continue
        if not all(alternatives_compatible(members[0], item) for item in members[1:]):
            continue
        fingerprint = "answer-consensus:" + stable_sha256(
            {
                "boundary": boundary,
                "answer": answer_text,
                "statements": sorted(cast(str, item["statement_fingerprint"]) for item in members),
            }
        )
        for item in members:
            consensus[cast(str, item["id"])] = fingerprint
    return consensus


def rebuild_equivalence_groups(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    contest_id: str | None,
    changed_at: str,
) -> tuple[Counter[str], list[dict[str, Any]]]:
    parameters: tuple[str, ...] = () if contest_id is None else (contest_id,)
    clause = "" if contest_id is None else "WHERE contest_id = ?"
    occurrences = connection.execute(
        "SELECT * FROM question_occurrences " + clause + " ORDER BY id", parameters
    ).fetchall()
    occurrence_ids = [cast(str, item["id"]) for item in occurrences]
    if occurrence_ids:
        placeholders = ",".join("?" for _ in occurrence_ids)
        connection.execute(
            f"UPDATE question_group_occurrences SET status = 'inactive', updated_at = ? "
            f"WHERE occurrence_id IN ({placeholders})",  # noqa: S608
            (changed_at, *occurrence_ids),
        )
    eligible = [
        item
        for item in occurrences
        if all(
            item[name] is not None
            for name in ("contest_id", "application_id", "role_id", "stage_id", "shift_id")
        )
    ]
    answer_consensus = _answer_consensus_fingerprints(connection, eligible)
    statement_groups: dict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
    grouped: dict[tuple[tuple[str, ...], str], list[sqlite3.Row]] = defaultdict(list)
    for occurrence in eligible:
        boundary: tuple[str, ...] = _boundary(occurrence)
        equivalence = answer_consensus.get(
            cast(str, occurrence["id"]),
            cast(str, occurrence["equivalence_fingerprint"]),
        )
        statement = cast(str, occurrence["statement_fingerprint"])
        grouped[(boundary, equivalence)].append(occurrence)
        statement_groups[(boundary, statement)].add(equivalence)
    totals: Counter[str] = Counter()
    conflicts: list[dict[str, Any]] = []
    for (boundary, grouping_fingerprint), members in sorted(
        grouped.items(), key=lambda item: item[0]
    ):
        relation_type = (
            "statement_answer_consensus"
            if grouping_fingerprint.startswith("answer-consensus:")
            else "deterministic_fingerprint"
        )
        collision = (
            relation_type == "deterministic_fingerprint"
            and len(statement_groups[(boundary, cast(str, members[0]["statement_fingerprint"]))])
            > 1
        )
        group_id, status, answer_conflict, classification_conflict = _upsert_group(
            connection,
            run_id=run_id,
            occurrences=members,
            statement_collision=collision,
            grouping_fingerprint=grouping_fingerprint,
            relation_type=relation_type,
            changed_at=changed_at,
        )
        totals[status] += 1
        totals["answer_conflict"] += int(answer_conflict)
        totals["classification_conflict"] += int(classification_conflict)
        if status != "confirmed":
            row = connection.execute(
                "SELECT reason FROM question_equivalence_groups WHERE id = ?", (group_id,)
            ).fetchone()
            conflicts.append(
                {
                    "groupId": group_id,
                    "status": status,
                    "reason": row["reason"],
                    "occurrenceIds": [cast(str, item["id"]) for item in members],
                }
            )
    totals["ungrouped"] = len(occurrences) - len(eligible)
    connection.execute(
        "UPDATE question_equivalence_groups SET status = 'rejected', "
        "reason = 'sem ocorrencias ativas apos revalidacao', updated_at = ? "
        "WHERE status != 'rejected' AND NOT EXISTS ("
        "SELECT 1 FROM question_group_occurrences go "
        "WHERE go.group_id = question_equivalence_groups.id AND go.status = 'active')",
        (changed_at,),
    )
    connection.execute(
        "UPDATE question_equivalence_review_queue SET status='superseded', updated_at=? "
        "WHERE status='pending' AND group_id IN ("
        "SELECT id FROM question_equivalence_groups WHERE status='rejected')",
        (changed_at,),
    )
    return totals, conflicts


def _measure_before(connection: sqlite3.Connection, contest_id: str | None) -> tuple[int, int]:
    if contest_id is None:
        rows = connection.execute("SELECT flags_json FROM questions").fetchall()
    else:
        rows = connection.execute(
            "SELECT q.flags_json FROM questions q JOIN documents d ON d.id = q.document_id "
            "WHERE d.canonical_contest_id = ?",
            (contest_id,),
        ).fetchall()
    return len(rows), sum("duplicate" in json.loads(cast(str, row["flags_json"])) for row in rows)


def _pending_questions(connection: sqlite3.Connection, contest_id: str | None) -> list[str]:
    clause = "" if contest_id is None else "AND d.canonical_contest_id = ?"
    parameters: tuple[str, ...] = () if contest_id is None else (contest_id,)
    rows = connection.execute(
        "SELECT q.id FROM questions q JOIN documents d ON d.id = q.document_id "
        "LEFT JOIN question_occurrences o ON o.question_id = q.id "
        "WHERE (o.id IS NULL OR o.source_updated_at != q.updated_at) " + clause + " ORDER BY q.id",
        parameters,
    ).fetchall()
    return [cast(str, row["id"]) for row in rows]


def _context_report(
    connection: sqlite3.Connection, contest_id: str | None
) -> dict[str, dict[str, int]]:
    clause = "" if contest_id is None else "WHERE o.contest_id = ?"
    parameters: tuple[str, ...] = () if contest_id is None else (contest_id,)
    rows = connection.execute(
        """
        SELECT COALESCE(r.display_name, '[cargo desconhecido]') AS role,
               COALESCE(sh.official_name, '[turno desconhecido]') AS shift,
               COUNT(DISTINCT o.id) AS occurrences,
               COUNT(DISTINCT CASE WHEN g.status = 'confirmed' THEN g.id END) AS canonical
        FROM question_occurrences o
        LEFT JOIN contest_roles r ON r.id = o.role_id
        LEFT JOIN application_shifts sh ON sh.id = o.shift_id
        LEFT JOIN question_group_occurrences go ON go.occurrence_id = o.id AND go.status = 'active'
        LEFT JOIN question_equivalence_groups g ON g.id = go.group_id
        """
        + clause
        + " GROUP BY r.display_name, sh.official_name ORDER BY r.display_name, sh.official_name",
        parameters,
    ).fetchall()
    return {
        f"{row['role']} | {row['shift']}": {
            "occurrences": int(row["occurrences"]),
            "canonical": int(row["canonical"]),
        }
        for row in rows
    }


def run_question_equivalence_migration(
    connection: sqlite3.Connection,
    *,
    contest_alias: str | None = None,
    apply: bool = False,
    run_id: str | None = None,
    limit: int | None = None,
) -> QuestionEquivalenceReport:
    if limit is not None and limit < 1:
        raise QuestionEquivalenceError("limit deve ser positivo")
    initialize_question_equivalence_schema(connection)
    connection.commit()
    effective_run_id = run_id or str(uuid.uuid4())
    mode: MigrationMode = "apply" if apply else "dry-run"
    contest_id: str | None = None
    if contest_alias:
        resolution = resolve_contest_alias(connection, contest_alias)
        if resolution.outcome != "selected":
            raise QuestionEquivalenceError(resolution.reason)
        contest_id = resolution.contest_id
    report = QuestionEquivalenceReport(
        run_id=effective_run_id,
        mode=mode,
        requested_contest=contest_alias,
        contest_id=contest_id,
    )
    report.questions_before, report.duplicate_flags_before = _measure_before(connection, contest_id)
    changed_at = _now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "INSERT OR IGNORE INTO question_equivalence_runs "
            "(id, schema_version, algorithm_version, mode, status, contest_id, report_json, "
            "started_at) VALUES (?, ?, ?, ?, 'running', ?, '{}', ?)",
            (
                effective_run_id,
                QUESTION_EQUIVALENCE_SCHEMA_VERSION,
                QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
                mode,
                contest_id,
                changed_at,
            ),
        )
        existing = connection.execute(
            "SELECT mode, contest_id FROM question_equivalence_runs WHERE id = ?",
            (effective_run_id,),
        ).fetchone()
        if existing["mode"] != mode or existing["contest_id"] != contest_id:
            raise QuestionEquivalenceError("run_id pertence a outro modo ou concurso")
        pending = _pending_questions(connection, contest_id)
        selected = pending[:limit] if limit is not None else pending
        for question_id in selected:
            sync_question_occurrence(connection, question_id, changed_at=changed_at)
        report.occurrences_analyzed = len(selected)
        remaining = _pending_questions(connection, contest_id)
        totals, conflicts = rebuild_equivalence_groups(
            connection,
            run_id=effective_run_id,
            contest_id=contest_id,
            changed_at=changed_at,
        )
        occurrence_parameters: tuple[str, ...] = () if contest_id is None else (contest_id,)
        occurrence_clause = "" if contest_id is None else " WHERE contest_id = ?"
        report.occurrences_total = int(
            connection.execute(
                "SELECT COUNT(*) FROM question_occurrences" + occurrence_clause,
                occurrence_parameters,
            ).fetchone()[0]
        )
        report.candidate_groups = sum(
            totals[name] for name in ("confirmed", "incomplete", "conflict", "needs_review")
        )
        report.confirmed_groups = totals["confirmed"]
        report.canonical_questions = int(
            connection.execute(
                "SELECT COUNT(*) FROM canonical_questions cq "
                "JOIN question_equivalence_groups g ON g.id = cq.group_id "
                + (
                    "WHERE g.status = 'confirmed'"
                    if contest_id is None
                    else "WHERE g.status = 'confirmed' AND g.contest_id = ?"
                ),
                occurrence_parameters,
            ).fetchone()[0]
        )
        report.ungrouped_occurrences = totals["ungrouped"]
        report.incomplete_groups = totals["incomplete"] + totals["needs_review"]
        report.conflicting_groups = totals["conflict"]
        report.answer_conflicts = totals["answer_conflict"]
        report.classification_conflicts = totals["classification_conflict"]
        report.sent_to_review = report.incomplete_groups + report.conflicting_groups
        report.remaining = len(remaining)
        report.status = "paused" if remaining else "completed"
        report.by_context = _context_report(connection, contest_id)
        report.conflicts = conflicts
        if apply:
            finished_at = _now() if report.status == "completed" else None
            connection.execute(
                "UPDATE question_equivalence_runs SET status = ?, cursor_question_id = ?, "
                "report_json = ?, finished_at = ? WHERE id = ?",
                (
                    report.status,
                    selected[-1] if selected else None,
                    canonical_json(report.as_dict()),
                    finished_at,
                    effective_run_id,
                ),
            )
            connection.commit()
        else:
            connection.rollback()
        return report
    except Exception:
        connection.rollback()
        report.status = "failed"
        raise


def question_equivalence_view(
    connection: sqlite3.Connection, question_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT o.id AS occurrence_id, g.id AS group_id, g.status AS group_status,
               g.reason, g.expected_occurrences, g.occurrence_count,
               cq.id AS canonical_question_id, cq.representative_occurrence_id,
               cq.canonical_answer_text, cq.editorial_status, cq.editorial_version,
               NOT EXISTS (
                   SELECT 1
                   FROM question_group_occurrences freshness_go
                   JOIN question_occurrences freshness_o
                     ON freshness_o.id = freshness_go.occurrence_id
                   JOIN questions freshness_q ON freshness_q.id = freshness_o.question_id
                   WHERE freshness_go.group_id = g.id AND freshness_go.status = 'active'
                     AND (
                         freshness_o.source_updated_at != freshness_q.updated_at
                         OR freshness_o.answer_key_link_id IS NOT freshness_q.answer_key_link_id
                         OR NOT EXISTS (
                             SELECT 1 FROM document_links freshness_link
                             WHERE freshness_link.id = freshness_q.answer_key_link_id
                               AND freshness_link.status = 'active'
                               AND freshness_link.algorithm_version = 'semantic-association-v2'
                         )
                     )
               ) AS group_fresh
        FROM question_occurrences o
        LEFT JOIN question_group_occurrences go ON go.occurrence_id = o.id AND go.status = 'active'
        LEFT JOIN question_equivalence_groups g ON g.id = go.group_id
        LEFT JOIN canonical_questions cq ON cq.group_id = g.id
        WHERE o.question_id = ?
        ORDER BY g.updated_at DESC LIMIT 1
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return None
    group_id = cast(str | None, row["group_id"])
    provenances: list[dict[str, Any]] = []
    if group_id:
        provenance_rows = connection.execute(
            """
            SELECT o.id AS occurrence_id, o.question_id, o.canonical_document_id,
                   o.document_version_id, o.scope_id, r.display_name AS role,
                   sh.official_name AS shift, b.display_name AS booklet,
                   o.original_number, o.pages_json, o.source_sha256, o.source_url,
                   o.answer_letter, o.answer_status, o.answer_key_link_id,
                   d.local_path, d.filename
            FROM question_group_occurrences go
            JOIN question_occurrences o ON o.id = go.occurrence_id
            JOIN documents d ON d.id = o.document_id
            LEFT JOIN contest_roles r ON r.id = o.role_id
            LEFT JOIN application_shifts sh ON sh.id = o.shift_id
            LEFT JOIN application_booklets b ON b.id = o.booklet_id
            WHERE go.group_id = ? AND go.status = 'active'
            ORDER BY o.booklet_id, o.original_number, o.id
            """,
            (group_id,),
        ).fetchall()
        provenances = [
            {
                "occurrenceId": item["occurrence_id"],
                "questionId": item["question_id"],
                "documentId": item["canonical_document_id"],
                "documentVersionId": item["document_version_id"],
                "scopeId": item["scope_id"],
                "role": item["role"],
                "shift": item["shift"],
                "booklet": item["booklet"],
                "questionNumber": item["original_number"],
                "pages": json.loads(cast(str, item["pages_json"])),
                "sha256": item["source_sha256"],
                "url": item["source_url"],
                "answer": item["answer_letter"],
                "answerStatus": item["answer_status"],
                "answerKeyLinkId": item["answer_key_link_id"],
                "localPath": item["local_path"],
                "filename": item["filename"],
            }
            for item in provenance_rows
        ]
    return {
        "occurrenceId": row["occurrence_id"],
        "groupId": group_id,
        "status": row["group_status"],
        "reason": row["reason"],
        "expectedOccurrences": row["expected_occurrences"],
        "occurrenceCount": row["occurrence_count"],
        "canonicalQuestionId": row["canonical_question_id"],
        "representativeOccurrenceId": row["representative_occurrence_id"],
        "canonicalAnswerText": row["canonical_answer_text"],
        "editorialStatus": row["editorial_status"],
        "editorialVersion": row["editorial_version"],
        "groupFresh": bool(row["group_fresh"]),
        "provenances": provenances,
    }


def is_confirmed_canonical_representative(connection: sqlite3.Connection, question_id: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM question_occurrences o
        JOIN question_group_occurrences go ON go.occurrence_id = o.id AND go.status = 'active'
        JOIN question_equivalence_groups g ON g.id = go.group_id AND g.status = 'confirmed'
        JOIN canonical_questions cq ON cq.group_id = g.id
        WHERE o.question_id = ? AND cq.representative_occurrence_id = o.id
        """,
        (question_id,),
    ).fetchone()
    return row is not None


def sync_canonical_editorial_from_question(
    connection: sqlite3.Connection, question_id: str, *, changed_at: str
) -> None:
    row = connection.execute(
        """
        SELECT cq.id, q.payload_json, q.classification_json, q.status
        FROM questions q
        JOIN question_occurrences o ON o.question_id = q.id
        JOIN canonical_questions cq ON cq.representative_occurrence_id = o.id
        WHERE q.id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return
    editorial_status = "approved" if row["status"] in {"approved", "exported"} else "pending"
    connection.execute(
        "UPDATE canonical_questions SET payload_json = ?, classification_json = ?, "
        "editorial_status = ?, editorial_version = editorial_version + 1, updated_at = ? "
        "WHERE id = ?",
        (
            row["payload_json"],
            row["classification_json"],
            editorial_status,
            changed_at,
            row["id"],
        ),
    )
    connection.execute(
        "UPDATE question_occurrences SET payload_json = ?, classification_json = ?, "
        "source_updated_at = ?, updated_at = ? WHERE question_id = ?",
        (
            row["payload_json"],
            row["classification_json"],
            changed_at,
            changed_at,
            question_id,
        ),
    )


def invalidate_question_equivalence(
    connection: sqlite3.Connection,
    question_id: str,
    *,
    actor: str,
    reason: str,
    changed_at: str,
) -> None:
    row = connection.execute(
        """
        SELECT g.id, g.status, cq.id AS canonical_question_id
        FROM question_occurrences o
        JOIN question_group_occurrences go ON go.occurrence_id = o.id AND go.status = 'active'
        JOIN question_equivalence_groups g ON g.id = go.group_id
        LEFT JOIN canonical_questions cq ON cq.group_id = g.id
        WHERE o.question_id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return
    connection.execute(
        "UPDATE question_equivalence_groups SET status = 'needs_review', reason = ?, "
        "updated_at = ? WHERE id = ?",
        (reason, changed_at, row["id"]),
    )
    connection.execute(
        "UPDATE canonical_questions SET editorial_status = 'blocked', updated_at = ? "
        "WHERE group_id = ?",
        (changed_at, row["id"]),
    )
    event_key = stable_sha256(
        {"groupId": row["id"], "questionId": question_id, "reason": reason, "at": changed_at}
    )
    connection.execute(
        "INSERT OR IGNORE INTO question_equivalence_events "
        "(event_key, group_id, canonical_question_id, action, actor, algorithm_version, "
        "after_json, reason, created_at) VALUES (?, ?, ?, 'group_invalidated', ?, ?, ?, ?, ?)",
        (
            event_key,
            row["id"],
            row["canonical_question_id"],
            actor,
            QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
            canonical_json({"status": "needs_review", "questionId": question_id}),
            reason,
            changed_at,
        ),
    )
