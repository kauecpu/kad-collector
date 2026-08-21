from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, cast

from .document_contract import NormalizedDocument
from .semantic_identity import (
    IDENTITY_ALGORITHM_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    IdentityResolution,
    canonical_json,
    stable_sha256,
)

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


@dataclass(frozen=True)
class ObservationClaim:
    observation_id: str
    origin_key: str
    exact_duplicate: bool
    document_id: str | None
    document_version_id: str | None
    resolution_status: str


def persist_identity_correction(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    profile: DocumentSemanticProfile,
    actor: str,
    corrected_at: str,
) -> tuple[IdentityResolution, bool]:
    """Move one operational version to a human-corrected canonical identity."""
    row = connection.execute(
        "SELECT d.document_version_id, d.semantic_resolution, v.identity_key, "
        "v.document_role, v.profile_json, v.content_sha256, v.version_number, "
        "v.predecessor_version_id FROM documents d "
        "JOIN document_versions v ON v.id = d.document_version_id WHERE d.id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise ValueError("documento resolvido não encontrado")
    if profile.identity_key is None:
        raise ValueError("identidade semântica insuficiente")
    if profile.has_conflict:
        raise ValueError("perfil semântico conflitante")

    version_id = cast(str, row["document_version_id"])
    collision = connection.execute(
        "SELECT id FROM document_versions WHERE identity_key = ? AND document_role = ? "
        "AND content_sha256 = ? AND id != ?",
        (
            profile.identity_key,
            profile.document_role,
            profile.content_fingerprint.sha256,
            version_id,
        ),
    ).fetchone()
    if collision is not None:
        raise ValueError("correção colide com versão existente")

    identity_json = canonical_json(profile.identity.model_dump(mode="json"))
    evidence = {
        name: getattr(profile.identity, name).model_dump(mode="json")
        for name in ExamSemanticIdentity.model_fields
    }
    profile_json = canonical_json(profile.model_dump(mode="json"))
    coverage_json = canonical_json(profile.coverage.model_dump(mode="json"))
    changed = (
        row["identity_key"] != profile.identity_key
        or row["document_role"] != profile.document_role
        or row["profile_json"] != profile_json
    )
    version_number = int(row["version_number"])
    predecessor_version_id = cast(str | None, row["predecessor_version_id"])
    if changed and (
        row["identity_key"] != profile.identity_key
        or row["document_role"] != profile.document_role
    ):
        predecessor = connection.execute(
            "SELECT id, version_number FROM document_versions WHERE identity_key = ? "
            "AND document_role = ? AND id != ? ORDER BY version_number DESC, id DESC LIMIT 1",
            (profile.identity_key, profile.document_role, version_id),
        ).fetchone()
        if predecessor is None:
            predecessor_version_id = None
            version_number = 1
        else:
            predecessor_version_id = cast(str, predecessor["id"])
            version_number = int(predecessor["version_number"]) + 1

    if changed:
        old_identity_key = cast(str, row["identity_key"])
        old_document_role = cast(str, row["document_role"])
        connection.execute(
            "INSERT INTO semantic_identities (identity_key, schema_version, algorithm_version, "
            "identity_json, evidence_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(identity_key) DO UPDATE SET schema_version = excluded.schema_version, "
            "algorithm_version = excluded.algorithm_version, "
            "identity_json = excluded.identity_json, "
            "evidence_json = excluded.evidence_json, updated_at = excluded.updated_at",
            (
                profile.identity_key,
                SEMANTIC_SCHEMA_VERSION,
                profile.algorithm_version,
                identity_json,
                canonical_json(evidence),
                corrected_at,
                corrected_at,
            ),
        )
        connection.execute(
            "UPDATE document_versions SET identity_key = ?, document_role = ?, "
            "answer_key_state = ?, coverage_json = ?, profile_json = ?, version_number = ?, "
            "predecessor_version_id = ?, updated_at = ? WHERE id = ?",
            (
                profile.identity_key,
                profile.document_role,
                profile.answer_key_state,
                coverage_json,
                profile_json,
                version_number,
                predecessor_version_id,
                corrected_at,
                version_id,
            ),
        )
        connection.execute(
            "UPDATE documents SET document_version_id = ? WHERE document_version_id = ?",
            (version_id, version_id),
        )
        connection.execute(
            "UPDATE document_observations SET document_version_id = ? "
            "WHERE document_version_id = ?",
            (version_id, version_id),
        )
        if (
            old_identity_key != profile.identity_key
            or old_document_role != profile.document_role
        ):
            remaining = connection.execute(
                "SELECT id FROM document_versions WHERE identity_key = ? "
                "AND document_role = ? ORDER BY version_number, id",
                (old_identity_key, old_document_role),
            ).fetchall()
            predecessor_id: str | None = None
            for ordinal, remaining_version in enumerate(remaining, start=1):
                remaining_id = cast(str, remaining_version["id"])
                connection.execute(
                    "UPDATE document_versions SET version_number = ?, "
                    "predecessor_version_id = ?, updated_at = ? WHERE id = ?",
                    (ordinal, predecessor_id, corrected_at, remaining_id),
                )
                predecessor_id = remaining_id

    payload = {
        "oldIdentityKey": row["identity_key"],
        "newIdentityKey": profile.identity_key,
        "oldValues": json.loads(cast(str, row["profile_json"]))["identity"],
        "newValues": profile.identity.model_dump(mode="json"),
        "evidence": {
            name: [
                item.model_dump(mode="json")
                for item in getattr(profile.identity, name).evidence
            ]
            for name in ExamSemanticIdentity.model_fields
        },
        "algorithmVersion": profile.algorithm_version,
        "documentRole": profile.document_role,
    }
    event_key = stable_sha256(
        {
            "action": "identity_corrected",
            "document_version_id": version_id,
            "actor": actor,
            "identity_key": profile.identity_key,
            "document_role": profile.document_role,
            "identity": profile.identity.model_dump(mode="json"),
            "coverage": profile.coverage.model_dump(mode="json"),
        }
    )
    event_created = connection.execute(
        "INSERT OR IGNORE INTO document_identity_events (event_key, document_id, "
        "document_version_id, action, actor, algorithm_version, payload_json, created_at) "
        "VALUES (?, ?, ?, 'identity_corrected', ?, ?, ?, ?)",
        (
            event_key,
            document_id,
            version_id,
            actor,
            profile.algorithm_version,
            canonical_json(payload),
            corrected_at,
        ),
    ).rowcount == 1
    outcome = cast(Any, row["semantic_resolution"] or "new_identity")
    return (
        IdentityResolution(
            outcome=outcome,
            profile=profile,
            document_version_id=version_id,
            predecessor_version_id=predecessor_version_id,
            version_number=version_number,
            reason="identidade corrigida por revisão humana",
        ),
        changed or event_created,
    )


def record_question_lineage(
    connection: sqlite3.Connection,
    *,
    predecessor_version_id: str,
    successor_version_id: str,
    question_number: int,
    predecessor_question_id: str | None,
    successor_question_id: str | None,
    comparison: str,
    content_equal: bool,
    answer_equal: bool,
    reason: str,
    recorded_at: str,
) -> tuple[dict[str, Any], bool]:
    """Record one canonical lineage fact and return the winning row."""
    lineage_id = stable_sha256(
        {
            "predecessor_version_id": predecessor_version_id,
            "successor_version_id": successor_version_id,
            "question_number": question_number,
        }
    )
    created = False
    try:
        connection.execute(
            "INSERT INTO question_lineage ("
            "id, predecessor_version_id, successor_version_id, question_number, "
            "predecessor_question_id, successor_question_id, comparison, content_equal, "
            "answer_equal, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lineage_id,
                predecessor_version_id,
                successor_version_id,
                question_number,
                predecessor_question_id,
                successor_question_id,
                comparison,
                int(content_equal),
                int(answer_equal),
                reason,
                recorded_at,
            ),
        )
        created = True
    except sqlite3.IntegrityError:
        pass
    row = connection.execute(
        "SELECT * FROM question_lineage WHERE predecessor_version_id = ? "
        "AND successor_version_id = ? AND question_number = ?",
        (predecessor_version_id, successor_version_id, question_number),
    ).fetchone()
    if row is None:
        raise RuntimeError("linhagem concorrente não pôde ser recarregada")
    return dict(row), created


def _observation_origin(document: NormalizedDocument) -> dict[str, object]:
    return {
        "entry_method": document.entry_method,
        "original_url": document.original_url,
        "resolved_url": document.resolved_url,
        "source_page_url": document.source_page_url,
        "title": document.title,
        "external_id": document.external_id,
        "source_id": document.source_id,
        "metadata": document.metadata,
    }


def claim_document_observation(
    connection: sqlite3.Connection,
    document: NormalizedDocument,
    observed_at: str,
) -> ObservationClaim:
    """Claim a binary document inside the caller's write transaction."""
    origin = _observation_origin(document)
    origin_json = canonical_json(origin)
    origin_key = stable_sha256(origin)
    row = connection.execute(
        "SELECT id, document_id, document_version_id, resolution_status "
        "FROM document_observations WHERE binary_sha256 = ?",
        (document.sha256,),
    ).fetchone()
    if row is None:
        observation_id = stable_sha256({"binary_sha256": document.sha256})
        connection.execute(
            "INSERT INTO document_observations ("
            "id, binary_sha256, size_bytes, resolution_status, first_seen_at, last_seen_at"
            ") VALUES (?, ?, ?, 'observed', ?, ?)",
            (
                observation_id,
                document.sha256,
                document.size_bytes,
                observed_at,
                observed_at,
            ),
        )
        connection.execute(
            "INSERT INTO document_observation_origins ("
            "observation_id, origin_key, normalized_json, first_seen_at, last_seen_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (observation_id, origin_key, origin_json, observed_at, observed_at),
        )
        event_payload = {
            "binarySha256": document.sha256,
            "observationId": observation_id,
            "origin": {"originKey": origin_key, "normalized": origin},
        }
        connection.execute(
            "INSERT OR IGNORE INTO document_identity_events ("
            "event_key, action, actor, algorithm_version, payload_json, created_at"
            ") VALUES (?, 'observed', 'system', ?, ?, ?)",
            (
                stable_sha256(
                    {
                        "action": "observed",
                        "observation_id": observation_id,
                        "origin_key": origin_key,
                    }
                ),
                IDENTITY_ALGORITHM_VERSION,
                canonical_json(event_payload),
                observed_at,
            ),
        )
        return ObservationClaim(
            observation_id=observation_id,
            origin_key=origin_key,
            exact_duplicate=False,
            document_id=None,
            document_version_id=None,
            resolution_status="observed",
        )

    observation_id = cast(str, row["id"])
    connection.execute(
        "UPDATE document_observations SET last_seen_at = ? WHERE id = ?",
        (observed_at, observation_id),
    )
    connection.execute(
        "INSERT INTO document_observation_origins ("
        "observation_id, origin_key, normalized_json, first_seen_at, last_seen_at"
        ") VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(observation_id, origin_key) DO UPDATE SET "
        "last_seen_at = excluded.last_seen_at",
        (observation_id, origin_key, origin_json, observed_at, observed_at),
    )
    event_payload = {
        "binarySha256": document.sha256,
        "existingDocumentVersionId": row["document_version_id"],
        "observationId": observation_id,
        "origin": {"originKey": origin_key, "normalized": origin},
    }
    connection.execute(
        "INSERT OR IGNORE INTO document_identity_events ("
        "event_key, document_id, document_version_id, action, actor, algorithm_version, "
        "payload_json, created_at"
        ") VALUES (?, ?, ?, 'exact_duplicate', 'system', ?, ?, ?)",
        (
            stable_sha256(
                {
                    "action": "exact_duplicate",
                    "observation_id": observation_id,
                    "origin_key": origin_key,
                }
            ),
            row["document_id"],
            row["document_version_id"],
            IDENTITY_ALGORITHM_VERSION,
            canonical_json(event_payload),
            observed_at,
        ),
    )
    return ObservationClaim(
        observation_id=observation_id,
        origin_key=origin_key,
        exact_duplicate=True,
        document_id=cast(str | None, row["document_id"]),
        document_version_id=cast(str | None, row["document_version_id"]),
        resolution_status=cast(str, row["resolution_status"]),
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


def active_answer_key_candidates(
    connection: sqlite3.Connection, exam_version_id: str | None = None
) -> list[dict[str, Any]]:
    """Return active keys in stable order, optionally scoped to one exam version."""
    exam_profile: dict[str, Any] | None = None
    if exam_version_id is not None:
        exam_row = connection.execute(
            "SELECT profile_json FROM document_versions "
            "WHERE id = ? AND document_role = 'exam'",
            (exam_version_id,),
        ).fetchone()
        if exam_row is None:
            return []
        exam_profile = json.loads(cast(str, exam_row["profile_json"]))
    join_scope = (
        "AND l.exam_version_id = ?" if exam_version_id is not None
        else "AND l.id = (SELECT MIN(active.id) FROM document_links active "
        "WHERE active.answer_key_version_id = v.id AND active.status = 'active')"
    )
    parameters: tuple[str, ...] = () if exam_version_id is None else (exam_version_id,)
    rows = connection.execute(
        """
        SELECT l.id AS link_id, l.exam_version_id, v.id AS answer_key_version_id,
               l.decision_json, l.algorithm_version, l.predecessor_link_id,
               v.identity_key, v.answer_key_state, v.coverage_json, v.profile_json,
               v.predecessor_version_id, v.version_number
        FROM document_versions v
        LEFT JOIN document_links l ON l.answer_key_version_id = v.id AND l.status = 'active' """
        + join_scope
        + " WHERE v.document_role = 'answer_key' "
        "ORDER BY v.identity_key, v.version_number, v.id",
        parameters,
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        profile = json.loads(cast(str, row["profile_json"]))
        if exam_profile is not None and not (
            _profiles_share_core(exam_profile, profile)
            and _profiles_match_scope(exam_profile, profile)
        ):
            continue
        candidates.append({
            "link_id": row["link_id"], "exam_version_id": row["exam_version_id"],
            "answer_key_version_id": row["answer_key_version_id"],
            "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
            "algorithm_version": row["algorithm_version"],
            "predecessor_link_id": row["predecessor_link_id"],
            "identity_key": row["identity_key"], "answer_key_state": row["answer_key_state"],
            "coverage": json.loads(row["coverage_json"]),
            "profile": profile,
            "predecessor_version_id": row["predecessor_version_id"],
            "version_number": row["version_number"],
        })
    return candidates


def record_document_link(
    connection: sqlite3.Connection,
    exam_version_id: str,
    answer_key_version_id: str,
    decision: Any,
    recorded_at: str,
) -> str | None:
    """Persist one selected association, atomically replacing a prior active link."""
    selected = getattr(decision, "selected_version_id", None)
    if selected != answer_key_version_id or getattr(decision, "outcome", None) != "selected":
        return None
    payload = decision.model_dump(mode="json") if hasattr(decision, "model_dump") else decision
    decision_json = canonical_json(payload)
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute(
            "SELECT id, answer_key_version_id, decision_json FROM document_links "
            "WHERE exam_version_id = ? AND status = 'active'",
            (exam_version_id,),
        ).fetchone()
        if current is not None and current["answer_key_version_id"] == answer_key_version_id:
            if own_transaction:
                connection.commit()
            return cast(str, current["id"])
        predecessor = cast(str | None, current["id"] if current is not None else None)
        if current is not None:
            connection.execute(
                "UPDATE document_links SET status = 'superseded', updated_at = ? WHERE id = ?",
                (recorded_at, current["id"]),
            )
            event = {
                "linkId": current["id"], "examVersionId": exam_version_id,
                "answerKeyVersionId": current["answer_key_version_id"],
            }
            connection.execute(
                "INSERT OR IGNORE INTO document_identity_events "
                "(event_key, document_version_id, action, actor, algorithm_version, "
                "payload_json, created_at) "
                "VALUES (?, ?, 'association_superseded', 'system', ?, ?, ?)",
                (stable_sha256({"action": "association_superseded", "link_id": current["id"]}),
                 exam_version_id, decision.algorithm_version, canonical_json(event), recorded_at),
            )
        link_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"kad:document-link:{exam_version_id}:{answer_key_version_id}:{predecessor or 'root'}",
        ))
        connection.execute(
            "INSERT INTO document_links (id, exam_version_id, answer_key_version_id, status, "
            "decision_json, "
            "algorithm_version, predecessor_link_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            (link_id, exam_version_id, answer_key_version_id, decision_json,
             decision.algorithm_version, predecessor, recorded_at, recorded_at),
        )
        event = {"linkId": link_id, "examVersionId": exam_version_id,
                 "answerKeyVersionId": answer_key_version_id, "decision": payload}
        connection.execute(
            "INSERT OR IGNORE INTO document_identity_events "
            "(event_key, document_version_id, action, actor, algorithm_version, "
            "payload_json, created_at) "
            "VALUES (?, ?, 'association_selected', 'system', ?, ?, ?)",
            (stable_sha256({"action": "association_selected", "link_id": link_id}),
             exam_version_id, decision.algorithm_version, canonical_json(event), recorded_at),
        )
        if own_transaction:
            connection.commit()
        return link_id
    except Exception:
        if own_transaction:
            connection.rollback()
        raise


def record_corrected_document_link(
    connection: sqlite3.Connection,
    exam_version_id: str,
    decision: Any,
    recorded_at: str,
) -> str | None:
    """Reject the stale active link and persist the correction's selected link, if any."""
    current = connection.execute(
        "SELECT id, answer_key_version_id, decision_json FROM document_links "
        "WHERE exam_version_id = ? AND status = 'active'",
        (exam_version_id,),
    ).fetchone()
    selected = cast(str | None, getattr(decision, "selected_version_id", None))
    decision_json = canonical_json(decision.model_dump(mode="json"))
    if (
        current is not None
        and current["answer_key_version_id"] == selected
        and current["decision_json"] == decision_json
    ):
        return cast(str, current["id"])
    predecessor = cast(str | None, current["id"] if current is not None else None)
    if current is not None:
        connection.execute(
            "UPDATE document_links SET status = 'rejected', updated_at = ? WHERE id = ?",
            (recorded_at, current["id"]),
        )
        payload = {
            "linkId": current["id"],
            "examVersionId": exam_version_id,
            "answerKeyVersionId": current["answer_key_version_id"],
            "reason": decision.reason,
            "decision": decision.model_dump(mode="json"),
        }
        connection.execute(
            "INSERT OR IGNORE INTO document_identity_events (event_key, document_version_id, "
            "action, actor, algorithm_version, payload_json, created_at) "
            "VALUES (?, ?, 'association_rejected', 'system', ?, ?, ?)",
            (
                stable_sha256(
                    {"action": "association_rejected", "link_id": current["id"],
                     "decision": decision.model_dump(mode="json")}
                ),
                exam_version_id,
                decision.algorithm_version,
                canonical_json(payload),
                recorded_at,
            ),
        )
    if selected is None or getattr(decision, "outcome", None) != "selected":
        return None
    link_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"kad:document-link:{exam_version_id}:{selected}:{predecessor or 'root'}",
        )
    )
    connection.execute(
        "INSERT INTO document_links (id, exam_version_id, answer_key_version_id, status, "
        "decision_json, algorithm_version, predecessor_link_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)",
        (
            link_id,
            exam_version_id,
            selected,
            decision_json,
            decision.algorithm_version,
            predecessor,
            recorded_at,
            recorded_at,
        ),
    )
    payload = {
        "linkId": link_id,
        "examVersionId": exam_version_id,
        "answerKeyVersionId": selected,
        "decision": decision.model_dump(mode="json"),
    }
    connection.execute(
        "INSERT OR IGNORE INTO document_identity_events (event_key, document_version_id, "
        "action, actor, algorithm_version, payload_json, created_at) "
        "VALUES (?, ?, 'association_selected', 'system', ?, ?, ?)",
        (
            stable_sha256({"action": "association_selected", "link_id": link_id}),
            exam_version_id,
            decision.algorithm_version,
            canonical_json(payload),
            recorded_at,
        ),
    )
    return link_id


def affected_exam_documents_after_identity_correction(
    connection: sqlite3.Connection,
    *,
    corrected_version_id: str,
    old_profile: DocumentSemanticProfile,
    new_profile: DocumentSemanticProfile,
) -> list[dict[str, Any]]:
    """Return one operational document for every exam in the old/new correction scope."""
    key_profiles = [
        profile.model_dump(mode="json")
        for profile in (old_profile, new_profile)
        if profile.document_role == "answer_key"
    ]
    rows = connection.execute(
        "SELECT v.id AS exam_version_id, v.profile_json, "
        "(SELECT d.id FROM documents d WHERE d.document_version_id = v.id "
        "ORDER BY (SELECT COUNT(*) FROM questions q WHERE q.document_id = d.id) DESC, "
        "d.created_at, d.id LIMIT 1) AS document_id, "
        "EXISTS(SELECT 1 FROM document_links l WHERE l.exam_version_id = v.id "
        "AND l.answer_key_version_id = ? AND l.status = 'active') AS linked_to_corrected "
        "FROM document_versions v WHERE v.document_role = 'exam' ORDER BY v.id",
        (corrected_version_id,),
    ).fetchall()
    affected: list[dict[str, Any]] = []
    for row in rows:
        exam_profile = json.loads(cast(str, row["profile_json"]))
        is_corrected_exam = (
            row["exam_version_id"] == corrected_version_id
            and new_profile.document_role == "exam"
        )
        matches_key_scope = any(
            _profiles_share_core(exam_profile, key_profile)
            and _profiles_match_scope(exam_profile, key_profile)
            for key_profile in key_profiles
        )
        if is_corrected_exam or bool(row["linked_to_corrected"]) or matches_key_scope:
            affected.append(dict(row))
    return affected


def exam_documents_affected_by_answer_key(
    connection: sqlite3.Connection, answer_key_version_id: str
) -> list[dict[str, Any]]:
    key_row = connection.execute(
        "SELECT profile_json FROM document_versions WHERE id = ? AND document_role = 'answer_key'",
        (answer_key_version_id,),
    ).fetchone()
    if key_row is None:
        return []
    key_profile = json.loads(cast(str, key_row["profile_json"]))

    rows = connection.execute(
        """
        SELECT d.*, v.id AS exam_version_id, v.identity_key, v.profile_json,
               v.coverage_json, v.answer_key_state
        FROM document_versions v JOIN documents d ON d.document_version_id = v.id
        WHERE v.document_role = 'exam' ORDER BY v.id, d.id
        """,
    ).fetchall()
    affected: list[dict[str, Any]] = []
    for row in rows:
        profile = json.loads(cast(str, row["profile_json"]))
        if _profiles_share_core(profile, key_profile) and _profiles_match_scope(
            profile, key_profile
        ):
            affected.append(dict(row))
    return affected


def _profiles_share_core(exam_profile: dict[str, Any], key_profile: dict[str, Any]) -> bool:
    exam_identity = exam_profile.get("identity", {})
    key_identity = key_profile.get("identity", {})
    return all(
        key_identity.get(name, {}).get("status") != "known"
        or exam_identity.get(name, {}).get("status") == "known"
        and bool(
            set(key_identity[name].get("normalized_values", ()))
            & set(exam_identity[name].get("normalized_values", ()))
        )
        for name in ("board", "concurso", "year")
    )


def _profiles_match_scope(exam_profile: dict[str, Any], key_profile: dict[str, Any]) -> bool:
    exam_identity = exam_profile.get("identity", {})
    key_coverage = key_profile.get("coverage", {})
    return all(
        key_coverage.get(name, {}).get("status") != "known"
        or exam_identity.get(name, {}).get("status") == "known"
        and bool(
            set(key_coverage[name].get("normalized_values", ()))
            & set(exam_identity[name].get("normalized_values", ()))
        )
        for name in ("roles", "stage", "turns", "variants")
    )


def _decode_json(value: object) -> object | None:
    return json.loads(cast(str, value)) if value is not None else None


def _count_rows(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
