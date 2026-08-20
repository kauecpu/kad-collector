from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

SEMANTIC_TABLES = frozenset(
    {
        "semantic_identities",
        "document_versions",
        "document_observations",
        "document_observation_origins",
        "document_links",
        "question_lineage",
        "document_identity_events",
    }
)


def initialize_semantic_schema(connection: sqlite3.Connection) -> None:
    """Add the semantic registry without altering existing collector records."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS semantic_identities (
            identity_key TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            algorithm_version TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_versions (
            id TEXT PRIMARY KEY,
            identity_key TEXT NOT NULL REFERENCES semantic_identities(identity_key),
            document_role TEXT NOT NULL,
            answer_key_state TEXT NOT NULL,
            coverage_json TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            content_normalizer_version TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            predecessor_version_id TEXT REFERENCES document_versions(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(identity_key, document_role, content_sha256),
            UNIQUE(identity_key, document_role, version_number)
        );
        CREATE TABLE IF NOT EXISTS document_observations (
            id TEXT PRIMARY KEY,
            binary_sha256 TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            document_id TEXT REFERENCES documents(id),
            document_version_id TEXT REFERENCES document_versions(id),
            resolution_status TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_observation_origins (
            observation_id TEXT NOT NULL REFERENCES document_observations(id) ON DELETE CASCADE,
            origin_key TEXT NOT NULL,
            normalized_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY(observation_id, origin_key)
        );
        CREATE TABLE IF NOT EXISTS document_links (
            id TEXT PRIMARY KEY,
            exam_version_id TEXT NOT NULL REFERENCES document_versions(id),
            answer_key_version_id TEXT NOT NULL REFERENCES document_versions(id),
            status TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            predecessor_link_id TEXT REFERENCES document_links(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS question_lineage (
            id TEXT PRIMARY KEY,
            predecessor_version_id TEXT REFERENCES document_versions(id),
            successor_version_id TEXT REFERENCES document_versions(id),
            question_number INTEGER NOT NULL,
            predecessor_question_id TEXT REFERENCES questions(id),
            successor_question_id TEXT REFERENCES questions(id),
            comparison TEXT NOT NULL,
            content_equal INTEGER NOT NULL,
            answer_equal INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_identity_events (
            event_key TEXT PRIMARY KEY,
            document_id TEXT REFERENCES documents(id),
            document_version_id TEXT REFERENCES document_versions(id),
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS document_links_one_active_exam_idx
            ON document_links(exam_version_id) WHERE status = 'active';
        CREATE UNIQUE INDEX IF NOT EXISTS question_lineage_successor_question_idx
            ON question_lineage(successor_question_id) WHERE successor_question_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS question_lineage_version_question_idx
            ON question_lineage(predecessor_version_id, successor_version_id, question_number)
            WHERE predecessor_version_id IS NOT NULL AND successor_version_id IS NOT NULL;
        """
    )
    document_columns = {
        cast(str, row["name"])
        for row in connection.execute("PRAGMA table_info(documents)").fetchall()
    }
    for name in ("document_version_id", "observation_id", "semantic_resolution"):
        if name not in document_columns:
            connection.execute(f"ALTER TABLE documents ADD COLUMN {name} TEXT")  # noqa: S608
    question_columns = {
        cast(str, row["name"])
        for row in connection.execute("PRAGMA table_info(questions)").fetchall()
    }
    if "decision_fingerprint" not in question_columns:
        connection.execute("ALTER TABLE questions ADD COLUMN decision_fingerprint TEXT")


def semantic_document_view(
    connection: sqlite3.Connection, document_id: str
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT d.id AS document_id, d.document_version_id, d.observation_id,
               d.semantic_resolution, dv.identity_key, dv.document_role,
               dv.answer_key_state, dv.version_number, dv.predecessor_version_id,
               si.identity_json, si.evidence_json, dv.coverage_json, dv.profile_json
        FROM documents d
        LEFT JOIN document_versions dv ON dv.id = d.document_version_id
        LEFT JOIN semantic_identities si ON si.identity_key = dv.identity_key
        WHERE d.id = ?
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError("documento nao encontrado")
    payload = dict(row)
    identity_key = payload["identity_key"]
    return {
        "documentId": payload["document_id"],
        "documentVersionId": payload["document_version_id"],
        "observationId": payload["observation_id"],
        "resolution": payload["semantic_resolution"],
        "identityStatus": "known" if identity_key is not None else "unknown",
        "identityKey": identity_key,
        "documentRole": payload["document_role"],
        "answerKeyState": payload["answer_key_state"],
        "versionNumber": payload["version_number"],
        "predecessorVersionId": payload["predecessor_version_id"],
        "identity": _decode_json(payload["identity_json"]),
        "evidence": _decode_json(payload["evidence_json"]),
        "coverage": _decode_json(payload["coverage_json"]),
        "profile": _decode_json(payload["profile_json"]),
    }


def semantic_summary(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS documents,
               SUM(CASE WHEN d.document_version_id IS NULL THEN 1 ELSE 0 END) AS unknown,
               SUM(CASE WHEN d.document_version_id IS NOT NULL THEN 1 ELSE 0 END) AS known
        FROM documents d
        """
    ).fetchone()
    return {
        "documents": int(row["documents"]),
        "known": int(row["known"] or 0),
        "unknown": int(row["unknown"] or 0),
        "conflict": 0,
        "observations": _count_rows(connection, "document_observations"),
        "versions": _count_rows(connection, "document_versions"),
        "events": _count_rows(connection, "document_identity_events"),
    }


def identity_events(connection: sqlite3.Connection, document_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT event_key, document_id, document_version_id, action, actor,
               algorithm_version, payload_json, created_at
        FROM document_identity_events
        WHERE document_id = ?
           OR document_version_id = (
               SELECT document_version_id FROM documents WHERE id = ?
           )
        ORDER BY created_at DESC, event_key DESC
        """,
        (document_id, document_id),
    ).fetchall()
    return [
        {
            "eventKey": row["event_key"],
            "documentId": row["document_id"],
            "documentVersionId": row["document_version_id"],
            "action": row["action"],
            "actor": row["actor"],
            "algorithmVersion": row["algorithm_version"],
            "payload": json.loads(cast(str, row["payload_json"])),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def _decode_json(value: object) -> object | None:
    return json.loads(cast(str, value)) if value is not None else None


def _count_rows(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
