from __future__ import annotations

import json
import sqlite3
import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from .official_regression import (
    OfficialContestManifest,
    OfficialDocumentSpec,
    load_official_manifest,
)
from .semantic_identity import (
    AnswerKeyCoverage,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    SemanticEvidence,
    SemanticField,
    canonical_json,
    stable_sha256,
)

CANONICAL_IDENTITY_SCHEMA_VERSION = 1
CANONICAL_IDENTITY_ALGORITHM_VERSION = "canonical-identity-v1"
AliasOutcome = Literal["selected", "unknown", "ambiguous"]
ApplicationOutcome = Literal["selected", "unknown", "ambiguous"]


class CanonicalIdentityError(ValueError):
    """The canonical catalog cannot prove one identity from the supplied evidence."""


class CanonicalIdentityConflict(CanonicalIdentityError):
    """Two records claim the same canonical key or alias."""


@dataclass(frozen=True)
class AliasResolution:
    outcome: AliasOutcome
    alias: str
    normalized_alias: str
    contest_id: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ApplicationResolution:
    outcome: ApplicationOutcome
    contest_id: str
    application_id: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class CanonicalMigrationReport:
    run_id: str
    mode: Literal["dry-run", "apply"]
    status: Literal["completed", "failed"] = "completed"
    manifests: list[str] = field(default_factory=list)
    requested_alias: str | None = None
    resolved_contest_id: str | None = None
    entity_counts: dict[str, int] = field(default_factory=dict)
    mapped_documents: int = 0
    mapped_versions: int = 0
    unresolved_documents: int = 0
    ambiguous_documents: int = 0
    conflicts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": CANONICAL_IDENTITY_SCHEMA_VERSION,
            "algorithmVersion": CANONICAL_IDENTITY_ALGORITHM_VERSION,
            "runId": self.run_id,
            "mode": self.mode,
            "status": self.status,
            "manifests": self.manifests,
            "requestedAlias": self.requested_alias,
            "resolvedContestId": self.resolved_contest_id,
            "entityCounts": dict(sorted(self.entity_counts.items())),
            "mappedDocuments": self.mapped_documents,
            "mappedVersions": self.mapped_versions,
            "unresolvedDocuments": self.unresolved_documents,
            "ambiguousDocuments": self.ambiguous_documents,
            "conflicts": self.conflicts,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_identity_token(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(text.casefold().split())


def _stable_id(kind: str, canonical_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"kad:{kind}:{canonical_key}"))


def _source_context(source_url: str) -> str:
    return (urlsplit(source_url).hostname or "").casefold()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        cast(str, row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
    }


def initialize_canonical_identity_schema(connection: sqlite3.Connection) -> None:
    """Create the additive canonical catalog and its audit trail."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS canonical_contests (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            official_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            notice_year INTEGER,
            board TEXT NOT NULL,
            organization TEXT NOT NULL,
            official_code TEXT,
            source_url TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contest_aliases (
            id TEXT PRIMARY KEY,
            contest_id TEXT NOT NULL REFERENCES canonical_contests(id),
            raw_value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            source_context TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(contest_id, normalized_value, source_context)
        );
        CREATE INDEX IF NOT EXISTS contest_alias_lookup_idx
            ON contest_aliases(normalized_value, status, source_context);
        CREATE TABLE IF NOT EXISTS exam_applications (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            contest_id TEXT NOT NULL REFERENCES canonical_contests(id),
            official_title TEXT NOT NULL,
            display_name TEXT NOT NULL,
            application_date TEXT NOT NULL,
            support_status TEXT NOT NULL,
            source_url TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(contest_id, application_date, official_title)
        );
        CREATE INDEX IF NOT EXISTS exam_applications_contest_idx
            ON exam_applications(contest_id, application_date);
        CREATE TABLE IF NOT EXISTS contest_roles (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            contest_id TEXT NOT NULL REFERENCES canonical_contests(id),
            official_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            official_code TEXT,
            normalized_name TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(contest_id, normalized_name)
        );
        CREATE TABLE IF NOT EXISTS contest_role_aliases (
            id TEXT PRIMARY KEY,
            role_id TEXT NOT NULL REFERENCES contest_roles(id),
            raw_value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            source_url TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(role_id, normalized_value)
        );
        CREATE TABLE IF NOT EXISTS application_stages (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            application_id TEXT NOT NULL REFERENCES exam_applications(id),
            official_name TEXT NOT NULL,
            category TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(application_id, normalized_name)
        );
        CREATE TABLE IF NOT EXISTS application_shifts (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            application_id TEXT NOT NULL REFERENCES exam_applications(id),
            official_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(application_id, normalized_name)
        );
        CREATE TABLE IF NOT EXISTS application_booklets (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            application_id TEXT NOT NULL REFERENCES exam_applications(id),
            official_code TEXT NOT NULL,
            display_name TEXT NOT NULL,
            normalized_code TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(application_id, normalized_code)
        );
        CREATE TABLE IF NOT EXISTS application_scopes (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            application_id TEXT NOT NULL REFERENCES exam_applications(id),
            role_id TEXT NOT NULL REFERENCES contest_roles(id),
            stage_id TEXT NOT NULL REFERENCES application_stages(id),
            shift_id TEXT NOT NULL REFERENCES application_shifts(id),
            booklet_id TEXT NOT NULL REFERENCES application_booklets(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(application_id, role_id, stage_id, shift_id, booklet_id)
        );
        CREATE TABLE IF NOT EXISTS canonical_documents (
            id TEXT PRIMARY KEY,
            canonical_key TEXT NOT NULL UNIQUE,
            source_document_key TEXT NOT NULL,
            contest_id TEXT NOT NULL REFERENCES canonical_contests(id),
            application_id TEXT NOT NULL REFERENCES exam_applications(id),
            document_kind TEXT NOT NULL,
            official_title TEXT NOT NULL,
            display_name TEXT NOT NULL,
            answer_key_state TEXT,
            source_url TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(contest_id, source_document_key)
        );
        CREATE INDEX IF NOT EXISTS canonical_documents_sha_idx ON canonical_documents(sha256);
        CREATE TABLE IF NOT EXISTS canonical_document_scopes (
            document_id TEXT NOT NULL REFERENCES canonical_documents(id) ON DELETE CASCADE,
            scope_id TEXT NOT NULL REFERENCES application_scopes(id),
            content_kind TEXT NOT NULL,
            first_question INTEGER,
            last_question INTEGER,
            created_at TEXT NOT NULL,
            PRIMARY KEY(document_id, scope_id, content_kind, first_question, last_question)
        );
        CREATE TABLE IF NOT EXISTS canonical_identity_migration_runs (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            algorithm_version TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            report_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS canonical_identity_mappings (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES canonical_identity_migration_runs(id),
            legacy_kind TEXT NOT NULL,
            legacy_id TEXT NOT NULL,
            canonical_kind TEXT NOT NULL,
            canonical_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, legacy_kind, legacy_id, canonical_kind)
        );
        CREATE TABLE IF NOT EXISTS canonical_identity_review_queue (
            id TEXT PRIMARY KEY,
            run_id TEXT REFERENCES canonical_identity_migration_runs(id),
            legacy_kind TEXT NOT NULL,
            legacy_id TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(legacy_kind, legacy_id, status)
        );
        CREATE TABLE IF NOT EXISTS canonical_identity_events (
            event_key TEXT PRIMARY KEY,
            run_id TEXT REFERENCES canonical_identity_migration_runs(id),
            action TEXT NOT NULL,
            entity_kind TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS canonical_identity_mappings_append_only_update
        BEFORE UPDATE ON canonical_identity_mappings
        BEGIN SELECT RAISE(ABORT, 'canonical identity mappings are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS canonical_identity_mappings_append_only_delete
        BEFORE DELETE ON canonical_identity_mappings
        BEGIN SELECT RAISE(ABORT, 'canonical identity mappings are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS canonical_identity_events_append_only_update
        BEFORE UPDATE ON canonical_identity_events
        BEGIN SELECT RAISE(ABORT, 'canonical identity events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS canonical_identity_events_append_only_delete
        BEFORE DELETE ON canonical_identity_events
        BEGIN SELECT RAISE(ABORT, 'canonical identity events are append-only'); END;
        """
    )
    for table in ("documents", "document_versions"):
        if not _columns(connection, table):
            continue
        existing = _columns(connection, table)
        additions = {
            "canonical_contest_id": "TEXT REFERENCES canonical_contests(id)",
            "canonical_application_id": "TEXT REFERENCES exam_applications(id)",
            "canonical_document_id": "TEXT REFERENCES canonical_documents(id)",
        }
        for name, declaration in additions.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"  # noqa: S608
                )


def resolve_contest_alias(
    connection: sqlite3.Connection, alias: str, *, source_context: str | None = None
) -> AliasResolution:
    normalized = normalize_identity_token(alias)
    context = (source_context or "").casefold()
    rows = connection.execute(
        "SELECT DISTINCT contest_id FROM contest_aliases "
        "WHERE normalized_value = ? AND status = 'active' "
        "AND (? = '' OR source_context IN ('', ?)) ORDER BY contest_id",
        (normalized, context, context),
    ).fetchall()
    candidates = tuple(cast(str, row["contest_id"]) for row in rows)
    if not candidates:
        return AliasResolution(
            outcome="unknown",
            alias=alias,
            normalized_alias=normalized,
            reason="alias canônico não cadastrado",
        )
    if len(candidates) > 1:
        return AliasResolution(
            outcome="ambiguous",
            alias=alias,
            normalized_alias=normalized,
            candidates=candidates,
            reason="alias aponta para mais de um concurso",
        )
    return AliasResolution(
        outcome="selected",
        alias=alias,
        normalized_alias=normalized,
        contest_id=candidates[0],
        candidates=candidates,
        reason="alias exato resolvido pelo catálogo canônico",
    )


def resolve_application(
    connection: sqlite3.Connection,
    contest_id: str,
    *,
    canonical_key: str | None = None,
    application_date: str | None = None,
    stage: str | None = None,
) -> ApplicationResolution:
    clauses = ["a.contest_id = ?"]
    parameters: list[str] = [contest_id]
    if canonical_key:
        clauses.append("a.canonical_key = ?")
        parameters.append(canonical_key)
    if application_date:
        clauses.append("a.application_date = ?")
        parameters.append(application_date)
    if stage:
        clauses.append(
            "EXISTS (SELECT 1 FROM application_stages s WHERE s.application_id = a.id "
            "AND s.normalized_name = ?)"
        )
        parameters.append(normalize_identity_token(stage))
    rows = connection.execute(
        "SELECT a.id FROM exam_applications a WHERE " + " AND ".join(clauses) + " ORDER BY a.id",
        tuple(parameters),
    ).fetchall()
    candidates = tuple(cast(str, row["id"]) for row in rows)
    if not candidates:
        return ApplicationResolution(
            outcome="unknown", contest_id=contest_id, reason="aplicação não encontrada"
        )
    if len(candidates) > 1:
        return ApplicationResolution(
            outcome="ambiguous",
            contest_id=contest_id,
            candidates=candidates,
            reason="o concurso possui várias aplicações compatíveis; informe data, etapa ou chave",
        )
    return ApplicationResolution(
        outcome="selected",
        contest_id=contest_id,
        application_id=candidates[0],
        candidates=candidates,
        reason="aplicação resolvida por evidência canônica",
    )


def _ensure_no_key_conflict(
    connection: sqlite3.Connection, table: str, canonical_key: str, entity_id: str
) -> None:
    row = connection.execute(
        f"SELECT id FROM {table} WHERE canonical_key = ?",  # noqa: S608
        (canonical_key,),
    ).fetchone()
    if row is not None and row["id"] != entity_id:
        raise CanonicalIdentityConflict(f"chave canônica em disputa: {canonical_key}")


def _register_alias(
    connection: sqlite3.Connection,
    *,
    contest_id: str,
    raw_value: str,
    alias_type: str,
    source_context: str,
    source_url: str,
    evidence: dict[str, Any],
    changed_at: str,
) -> None:
    normalized = normalize_identity_token(raw_value)
    conflicts = connection.execute(
        "SELECT DISTINCT contest_id FROM contest_aliases WHERE normalized_value = ? "
        "AND source_context = ? AND status = 'active' AND contest_id != ?",
        (normalized, source_context, contest_id),
    ).fetchall()
    if conflicts:
        raise CanonicalIdentityConflict(
            f"alias {raw_value!r} já pertence a outro concurso no contexto {source_context!r}"
        )
    alias_id = _stable_id("contest-alias", f"{contest_id}:{source_context}:{normalized}")
    connection.execute(
        "INSERT INTO contest_aliases "
        "(id, contest_id, raw_value, normalized_value, alias_type, source_context, source_url, "
        "evidence_json, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?) "
        "ON CONFLICT(contest_id, normalized_value, source_context) DO UPDATE SET "
        "raw_value=excluded.raw_value, alias_type=excluded.alias_type, "
        "source_url=excluded.source_url, evidence_json=excluded.evidence_json, "
        "updated_at=excluded.updated_at",
        (
            alias_id,
            contest_id,
            raw_value,
            normalized,
            alias_type,
            source_context,
            source_url,
            canonical_json(evidence),
            changed_at,
            changed_at,
        ),
    )


def _upsert_named_entity(
    connection: sqlite3.Connection,
    *,
    table: str,
    entity_id: str,
    canonical_key: str,
    columns: dict[str, object],
    changed_at: str,
) -> None:
    _ensure_no_key_conflict(connection, table, canonical_key, entity_id)
    values = {
        "id": entity_id,
        "canonical_key": canonical_key,
        **columns,
        "created_at": changed_at,
        "updated_at": changed_at,
    }
    names = tuple(values)
    placeholders = ", ".join("?" for _ in names)
    updates = ", ".join(
        f"{name}=excluded.{name}" for name in names if name not in {"id", "created_at"}
    )
    connection.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders}) "  # noqa: S608
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        tuple(values[name] for name in names),
    )


def _application_entities(
    connection: sqlite3.Connection,
    manifest: OfficialContestManifest,
    *,
    contest_id: str,
    evidence: dict[str, Any],
    changed_at: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for application in manifest.applications:
        canonical_key = f"{manifest.id}:application:{application.id}"
        entity_id = _stable_id("exam-application", canonical_key)
        _upsert_named_entity(
            connection,
            table="exam_applications",
            entity_id=entity_id,
            canonical_key=canonical_key,
            columns={
                "contest_id": contest_id,
                "official_title": application.title,
                "display_name": application.title,
                "application_date": application.application_date.isoformat(),
                "support_status": application.support_status,
                "source_url": manifest.source_page_url,
                "evidence_json": canonical_json({**evidence, "notes": application.notes}),
            },
            changed_at=changed_at,
        )
        _named_scope_entity(
            connection,
            table="application_stages",
            kind="stage",
            parent_key=canonical_key,
            parent_column="application_id",
            parent_id=entity_id,
            raw_value=application.stage,
            evidence={**evidence, "notes": application.notes},
            changed_at=changed_at,
        )
        result[application.id] = entity_id
    return result


def _named_scope_entity(
    connection: sqlite3.Connection,
    *,
    table: str,
    kind: str,
    parent_key: str,
    parent_column: str,
    parent_id: str,
    raw_value: str,
    evidence: dict[str, Any],
    changed_at: str,
    extra: dict[str, object] | None = None,
) -> str:
    normalized = normalize_identity_token(raw_value)
    canonical_key = f"{parent_key}:{kind}:{normalized}"
    entity_id = _stable_id(kind, canonical_key)
    columns: dict[str, object] = {
        parent_column: parent_id,
        "evidence_json": canonical_json(evidence),
        **(extra or {}),
    }
    if table == "contest_roles":
        columns.update(
            official_name=raw_value,
            display_name=raw_value,
            official_code=None,
            normalized_name=normalized,
        )
    elif table == "application_stages":
        columns.update(official_name=raw_value, category=normalized, normalized_name=normalized)
    elif table == "application_shifts":
        columns.update(official_name=raw_value, normalized_name=normalized)
    elif table == "application_booklets":
        columns.update(
            official_code=raw_value,
            display_name=f"Tipo {raw_value}",
            normalized_code=normalized,
        )
    _upsert_named_entity(
        connection,
        table=table,
        entity_id=entity_id,
        canonical_key=canonical_key,
        columns=columns,
        changed_at=changed_at,
    )
    return entity_id


def _scope_for(
    connection: sqlite3.Connection,
    *,
    manifest: OfficialContestManifest,
    contest_id: str,
    application_id: str,
    application_source_id: str,
    role: str,
    stage: str,
    shift: str,
    booklet_type: int,
    evidence: dict[str, Any],
    changed_at: str,
) -> str:
    contest_key = manifest.id
    application_key = f"{manifest.id}:application:{application_source_id}"
    role_id = _named_scope_entity(
        connection,
        table="contest_roles",
        kind="role",
        parent_key=contest_key,
        parent_column="contest_id",
        parent_id=contest_id,
        raw_value=role,
        evidence=evidence,
        changed_at=changed_at,
    )
    role_alias = normalize_identity_token(role)
    role_alias_id = _stable_id("role-alias", f"{role_id}:{role_alias}")
    connection.execute(
        "INSERT INTO contest_role_aliases "
        "(id, role_id, raw_value, normalized_value, source_url, evidence_json, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?) "
        "ON CONFLICT(role_id, normalized_value) DO UPDATE SET "
        "raw_value=excluded.raw_value, evidence_json=excluded.evidence_json, "
        "updated_at=excluded.updated_at",
        (
            role_alias_id,
            role_id,
            role,
            role_alias,
            manifest.source_page_url,
            canonical_json(evidence),
            changed_at,
            changed_at,
        ),
    )
    stage_id = _named_scope_entity(
        connection,
        table="application_stages",
        kind="stage",
        parent_key=application_key,
        parent_column="application_id",
        parent_id=application_id,
        raw_value=stage,
        evidence=evidence,
        changed_at=changed_at,
    )
    shift_order = {"manha": 1, "tarde": 2, "noite": 3}.get(
        normalize_identity_token(shift), 99
    )
    shift_id = _named_scope_entity(
        connection,
        table="application_shifts",
        kind="shift",
        parent_key=application_key,
        parent_column="application_id",
        parent_id=application_id,
        raw_value=shift,
        evidence=evidence,
        changed_at=changed_at,
        extra={"sort_order": shift_order},
    )
    booklet_id = _named_scope_entity(
        connection,
        table="application_booklets",
        kind="booklet",
        parent_key=application_key,
        parent_column="application_id",
        parent_id=application_id,
        raw_value=str(booklet_type),
        evidence=evidence,
        changed_at=changed_at,
    )
    canonical_key = ":".join(
        (application_key, "scope", role_id, stage_id, shift_id, booklet_id)
    )
    scope_id = _stable_id("application-scope", canonical_key)
    _upsert_named_entity(
        connection,
        table="application_scopes",
        entity_id=scope_id,
        canonical_key=canonical_key,
        columns={
            "application_id": application_id,
            "role_id": role_id,
            "stage_id": stage_id,
            "shift_id": shift_id,
            "booklet_id": booklet_id,
        },
        changed_at=changed_at,
    )
    return scope_id


def _register_document_scopes(
    connection: sqlite3.Connection,
    manifest: OfficialContestManifest,
    document: OfficialDocumentSpec,
    *,
    document_id: str,
    contest_id: str,
    application_id: str,
    changed_at: str,
) -> None:
    evidence = {"manifestDocumentId": document.id, "sourceUrl": document.source_url}
    relations: set[tuple[str, str, int | None, int | None]] = set()
    if document.kind == "exam":
        assert document.shift is not None and document.booklet_type is not None
        for role in document.roles:
            scope_id = _scope_for(
                connection,
                manifest=manifest,
                contest_id=contest_id,
                application_id=application_id,
                application_source_id=document.application_id,
                role=role,
                stage=document.stage,
                shift=document.shift,
                booklet_type=document.booklet_type,
                evidence=evidence,
                changed_at=changed_at,
            )
            relations.update(
                (scope_id, section.kind, section.first, section.last)
                for section in document.sections
            )
    else:
        for answer_scope in document.answer_scopes:
            for booklet_type in answer_scope.booklet_types:
                scope_id = _scope_for(
                    connection,
                    manifest=manifest,
                    contest_id=contest_id,
                    application_id=application_id,
                    application_source_id=document.application_id,
                    role=answer_scope.role,
                    stage=document.stage,
                    shift=answer_scope.shift,
                    booklet_type=booklet_type,
                    evidence=evidence,
                    changed_at=changed_at,
                )
                relations.add(
                    (scope_id, "answer_key", answer_scope.first, answer_scope.last)
                )
    for scope_id, content_kind, first, last in sorted(relations):
        connection.execute(
            "INSERT OR IGNORE INTO canonical_document_scopes "
            "(document_id, scope_id, content_kind, first_question, last_question, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (document_id, scope_id, content_kind, first, last, changed_at),
        )


def register_official_manifest(
    connection: sqlite3.Connection,
    manifest: OfficialContestManifest,
    *,
    manifest_path: Path,
    changed_at: str,
) -> str:
    """Import an official manifest into the canonical catalog without reading its PDFs."""
    contest_id = _stable_id("contest", manifest.id)
    evidence = {
        "manifest": str(manifest_path),
        "sourcePageUrl": manifest.source_page_url,
        "evidenceUrls": list(manifest.evidence_urls),
    }
    _upsert_named_entity(
        connection,
        table="canonical_contests",
        entity_id=contest_id,
        canonical_key=manifest.id,
        columns={
            "official_name": manifest.contest_name,
            "display_name": manifest.contest_name,
            "notice_year": manifest.notice_year,
            "board": manifest.board,
            "organization": manifest.organization,
            "official_code": None,
            "source_url": manifest.source_page_url,
            "evidence_json": canonical_json(evidence),
        },
        changed_at=changed_at,
    )
    context = _source_context(manifest.source_page_url)
    _register_alias(
        connection,
        contest_id=contest_id,
        raw_value=manifest.id,
        alias_type="canonical_key",
        source_context="",
        source_url=manifest.source_page_url,
        evidence=evidence,
        changed_at=changed_at,
    )
    _register_alias(
        connection,
        contest_id=contest_id,
        raw_value=manifest.contest_name,
        alias_type="official_name",
        source_context=context,
        source_url=manifest.source_page_url,
        evidence=evidence,
        changed_at=changed_at,
    )
    for alias in manifest.contest_aliases:
        _register_alias(
            connection,
            contest_id=contest_id,
            raw_value=alias,
            alias_type="input_alias",
            source_context="",
            source_url=manifest.source_page_url,
            evidence=evidence,
            changed_at=changed_at,
        )
    applications = _application_entities(
        connection,
        manifest,
        contest_id=contest_id,
        evidence=evidence,
        changed_at=changed_at,
    )
    for document in manifest.documents:
        canonical_key = f"{manifest.id}:document:{document.id}"
        document_id = _stable_id("canonical-document", canonical_key)
        _upsert_named_entity(
            connection,
            table="canonical_documents",
            entity_id=document_id,
            canonical_key=canonical_key,
            columns={
                "source_document_key": document.id,
                "contest_id": contest_id,
                "application_id": applications[document.application_id],
                "document_kind": document.kind,
                "official_title": document.title,
                "display_name": document.title,
                "answer_key_state": document.answer_key_status,
                "source_url": document.source_url,
                "sha256": document.sha256,
                "evidence_json": canonical_json(
                    {**evidence, "manifestDocumentId": document.id}
                ),
            },
            changed_at=changed_at,
        )
        _register_document_scopes(
            connection,
            manifest,
            document,
            document_id=document_id,
            contest_id=contest_id,
            application_id=applications[document.application_id],
            changed_at=changed_at,
        )
    return contest_id


def _record_review(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    legacy_id: str,
    reason: str,
    candidates: Sequence[str],
    changed_at: str,
) -> None:
    queue_id = _stable_id("canonical-review", f"document:{legacy_id}:pending")
    connection.execute(
        "INSERT INTO canonical_identity_review_queue "
        "(id, run_id, legacy_kind, legacy_id, status, reason, candidates_json, "
        "created_at, updated_at) VALUES (?, ?, 'document', ?, 'pending', ?, ?, ?, ?) "
        "ON CONFLICT(legacy_kind, legacy_id, status) DO UPDATE SET "
        "run_id=excluded.run_id, reason=excluded.reason, "
        "candidates_json=excluded.candidates_json, updated_at=excluded.updated_at",
        (
            queue_id,
            run_id,
            legacy_id,
            reason,
            canonical_json(list(candidates)),
            changed_at,
            changed_at,
        ),
    )


def _record_mapping(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    legacy_kind: str,
    legacy_id: str,
    canonical_kind: str,
    canonical_id: str,
    evidence: dict[str, Any],
    changed_at: str,
) -> None:
    mapping_id = _stable_id(
        "canonical-mapping", f"{run_id}:{legacy_kind}:{legacy_id}:{canonical_kind}"
    )
    connection.execute(
        "INSERT OR IGNORE INTO canonical_identity_mappings "
        "(id, run_id, legacy_kind, legacy_id, canonical_kind, canonical_id, evidence_json, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            mapping_id,
            run_id,
            legacy_kind,
            legacy_id,
            canonical_kind,
            canonical_id,
            canonical_json(evidence),
            changed_at,
        ),
    )


def _candidate_documents_for_legacy(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> list[sqlite3.Row]:
    metadata = json.loads(cast(str, row["metadata_json"]))
    external_id = str(metadata.get("external_id") or "").strip()
    digest = str(row["sha256"] or "").casefold()
    clauses: list[str] = []
    parameters: list[str] = []
    if external_id:
        clauses.append("source_document_key = ?")
        parameters.append(external_id)
    if digest:
        clauses.append("sha256 = ?")
        parameters.append(digest)
    if not clauses:
        return []
    return connection.execute(
        "SELECT id, contest_id, application_id, source_document_key, sha256 "
        "FROM canonical_documents WHERE " + " OR ".join(clauses) + " ORDER BY id",
        tuple(parameters),
    ).fetchall()


def backfill_legacy_documents(
    connection: sqlite3.Connection, *, run_id: str, changed_at: str
) -> tuple[int, int, int, int]:
    if not _columns(connection, "documents"):
        return 0, 0, 0, 0
    rows = connection.execute(
        "SELECT id, sha256, metadata_json, document_version_id, canonical_document_id "
        "FROM documents ORDER BY id"
    ).fetchall()
    mapped_documents = 0
    mapped_versions: set[str] = set()
    unresolved = 0
    ambiguous = 0
    for row in rows:
        if row["canonical_document_id"]:
            continue
        candidates = _candidate_documents_for_legacy(connection, row)
        candidate_ids = tuple(cast(str, item["id"]) for item in candidates)
        if len(candidates) != 1:
            metadata = json.loads(cast(str, row["metadata_json"]))
            alias = str(metadata.get("concurso") or "").strip()
            alias_resolution = (
                resolve_contest_alias(connection, alias) if alias else None
            )
            reason = (
                "mais de um documento canônico corresponde ao SHA-256 ou external_id"
                if len(candidates) > 1
                else "documento sem SHA-256 ou external_id presente no catálogo oficial"
            )
            contest_candidates = (
                alias_resolution.candidates if alias_resolution is not None else ()
            )
            _record_review(
                connection,
                run_id=run_id,
                legacy_id=cast(str, row["id"]),
                reason=reason,
                candidates=(*candidate_ids, *contest_candidates),
                changed_at=changed_at,
            )
            if len(candidates) > 1 or (
                alias_resolution is not None and alias_resolution.outcome == "ambiguous"
            ):
                ambiguous += 1
            else:
                unresolved += 1
            continue
        candidate = candidates[0]
        connection.execute(
            "UPDATE documents SET canonical_contest_id = ?, canonical_application_id = ?, "
            "canonical_document_id = ? WHERE id = ?",
            (
                candidate["contest_id"],
                candidate["application_id"],
                candidate["id"],
                row["id"],
            ),
        )
        mapped_documents += 1
        evidence = {
            "sha256": row["sha256"],
            "sourceDocumentKey": candidate["source_document_key"],
        }
        _record_mapping(
            connection,
            run_id=run_id,
            legacy_kind="document",
            legacy_id=cast(str, row["id"]),
            canonical_kind="canonical_document",
            canonical_id=cast(str, candidate["id"]),
            evidence=evidence,
            changed_at=changed_at,
        )
        version_id = cast(str | None, row["document_version_id"])
        if version_id:
            connection.execute(
                "UPDATE document_versions SET canonical_contest_id = ?, "
                "canonical_application_id = ?, canonical_document_id = ? WHERE id = ?",
                (
                    candidate["contest_id"],
                    candidate["application_id"],
                    candidate["id"],
                    version_id,
                ),
            )
            mapped_versions.add(version_id)
            _record_mapping(
                connection,
                run_id=run_id,
                legacy_kind="document_version",
                legacy_id=version_id,
                canonical_kind="canonical_document",
                canonical_id=cast(str, candidate["id"]),
                evidence=evidence,
                changed_at=changed_at,
            )
    return mapped_documents, len(mapped_versions), unresolved, ambiguous


def canonical_entity_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "canonical_contests",
        "contest_aliases",
        "exam_applications",
        "contest_roles",
        "contest_role_aliases",
        "application_stages",
        "application_shifts",
        "application_booklets",
        "application_scopes",
        "canonical_documents",
        "canonical_document_scopes",
    )
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
        for table in tables
    }


def run_canonical_identity_migration(
    connection: sqlite3.Connection,
    *,
    manifest_paths: Sequence[Path],
    contest_alias: str | None = None,
    apply: bool = False,
    run_id: str | None = None,
) -> CanonicalMigrationReport:
    effective_run_id = run_id or str(uuid.uuid4())
    mode: Literal["dry-run", "apply"] = "apply" if apply else "dry-run"
    report = CanonicalMigrationReport(
        run_id=effective_run_id,
        mode=mode,
        manifests=[str(path.resolve()) for path in manifest_paths],
        requested_alias=contest_alias,
    )
    changed_at = _now()
    initialize_canonical_identity_schema(connection)
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "INSERT OR IGNORE INTO canonical_identity_migration_runs "
            "(id, schema_version, algorithm_version, mode, status, report_json, started_at) "
            "VALUES (?, ?, ?, ?, 'running', '{}', ?)",
            (
                effective_run_id,
                CANONICAL_IDENTITY_SCHEMA_VERSION,
                CANONICAL_IDENTITY_ALGORITHM_VERSION,
                mode,
                changed_at,
            ),
        )
        existing_run = connection.execute(
            "SELECT mode FROM canonical_identity_migration_runs WHERE id = ?",
            (effective_run_id,),
        ).fetchone()
        if existing_run is not None and existing_run["mode"] != mode:
            raise CanonicalIdentityConflict("o run_id já pertence a outro modo de execução")
        for manifest_path in manifest_paths:
            loaded = load_official_manifest(manifest_path)
            register_official_manifest(
                connection,
                loaded.spec,
                manifest_path=loaded.path,
                changed_at=changed_at,
            )
        if contest_alias:
            resolution = resolve_contest_alias(connection, contest_alias)
            if resolution.outcome != "selected":
                raise CanonicalIdentityError(resolution.reason)
            report.resolved_contest_id = resolution.contest_id
        (
            report.mapped_documents,
            report.mapped_versions,
            report.unresolved_documents,
            report.ambiguous_documents,
        ) = backfill_legacy_documents(
            connection, run_id=effective_run_id, changed_at=changed_at
        )
        report.entity_counts = canonical_entity_counts(connection)
        if apply:
            finished_at = _now()
            event_payload = report.as_dict()
            connection.execute(
                "INSERT OR IGNORE INTO canonical_identity_events "
                "(event_key, run_id, action, entity_kind, entity_id, actor, algorithm_version, "
                "payload_json, created_at) VALUES (?, ?, 'migration_completed', 'catalog', ?, "
                "'system', ?, ?, ?)",
                (
                    stable_sha256({"runId": effective_run_id, "action": "migration_completed"}),
                    effective_run_id,
                    report.resolved_contest_id or "all",
                    CANONICAL_IDENTITY_ALGORITHM_VERSION,
                    canonical_json(event_payload),
                    finished_at,
                ),
            )
            connection.execute(
                "UPDATE canonical_identity_migration_runs SET status = 'completed', "
                "report_json = ?, finished_at = ? WHERE id = ?",
                (canonical_json(event_payload), finished_at, effective_run_id),
            )
            connection.commit()
        else:
            connection.rollback()
        return report
    except Exception as exc:
        connection.rollback()
        report.status = "failed"
        report.conflicts.append(str(exc))
        raise


def canonical_identity_for_version(
    connection: sqlite3.Connection, version_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT dv.canonical_contest_id, dv.canonical_application_id,
               dv.canonical_document_id, c.canonical_key AS contest_key,
               c.display_name AS contest_name, a.canonical_key AS application_key,
               a.display_name AS application_name
        FROM document_versions dv
        LEFT JOIN canonical_contests c ON c.id = dv.canonical_contest_id
        LEFT JOIN exam_applications a ON a.id = dv.canonical_application_id
        WHERE dv.id = ?
        """,
        (version_id,),
    ).fetchone()
    if row is None or row["canonical_document_id"] is None:
        return None
    scopes = connection.execute(
        """
        SELECT s.id AS scope_id, s.role_id, s.stage_id, s.shift_id, s.booklet_id,
               r.display_name AS role_name, st.official_name AS stage_name,
               sh.official_name AS shift_name, b.display_name AS booklet_name,
               cds.content_kind, cds.first_question, cds.last_question
        FROM canonical_document_scopes cds
        JOIN application_scopes s ON s.id = cds.scope_id
        JOIN contest_roles r ON r.id = s.role_id
        JOIN application_stages st ON st.id = s.stage_id
        JOIN application_shifts sh ON sh.id = s.shift_id
        JOIN application_booklets b ON b.id = s.booklet_id
        WHERE cds.document_id = ? ORDER BY s.id, cds.content_kind
        """,
        (row["canonical_document_id"],),
    ).fetchall()
    aliases = connection.execute(
        "SELECT raw_value FROM contest_aliases WHERE contest_id = ? AND status = 'active' "
        "ORDER BY alias_type, normalized_value",
        (row["canonical_contest_id"],),
    ).fetchall()
    return {
        "contestId": row["canonical_contest_id"],
        "contestKey": row["contest_key"],
        "contestName": row["contest_name"],
        "applicationId": row["canonical_application_id"],
        "applicationKey": row["application_key"],
        "applicationName": row["application_name"],
        "documentId": row["canonical_document_id"],
        "scopeIds": sorted({cast(str, item["scope_id"]) for item in scopes}),
        "aliases": [cast(str, item["raw_value"]) for item in aliases],
        "scopes": [dict(item) for item in scopes],
    }


def _canonical_field(name: str, values: Sequence[str]) -> SemanticField:
    unique = tuple(sorted(set(values)))
    if not unique:
        return SemanticField.unknown(f"{name} canônico ausente")
    evidence = tuple(
        SemanticEvidence.metadata(f"canonical:{name}", value) for value in unique
    )
    return SemanticField(
        status="known",
        raw_values=unique,
        normalized_values=unique,
        evidence=evidence,
        method="canonical_catalog",
        confidence=1.0,
        reason=f"{name} resolvido pelo catálogo canônico",
    )


def canonicalize_profile_for_version(
    connection: sqlite3.Connection,
    version_id: str,
    profile: DocumentSemanticProfile,
) -> DocumentSemanticProfile:
    identity = canonical_identity_for_version(connection, version_id)
    if identity is None:
        return profile
    scopes = cast(list[dict[str, Any]], identity["scopes"])
    roles = [cast(str, item["role_id"]) for item in scopes]
    stages = [cast(str, item["stage_id"]) for item in scopes]
    shifts = [cast(str, item["shift_id"]) for item in scopes]
    booklets = [cast(str, item["booklet_id"]) for item in scopes]
    application_row = connection.execute(
        "SELECT application_date FROM exam_applications WHERE id = ?",
        (identity["applicationId"],),
    ).fetchone()
    year = cast(str, application_row["application_date"]).split("-", 1)[0]
    canonical_exam_identity = ExamSemanticIdentity(
        board=profile.identity.board,
        concurso=_canonical_field("concurso", [cast(str, identity["contestId"])]),
        organization=profile.identity.organization,
        year=_canonical_field("year", [year]),
        roles=_canonical_field("roles", roles),
        stage=_canonical_field("stage", stages),
        turns=_canonical_field("turns", shifts),
        variants=_canonical_field("variants", booklets),
    )
    coverage = AnswerKeyCoverage(
        roles=_canonical_field("roles", roles),
        stage=_canonical_field("stage", stages),
        turns=_canonical_field("turns", shifts),
        variants=_canonical_field("variants", booklets),
    )
    return profile.model_copy(
        update={
            "identity": canonical_exam_identity,
            "identity_key": stable_sha256(
                {
                    "schemaVersion": CANONICAL_IDENTITY_SCHEMA_VERSION,
                    "contestId": identity["contestId"],
                    "applicationId": identity["applicationId"],
                    "scopeIds": identity["scopeIds"],
                }
            ),
            "coverage": coverage,
            "has_conflict": False,
        }
    )


def canonical_summary(connection: sqlite3.Connection) -> dict[str, int]:
    counts = canonical_entity_counts(connection)
    counts.update(
        mappedDocuments=int(
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE canonical_document_id IS NOT NULL"
            ).fetchone()[0]
        ),
        pendingReview=int(
            connection.execute(
                "SELECT COUNT(*) FROM canonical_identity_review_queue WHERE status = 'pending'"
            ).fetchone()[0]
        ),
    )
    return counts


def contest_inventory(connection: sqlite3.Connection, alias: str) -> dict[str, Any]:
    resolution = resolve_contest_alias(connection, alias)
    if resolution.outcome != "selected" or resolution.contest_id is None:
        return {
            "alias": alias,
            "outcome": resolution.outcome,
            "reason": resolution.reason,
            "candidates": list(resolution.candidates),
        }
    contest = connection.execute(
        "SELECT * FROM canonical_contests WHERE id = ?", (resolution.contest_id,)
    ).fetchone()
    applications = connection.execute(
        "SELECT id, canonical_key, display_name, application_date, support_status "
        "FROM exam_applications WHERE contest_id = ? ORDER BY application_date, id",
        (resolution.contest_id,),
    ).fetchall()
    document_counts = Counter(
        {
            cast(str, row["document_kind"]): int(row["total"])
            for row in connection.execute(
                "SELECT document_kind, COUNT(*) AS total FROM canonical_documents "
                "WHERE contest_id = ? GROUP BY document_kind",
                (resolution.contest_id,),
            ).fetchall()
        }
    )
    return {
        "alias": alias,
        "outcome": "selected",
        "contest": dict(contest),
        "applications": [dict(item) for item in applications],
        "documentCounts": dict(document_counts),
    }
