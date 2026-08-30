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
from itertools import permutations
from typing import Any, Literal, cast

from .canonical_identity import resolve_contest_alias
from .semantic_identity import canonical_json, stable_sha256

QUESTION_EQUIVALENCE_SCHEMA_VERSION = 3
QUESTION_EQUIVALENCE_ALGORITHM_VERSION = "question-equivalence-v3"
MigrationMode = Literal["dry-run", "apply"]
GroupStatus = Literal[
    "candidate", "confirmed", "incomplete", "conflict", "needs_review", "rejected"
]

_ALTERNATIVE_PREFIX = re.compile(r"^\s*[A-H]\s*[\)\.\-:]\s*", re.IGNORECASE)
_QUESTION_PREFIX = re.compile(
    r"^\s*(?:quest(?:ão|ao)|q\.?)[-_ ]*\d+\s*[\)\.\-:]?\s*", re.IGNORECASE
)
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
    else:
        normalized = _QUESTION_PREFIX.sub("", normalized)
    return normalized


def _comparison_text(value: str, *, alternative: bool = False) -> str:
    """Return a conservative matching key tolerant of spacing and punctuation OCR noise."""
    normalized = unicodedata.normalize(
        "NFKD", normalize_question_text(value, alternative=alternative)
    )
    return "".join(
        character.casefold()
        for character in normalized
        if character.isalnum() and not unicodedata.combining(character)
    )


def question_fingerprints(payload: dict[str, Any]) -> QuestionFingerprints:
    statement = normalize_question_text(str(payload.get("statement") or ""))
    comparable_statement = _comparison_text(str(payload.get("statement") or ""))
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
            "statement": comparable_statement,
            "alternatives": tuple(_comparison_text(item, alternative=True) for item in ordered),
        }
    )
    invariant = stable_sha256(
        {
            "algorithm": QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
            "statement": comparable_statement,
            "alternatives": sorted(
                _comparison_text(item, alternative=True) for item in ordered
            ),
        }
    )
    return QuestionFingerprints(
        exact=exact,
        invariant=invariant,
        statement=stable_sha256(
            {
                "algorithm": QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
                "statement": comparable_statement,
            }
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
            algorithm_version TEXT NOT NULL,
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
            has_statement_variants INTEGER NOT NULL DEFAULT 0,
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
    occurrence_columns = {
        cast(str, row["name"])
        for row in connection.execute("PRAGMA table_info(question_occurrences)")
    }
    if "algorithm_version" not in occurrence_columns:
        connection.execute(
            "ALTER TABLE question_occurrences ADD COLUMN algorithm_version "
            "TEXT NOT NULL DEFAULT 'legacy'"
        )
    group_columns = {
        cast(str, row["name"])
        for row in connection.execute("PRAGMA table_info(question_equivalence_groups)")
    }
    if "has_statement_variants" not in group_columns:
        connection.execute(
            "ALTER TABLE question_equivalence_groups ADD COLUMN "
            "has_statement_variants INTEGER NOT NULL DEFAULT 0"
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
        "AND algorithm_version = 'semantic-association-v3'",
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
        "algorithm_version": QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
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
        for name in ("discipline", "matter", "subject", "level")
        if payload.get(name) not in {None, ""}
    }


def _alternative_items(row: sqlite3.Row) -> list[tuple[str, str, bool]]:
    payload = json.loads(cast(str, row["payload_json"]))
    return [
        (
            normalize_question_text(str(item.get("text") or ""), alternative=True),
            _comparison_text(str(item.get("text") or ""), alternative=True),
            "\n" in str(item.get("text") or ""),
        )
        for item in payload.get("alternatives", [])
        if isinstance(item, dict)
    ]


def _alternative_pair_score(left: tuple[str, str, bool], right: tuple[str, str, bool]) -> float:
    if not left[1] or not right[1]:
        return 0.0
    return max(
        SequenceMatcher(None, left[0], right[0]).ratio(),
        SequenceMatcher(None, left[1], right[1]).ratio(),
    )


def _alternative_alignment(
    left: Sequence[tuple[str, str, bool]],
    right: Sequence[tuple[str, str, bool]],
) -> list[tuple[int, int, float, float]]:
    if len(left) != len(right) or not left:
        return []
    matrix: list[list[tuple[float, float]]] = []
    for left_value in left:
        row: list[tuple[float, float]] = []
        for right_value in right:
            score = _alternative_pair_score(left_value, right_value)
            shorter, longer = sorted(
                (left_value[1], right_value[1]), key=lambda value: (len(value), value)
            )
            containment = (
                len(shorter) / len(longer)
                if shorter and shorter in longer and len(longer) > 0
                else 0.0
            )
            row.append((score, containment))
        matrix.append(row)
    best: list[tuple[int, int, float, float]] = []
    best_rank: tuple[int, float, float] = (-1, -1.0, -1.0)
    for order in permutations(range(len(right))):
        matches = [
            (left_index, right_index, *matrix[left_index][right_index])
            for left_index, right_index in enumerate(order)
        ]
        rank = (
            sum(score >= 0.94 for _, _, score, _ in matches),
            min(score for _, _, score, _ in matches),
            sum(score for _, _, score, _ in matches),
        )
        if rank > best_rank:
            best_rank = rank
            best = matches
    return best


def _alternatives_compatible(left: sqlite3.Row, right: sqlite3.Row) -> bool:
    left_items = _alternative_items(left)
    right_items = _alternative_items(right)
    matches = _alternative_alignment(left_items, right_items)
    if not matches:
        return False
    if all(score >= 0.90 for _, _, score, _ in matches):
        return True
    strong = [item for item in matches if item[2] >= 0.94]
    weak = [item for item in matches if item[2] < 0.94]
    if len(strong) < len(matches) - 1 or len(weak) != 1:
        return False
    left_index, right_index, score, containment = weak[0]
    left_value = left_items[left_index]
    right_value = right_items[right_index]
    shortest_length = min(len(left_value[1]), len(right_value[1]))
    extraction_evidence = (
        left_value[2]
        or right_value[2]
        or max(len(left_value[1]), len(right_value[1]))
        >= max(shortest_length * 1.35, shortest_length + 18)
    )
    if (
        (left_value[2] or right_value[2])
        and shortest_length >= 5
        and containment >= 0.25
    ):
        return True
    return bool(
        len(matches) >= 4
        and extraction_evidence
        and shortest_length >= 8
        and (score >= 0.72 or containment >= 0.45)
    )


def _mapped_answer_value(reference: sqlite3.Row, occurrence: sqlite3.Row) -> str | None:
    answer = cast(str | None, occurrence["normalized_answer_text"])
    if not answer:
        return None
    reference_items = _alternative_items(reference)
    occurrence_items = _alternative_items(occurrence)
    alignment = _alternative_alignment(occurrence_items, reference_items)
    answer_key = _comparison_text(answer, alternative=True)
    source_index = next(
        (index for index, item in enumerate(occurrence_items) if item[1] == answer_key),
        None,
    )
    if source_index is not None and alignment:
        target_index = next(
            (
                right_index
                for left_index, right_index, _, _ in alignment
                if left_index == source_index
            ),
            None,
        )
        if target_index is not None:
            return reference_items[target_index][1]
    candidates = [
        (_alternative_pair_score((answer, answer_key, False), item), item[1])
        for item in reference_items
    ]
    if candidates:
        score, value = max(candidates)
        if score >= 0.90:
            return value
    return answer_key


def _propagate_editorial_fields(
    connection: sqlite3.Connection,
    representative: sqlite3.Row,
    occurrences: Sequence[sqlite3.Row],
    *,
    changed_at: str,
) -> None:
    representative_payload = json.loads(cast(str, representative["payload_json"]))
    representative_classification = json.loads(
        cast(str, representative["classification_json"])
    )
    classification_fields = {
        "discipline": "discipline",
        "matter": "subject",
        "subject": "topic",
        "level": "level",
    }
    inherited = {
        name: representative_payload.get(name)
        for name in classification_fields
        if representative_payload.get(name) not in {None, ""}
    }
    if not inherited:
        return
    for occurrence in occurrences:
        if occurrence["id"] == representative["id"]:
            continue
        payload = json.loads(cast(str, occurrence["payload_json"]))
        classification = json.loads(cast(str, occurrence["classification_json"]))
        before = (canonical_json(payload), canonical_json(classification))
        for field_name, value in inherited.items():
            payload[field_name] = value
            classification_name = classification_fields[field_name]
            representative_value = representative_classification.get(classification_name)
            if representative_value is not None:
                classification[classification_name] = representative_value
        after = (canonical_json(payload), canonical_json(classification))
        if before == after:
            continue
        connection.execute(
            "UPDATE questions SET payload_json=?,classification_json=?,updated_at=? WHERE id=?",
            (*after, changed_at, occurrence["question_id"]),
        )
        connection.execute(
            "UPDATE question_occurrences SET payload_json=?,classification_json=?,"
            "source_updated_at=?,updated_at=? WHERE id=?",
            (*after, changed_at, changed_at, occurrence["id"]),
        )


def _representative(
    connection: sqlite3.Connection, occurrences: Sequence[sqlite3.Row]
) -> sqlite3.Row:
    question_ids = [cast(str, item["question_id"]) for item in occurrences]
    placeholders = ",".join("?" for _ in question_ids)
    detail_rows = connection.execute(
        f"""
        SELECT q.id, q.status AS question_status, d.status AS document_status,
               d.warnings_json, d.canonical_document_id,
               COALESCE(cd.canonical_key, d.id) AS canonical_key,
               key_version.answer_key_state
        FROM questions q
        JOIN documents d ON d.id = q.document_id
        LEFT JOIN canonical_documents cd ON cd.id = d.canonical_document_id
        LEFT JOIN document_links l ON l.id = q.answer_key_link_id
        LEFT JOIN document_versions key_version ON key_version.id = l.answer_key_version_id
        WHERE q.id IN ({placeholders})
        """,  # noqa: S608
        question_ids,
    ).fetchall()
    details_by_id = {cast(str, row["id"]): row for row in detail_rows}

    def rank(item: sqlite3.Row) -> tuple[int, int, int, int, int, int, int, str, str]:
        details = details_by_id[cast(str, item["question_id"])]
        warnings = json.loads(cast(str, details["warnings_json"]))
        payload = json.loads(cast(str, item["payload_json"]))
        alternatives = [
            entry
            for entry in payload.get("alternatives", [])
            if isinstance(entry, dict) and str(entry.get("text") or "").strip()
        ]
        completeness = sum(
            (
                bool(str(payload.get("statement") or "").strip()),
                len(alternatives) >= 2,
                bool(payload.get("source_pages")),
                item["answer_status"] in {"matched", "annulled"},
            )
        )
        return (
            0 if details["question_status"] in {"approved", "exported"} else 1,
            0 if details["answer_key_state"] == "definitive" else 1,
            0 if item["answer_link_valid"] else 1,
            0 if item["occurrence_status"] == "ready" else 1,
            -completeness,
            0 if details["document_status"] in {"extracted", "processed"} else 1,
            len(warnings),
            cast(str, details["canonical_key"]),
            cast(str, item["question_id"]),
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
    representative = _representative(connection, occurrences)
    expected_booklets = _expected_booklets(connection, boundary)
    present_booklets = tuple(
        sorted(cast(str, item["booklet_id"]) for item in occurrences if item["booklet_id"])
    )
    duplicate_booklet = len(present_booklets) != len(set(present_booklets))
    content_problem = any(item["occurrence_status"] != "ready" for item in occurrences)
    answer_statuses = {cast(str, item["answer_status"]) for item in occurrences}
    valid_answers = all(bool(item["answer_link_valid"]) for item in occurrences)
    answer_values = {
        mapped
        for item in occurrences
        if (mapped := _mapped_answer_value(representative, item)) is not None
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
    reason_parts = [
        "conteúdo e resposta são compatíveis; cópia principal escolhida automaticamente"
    ]
    if statement_collision:
        reason_parts.append("outras questões com o mesmo enunciado foram mantidas separadas")
    if coverage_incomplete:
        reason_parts.append("nem todos os tipos possuem esta questão")
    if classification_conflict:
        reason_parts.append("a classificação da cópia principal prevalece")
    reason = "; ".join(reason_parts)
    if duplicate_booklet or answer_conflict:
        status = "conflict"
        reasons = []
        if duplicate_booklet:
            reasons.append("mais de uma ocorrência pertence ao mesmo caderno")
        if answer_conflict:
            reasons.append("respostas oficiais apontam para conteúdos diferentes")
        reason = "; ".join(reasons)
    elif content_problem:
        status = "needs_review"
        reason = "uma ocorrência possui escopo, conteúdo ou recurso visual incompleto"
    elif answer_incomplete:
        status = "incomplete"
        reason = "resposta oficial ativa está ausente ou incompleta"
    _propagate_editorial_fields(
        connection, representative, occurrences, changed_at=changed_at
    )
    canonical_key = ":".join((*boundary, grouping_fingerprint))
    group_id = _stable_id("question-equivalence-group", canonical_key)
    old = connection.execute(
        "SELECT status, representative_occurrence_id, reason FROM question_equivalence_groups "
        "WHERE id = ?",
        (group_id,),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO question_equivalence_groups (
            id, canonical_key, algorithm_version, equivalence_fingerprint,
            statement_fingerprint, contest_id, application_id, role_id, stage_id,
            shift_id, content_kind, status, reason, expected_occurrences,
            occurrence_count, representative_occurrence_id, has_statement_variants,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET status=excluded.status, reason=excluded.reason,
            expected_occurrences=excluded.expected_occurrences,
            occurrence_count=excluded.occurrence_count,
            representative_occurrence_id=excluded.representative_occurrence_id,
            has_statement_variants=excluded.has_statement_variants,
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
            int(statement_collision),
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
    if status == "confirmed":
        connection.execute(
            "DELETE FROM question_equivalence_review_queue WHERE group_id = ?", (group_id,)
        )
    else:
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
        canonical_question_id=canonical_question_id,
        action=("group_created" if old is None else "group_revalidated"),
        before=before,
        after=after,
        reason=reason,
        changed_at=changed_at,
    )
    return group_id, status, answer_conflict, classification_conflict


def _content_groupings(
    occurrences: Sequence[sqlite3.Row],
) -> dict[str, tuple[str, str]]:
    """Cluster exact and extraction-noisy copies without comparing unrelated questions."""
    statement_buckets: dict[
        tuple[tuple[str, str, str, str, str, str], str], list[sqlite3.Row]
    ] = defaultdict(list)
    for occurrence in occurrences:
        statement_buckets[
            (_boundary(occurrence), cast(str, occurrence["statement_fingerprint"]))
        ].append(occurrence)

    result: dict[str, tuple[str, str]] = {}
    for (_, statement_fingerprint), members in sorted(statement_buckets.items()):
        exact: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for occurrence in members:
            exact[cast(str, occurrence["equivalence_fingerprint"])].append(occurrence)
        clusters = [
            sorted(group, key=lambda item: cast(str, item["id"]))
            for _, group in sorted(exact.items())
        ]
        merged = True
        while merged:
            merged = False
            for left_index, left in enumerate(clusters):
                left_booklets = {cast(str, item["booklet_id"]) for item in left}
                for right_index in range(left_index + 1, len(clusters)):
                    right = clusters[right_index]
                    right_booklets = {cast(str, item["booklet_id"]) for item in right}
                    if left_booklets & right_booklets:
                        continue
                    if not all(
                        _alternatives_compatible(left_item, right_item)
                        for left_item in left
                        for right_item in right
                    ):
                        continue
                    clusters[left_index] = sorted(
                        [*left, *right], key=lambda item: cast(str, item["id"])
                    )
                    del clusters[right_index]
                    merged = True
                    break
                if merged:
                    break
        for cluster in clusters:
            fingerprints = sorted(
                {cast(str, item["equivalence_fingerprint"]) for item in cluster}
            )
            if len(fingerprints) == 1:
                grouping_fingerprint = fingerprints[0]
                relation_type = "deterministic_fingerprint"
            else:
                grouping_fingerprint = "normalized-content:" + stable_sha256(
                    {
                        "algorithm": QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
                        "statement": statement_fingerprint,
                        "contentFingerprints": fingerprints,
                    }
                )
                relation_type = "normalized_content"
            for occurrence in cluster:
                result[cast(str, occurrence["id"])] = (
                    grouping_fingerprint,
                    relation_type,
                )
    return result


def rebuild_equivalence_groups(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    contest_id: str | None,
    changed_at: str,
    question_ids: set[str] | None = None,
) -> tuple[Counter[str], list[dict[str, Any]]]:
    parameters: list[str] = []
    conditions: list[str] = []
    if contest_id is not None:
        conditions.append("contest_id = ?")
        parameters.append(contest_id)
    if question_ids is not None:
        if not question_ids:
            occurrences = []
        else:
            placeholders = ",".join("?" for _ in question_ids)
            seed_rows = connection.execute(
                f"SELECT * FROM question_occurrences WHERE question_id IN ({placeholders})",  # noqa: S608
                tuple(sorted(question_ids)),
            ).fetchall()
            boundary_names = (
                "contest_id",
                "application_id",
                "role_id",
                "stage_id",
                "shift_id",
                "content_kind",
            )
            complete_boundaries = {
                tuple(cast(str, row[name]) for name in boundary_names)
                for row in seed_rows
                if all(row[name] is not None for name in boundary_names)
            }
            boundary_clauses: list[str] = []
            boundary_parameters: list[str] = []
            for scope_boundary in sorted(complete_boundaries):
                boundary_clauses.append(
                    "(" + " AND ".join(f"{name}=?" for name in boundary_names) + ")"
                )
                boundary_parameters.extend(scope_boundary)
            direct_clause = f"question_id IN ({placeholders})"  # noqa: S608
            scope_clause = direct_clause
            if boundary_clauses:
                scope_clause += " OR " + " OR ".join(boundary_clauses)
            scoped_parameters = [*sorted(question_ids), *boundary_parameters]
            if contest_id is not None:
                scope_clause = f"contest_id=? AND ({scope_clause})"
                scoped_parameters.insert(0, contest_id)
            occurrences = connection.execute(
                "SELECT * FROM question_occurrences WHERE "
                + scope_clause
                + " ORDER BY id",
                tuple(scoped_parameters),
            ).fetchall()
    else:
        clause = "" if not conditions else "WHERE " + " AND ".join(conditions)
        occurrences = connection.execute(
            "SELECT * FROM question_occurrences " + clause + " ORDER BY id",
            tuple(parameters),
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
    content_groupings = _content_groupings(eligible)
    statement_groups: dict[tuple[tuple[str, ...], str], set[str]] = defaultdict(set)
    grouped: dict[tuple[tuple[str, ...], str], list[sqlite3.Row]] = defaultdict(list)
    relation_types: dict[tuple[tuple[str, ...], str], str] = {}
    for occurrence in eligible:
        boundary: tuple[str, ...] = _boundary(occurrence)
        equivalence, relation_type = content_groupings.get(
            cast(str, occurrence["id"]),
            (
                cast(str, occurrence["equivalence_fingerprint"]),
                "deterministic_fingerprint",
            ),
        )
        statement = cast(str, occurrence["statement_fingerprint"])
        grouped[(boundary, equivalence)].append(occurrence)
        relation_types[(boundary, equivalence)] = relation_type
        statement_groups[(boundary, statement)].add(equivalence)
    totals: Counter[str] = Counter()
    conflicts: list[dict[str, Any]] = []
    for (boundary, grouping_fingerprint), members in sorted(
        grouped.items(), key=lambda item: item[0]
    ):
        relation_type = relation_types[(boundary, grouping_fingerprint)]
        collision = len(
            statement_groups[(boundary, cast(str, members[0]["statement_fingerprint"]))]
        ) > 1
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
    parameters = (
        (QUESTION_EQUIVALENCE_ALGORITHM_VERSION,)
        if contest_id is None
        else (QUESTION_EQUIVALENCE_ALGORITHM_VERSION, contest_id)
    )
    rows = connection.execute(
        "SELECT q.id FROM questions q JOIN documents d ON d.id = q.document_id "
        "LEFT JOIN question_occurrences o ON o.question_id = q.id "
        "WHERE (o.id IS NULL OR o.source_updated_at != q.updated_at "
        "OR o.algorithm_version != ?) "
        + clause
        + " ORDER BY q.id",
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
    question_ids: set[str] | None = None,
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
        if question_ids is not None:
            pending = [question_id for question_id in pending if question_id in question_ids]
        selected = pending[:limit] if limit is not None else pending
        for question_id in selected:
            sync_question_occurrence(connection, question_id, changed_at=changed_at)
        report.occurrences_analyzed = len(selected)
        remaining = _pending_questions(connection, contest_id)
        if question_ids is not None:
            remaining = [question_id for question_id in remaining if question_id in question_ids]
        totals, conflicts = rebuild_equivalence_groups(
            connection,
            run_id=effective_run_id,
            contest_id=contest_id,
            changed_at=changed_at,
            question_ids=question_ids,
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
               g.has_statement_variants,
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
                               AND freshness_link.algorithm_version = 'semantic-association-v3'
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
        "hasStatementVariants": bool(row["has_statement_variants"]),
        "canonicalQuestionId": row["canonical_question_id"],
        "representativeOccurrenceId": row["representative_occurrence_id"],
        "isRepresentative": bool(
            row["occurrence_id"]
            and row["occurrence_id"] == row["representative_occurrence_id"]
        ),
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
        SELECT cq.id, cq.group_id, o.id AS occurrence_id,
               q.payload_json, q.classification_json, q.status
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
    occurrences = connection.execute(
        """
        SELECT o.id, o.question_id, q.payload_json, q.classification_json
        FROM question_group_occurrences go
        JOIN question_occurrences o ON o.id = go.occurrence_id
        JOIN questions q ON q.id = o.question_id
        WHERE go.group_id = ? AND go.status = 'active'
        ORDER BY o.id
        """,
        (row["group_id"],),
    ).fetchall()
    representative = next(
        (item for item in occurrences if item["id"] == row["occurrence_id"]),
        None,
    )
    if representative is None:
        return
    _propagate_editorial_fields(
        connection, representative, occurrences, changed_at=changed_at
    )


def _protected_classification_priority(value: dict[str, Any]) -> int:
    source = str(value.get("source") or "").casefold()
    evidence = str(value.get("evidence") or "").casefold()
    if source == "human_review" or "revisão humana" in evidence:
        return 2
    if source == "ai_suggestion":
        return 1
    return 0


def recover_canonical_editorial_classifications(
    connection: sqlite3.Connection, *, changed_at: str
) -> dict[str, int]:
    """Restore protected editorial values left on copies to their representative."""

    fields = (
        ("discipline", "discipline"),
        ("matter", "subject"),
        ("subject", "topic"),
        ("level", "level"),
    )
    groups = connection.execute(
        """
        SELECT g.id, cq.id AS canonical_question_id,
               g.representative_occurrence_id
        FROM question_equivalence_groups g
        LEFT JOIN canonical_questions cq ON cq.group_id = g.id
        WHERE g.status != 'rejected' AND g.representative_occurrence_id IS NOT NULL
        ORDER BY g.id
        """
    ).fetchall()
    report = {
        "groupsScanned": len(groups),
        "groupsRecovered": 0,
        "fieldsRecovered": 0,
        "conflictingGroups": 0,
    }
    for group in groups:
        occurrences = connection.execute(
            """
            SELECT o.id, o.question_id, q.payload_json, q.classification_json
            FROM question_group_occurrences go
            JOIN question_occurrences o ON o.id = go.occurrence_id
            JOIN questions q ON q.id = o.question_id
            WHERE go.group_id = ? AND go.status = 'active'
            ORDER BY o.id
            """,
            (group["id"],),
        ).fetchall()
        representative = next(
            (
                item
                for item in occurrences
                if item["id"] == group["representative_occurrence_id"]
            ),
            None,
        )
        if representative is None:
            continue
        representative_payload = json.loads(cast(str, representative["payload_json"]))
        representative_classification = json.loads(
            cast(str, representative["classification_json"])
        )
        before_payload = canonical_json(representative_payload)
        before_classification = canonical_json(representative_classification)
        recovered_fields: list[str] = []
        conflict_fields: list[str] = []
        for payload_field, classification_field in fields:
            candidates: list[tuple[int, str, dict[str, Any]]] = []
            for occurrence in occurrences:
                classification = json.loads(
                    cast(str, occurrence["classification_json"])
                )
                value = classification.get(classification_field)
                if not isinstance(value, dict) or value.get("value") in {None, ""}:
                    continue
                priority = _protected_classification_priority(value)
                if priority:
                    candidates.append((priority, str(value["value"]), value))
            if not candidates:
                continue
            highest = max(item[0] for item in candidates)
            preferred = [item for item in candidates if item[0] == highest]
            distinct = {item[1].strip().casefold() for item in preferred}
            if len(distinct) != 1:
                conflict_fields.append(payload_field)
                continue
            _, selected_text, selected_value = preferred[0]
            current = representative_classification.get(classification_field)
            current_priority = (
                _protected_classification_priority(current)
                if isinstance(current, dict)
                else 0
            )
            current_text = (
                str(current.get("value") or "") if isinstance(current, dict) else ""
            )
            if current_priority > highest or (
                current_priority == highest
                and current_text.strip().casefold() == selected_text.strip().casefold()
            ):
                continue
            representative_payload[payload_field] = selected_value["value"]
            representative_classification[classification_field] = selected_value
            recovered_fields.append(payload_field)

        if conflict_fields:
            report["conflictingGroups"] += 1
            reason = "Classificações protegidas conflitantes: " + ", ".join(conflict_fields)
            connection.execute(
                "UPDATE question_equivalence_groups SET status='needs_review',reason=?,"
                "updated_at=? WHERE id=?",
                (reason, changed_at, group["id"]),
            )
            if group["canonical_question_id"] is not None:
                connection.execute(
                    "UPDATE canonical_questions SET editorial_status='blocked',updated_at=? "
                    "WHERE id=?",
                    (changed_at, group["canonical_question_id"]),
                )
            connection.execute(
                "INSERT INTO question_equivalence_review_queue "
                "(group_id,run_id,status,reason,occurrence_ids_json,created_at,updated_at) "
                "VALUES (?,NULL,'pending',?,?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET status='pending',reason=excluded.reason,"
                "occurrence_ids_json=excluded.occurrence_ids_json,updated_at=excluded.updated_at",
                (
                    group["id"],
                    reason,
                    canonical_json([item["id"] for item in occurrences]),
                    changed_at,
                    changed_at,
                ),
            )
            conflict_after = {
                "status": "needs_review",
                "fields": conflict_fields,
                "occurrenceIds": [item["id"] for item in occurrences],
            }
            event_key = stable_sha256(
                {
                    "groupId": group["id"],
                    "action": "canonical_classification_conflict",
                    "after": conflict_after,
                }
            )
            connection.execute(
                "INSERT OR IGNORE INTO question_equivalence_events "
                "(event_key,group_id,canonical_question_id,action,actor,algorithm_version,"
                "after_json,reason,created_at) VALUES "
                "(?,?,?,'canonical_classification_conflict','system',?,?,?,?)",
                (
                    event_key,
                    group["id"],
                    group["canonical_question_id"],
                    QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
                    canonical_json(conflict_after),
                    reason,
                    changed_at,
                ),
            )
            continue
        if not recovered_fields:
            continue

        after_payload = canonical_json(representative_payload)
        after_classification = canonical_json(representative_classification)
        connection.execute(
            "UPDATE questions SET payload_json=?,classification_json=?,updated_at=? WHERE id=?",
            (
                after_payload,
                after_classification,
                changed_at,
                representative["question_id"],
            ),
        )
        connection.execute(
            "UPDATE question_occurrences SET payload_json=?,classification_json=?,"
            "source_updated_at=?,updated_at=? WHERE id=?",
            (
                after_payload,
                after_classification,
                changed_at,
                changed_at,
                representative["id"],
            ),
        )
        if group["canonical_question_id"] is not None:
            connection.execute(
                "UPDATE canonical_questions SET payload_json=?,classification_json=?,"
                "editorial_version=editorial_version+1,updated_at=? WHERE id=?",
                (
                    after_payload,
                    after_classification,
                    changed_at,
                    group["canonical_question_id"],
                ),
            )
        refreshed = connection.execute(
            """
            SELECT o.id, o.question_id, q.payload_json, q.classification_json
            FROM question_group_occurrences go
            JOIN question_occurrences o ON o.id = go.occurrence_id
            JOIN questions q ON q.id = o.question_id
            WHERE go.group_id = ? AND go.status = 'active'
            ORDER BY o.id
            """,
            (group["id"],),
        ).fetchall()
        refreshed_representative = next(
            item for item in refreshed if item["id"] == representative["id"]
        )
        _propagate_editorial_fields(
            connection, refreshed_representative, refreshed, changed_at=changed_at
        )
        event_after = {
            "questionId": representative["question_id"],
            "fields": recovered_fields,
            "classification": representative_classification,
        }
        event_key = stable_sha256(
            {
                "groupId": group["id"],
                "action": "canonical_classification_recovered",
                "after": event_after,
            }
        )
        connection.execute(
            "INSERT OR IGNORE INTO question_equivalence_events "
            "(event_key,group_id,canonical_question_id,action,actor,algorithm_version,"
            "before_json,after_json,reason,created_at) "
            "VALUES (?,?,?,'canonical_classification_recovered','system',?,?,?,?,?)",
            (
                event_key,
                group["id"],
                group["canonical_question_id"],
                QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
                canonical_json(
                    {
                        "payload": json.loads(before_payload),
                        "classification": json.loads(before_classification),
                    }
                ),
                canonical_json(event_after),
                "Classificação protegida recuperada de uma cópia equivalente.",
                changed_at,
            ),
        )
        connection.execute(
            "INSERT INTO audit_log "
            "(question_id,action,actor,created_at,before_json,after_json,notes) "
            "VALUES (?,'canonical_classification_recovered','system',?,?,?,?)",
            (
                representative["question_id"],
                changed_at,
                canonical_json(
                    {
                        "payload": json.loads(before_payload),
                        "classification": json.loads(before_classification),
                    }
                ),
                canonical_json(event_after),
                "Classificação protegida recuperada de uma cópia equivalente.",
            ),
        )
        report["groupsRecovered"] += 1
        report["fieldsRecovered"] += len(recovered_fields)
    return report


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
