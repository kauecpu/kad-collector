from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import CollectionTelemetryEvent
from .url_utils import canonicalize_url

_SCHEMA_VERSION = "2"


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
                INSERT OR IGNORE INTO engine_meta(key, value) VALUES ('schema_version', '2');

                CREATE TABLE IF NOT EXISTS http_cache (
                    url TEXT PRIMARY KEY,
                    original_url TEXT NOT NULL,
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
            if version is None:
                raise RuntimeError("versao desconhecida do banco do motor de coleta")
            if version["value"] == "1":
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(http_cache)").fetchall()
                }
                if "original_url" not in columns:
                    connection.execute(
                        "ALTER TABLE http_cache ADD COLUMN original_url TEXT NOT NULL DEFAULT ''"
                    )
                    connection.execute(
                        "UPDATE http_cache SET original_url = url WHERE original_url = ''"
                    )
                rows = connection.execute(
                    "SELECT url, original_url FROM http_cache ORDER BY checked_at DESC"
                ).fetchall()
                retained: set[str] = set()
                for row in rows:
                    original_url = str(row["original_url"] or row["url"])
                    try:
                        canonical_url = canonicalize_url(str(row["url"]))
                    except ValueError:
                        canonical_url = str(row["url"])
                    if canonical_url in retained:
                        connection.execute("DELETE FROM http_cache WHERE url = ?", (row["url"],))
                        continue
                    if canonical_url != row["url"]:
                        connection.execute(
                            "DELETE FROM http_cache WHERE url = ? AND url != ?",
                            (canonical_url, row["url"]),
                        )
                        connection.execute(
                            "UPDATE http_cache SET url = ?, original_url = ? WHERE url = ?",
                            (canonical_url, original_url, row["url"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE http_cache SET original_url = ? WHERE url = ?",
                            (original_url, row["url"]),
                        )
                    retained.add(canonical_url)
                connection.execute(
                    "UPDATE engine_meta SET value = ? WHERE key = 'schema_version'",
                    (_SCHEMA_VERSION,),
                )
            elif version["value"] != _SCHEMA_VERSION:
                raise RuntimeError("versao desconhecida do banco do motor de coleta")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"banco do motor de coleta corrompido: {integrity}")
            connection.commit()

    def cache_entry(self, url: str) -> dict[str, Any] | None:
        canonical_url = canonicalize_url(url)
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM http_cache WHERE url = ?", (canonical_url,)
            ).fetchone()
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
        canonical_url = canonicalize_url(url)
        timestamp = _now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO http_cache(
                    url, original_url, final_url, etag, last_modified, sha256, content_type,
                    size_bytes, local_path, downloaded_at, checked_at, status_code, strategy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    original_url=CASE
                        WHEN http_cache.original_url = '' THEN excluded.original_url
                        ELSE http_cache.original_url
                    END,
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
                    canonical_url,
                    url.strip(),
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
        canonical_url = canonicalize_url(url)
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE http_cache SET checked_at = ?, status_code = ? WHERE url = ?",
                (_now(), status_code, canonical_url),
            )
            connection.commit()

    def invalidate_cache(self, url: str) -> None:
        canonical_url = canonicalize_url(url)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("DELETE FROM http_cache WHERE url = ?", (canonical_url,))
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
