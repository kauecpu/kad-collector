from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from collections import Counter
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from .desktop_limits import validate_pdf_batch
from .desktop_models import (
    ClassifierProviderName,
    DesktopFilterSet,
    DesktopImportMetadata,
    DesktopQuestionStatus,
    QuestionClassification,
)
from .models import QuestionRecord
from .validation import validate_editorial_question


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def question_fingerprint(question: QuestionRecord) -> str:
    payload = {
        "statement": _normalize(" ".join(question.statement.split())),
        "alternatives": [
            [item.letter, _normalize(" ".join(item.text.split()))] for item in question.alternatives
        ],
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _classification_confidences(
    classification: QuestionClassification,
) -> list[float]:
    return [
        value.confidence
        for field_name in ("discipline", "subject", "topic")
        if (value := getattr(classification, field_name)).value is not None
    ]


def question_quality_flags(
    question: QuestionRecord,
    classification: QuestionClassification,
) -> list[str]:
    flags: list[str] = []
    letters = [alternative.letter for alternative in question.alternatives]
    expected_letters = list("ABCDE"[: len(letters)])
    if (
        len(question.statement.strip()) < 10
        or not question.source_pages
        or not 2 <= len(letters) <= 5
        or letters != expected_letters
        or question.correct_answer is not None
        and question.correct_answer not in letters
    ):
        flags.append("incomplete")
    if not (question.explanation or "").strip():
        flags.append("without_explanation")
    if question.answer_status == "annulled":
        flags.append("annulled")
    if question.answer_status == "missing":
        flags.append("without_answer")
    searchable = _normalize(
        " ".join(
            [
                question.statement,
                *[item.text for item in question.alternatives],
                *question.review_notes,
            ]
        )
    )
    if any(
        marker in searchable
        for marker in ("alternativa visual", "imagem necessaria", "figura necessaria")
    ):
        flags.append("visual")
    required_values = (
        question.discipline,
        question.matter,
        question.subject,
        question.board,
        question.year,
        question.role,
        question.organization,
        question.concurso,
        question.level,
        question.difficulty,
    )
    if any(value is None or value == "" for value in required_values):
        flags.append("missing_fields")
    confidence_values = _classification_confidences(classification)
    if not confidence_values or min(confidence_values) < 0.65:
        flags.append("low_confidence")
    return list(dict.fromkeys(flags))


class DesktopStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    classifier_provider TEXT NOT NULL,
                    total_pages INTEGER NOT NULL DEFAULT 0,
                    processed_pages INTEGER NOT NULL DEFAULT 0,
                    current_file TEXT,
                    message TEXT,
                    error TEXT,
                    started_at TEXT,
                    eta_seconds INTEGER
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    local_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    sha256 TEXT,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    processed_pages INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    needs_ocr INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, local_path)
                );
                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    character_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    PRIMARY KEY(document_id, page_number)
                );
                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    question_number INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    classification_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    flags_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reviewer TEXT,
                    review_notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    exported_at TEXT,
                    UNIQUE(document_id, question_number)
                );
                CREATE INDEX IF NOT EXISTS questions_fingerprint_idx ON questions(fingerprint);
                CREATE INDEX IF NOT EXISTS questions_status_idx ON questions(status);
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    actor TEXT,
                    created_at TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    notes TEXT
                );
                CREATE TABLE IF NOT EXISTS saved_filters (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    filters_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            job_columns = {
                cast(str, row["name"])
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "started_at" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN started_at TEXT")
            if "eta_seconds" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN eta_seconds INTEGER")
            connection.commit()

    def create_job(
        self,
        paths: list[Path],
        metadata: DesktopImportMetadata,
        classifier_provider: ClassifierProviderName,
        *,
        metadata_by_path: dict[str, DesktopImportMetadata] | None = None,
    ) -> str:
        resolved = validate_pdf_batch(paths)
        document_metadata_by_path = {
            str(Path(path).resolve()).casefold(): value
            for path, value in (metadata_by_path or {}).items()
        }
        job_id = str(uuid.uuid4())
        created_at = _now()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, created_at, updated_at, status, classifier_provider, message
                ) VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, created_at, created_at, classifier_provider, "Aguardando processamento"),
            )
            for path in resolved:
                document_id = str(uuid.uuid4())
                document_metadata = document_metadata_by_path.get(
                    str(path).casefold(), metadata
                ).model_copy(deep=True)
                if document_metadata.external_id is None:
                    document_metadata.external_id = path.stem
                initial_sha256 = (
                    document_metadata.external_id.casefold()
                    if re.fullmatch(r"[0-9a-fA-F]{64}", document_metadata.external_id or "")
                    else None
                )
                connection.execute(
                    """
                    INSERT INTO documents (
                        id, job_id, local_path, filename, sha256, size_bytes, metadata_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        job_id,
                        str(path),
                        path.name,
                        initial_sha256,
                        path.stat().st_size,
                        _json(document_metadata.model_dump(mode="json")),
                        created_at,
                        created_at,
                    ),
                )
            connection.commit()
        return job_id

    def processed_sha256s(self, values: Iterable[str]) -> set[str]:
        requested = {value.casefold() for value in values if value}
        if not requested:
            return set()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT sha256
                FROM documents
                WHERE sha256 IS NOT NULL AND status IN ('extracted', 'processed')
                """
            ).fetchall()
        return {cast(str, row["sha256"]).casefold() for row in rows} & requested

    def cached_answer_keys(
        self,
        *,
        provider: str | None,
        concurso: str | None,
        exclude_job_id: str,
    ) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM documents
                WHERE job_id != ? AND status IN ('extracted', 'processed')
                ORDER BY updated_at DESC
                """,
                (exclude_job_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for row in rows:
            document = self._document_row(row)
            metadata = DesktopImportMetadata.model_validate(document["metadata"])
            if metadata.document_type != "answer_key":
                continue
            if provider is not None and metadata.provider != provider:
                continue
            if concurso is not None and metadata.concurso != concurso:
                continue
            digest = cast(str | None, document["sha256"])
            if digest and digest in seen_hashes:
                continue
            text = "\n".join(
                str(page["text"]) for page in self.pages(cast(str, document["id"]))
            )
            if not text.strip():
                continue
            if digest:
                seen_hashes.add(digest)
            result.append({**document, "answer_key_text": text})
        return result

    def list_jobs(self, limit: int = 12) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def job(self, job_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("lote nao encontrado")
        return dict(row)

    def job_question_summary(self, job_ids: list[str]) -> dict[str, int]:
        if not job_ids:
            return {
                "questions": 0,
                "matched_answers": 0,
                "annulled_answers": 0,
                "missing_answers": 0,
            }
        placeholders = ",".join("?" for _ in job_ids)
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS questions,
                       SUM(CASE WHEN json_extract(q.payload_json, '$.answer_status') = 'matched'
                           THEN 1 ELSE 0 END) AS matched_answers,
                       SUM(CASE WHEN json_extract(q.payload_json, '$.answer_status') = 'annulled'
                           THEN 1 ELSE 0 END) AS annulled_answers,
                       SUM(CASE WHEN json_extract(q.payload_json, '$.answer_status') = 'missing'
                           THEN 1 ELSE 0 END) AS missing_answers
                FROM questions q
                JOIN documents d ON d.id = q.document_id
                WHERE d.job_id IN ({placeholders})
                """,  # noqa: S608
                tuple(job_ids),
            ).fetchone()
        return {
            "questions": int(row["questions"] or 0),
            "matched_answers": int(row["matched_answers"] or 0),
            "annulled_answers": int(row["annulled_answers"] or 0),
            "missing_answers": int(row["missing_answers"] or 0),
        }

    def documents_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM documents WHERE job_id = ? ORDER BY filename", (job_id,)
            ).fetchall()
        return [self._document_row(row) for row in rows]

    def document(self, document_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise ValueError("documento nao encontrado")
        return self._document_row(row)

    @staticmethod
    def _document_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = json.loads(cast(str, payload.pop("metadata_json")))
        payload["warnings"] = json.loads(cast(str, payload.pop("warnings_json")))
        payload["needs_ocr"] = bool(payload["needs_ocr"])
        return payload

    def update_job(self, job_id: str, **changes: Any) -> None:
        allowed = {
            "status",
            "total_pages",
            "processed_pages",
            "current_file",
            "message",
            "error",
            "started_at",
            "eta_seconds",
        }
        selected = {key: value for key, value in changes.items() if key in allowed}
        if not selected:
            return
        selected["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in selected)
        with closing(self._connect()) as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",  # noqa: S608
                (*selected.values(), job_id),
            )
            connection.commit()

    def update_document(self, document_id: str, **changes: Any) -> None:
        allowed = {
            "sha256",
            "size_bytes",
            "page_count",
            "processed_pages",
            "status",
            "needs_ocr",
            "warnings_json",
            "metadata_json",
        }
        selected = {key: value for key, value in changes.items() if key in allowed}
        if not selected:
            return
        selected["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in selected)
        with closing(self._connect()) as connection:
            connection.execute(
                f"UPDATE documents SET {assignments} WHERE id = ?",  # noqa: S608
                (*selected.values(), document_id),
            )
            connection.commit()

    def update_document_metadata(
        self, document_id: str, metadata: DesktopImportMetadata, *, actor: str
    ) -> None:
        self.document(document_id)
        self.update_document(
            document_id,
            metadata_json=_json(metadata.model_dump(mode="json")),
        )
        for row in self._question_rows_for_document(document_id):
            question = QuestionRecord.model_validate(json.loads(cast(str, row["payload_json"])))
            updated = question.model_copy(
                update={
                    "discipline": metadata.discipline or question.discipline,
                    "matter": metadata.subject or question.matter,
                    "subject": metadata.topic or question.subject,
                    "board": metadata.board or question.board,
                    "organization": metadata.organization or question.organization,
                    "role": metadata.role or question.role,
                    "year": metadata.year or question.year,
                    "concurso": metadata.concurso or question.concurso,
                    "level": metadata.level or question.level,
                    "difficulty": metadata.difficulty or question.difficulty,
                }
            )
            classification = QuestionClassification.model_validate(
                json.loads(cast(str, row["classification_json"]))
            )
            self.update_question(cast(str, row["id"]), updated, classification, actor=actor)

    def page_exists(self, document_id: str, page_number: int) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM pages WHERE document_id = ? AND page_number = ?",
                (document_id, page_number),
            ).fetchone()
        return row is not None

    def save_page(
        self,
        document_id: str,
        page_number: int,
        text: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO pages (
                    document_id, page_number, text, character_count, status, error
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, page_number) DO UPDATE SET
                    text = excluded.text,
                    character_count = excluded.character_count,
                    status = excluded.status,
                    error = excluded.error
                """,
                (document_id, page_number, text, len(text), status, error),
            )
            connection.commit()

    def pages(self, document_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM pages WHERE document_id = ? ORDER BY page_number",
                (document_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_question(
        self,
        document_id: str,
        question: QuestionRecord,
        classification: QuestionClassification,
    ) -> str:
        question_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{question.number}"))
        fingerprint = question_fingerprint(question)
        flags = question_quality_flags(question, classification)
        confidence_values = _classification_confidences(classification)
        confidence = min(confidence_values, default=0)
        duplicate = self._duplicate_question_id(fingerprint, exclude_id=question_id)
        if duplicate is not None:
            flags.append("duplicate")
            self._add_flag(duplicate, "duplicate")
        status: DesktopQuestionStatus = (
            "exception"
            if any(
                flag in flags
                for flag in ("annulled", "visual", "without_answer", "incomplete")
            )
            else "pending"
        )
        created_at = _now()
        with closing(self._connect()) as connection:
            existing = connection.execute(
                """
                SELECT fingerprint, payload_json, status, reviewer, review_notes, exported_at
                FROM questions WHERE id = ?
                """,
                (question_id,),
            ).fetchone()
            decision_invalidated = (
                existing is not None
                and cast(str, existing["fingerprint"]) != fingerprint
                and existing["status"] in {"approved", "rejected", "exported"}
            )
            connection.execute(
                """
                INSERT INTO questions (
                    id, document_id, question_number, fingerprint, payload_json,
                    classification_json, confidence, flags_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, question_number) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    payload_json = excluded.payload_json,
                    classification_json = excluded.classification_json,
                    confidence = excluded.confidence,
                    flags_json = excluded.flags_json,
                    status = CASE
                        WHEN questions.fingerprint = excluded.fingerprint
                            AND questions.status IN ('approved', 'rejected', 'exported')
                        THEN questions.status ELSE excluded.status END,
                    reviewer = CASE WHEN questions.fingerprint = excluded.fingerprint
                        THEN questions.reviewer ELSE NULL END,
                    review_notes = CASE WHEN questions.fingerprint = excluded.fingerprint
                        THEN questions.review_notes ELSE NULL END,
                    exported_at = CASE WHEN questions.fingerprint = excluded.fingerprint
                        THEN questions.exported_at ELSE NULL END,
                    updated_at = excluded.updated_at
                """,
                (
                    question_id,
                    document_id,
                    question.number,
                    fingerprint,
                    _json(question.model_dump(mode="json")),
                    _json(classification.model_dump(mode="json")),
                    confidence,
                    _json(flags),
                    status,
                    created_at,
                    created_at,
                ),
            )
            if decision_invalidated and existing is not None:
                self._audit(
                    connection,
                    question_id,
                    "decision_invalidated",
                    None,
                    {
                        "status": existing["status"],
                        "fingerprint": existing["fingerprint"],
                        "question": json.loads(cast(str, existing["payload_json"])),
                    },
                    {
                        "status": status,
                        "fingerprint": fingerprint,
                        "question": question.model_dump(mode="json"),
                    },
                    "Decisão editorial invalidada após reprocessamento com conteúdo alterado.",
                )
            connection.commit()
        return question_id

    def _duplicate_question_id(self, fingerprint: str, *, exclude_id: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT id FROM questions WHERE fingerprint = ? AND id != ? LIMIT 1",
                (fingerprint, exclude_id),
            ).fetchone()
        return cast(str, row["id"]) if row is not None else None

    def _add_flag(self, question_id: str, flag: str) -> None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT flags_json FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
            if row is None:
                return
            flags = list(json.loads(cast(str, row["flags_json"])))
            if flag not in flags:
                flags.append(flag)
                connection.execute(
                    "UPDATE questions SET flags_json = ?, updated_at = ? WHERE id = ?",
                    (_json(flags), _now(), question_id),
                )
                connection.commit()

    def question(self, question_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT q.*, d.filename, d.local_path, d.sha256 AS document_sha256,
                       d.metadata_json, d.warnings_json, d.job_id
                FROM questions q JOIN documents d ON d.id = q.document_id
                WHERE q.id = ?
                """,
                (question_id,),
            ).fetchone()
        if row is None:
            raise ValueError("questao nao encontrada")
        return self._question_view(row)

    def update_question(
        self,
        question_id: str,
        question: QuestionRecord,
        classification: QuestionClassification | None = None,
        *,
        actor: str,
        notes: str | None = None,
    ) -> None:
        before = self.question(question_id)
        active_classification = classification or QuestionClassification.model_validate(
            before["classification"]
        )
        flags = question_quality_flags(question, active_classification)
        fingerprint = question_fingerprint(question)
        duplicate = self._duplicate_question_id(fingerprint, exclude_id=question_id)
        if duplicate is not None:
            flags.append("duplicate")
        confidence_values = _classification_confidences(active_classification)
        confidence = min(confidence_values, default=0)
        next_status: DesktopQuestionStatus = "pending"
        if any(
            flag in flags for flag in ("annulled", "visual", "without_answer", "incomplete")
        ):
            next_status = "exception"
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE questions SET payload_json = ?, classification_json = ?, fingerprint = ?,
                    confidence = ?, flags_json = ?, status = ?, reviewer = ?, review_notes = ?,
                    updated_at = ?, exported_at = NULL
                WHERE id = ?
                """,
                (
                    _json(question.model_dump(mode="json")),
                    _json(active_classification.model_dump(mode="json")),
                    fingerprint,
                    confidence,
                    _json(list(dict.fromkeys(flags))),
                    next_status,
                    actor,
                    notes,
                    _now(),
                    question_id,
                ),
            )
            self._audit(
                connection,
                question_id,
                "updated",
                actor,
                before,
                question.model_dump(mode="json"),
                notes,
            )
            connection.commit()

    def decide_question(
        self,
        question_id: str,
        status: Literal["approved", "rejected", "exception"],
        *,
        actor: str,
        notes: str | None,
    ) -> None:
        if not actor.strip():
            raise ValueError("informe o revisor")
        if status == "rejected" and not (notes or "").strip():
            raise ValueError("uma rejeicao exige justificativa")
        before = self.question(question_id)
        question = QuestionRecord.model_validate(before["question"])
        if status == "approved":
            errors = validate_editorial_question(question)
            if errors:
                raise ValueError("questao ainda nao exportavel: " + "; ".join(errors))
            if "duplicate" in before["flags"]:
                raise ValueError("resolva a duplicata antes de aprovar")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE questions SET status = ?, reviewer = ?, review_notes = ?,
                    updated_at = ?, exported_at = NULL WHERE id = ?
                """,
                (status, actor.strip(), notes, _now(), question_id),
            )
            self._audit(connection, question_id, status, actor, before, None, notes)
            connection.commit()

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        question_id: str,
        action: str,
        actor: str | None,
        before: Any,
        after: Any,
        notes: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log (
                question_id, action, actor, created_at, before_json, after_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                action,
                actor,
                _now(),
                _json(before) if before is not None else None,
                _json(after) if after is not None else None,
                notes,
            ),
        )

    def audit_log(self, question_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM audit_log WHERE question_id = ? ORDER BY id DESC",
                (question_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _question_rows_for_document(self, document_id: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return list(
                connection.execute(
                    "SELECT * FROM questions WHERE document_id = ? ORDER BY question_number",
                    (document_id,),
                ).fetchall()
            )

    def query(self, filters: DesktopFilterSet) -> dict[str, Any]:
        rows = self._all_question_rows()
        views = [self._question_view(row) for row in rows]
        selected = [view for view in views if self._matches(view, filters)]
        return {
            "questions": selected,
            "total": len(selected),
            "summary": self._summary(views),
            "facets": self._facets(views, filters),
            "filters": filters.model_dump(mode="json"),
        }

    def export_candidates(self, filters: DesktopFilterSet) -> list[dict[str, Any]]:
        return [
            self._question_view(row)
            for row in self._all_question_rows()
            if self._matches(self._question_view(row), filters)
        ]

    def _all_question_rows(self) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT q.*, d.filename, d.local_path, d.sha256 AS document_sha256,
                           d.metadata_json, d.warnings_json, d.job_id,
                           d.size_bytes, d.created_at AS document_created_at
                    FROM questions q JOIN documents d ON d.id = q.document_id
                    ORDER BY d.filename, q.question_number
                    """
                ).fetchall()
            )

    @staticmethod
    def _question_view(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        question = json.loads(cast(str, payload.pop("payload_json")))
        classification = json.loads(cast(str, payload.pop("classification_json")))
        flags = json.loads(cast(str, payload.pop("flags_json")))
        metadata = json.loads(cast(str, payload.pop("metadata_json")))
        warnings = json.loads(cast(str, payload.pop("warnings_json")))
        payload.update(
            {
                "question": question,
                "classification": classification,
                "flags": flags,
                "metadata": metadata,
                "document_warnings": warnings,
                "exportable": payload["status"] == "approved"
                and not validate_editorial_question(QuestionRecord.model_validate(question))
                and "duplicate" not in flags,
            }
        )
        if document_title := metadata.get("document_title"):
            payload["stored_filename"] = payload["filename"]
            payload["filename"] = document_title
        return payload

    @staticmethod
    def _summary(views: list[dict[str, Any]]) -> dict[str, int]:
        counts = Counter(cast(str, view["status"]) for view in views)
        counts["exportable"] = sum(bool(view["exportable"]) for view in views)
        counts["total"] = len(views)
        return dict(counts)

    def _matches(
        self,
        view: dict[str, Any],
        filters: DesktopFilterSet,
        *,
        skip: str | None = None,
    ) -> bool:
        question = cast(dict[str, Any], view["question"])
        metadata = cast(dict[str, Any], view["metadata"])
        fields: dict[str, tuple[str | int | None, list[str] | list[int]]] = {
            "source_files": (cast(str, view["filename"]), filters.source_files),
            "concursos": (question.get("concurso") or metadata.get("concurso"), filters.concursos),
            "boards": (question.get("board") or metadata.get("board"), filters.boards),
            "years": (question.get("year") or metadata.get("year"), filters.years),
            "roles": (question.get("role") or metadata.get("role"), filters.roles),
            "levels": (question.get("level") or metadata.get("level"), filters.levels),
            "disciplines": (
                question.get("discipline") or metadata.get("discipline"),
                filters.disciplines,
            ),
            "subjects": (question.get("matter") or metadata.get("subject"), filters.subjects),
            "topics": (question.get("subject") or metadata.get("topic"), filters.topics),
            "difficulties": (
                question.get("difficulty") or metadata.get("difficulty"),
                filters.difficulties,
            ),
        }
        for name, (actual, requested) in fields.items():
            if name == skip or not requested:
                continue
            if isinstance(actual, int):
                if actual not in requested:
                    return False
            elif actual is None or _normalize(str(actual)) not in {
                _normalize(str(value)) for value in requested
            }:
                return False
        if skip != "statuses" and filters.statuses:
            actual_statuses = {cast(str, view["status"])}
            if view["exportable"]:
                actual_statuses.add("exportable")
            if not actual_statuses.intersection(filters.statuses):
                return False
        if (
            skip != "quality_flags"
            and filters.quality_flags
            and not set(filters.quality_flags).intersection(cast(list[str], view["flags"]))
        ):
            return False
        if (
            skip != "min_confidence"
            and filters.min_confidence is not None
            and float(view["confidence"]) < filters.min_confidence
        ):
            return False
        if skip != "search" and filters.search.strip():
            haystack = _normalize(
                " ".join(
                    [
                        str(question.get("statement", "")),
                        *[str(item.get("text", "")) for item in question.get("alternatives", [])],
                    ]
                )
            )
            if _normalize(filters.search.strip()) not in haystack:
                return False
        return True

    def _facets(
        self, views: list[dict[str, Any]], filters: DesktopFilterSet
    ) -> dict[str, list[dict[str, Any]]]:
        definitions: dict[str, Any] = {
            "source_files": lambda view: view["filename"],
            "concursos": lambda view: (
                view["question"].get("concurso") or view["metadata"].get("concurso")
            ),
            "boards": lambda view: view["question"].get("board") or view["metadata"].get("board"),
            "years": lambda view: view["question"].get("year") or view["metadata"].get("year"),
            "roles": lambda view: view["question"].get("role") or view["metadata"].get("role"),
            "levels": lambda view: view["question"].get("level") or view["metadata"].get("level"),
            "disciplines": lambda view: (
                view["question"].get("discipline") or view["metadata"].get("discipline")
            ),
            "subjects": lambda view: (
                view["question"].get("matter") or view["metadata"].get("subject")
            ),
            "topics": lambda view: view["question"].get("subject") or view["metadata"].get("topic"),
            "difficulties": lambda view: (
                view["question"].get("difficulty") or view["metadata"].get("difficulty")
            ),
            "statuses": lambda view: (
                [view["status"], "exportable"] if view["exportable"] else view["status"]
            ),
            "quality_flags": lambda view: view["flags"],
        }
        selected = filters.model_dump(mode="json")
        facets: dict[str, list[dict[str, Any]]] = {}
        for name, getter in definitions.items():
            counts: Counter[str | int] = Counter()
            for view in views:
                if not self._matches(view, filters, skip=name):
                    continue
                value = getter(view)
                values: Iterable[str | int] = value if isinstance(value, list) else [value]
                counts.update(item for item in values if item is not None and item != "")
            facets[name] = [
                {
                    "value": value,
                    "count": count,
                    "selected": value in selected.get(name, []),
                }
                for value, count in sorted(
                    counts.items(), key=lambda item: (-item[1], str(item[0]).casefold())
                )
            ]
        return facets

    def save_filter(self, name: str, filters: DesktopFilterSet) -> dict[str, Any]:
        normalized = " ".join(name.split())
        if len(normalized) < 2:
            raise ValueError("informe um nome para o filtro")
        filter_id = str(uuid.uuid5(uuid.NAMESPACE_URL, normalized.casefold()))
        now = _now()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO saved_filters (id, name, filters_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET filters_json = excluded.filters_json,
                    updated_at = excluded.updated_at
                """,
                (filter_id, normalized, _json(filters.model_dump(mode="json")), now, now),
            )
            connection.commit()
        return {"id": filter_id, "name": normalized, "filters": filters.model_dump(mode="json")}

    def saved_filters(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM saved_filters ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "filters": json.loads(cast(str, row["filters_json"])),
            }
            for row in rows
        ]

    def delete_filter(self, filter_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM saved_filters WHERE id = ?", (filter_id,))
            connection.commit()

    def mark_exported(self, question_ids: list[str]) -> None:
        if not question_ids:
            return
        placeholders = ",".join("?" for _ in question_ids)
        now = _now()
        with closing(self._connect()) as connection:
            connection.execute(
                f"""UPDATE questions SET status = 'exported', exported_at = ?, updated_at = ?
                    WHERE id IN ({placeholders})""",  # noqa: S608
                (now, now, *question_ids),
            )
            connection.commit()

    def document_exceptions(self, document_ids: set[str] | None = None) -> list[dict[str, Any]]:
        if document_ids == set():
            return []
        parameters: tuple[str, ...] = ()
        document_clause = ""
        if document_ids is not None:
            placeholders = ",".join("?" for _ in document_ids)
            document_clause = f" AND id IN ({placeholders})"
            parameters = tuple(sorted(document_ids))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT id, filename, local_path, status, needs_ocr, warnings_json, metadata_json
                FROM documents WHERE (status = 'exception' OR needs_ocr = 1)
                {document_clause}
                ORDER BY filename
                """,  # noqa: S608
                parameters,
            ).fetchall()
        return [
            {
                "kind": "document",
                "documentId": row["id"],
                "filename": row["filename"],
                "needsOcr": bool(row["needs_ocr"]),
                "issues": json.loads(cast(str, row["warnings_json"])),
                "metadata": json.loads(cast(str, row["metadata_json"])),
            }
            for row in rows
        ]
