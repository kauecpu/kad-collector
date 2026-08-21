from __future__ import annotations

import hashlib
import json
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
from .document_contract import NormalizedDocument, normalize_local_document
from .models import QuestionRecord
from .semantic_identity import (
    IDENTITY_ALGORITHM_VERSION,
    AssociationCandidate,
    DocumentSemanticProfile,
    IdentityResolution,
    extract_semantic_profile,
)
from .semantic_registry import (
    ObservationClaim,
    active_answer_key_candidates,
    claim_document_observation,
    exam_documents_affected_by_answer_key,
    identity_events,
    initialize_semantic_schema,
    record_document_link,
    record_question_lineage,
    semantic_document_view,
    semantic_summary,
)
from .semantic_resolution import resolve_document_version, select_answer_key
from .validation import validate_editorial_question


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _editorial_metadata(document: NormalizedDocument) -> DesktopImportMetadata:
    raw = dict(document.metadata)
    values: dict[str, Any] = {
        key: raw[key]
        for key in DesktopImportMetadata.model_fields
        if key in raw and raw[key] is not None
    }
    if document.source_id is not None:
        values.setdefault("provider", document.source_id)
    values.setdefault("source_url", document.original_url)
    values.setdefault("canonical_url", document.resolved_url)
    values.setdefault("external_id", document.external_id)
    values.setdefault("document_title", document.title)
    values.setdefault(
        "document_type",
        (
            document.declared_type
            if document.declared_type in {"auto", "exam", "answer_key"}
            else "auto"
        ),
    )
    aliases = {
        "banca": "board",
        "ano": "year",
        "cargo": "role",
        "orgao": "organization",
    }
    for source, target in aliases.items():
        if target not in values and source in raw:
            values[target] = raw[source]
    return DesktopImportMetadata.model_validate(
        {key: value for key, value in values.items() if value is not None}
    )


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


def question_decision_fingerprint(question: QuestionRecord) -> str:
    payload = {
        "content": question_fingerprint(question),
        "answer_status": question.answer_status,
        "correct_answer": question.correct_answer,
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def invalidate_changed_official_answer(
    connection: sqlite3.Connection,
    *,
    question_id: str,
    document_id: str,
    document_version_id: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
    reason: str,
    changed_at: str,
) -> None:
    """Audit one invalidated human decision inside the caller's transaction."""
    connection.execute(
        "INSERT INTO audit_log (question_id, action, actor, created_at, before_json, "
        "after_json, notes) VALUES (?, 'decision_invalidated', NULL, ?, ?, ?, ?)",
        (question_id, changed_at, _json(before), _json(after), reason),
    )
    event_payload = {
        "questionId": question_id,
        "reason": reason,
        "before": before,
        "after": after,
    }
    event_key = hashlib.sha256(
        _json(
            {
                "action": "decision_invalidated",
                "question_id": question_id,
                "before_decision_fingerprint": before.get("decision_fingerprint"),
                "after_decision_fingerprint": after.get("decision_fingerprint"),
            }
        ).encode("utf-8")
    ).hexdigest()
    connection.execute(
        "INSERT OR IGNORE INTO document_identity_events ("
        "event_key, document_id, document_version_id, action, actor, algorithm_version, "
        "payload_json, created_at) VALUES (?, ?, ?, 'decision_invalidated', 'system', ?, ?, ?)",
        (
            event_key,
            document_id,
            document_version_id,
            IDENTITY_ALGORITHM_VERSION,
            _json(event_payload),
            changed_at,
        ),
    )


def carry_forward_question_decision(
    connection: sqlite3.Connection,
    *,
    predecessor: dict[str, Any],
    successor: dict[str, Any],
    lineage_id: str,
    document_id: str,
    document_version_id: str,
    recorded_at: str,
) -> bool:
    """Copy only a verified human decision into an identical successor."""
    predecessor_status = predecessor["status"]
    predecessor_decision = predecessor["decision_fingerprint"]
    if predecessor_status not in {"approved", "rejected", "exported"}:
        return False
    if successor["status"] in {"approved", "rejected", "exported"}:
        return False
    if (
        predecessor_decision is None
        or successor["decision_fingerprint"] is None
        or predecessor_decision != successor["decision_fingerprint"]
    ):
        return False
    next_status = "approved" if predecessor_status == "exported" else predecessor_status
    before = {
        "status": successor["status"],
        "reviewer": successor["reviewer"],
        "review_notes": successor["review_notes"],
        "exported_at": successor["exported_at"],
        "decision_fingerprint": successor["decision_fingerprint"],
    }
    after = {
        "status": next_status,
        "reviewer": predecessor["reviewer"],
        "review_notes": predecessor["review_notes"],
        "exported_at": None,
        "decision_fingerprint": successor["decision_fingerprint"],
        "predecessor_question_id": predecessor["id"],
    }
    if all(before.get(key) == after.get(key) for key in ("status", "reviewer", "review_notes")):
        return False
    updated = connection.execute(
        "UPDATE questions SET status = ?, reviewer = ?, review_notes = ?, exported_at = NULL, "
        "updated_at = ? WHERE id = ? AND decision_fingerprint = ? "
        "AND status NOT IN ('approved', 'rejected', 'exported')",
        (
            next_status,
            predecessor["reviewer"],
            predecessor["review_notes"],
            recorded_at,
            successor["id"],
            predecessor_decision,
        ),
    )
    if updated.rowcount != 1:
        return False
    connection.execute(
        "INSERT INTO audit_log (question_id, action, actor, created_at, before_json, "
        "after_json, notes) VALUES (?, 'decision_carried_forward', 'system', ?, ?, ?, ?)",
        (
            successor["id"],
            recorded_at,
            _json(before),
            _json(after),
            "Decisão editorial transportada de questão idêntica na versão predecessora.",
        ),
    )
    event_payload = {
        "lineageId": lineage_id,
        "predecessorQuestionId": predecessor["id"],
        "successorQuestionId": successor["id"],
        "status": next_status,
    }
    event_key = hashlib.sha256(
        _json({"action": "decision_carried_forward", "lineage_id": lineage_id}).encode("utf-8")
    ).hexdigest()
    connection.execute(
        "INSERT OR IGNORE INTO document_identity_events ("
        "event_key, document_id, document_version_id, action, actor, algorithm_version, "
        "payload_json, created_at) VALUES (?, ?, ?, 'decision_carried_forward', 'system', ?, ?, ?)",
        (
            event_key,
            document_id,
            document_version_id,
            IDENTITY_ALGORITHM_VERSION,
            _json(event_payload),
            recorded_at,
        ),
    )
    return True


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
                    normalized_json TEXT,
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
            document_columns = {
                cast(str, row["name"])
                for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "normalized_json" not in document_columns:
                connection.execute("ALTER TABLE documents ADD COLUMN normalized_json TEXT")
            initialize_semantic_schema(connection)
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
        documents: list[NormalizedDocument] = []
        for path in resolved:
            document_metadata = document_metadata_by_path.get(str(path).casefold(), metadata)
            document_metadata = document_metadata.model_copy(deep=True)
            if document_metadata.external_id is None:
                document_metadata.external_id = path.stem
            documents.append(
                normalize_local_document(path).model_copy(
                    update={
                        "declared_type": document_metadata.document_type,
                        "title": document_metadata.document_title or path.name,
                        "metadata": document_metadata.model_dump(
                            mode="json", exclude_none=True
                        )
                    }
                )
            )
        job_id = self.create_interpretation_job(documents, classifier_provider)
        if job_id is None:
            raise ValueError("todos os PDFs selecionados ja sao conhecidos")
        return job_id

    def create_interpretation_job(
        self,
        documents: list[NormalizedDocument],
        classifier_provider: ClassifierProviderName,
        *,
        force_reprocess: bool = False,
    ) -> str | None:
        if not documents:
            raise ValueError("selecione ao menos um PDF")
        observed_at = _now()
        for attempt in range(2):
            job_id = str(uuid.uuid4())
            with closing(self._connect()) as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    claimed: list[tuple[NormalizedDocument, ObservationClaim]] = [
                        (
                            normalized_document,
                            claim_document_observation(
                                connection, normalized_document, observed_at
                            ),
                        )
                        for normalized_document in documents
                    ]
                    selected = [
                        item
                        for item in claimed
                        if force_reprocess or not item[1].exact_duplicate
                    ]
                    if not selected:
                        connection.commit()
                        return None
                    connection.execute(
                        """
                        INSERT INTO jobs (
                            id, created_at, updated_at, status, classifier_provider, message
                        ) VALUES (?, ?, ?, 'queued', ?, ?)
                        """,
                        (
                            job_id,
                            observed_at,
                            observed_at,
                            classifier_provider,
                            "Aguardando processamento",
                        ),
                    )
                    for normalized_document, claim in selected:
                        document_id = str(uuid.uuid4())
                        document_metadata = _editorial_metadata(normalized_document)
                        path = Path(normalized_document.local_path).resolve()
                        connection.execute(
                            """
                            INSERT INTO documents (
                                id, job_id, local_path, filename, sha256, size_bytes,
                                metadata_json, normalized_json, warnings_json, created_at,
                                updated_at, document_version_id, observation_id,
                                semantic_resolution
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                document_id,
                                job_id,
                                str(path),
                                path.name,
                                normalized_document.sha256,
                                normalized_document.size_bytes,
                                _json(document_metadata.model_dump(mode="json")),
                                _json(normalized_document.model_dump(mode="json")),
                                _json(normalized_document.warnings),
                                observed_at,
                                observed_at,
                                claim.document_version_id,
                                claim.observation_id,
                                claim.resolution_status,
                            ),
                        )
                        if not claim.exact_duplicate:
                            connection.execute(
                                "UPDATE document_observations SET document_id = ? WHERE id = ?",
                                (document_id, claim.observation_id),
                            )
                    connection.commit()
                    return job_id
                except sqlite3.IntegrityError:
                    connection.rollback()
                    if attempt == 1:
                        raise
        raise RuntimeError("nao foi possivel registrar a tarefa de interpretacao")

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

    def semantic_document_view(self, document_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            return semantic_document_view(connection, document_id)

    def semantic_summary(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            return semantic_summary(connection)

    def identity_events(self, document_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return identity_events(connection, document_id)

    def answer_key_candidates(self, exam_version_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return active_answer_key_candidates(connection, exam_version_id)

    def exams_affected_by_answer_key(
        self, answer_key_version_id: str
    ) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return exam_documents_affected_by_answer_key(connection, answer_key_version_id)

    def active_answer_key_version(self, exam_version_id: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT answer_key_version_id FROM document_links "
                "WHERE exam_version_id = ? AND status = 'active'",
                (exam_version_id,),
            ).fetchone()
        return cast(str, row["answer_key_version_id"]) if row is not None else None

    def answer_key_document(self, answer_key_version_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id FROM documents WHERE document_version_id = ? "
                "ORDER BY created_at, id",
                (answer_key_version_id,),
            ).fetchall()
        for row in rows:
            document = self.document(cast(str, row["id"]))
            text = "\n".join(str(page["text"]) for page in self.pages(cast(str, row["id"])))
            if text.strip():
                return {
                    **document,
                    "answer_key_text": text,
                    "version_id": answer_key_version_id,
                }
        return None

    def question_records(
        self, document_id: str
    ) -> list[tuple[QuestionRecord, QuestionClassification]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload_json, classification_json FROM questions "
                "WHERE document_id = ? ORDER BY question_number",
                (document_id,),
            ).fetchall()
        return [
            (
                QuestionRecord.model_validate(json.loads(cast(str, row["payload_json"]))),
                QuestionClassification.model_validate(
                    json.loads(cast(str, row["classification_json"]))
                ),
            )
            for row in rows
        ]

    def persist_document_link(
        self,
        exam_version_id: str,
        answer_key_version_id: str,
        decision: Any,
    ) -> str | None:
        with closing(self._connect()) as connection:
            link_id = record_document_link(
                connection, exam_version_id, answer_key_version_id, decision, _now()
            )
            connection.commit()
        return link_id

    def apply_answer_key_updates(
        self,
        document_id: str,
        exam_version_id: str,
        answer_key_version_id: str,
        decision: Any,
        updates: dict[int, tuple[str, str | None]],
    ) -> bool:
        """Atomically update recognized answers and replace the active semantic link."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    "SELECT answer_key_version_id FROM document_links "
                    "WHERE exam_version_id = ? AND status = 'active'",
                    (exam_version_id,),
                ).fetchone()
                if (
                    current is not None
                    and current["answer_key_version_id"] == answer_key_version_id
                ):
                    connection.commit()
                    return False
                exam_row = connection.execute(
                    "SELECT profile_json FROM document_versions "
                    "WHERE id = ? AND document_role = 'exam'",
                    (exam_version_id,),
                ).fetchone()
                if exam_row is None:
                    connection.rollback()
                    return False
                candidates = active_answer_key_candidates(connection, exam_version_id)
                current_decision = select_answer_key(
                    DocumentSemanticProfile.model_validate_json(
                        cast(str, exam_row["profile_json"])
                    ),
                    [
                        AssociationCandidate(
                            version_id=cast(str, candidate["answer_key_version_id"]),
                            profile=DocumentSemanticProfile.model_validate_json(
                                _json(candidate["profile"])
                            ),
                            predecessor_version_id=cast(
                                str | None, candidate["predecessor_version_id"]
                            ),
                        )
                        for candidate in candidates
                    ],
                )
                if current_decision.selected_version_id != answer_key_version_id:
                    connection.commit()
                    return False
                rows = connection.execute(
                    "SELECT * FROM questions WHERE document_id = ? ORDER BY question_number",
                    (document_id,),
                ).fetchall()
                applied = 0
                now = _now()
                for row in rows:
                    update = updates.get(int(row["question_number"]))
                    if update is None:
                        continue
                    question = QuestionRecord.model_validate(
                        json.loads(cast(str, row["payload_json"]))
                    ).model_copy(
                        update={"answer_status": update[0], "correct_answer": update[1]}
                    )
                    classification = QuestionClassification.model_validate(
                        json.loads(cast(str, row["classification_json"]))
                    )
                    fingerprint = question_fingerprint(question)
                    decision_fingerprint = question_decision_fingerprint(question)
                    flags = question_quality_flags(question, classification)
                    duplicate = connection.execute(
                        "SELECT 1 FROM questions WHERE fingerprint = ? AND id != ? LIMIT 1",
                        (fingerprint, row["id"]),
                    ).fetchone()
                    if duplicate is not None:
                        flags.append("duplicate")
                    confidence = min(_classification_confidences(classification), default=0)
                    next_status: DesktopQuestionStatus = (
                        "exception"
                        if any(
                            flag in flags
                            for flag in ("annulled", "visual", "without_answer", "incomplete")
                        )
                        else "pending"
                    )
                    decision_unchanged = (
                        row["decision_fingerprint"] is not None
                        and row["decision_fingerprint"] == decision_fingerprint
                    )
                    status = (
                        cast(DesktopQuestionStatus, row["status"])
                        if decision_unchanged
                        and row["status"] in {"approved", "rejected", "exported"}
                        else next_status
                    )
                    reviewer = row["reviewer"] if decision_unchanged else None
                    review_notes = row["review_notes"] if decision_unchanged else None
                    exported_at = row["exported_at"] if decision_unchanged else None
                    connection.execute(
                        "UPDATE questions SET payload_json = ?, fingerprint = ?, "
                        "decision_fingerprint = ?, confidence = ?, flags_json = ?, status = ?, "
                        "reviewer = ?, review_notes = ?, exported_at = ?, updated_at = ? "
                        "WHERE id = ?",
                        (
                            _json(question.model_dump(mode="json")),
                            fingerprint,
                            decision_fingerprint,
                            confidence,
                            _json(list(dict.fromkeys(flags))),
                            status,
                            reviewer,
                            review_notes,
                            exported_at,
                            now,
                            row["id"],
                        ),
                    )
                    if (
                        row["status"] in {"approved", "rejected", "exported"}
                        and not decision_unchanged
                    ):
                        invalidate_changed_official_answer(
                            connection,
                            question_id=cast(str, row["id"]),
                            document_id=document_id,
                            document_version_id=exam_version_id,
                            before={
                                "status": row["status"],
                                "reviewer": row["reviewer"],
                                "review_notes": row["review_notes"],
                                "exported_at": row["exported_at"],
                                "decision_fingerprint": row["decision_fingerprint"],
                                "question": json.loads(cast(str, row["payload_json"])),
                            },
                            after={
                                "status": status,
                                "reviewer": None,
                                "review_notes": None,
                                "exported_at": None,
                                "decision_fingerprint": decision_fingerprint,
                                "question": question.model_dump(mode="json"),
                            },
                            reason="Decisão editorial invalidada após mudança da resposta oficial.",
                            changed_at=now,
                        )
                    applied += 1
                if not applied:
                    connection.rollback()
                    return False
                link_id = record_document_link(
                    connection,
                    exam_version_id,
                    answer_key_version_id,
                    current_decision,
                    now,
                )
                if link_id is None:
                    connection.rollback()
                    return False
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def reconcile_question_lineage(self, document_id: str) -> int:
        """Compare one successor exam with its operational predecessor atomically."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                successor_document = connection.execute(
                    "SELECT document_version_id FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
                if successor_document is None or successor_document["document_version_id"] is None:
                    connection.commit()
                    return 0
                successor_version_id = cast(str, successor_document["document_version_id"])
                version = connection.execute(
                    "SELECT predecessor_version_id FROM document_versions "
                    "WHERE id = ? AND document_role = 'exam'",
                    (successor_version_id,),
                ).fetchone()
                if version is None or version["predecessor_version_id"] is None:
                    connection.commit()
                    return 0
                predecessor_version_id = cast(str, version["predecessor_version_id"])
                predecessor_document = connection.execute(
                    "SELECT d.id FROM documents d JOIN questions q ON q.document_id = d.id "
                    "WHERE d.document_version_id = ? GROUP BY d.id "
                    "ORDER BY COUNT(q.id) DESC, d.created_at, d.id LIMIT 1",
                    (predecessor_version_id,),
                ).fetchone()
                if predecessor_document is None:
                    connection.commit()
                    return 0
                predecessor_rows = {
                    int(row["question_number"]): dict(row)
                    for row in connection.execute(
                        "SELECT * FROM questions WHERE document_id = ? ORDER BY question_number",
                        (predecessor_document["id"],),
                    ).fetchall()
                }
                successor_rows = {
                    int(row["question_number"]): dict(row)
                    for row in connection.execute(
                        "SELECT * FROM questions WHERE document_id = ? ORDER BY question_number",
                        (document_id,),
                    ).fetchall()
                }
                recorded_at = _now()
                recorded = 0
                for number in sorted(predecessor_rows.keys() | successor_rows.keys()):
                    predecessor = predecessor_rows.get(number)
                    successor = successor_rows.get(number)
                    content_equal = False
                    answer_equal = False
                    if predecessor is None:
                        comparison = "added"
                        reason = "Questão adicionada na versão sucessora."
                    elif successor is None:
                        comparison = "removed"
                        reason = "Questão ausente na versão sucessora."
                    else:
                        content_equal = predecessor["fingerprint"] == successor["fingerprint"]
                        predecessor_decision = predecessor["decision_fingerprint"]
                        successor_decision = successor["decision_fingerprint"]
                        predecessor_question = json.loads(
                            cast(str, predecessor["payload_json"])
                        )
                        successor_question = json.loads(cast(str, successor["payload_json"]))
                        if (
                            content_equal
                            and predecessor_question.get("answer_status") != "missing"
                            and successor_question.get("answer_status") == "missing"
                        ):
                            continue
                        if predecessor_decision is None or successor_decision is None:
                            comparison = "changed"
                            reason = (
                                "Impressão de decisão legada ausente; decisão não transportada."
                            )
                        else:
                            answer_equal = (
                                predecessor_question.get("answer_status")
                                == successor_question.get("answer_status")
                                and predecessor_question.get("correct_answer")
                                == successor_question.get("correct_answer")
                            )
                            comparison = (
                                "unchanged" if content_equal and answer_equal else "changed"
                            )
                            if not content_equal:
                                reason = "Enunciado ou alternativas alterados na versão sucessora."
                            elif not answer_equal:
                                reason = "Resposta oficial alterada na versão sucessora."
                            else:
                                reason = "Conteúdo e resposta oficial permanecem idênticos."
                    lineage, created = record_question_lineage(
                        connection,
                        predecessor_version_id=predecessor_version_id,
                        successor_version_id=successor_version_id,
                        question_number=number,
                        predecessor_question_id=(
                            cast(str, predecessor["id"]) if predecessor is not None else None
                        ),
                        successor_question_id=(
                            cast(str, successor["id"]) if successor is not None else None
                        ),
                        comparison=comparison,
                        content_equal=content_equal,
                        answer_equal=answer_equal,
                        reason=reason,
                        recorded_at=recorded_at,
                    )
                    if not created:
                        continue
                    recorded += 1
                    if predecessor is None or successor is None:
                        continue
                    if comparison == "unchanged":
                        carry_forward_question_decision(
                            connection,
                            predecessor=predecessor,
                            successor=successor,
                            lineage_id=cast(str, lineage["id"]),
                            document_id=document_id,
                            document_version_id=successor_version_id,
                            recorded_at=recorded_at,
                        )
                    elif (
                        predecessor["status"] in {"approved", "rejected", "exported"}
                        and successor["status"]
                        not in {"approved", "rejected", "exported"}
                    ):
                        flags = set(json.loads(cast(str, successor["flags_json"])))
                        next_status: DesktopQuestionStatus = (
                            "exception"
                            if flags.intersection(
                                {"annulled", "visual", "without_answer", "incomplete"}
                            )
                            else "pending"
                        )
                        connection.execute(
                            "UPDATE questions SET status = ?, reviewer = NULL, "
                            "review_notes = NULL, exported_at = NULL, updated_at = ? WHERE id = ?",
                            (next_status, recorded_at, successor["id"]),
                        )
                        invalidate_changed_official_answer(
                            connection,
                            question_id=cast(str, successor["id"]),
                            document_id=document_id,
                            document_version_id=successor_version_id,
                            before={
                                "source_question_id": predecessor["id"],
                                "status": predecessor["status"],
                                "reviewer": predecessor["reviewer"],
                                "review_notes": predecessor["review_notes"],
                                "exported_at": predecessor["exported_at"],
                                "decision_fingerprint": predecessor["decision_fingerprint"],
                            },
                            after={
                                "status": next_status,
                                "reviewer": None,
                                "review_notes": None,
                                "exported_at": None,
                                "decision_fingerprint": successor["decision_fingerprint"],
                            },
                            reason=reason,
                            changed_at=recorded_at,
                        )
                connection.commit()
                return recorded
            except Exception:
                connection.rollback()
                raise

    def resolve_extracted_document(self, document_id: str) -> IdentityResolution:
        document = self.document(document_id)
        normalized = cast(NormalizedDocument | None, document["normalized_document"])
        if normalized is None:
            raise ValueError("documento não possui contrato normalizado")
        pages = [(int(page["page_number"]), str(page["text"])) for page in self.pages(document_id)]
        profile = extract_semantic_profile(normalized, pages)
        with closing(self._connect()) as connection:
            result = resolve_document_version(connection, document_id, profile, _now())
            connection.commit()
        return result

    def reprocessing_contract(self, document_id: str) -> NormalizedDocument:
        """Return a local contract copy without changing historical document evidence."""
        document = self.document(document_id)
        normalized = cast(NormalizedDocument | None, document["normalized_document"])
        if normalized is not None:
            return normalized.model_copy(update={"entry_method": "reprocessing"})

        metadata = cast(dict[str, Any], document["metadata"])
        digest = document["sha256"]
        size_bytes = int(document["size_bytes"])
        if not isinstance(digest, str) or not digest or size_bytes < 1:
            raise ValueError("documento legado nao possui integridade local comprovada")
        declared_type = metadata.get("document_type", "auto")
        if declared_type not in {"auto", "exam", "answer_key"}:
            declared_type = "auto"
        title = metadata.get("document_title")
        original_url = metadata.get("source_url")
        resolved_url = metadata.get("canonical_url")
        return NormalizedDocument(
            local_path=cast(str, document["local_path"]),
            sha256=digest,
            size_bytes=size_bytes,
            declared_type=declared_type,
            title=title if isinstance(title, str) and title else cast(str, document["filename"]),
            original_url=original_url if isinstance(original_url, str) else None,
            resolved_url=resolved_url if isinstance(resolved_url, str) else None,
            entry_method="reprocessing",
            metadata={
                key: value for key, value in metadata.items() if isinstance(value, (str, int))
            },
            warnings=[
                "contrato legado reprocessado com compatibilidade; campos de origem ausentes "
                "foram preservados como desconhecidos"
            ],
            external_id=(
                metadata["external_id"]
                if isinstance(metadata.get("external_id"), str)
                else None
            ),
        )

    @staticmethod
    def _document_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = json.loads(cast(str, payload.pop("metadata_json")))
        payload["warnings"] = json.loads(cast(str, payload.pop("warnings_json")))
        normalized_json = payload.pop("normalized_json", None)
        payload["normalized_document"] = (
            NormalizedDocument.model_validate(json.loads(cast(str, normalized_json)))
            if normalized_json is not None
            else None
        )
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
        decision_fingerprint = question_decision_fingerprint(question)
        flags = question_quality_flags(question, classification)
        confidence_values = _classification_confidences(classification)
        confidence = min(confidence_values, default=0)
        duplicate = self._duplicate_question_id(fingerprint, exclude_id=question_id)
        if duplicate is not None:
            flags.append("duplicate")
            if not self._is_reprocessing_document(document_id):
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
                SELECT fingerprint, decision_fingerprint, payload_json, status, reviewer,
                       review_notes, exported_at
                FROM questions WHERE id = ?
                """,
                (question_id,),
            ).fetchone()
            decision_invalidated = (
                existing is not None
                and (
                    existing["decision_fingerprint"] is None
                    or existing["decision_fingerprint"] != decision_fingerprint
                )
                and existing["status"] in {"approved", "rejected", "exported"}
            )
            connection.execute(
                """
                INSERT INTO questions (
                    id, document_id, question_number, fingerprint, decision_fingerprint,
                    payload_json,
                    classification_json, confidence, flags_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, question_number) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    decision_fingerprint = excluded.decision_fingerprint,
                    payload_json = excluded.payload_json,
                    classification_json = excluded.classification_json,
                    confidence = excluded.confidence,
                    flags_json = excluded.flags_json,
                    status = CASE
                        WHEN questions.decision_fingerprint IS NOT NULL
                            AND questions.decision_fingerprint = excluded.decision_fingerprint
                            AND questions.status IN ('approved', 'rejected', 'exported')
                        THEN questions.status ELSE excluded.status END,
                    reviewer = CASE WHEN questions.decision_fingerprint IS NOT NULL
                            AND questions.decision_fingerprint = excluded.decision_fingerprint
                        THEN questions.reviewer ELSE NULL END,
                    review_notes = CASE WHEN questions.decision_fingerprint IS NOT NULL
                            AND questions.decision_fingerprint = excluded.decision_fingerprint
                        THEN questions.review_notes ELSE NULL END,
                    exported_at = CASE WHEN questions.decision_fingerprint IS NOT NULL
                            AND questions.decision_fingerprint = excluded.decision_fingerprint
                        THEN questions.exported_at ELSE NULL END,
                    updated_at = excluded.updated_at
                """,
                (
                    question_id,
                    document_id,
                    question.number,
                    fingerprint,
                    decision_fingerprint,
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
                document = connection.execute(
                    "SELECT document_version_id FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                invalidate_changed_official_answer(
                    connection,
                    question_id=question_id,
                    document_id=document_id,
                    document_version_id=cast(
                        str | None, document["document_version_id"] if document else None
                    ),
                    before={
                        "status": existing["status"],
                        "fingerprint": existing["fingerprint"],
                        "decision_fingerprint": existing["decision_fingerprint"],
                        "question": json.loads(cast(str, existing["payload_json"])),
                    },
                    after={
                        "status": status,
                        "fingerprint": fingerprint,
                        "decision_fingerprint": decision_fingerprint,
                        "question": question.model_dump(mode="json"),
                    },
                    reason="Decisão editorial invalidada após reprocessamento alterado.",
                    changed_at=created_at,
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

    def _is_reprocessing_document(self, document_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT normalized_json FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        if row is None or row["normalized_json"] is None:
            return False
        payload = json.loads(cast(str, row["normalized_json"]))
        entry_method = cast(str | None, payload.get("entry_method"))
        return entry_method == "reprocessing"

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
        decision_fingerprint = question_decision_fingerprint(question)
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
                    decision_fingerprint = ?, confidence = ?, flags_json = ?, status = ?,
                    reviewer = ?, review_notes = ?, updated_at = ?, exported_at = NULL
                WHERE id = ?
                """,
                (
                    _json(question.model_dump(mode="json")),
                    _json(active_classification.model_dump(mode="json")),
                    fingerprint,
                    decision_fingerprint,
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
        status: Literal["pending", "approved", "rejected", "exception"],
        *,
        actor: str,
        notes: str | None,
    ) -> None:
        if not actor.strip():
            raise ValueError("informe o revisor")
        if status in {"rejected", "exception"} and not (notes or "").strip():
            action = "excecao" if status == "exception" else "rejeicao"
            raise ValueError(f"uma {action} exige justificativa")
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
            action = "deferred" if status == "pending" else status
            self._audit(
                connection,
                question_id,
                action,
                actor,
                before,
                {"status": status},
                notes,
            )
            connection.commit()

    def approve_questions(
        self,
        question_ids: list[str],
        *,
        actor: str,
        notes: str | None = None,
    ) -> int:
        reviewer = actor.strip()
        if not reviewer:
            raise ValueError("informe o revisor")
        normalized_ids = list(dict.fromkeys(question_ids))
        if not normalized_ids:
            raise ValueError("selecione ao menos uma questao")
        if len(normalized_ids) > 1_000:
            raise ValueError("a aprovacao em lote aceita no maximo 1000 questoes")

        before_views = [self.question(question_id) for question_id in normalized_ids]
        invalid: list[str] = []
        for before in before_views:
            if before["status"] != "pending":
                invalid.append(f"questao {before['question']['number']}: nao esta pendente")
                continue
            question = QuestionRecord.model_validate(before["question"])
            errors = validate_editorial_question(question)
            if "duplicate" in before["flags"]:
                errors.append(f"questao {question.number}: duplicata nao resolvida")
            invalid.extend(errors)
        if invalid:
            raise ValueError("lote nao exportavel: " + "; ".join(invalid[:20]))

        now = _now()
        with closing(self._connect()) as connection:
            for before in before_views:
                question_id = cast(str, before["id"])
                cursor = connection.execute(
                    """
                    UPDATE questions SET status = 'approved', reviewer = ?, review_notes = ?,
                        updated_at = ?, exported_at = NULL
                    WHERE id = ? AND status = 'pending'
                    """,
                    (reviewer, notes, now, question_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("a fila mudou durante a aprovacao; tente novamente")
                self._audit(
                    connection,
                    question_id,
                    "approved",
                    reviewer,
                    before,
                    {"status": "approved"},
                    notes,
                )
            connection.commit()
        return len(before_views)

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
        for status in ("pending", "approved", "rejected", "exception", "exported"):
            counts.setdefault(status, 0)
        answer_statuses = Counter(
            cast(str, view["question"]["answer_status"]) for view in views
        )
        counts["answer_matched"] = answer_statuses["matched"]
        counts["answer_annulled"] = answer_statuses["annulled"]
        counts["answer_official"] = (
            answer_statuses["matched"] + answer_statuses["annulled"]
        )
        counts["answer_missing"] = answer_statuses["missing"]
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
            "variants": (metadata.get("variant"), filters.variants),
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
            "variants": lambda view: view["metadata"].get("variant"),
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
