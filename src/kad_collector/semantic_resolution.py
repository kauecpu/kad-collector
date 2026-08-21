from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from .semantic_identity import (
    IDENTITY_ALGORITHM_VERSION,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    IdentityResolution,
    KnownDocumentVersion,
    canonical_json,
    stable_sha256,
)


def decide_document_version(
    profile: DocumentSemanticProfile,
    known_versions: tuple[KnownDocumentVersion, ...] | list[KnownDocumentVersion],
) -> IdentityResolution:
    """Purely classify a profile against versions already known for the registry."""
    if profile.identity_key is None or profile.has_conflict:
        return IdentityResolution(
            outcome="uncertain",
            profile=profile,
            reason="identidade semântica insuficiente ou conflitante",
        )
    same_identity_and_role = [
        item
        for item in known_versions
        if item.identity_key == profile.identity_key and item.document_role == profile.document_role
    ]
    same_content = [
        item
        for item in same_identity_and_role
        if item.content_sha256 == profile.content_fingerprint.sha256
    ]
    if same_content:
        winner = same_content[-1]
        return IdentityResolution(
            outcome="republication",
            profile=profile,
            document_version_id=winner.version_id,
            predecessor_version_id=winner.predecessor_version_id,
            version_number=winner.version_number,
            reason="conteúdo equivalente já registrado",
        )
    if same_identity_and_role:
        predecessor = same_identity_and_role[-1]
        return IdentityResolution(
            outcome="new_version",
            profile=profile,
            predecessor_version_id=predecessor.version_id,
            version_number=predecessor.version_number + 1,
            reason="mesma identidade e papel, conteúdo alterado",
        )
    return IdentityResolution(
        outcome="new_identity",
        profile=profile,
        version_number=1,
        reason="primeira versão para identidade e papel",
    )


def _known_versions(
    connection: sqlite3.Connection, profile: DocumentSemanticProfile
) -> tuple[KnownDocumentVersion, ...]:
    if profile.identity_key is None:
        return ()
    rows = connection.execute(
        "SELECT id, identity_key, document_role, content_sha256, version_number, "
        "predecessor_version_id FROM document_versions "
        "WHERE identity_key = ? AND document_role = ? ORDER BY version_number",
        (profile.identity_key, profile.document_role),
    ).fetchall()
    return tuple(
        KnownDocumentVersion(
            version_id=row["id"],
            identity_key=row["identity_key"],
            document_role=row["document_role"],
            content_sha256=row["content_sha256"],
            version_number=row["version_number"],
            predecessor_version_id=row["predecessor_version_id"],
        )
        for row in rows
    )


def _version_id(profile: DocumentSemanticProfile) -> str:
    assert profile.identity_key is not None
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{profile.identity_key}:{profile.document_role}:{profile.content_fingerprint.sha256}",
        )
    )


def _event(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    version_id: str | None,
    action: str,
    payload: dict[str, Any],
    resolved_at: str,
) -> None:
    key = stable_sha256({"action": action, "document_id": document_id, "version_id": version_id})
    connection.execute(
        "INSERT OR IGNORE INTO document_identity_events "
        "(event_key, document_id, document_version_id, action, actor, algorithm_version, "
        "payload_json, created_at) "
        "VALUES (?, ?, ?, ?, 'system', ?, ?, ?)",
        (
            key,
            document_id,
            version_id,
            action,
            IDENTITY_ALGORITHM_VERSION,
            canonical_json(payload),
            resolved_at,
        ),
    )


def resolve_document_version(
    connection: sqlite3.Connection,
    document_id: str,
    profile: DocumentSemanticProfile,
    resolved_at: str,
) -> IdentityResolution:
    """Resolve and persist one extracted document atomically and idempotently."""
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT semantic_resolution, document_version_id FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if existing is None:
            raise ValueError("documento não encontrado")
        existing_outcome = existing["semantic_resolution"]
        if existing_outcome in {"uncertain", "new_identity", "new_version", "republication"}:
            existing_version = existing["document_version_id"]
            if existing_outcome == "uncertain":
                result = IdentityResolution(
                    outcome="uncertain",
                    profile=profile,
                    reason="identidade semântica insuficiente ou conflitante",
                )
            else:
                version = connection.execute(
                    "SELECT version_number, predecessor_version_id "
                    "FROM document_versions WHERE id = ?",
                    (existing_version,),
                ).fetchone()
                if version is None:
                    raise RuntimeError("documento resolvido aponta para versão ausente")
                result = IdentityResolution(
                    outcome=existing_outcome,
                    profile=profile,
                    document_version_id=existing_version,
                    predecessor_version_id=version["predecessor_version_id"],
                    version_number=version["version_number"],
                    reason="resolução já persistida para este documento",
                )
            if owns_transaction:
                connection.commit()
            return result
        decision = decide_document_version(profile, _known_versions(connection, profile))
        if decision.outcome == "uncertain":
            connection.execute(
                "UPDATE documents SET semantic_resolution = ?, updated_at = ? WHERE id = ?",
                (decision.outcome, resolved_at, document_id),
            )
            connection.execute(
                "UPDATE document_observations SET resolution_status = ? WHERE document_id = ?",
                (decision.outcome, document_id),
            )
            _event(
                connection,
                document_id=document_id,
                version_id=None,
                action="uncertain",
                payload={"reason": decision.reason},
                resolved_at=resolved_at,
            )
            if owns_transaction:
                connection.commit()
            return decision

        assert profile.identity_key is not None
        identity_json = canonical_json(profile.identity.model_dump(mode="json"))
        evidence = {
            name: getattr(profile.identity, name).model_dump(mode="json")
            for name in ExamSemanticIdentity.model_fields
        }
        connection.execute(
            "INSERT OR IGNORE INTO semantic_identities "
            "(identity_key, schema_version, algorithm_version, identity_json, evidence_json, "
            "created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?)",
            (
                profile.identity_key,
                IDENTITY_ALGORITHM_VERSION,
                identity_json,
                canonical_json(evidence),
                resolved_at,
                resolved_at,
            ),
        )
        version_id = decision.document_version_id or _version_id(profile)
        predecessor = decision.predecessor_version_id
        version_number = decision.version_number or 1
        if decision.outcome == "new_version":
            connection.execute(
                "INSERT OR IGNORE INTO document_versions "
                "(id, identity_key, document_role, answer_key_state, coverage_json, profile_json, "
                "content_sha256, content_normalizer_version, version_number, "
                "predecessor_version_id, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    profile.identity_key,
                    profile.document_role,
                    profile.answer_key_state,
                    canonical_json(profile.coverage.model_dump(mode="json")),
                    canonical_json(profile.model_dump(mode="json")),
                    profile.content_fingerprint.sha256,
                    profile.content_fingerprint.normalizer_version,
                    version_number,
                    predecessor,
                    resolved_at,
                    resolved_at,
                ),
            )
        elif decision.outcome == "new_identity":
            connection.execute(
                "INSERT OR IGNORE INTO document_versions "
                "(id, identity_key, document_role, answer_key_state, coverage_json, profile_json, "
                "content_sha256, content_normalizer_version, version_number, created_at, "
                "updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    profile.identity_key,
                    profile.document_role,
                    profile.answer_key_state,
                    canonical_json(profile.coverage.model_dump(mode="json")),
                    canonical_json(profile.model_dump(mode="json")),
                    profile.content_fingerprint.sha256,
                    profile.content_fingerprint.normalizer_version,
                    version_number,
                    resolved_at,
                    resolved_at,
                ),
            )
        winner = connection.execute(
            "SELECT id, version_number, predecessor_version_id FROM document_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if winner is None:
            raise RuntimeError("versão sem registro após resolução")
        version_id = winner["id"]
        version_number = winner["version_number"]
        predecessor = winner["predecessor_version_id"]
        connection.execute(
            "UPDATE documents SET document_version_id = ?, semantic_resolution = ?, "
            "updated_at = ? WHERE id = ?",
            (version_id, decision.outcome, resolved_at, document_id),
        )
        connection.execute(
            "UPDATE document_observations SET document_id = ?, document_version_id = ?, "
            "resolution_status = ? "
            "WHERE document_id = ? OR id = (SELECT observation_id FROM documents WHERE id = ?)",
            (document_id, version_id, decision.outcome, document_id, document_id),
        )
        _event(
            connection,
            document_id=document_id,
            version_id=version_id,
            action=decision.outcome,
            payload={"identityKey": profile.identity_key, "versionNumber": version_number},
            resolved_at=resolved_at,
        )
        result = decision.model_copy(
            update={
                "document_version_id": version_id,
                "version_number": version_number,
                "predecessor_version_id": predecessor,
            }
        )
        if owns_transaction:
            connection.commit()
        return result
    except Exception:
        if owns_transaction:
            connection.rollback()
        raise
