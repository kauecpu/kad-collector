from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import CollectionTelemetryEvent


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CollectionStateStore:
    """Persistent cache, checkpoints and sanitized collection telemetry."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS engine_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO engine_meta(key, value) VALUES ('schema_version', '1');

                CREATE TABLE IF NOT EXISTS http_cache (
                    url TEXT PRIMARY KEY,
                    final_url TEXT NOT NULL,
                    etag TEXT,
                    last_modified TEXT,
                    sha256 TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    local_path TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    strategy TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_checkpoints (
                    checkpoint_key TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collection_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    status_code INTEGER,
                    duration_ms INTEGER NOT NULL,
                    bytes_received INTEGER NOT NULL,
                    attempt INTEGER NOT NULL,
                    wait_seconds REAL NOT NULL,
                    cache_status TEXT NOT NULL,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS telemetry_run_id
                    ON collection_telemetry(run_id, id);
                """
            )
            version = connection.execute(
                "SELECT value FROM engine_meta WHERE key = 'schema_version'"
            ).fetchone()
            if version is None or version["value"] != "1":
                raise RuntimeError("versao desconhecida do banco do motor de coleta")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"banco do motor de coleta corrompido: {integrity}")
            connection.commit()

    def cache_entry(self, url: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM http_cache WHERE url = ?", (url,)).fetchone()
        return dict(row) if row is not None else None

    def store_cache(
        self,
        *,
        url: str,
        final_url: str,
        etag: str | None,
        last_modified: str | None,
        sha256: str,
        content_type: str,
        size_bytes: int,
        local_path: Path,
        status_code: int,
        strategy: str,
    ) -> None:
        timestamp = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO http_cache(
                    url, final_url, etag, last_modified, sha256, content_type,
                    size_bytes, local_path, downloaded_at, checked_at, status_code, strategy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    final_url=excluded.final_url,
                    etag=excluded.etag,
                    last_modified=excluded.last_modified,
                    sha256=excluded.sha256,
                    content_type=excluded.content_type,
                    size_bytes=excluded.size_bytes,
                    local_path=excluded.local_path,
                    downloaded_at=excluded.downloaded_at,
                    checked_at=excluded.checked_at,
                    status_code=excluded.status_code,
                    strategy=excluded.strategy
                """,
                (
                    url,
                    final_url,
                    etag,
                    last_modified,
                    sha256,
                    content_type,
                    size_bytes,
                    str(local_path.resolve()),
                    timestamp,
                    timestamp,
                    status_code,
                    strategy,
                ),
            )
            connection.commit()

    def touch_cache(self, url: str, *, status_code: int = 304) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE http_cache SET checked_at = ?, status_code = ? WHERE url = ?",
                (_now(), status_code, url),
            )
            connection.commit()

    def invalidate_cache(self, url: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("DELETE FROM http_cache WHERE url = ?", (url,))
            connection.commit()

    def save_checkpoint(
        self,
        checkpoint_key: str,
        source_id: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        timestamp = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO collection_checkpoints(
                    checkpoint_key, source_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_key) DO UPDATE SET
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    checkpoint_key,
                    source_id,
                    status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()

    def load_checkpoint(self, checkpoint_key: str) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status, payload_json FROM collection_checkpoints WHERE checkpoint_key = ?",
                (checkpoint_key,),
            ).fetchone()
        if row is None:
            return None
        return {"status": row["status"], "payload": json.loads(row["payload_json"])}

    def delete_checkpoint(self, checkpoint_key: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM collection_checkpoints WHERE checkpoint_key = ?", (checkpoint_key,)
            )
            connection.commit()

    def add_event(self, run_id: str, event: CollectionTelemetryEvent) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO collection_telemetry(
                    run_id, occurred_at, source_id, url, strategy, outcome, status_code,
                    duration_ms, bytes_received, attempt, wait_seconds, cache_status, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event.occurred_at.isoformat(),
                    event.source_id,
                    event.url,
                    event.strategy,
                    event.outcome,
                    event.status_code,
                    event.duration_ms,
                    event.bytes_received,
                    event.attempt,
                    event.wait_seconds,
                    event.cache_status,
                    event.detail,
                ),
            )
            connection.commit()

    def events(self, run_id: str, *, limit: int = 500) -> list[CollectionTelemetryEvent]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT occurred_at, source_id, url, strategy, outcome, status_code,
                       duration_ms, bytes_received, attempt, wait_seconds, cache_status, detail
                FROM collection_telemetry WHERE run_id = ? ORDER BY id DESC LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [CollectionTelemetryEvent.model_validate(dict(row)) for row in reversed(rows)]

    def cache_summary(self) -> dict[str, int]:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS entries, COALESCE(SUM(size_bytes), 0) AS bytes FROM http_cache"
            ).fetchone()
        return {"entries": int(row["entries"]), "bytes": int(row["bytes"])}
