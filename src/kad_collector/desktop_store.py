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

from .answer_association import decide_runtime_association, invalidate_answer_association
from .answer_key import parse_answer_key
from .answer_key_diagnostics import AnswerKeyEvidence, diagnose_answer_key
from .canonical_classification import (
    initialize_canonical_classification_schema,
    invalidate_canonical_classification,
)
from .canonical_identity import (
    canonical_identity_for_version,
    canonical_summary,
    initialize_canonical_identity_schema,
)
from .desktop_limits import validate_pdf_batch
from .desktop_models import (
    ClassifierProviderName,
    DesktopFilterSet,
    DesktopImportMetadata,
    DesktopQuestionStatus,
    QuestionClassification,
)
from .document_contract import NormalizedDocument, normalize_local_document
from .editorial_taxonomy import EditorialTaxonomy
from .import_readiness import diagnose_import_readiness
from .models import QuestionRecord
from .question_equivalence import (
    initialize_question_equivalence_schema,
    invalidate_question_equivalence,
    question_equivalence_view,
    sync_canonical_editorial_from_question,
)
from .semantic_identity import (
    IDENTITY_ALGORITHM_VERSION,
    DocumentAssociationDecision,
    DocumentSemanticProfile,
    IdentityResolution,
    extract_semantic_profile,
    semantic_public_dto,
)
from .semantic_registry import (
    ObservationClaim,
    active_answer_key_candidates,
    affected_exam_documents_after_identity_correction,
    claim_document_observation,
    exam_documents_affected_by_answer_key,
    identity_events,
    initialize_semantic_schema,
    persist_identity_correction,
    reconcile_question_lineage_after_correction,
    record_corrected_document_link,
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
        "ano_publicacao": "year",
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
    if question.difficulty is None:
        flags.append("without_difficulty")
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
                    parsing_result_json TEXT,
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
                CREATE TABLE IF NOT EXISTS classification_review_batches (
                    id TEXT PRIMARY KEY,
                    confirmation_token TEXT NOT NULL,
                    suggestion_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reverted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS classification_review_batch_items (
                    batch_id TEXT NOT NULL REFERENCES classification_review_batches(id),
                    question_id TEXT NOT NULL REFERENCES questions(id),
                    before_classification_json TEXT NOT NULL,
                    after_classification_json TEXT NOT NULL,
                    PRIMARY KEY(batch_id, question_id)
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
            if "parsing_result_json" not in document_columns:
                connection.execute("ALTER TABLE documents ADD COLUMN parsing_result_json TEXT")
            initialize_semantic_schema(connection)
            initialize_canonical_identity_schema(connection)
            initialize_question_equivalence_schema(connection)
            initialize_canonical_classification_schema(connection)
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
            view = semantic_document_view(connection, document_id)
            version_id = cast(str | None, view["documentVersionId"])
            view["canonicalIdentity"] = (
                canonical_identity_for_version(connection, version_id)
                if version_id is not None
                else None
            )
            return view

    def semantic_summary(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            return semantic_summary(connection)

    def canonical_summary(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            return canonical_summary(connection)

    def semantic_presentation_summary(self) -> dict[str, int]:
        """Return the compact, stable semantic read model for the desktop bootstrap."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM document_observations) AS observations,
                    (SELECT COUNT(*) FROM document_versions) AS logical_versions,
                    (SELECT COUNT(*) FROM document_identity_events
                     WHERE action = 'exact_duplicate') AS exact_duplicates,
                    (SELECT COUNT(*) FROM documents
                     WHERE semantic_resolution = 'republication') AS republications,
                    (SELECT COUNT(*) FROM document_links
                     WHERE status = 'active') AS active_links,
                    (SELECT COUNT(*) FROM documents
                     WHERE semantic_resolution = 'uncertain') AS uncertain
                """
            ).fetchone()
        assert row is not None
        return {
            "observations": int(row["observations"]),
            "logicalVersions": int(row["logical_versions"]),
            "exactDuplicates": int(row["exact_duplicates"]),
            "republications": int(row["republications"]),
            "activeLinks": int(row["active_links"]),
            "uncertain": int(row["uncertain"]),
        }

    def operational_presentation_summary(self) -> dict[str, Any]:
        """Return a read-only overview of the desktop preparation pipeline."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM documents) AS documents,
                    (SELECT COUNT(*) FROM documents
                     WHERE status = 'completed') AS documents_completed,
                    (SELECT COUNT(*) FROM documents
                     WHERE status IN ('failed', 'exception')) AS documents_blocked,
                    (SELECT COUNT(*) FROM questions) AS raw_questions,
                    (SELECT COUNT(*) FROM question_occurrences
                     WHERE scope_id IS NOT NULL) AS occurrences,
                    (SELECT COUNT(*) FROM question_equivalence_groups) AS groups_total,
                    (SELECT COUNT(*) FROM question_equivalence_groups
                     WHERE status = 'confirmed') AS groups_confirmed,
                    (SELECT COUNT(*) FROM question_equivalence_groups
                     WHERE status != 'confirmed') AS groups_pending,
                    (SELECT COUNT(*) FROM canonical_questions cq
                     JOIN question_equivalence_groups g ON g.id=cq.group_id
                     WHERE g.status='confirmed') AS canonical_questions,
                    (SELECT COUNT(*) FROM canonical_questions cq
                     JOIN question_equivalence_groups g ON g.id=cq.group_id
                     WHERE g.status='confirmed'
                       AND cq.editorial_status = 'blocked') AS canonical_blocked,
                    (SELECT COUNT(*) FROM question_equivalence_review_queue
                     WHERE status = 'pending') AS equivalence_review_pending
                """
            ).fetchone()
            document_rows = connection.execute(
                "SELECT metadata_json FROM documents"
            ).fetchall()
        assert row is not None
        exams = 0
        answer_keys = 0
        for document in document_rows:
            try:
                metadata = json.loads(cast(str, document["metadata_json"]))
            except (TypeError, json.JSONDecodeError):
                continue
            document_type = metadata.get("document_type")
            if document_type == "exam":
                exams += 1
            elif document_type == "answer_key":
                answer_keys += 1
        return {
            "documents": int(row["documents"]),
            "documentsCompleted": int(row["documents_completed"]),
            "documentsBlocked": int(row["documents_blocked"]),
            "exams": exams,
            "answerKeys": answer_keys,
            "rawQuestions": int(row["raw_questions"]),
            "occurrences": int(row["occurrences"]),
            "equivalenceGroups": int(row["groups_total"]),
            "confirmedGroups": int(row["groups_confirmed"]),
            "pendingGroups": int(row["groups_pending"]),
            "canonicalQuestions": int(row["canonical_questions"]),
            "canonicalBlocked": int(row["canonical_blocked"]),
            "equivalenceReviewPending": int(row["equivalence_review_pending"]),
        }

    def identity_events(self, document_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return identity_events(connection, document_id)

    def document_identity(self, document_id: str) -> dict[str, Any]:
        """Return semantic identity metadata without document page content."""
        view = self.semantic_document_view(document_id)
        profile = cast(dict[str, Any] | None, view["profile"])
        events = self.identity_events(document_id)
        uncertain_event = next(
            (
                event
                for event in events
                if event["action"] == "uncertain" and isinstance(event["payload"], dict)
            ),
            None,
        )
        uncertain_payload = (
            cast(dict[str, Any], uncertain_event["payload"])
            if uncertain_event is not None
            else {}
        )
        if view["resolution"] == "uncertain":
            reason = (
                cast(str, uncertain_payload["reason"])
                if isinstance(uncertain_payload.get("reason"), str)
                else None
            )
        else:
            reason = next(
                (
                    cast(str, event["payload"]["reason"])
                    for event in events
                    if isinstance(event["payload"], dict)
                    and isinstance(event["payload"].get("reason"), str)
                ),
                cast(str | None, view["resolution"]),
            )
        evidence = view["evidence"] or uncertain_payload.get("evidence")
        if (
            not evidence
            and uncertain_event is not None
            and isinstance(uncertain_payload.get("reason"), str)
        ):
            evidence = {"resolution": {"reason": uncertain_payload["reason"]}}
        version_id = cast(str | None, view["documentVersionId"])
        active_key = (
            self.active_answer_key_version(version_id)
            if version_id is not None and view["documentRole"] == "exam"
            else None
        )
        safe_events = [
            {
                "action": event["action"],
                "actor": event["actor"],
                "algorithmVersion": event["algorithmVersion"],
                "createdAt": event["createdAt"],
                "reason": (
                    event["payload"].get("reason")
                    if isinstance(event["payload"], dict)
                    else None
                ),
            }
            for event in events
        ]
        return cast(dict[str, Any], semantic_public_dto({
            "documentId": view["documentId"],
            "resolution": view["resolution"],
            "identityStatus": view["identityStatus"],
            "identityKey": view["identityKey"],
            "documentRole": view["documentRole"],
            "answerKeyState": view["answerKeyState"],
            "versionNumber": view["versionNumber"],
            "predecessorVersionId": view["predecessorVersionId"],
            "activeAnswerKeyVersion": active_key,
            "identity": view["identity"] or uncertain_payload.get("identity"),
            "evidence": evidence or {},
            "reason": reason,
            "algorithmVersion": (
                profile.get("algorithm_version")
                if profile is not None
                else (
                    uncertain_event["algorithmVersion"]
                    if uncertain_event is not None
                    else (events[0]["algorithmVersion"] if events else None)
                )
            ),
            "events": safe_events,
        }))

    def answer_key_candidates(self, exam_version_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return active_answer_key_candidates(connection, exam_version_id)

    def answer_key_version_ids(self) -> list[str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id FROM document_versions WHERE document_role = 'answer_key' "
                "ORDER BY identity_key, version_number, id"
            ).fetchall()
        return [cast(str, row["id"]) for row in rows]

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

    def classification_question_rows(self) -> list[dict[str, Any]]:
        """Return the stored question/classification inputs needed for local reclassification."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT q.id, q.document_id, q.payload_json, q.classification_json,
                       q.updated_at,
                       d.metadata_json
                FROM questions q JOIN documents d ON d.id = q.document_id
                LEFT JOIN question_occurrences qo ON qo.question_id = q.id
                LEFT JOIN question_group_occurrences qgo
                  ON qgo.occurrence_id = qo.id AND qgo.status = 'active'
                LEFT JOIN question_equivalence_groups qeg ON qeg.id = qgo.group_id
                LEFT JOIN canonical_questions cq ON cq.group_id = qeg.id
                WHERE qo.id IS NULL OR (
                    qeg.status = 'confirmed' AND cq.representative_occurrence_id = qo.id
                )
                ORDER BY q.document_id, q.question_number
                """
            ).fetchall()
        return [
            {
                "id": cast(str, row["id"]),
                "document_id": cast(str, row["document_id"]),
                "question": QuestionRecord.model_validate_json(
                    cast(str, row["payload_json"])
                ),
                "classification": QuestionClassification.model_validate_json(
                    cast(str, row["classification_json"])
                ),
                "metadata": DesktopImportMetadata.model_validate_json(
                    cast(str, row["metadata_json"])
                ),
                "updated_at": cast(str, row["updated_at"]),
            }
            for row in rows
        ]

    def save_reclassifications(
        self,
        updates: list[tuple[str, QuestionRecord, QuestionClassification, str]],
        *,
        taxonomy_version: str,
    ) -> int:
        """Persist classification-only changes without invalidating human decisions."""

        changed = 0
        updated_at = _now()
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for question_id, question, classification, expected_updated_at in updates:
                    row = connection.execute(
                        "SELECT payload_json, classification_json, flags_json, updated_at "
                        "FROM questions "
                        "WHERE id = ?",
                        (question_id,),
                    ).fetchone()
                    if row is None:
                        continue
                    if row["updated_at"] != expected_updated_at:
                        raise RuntimeError(
                            "o acervo mudou durante a reclassificação; tente novamente"
                        )
                    payload_json = _json(question.model_dump(mode="json"))
                    classification_json = _json(classification.model_dump(mode="json"))
                    flags = question_quality_flags(question, classification)
                    duplicate = self._duplicate_question_id_in_connection(
                        connection,
                        question_fingerprint(question),
                        exclude_id=question_id,
                    )
                    if duplicate is not None:
                        flags.append("duplicate")
                    flags_json = _json(list(dict.fromkeys(flags)))
                    if (
                        payload_json == row["payload_json"]
                        and classification_json == row["classification_json"]
                        and flags_json == row["flags_json"]
                    ):
                        continue
                    confidence_values = _classification_confidences(classification)
                    confidence = min(confidence_values, default=0)
                    cursor = connection.execute(
                        "UPDATE questions SET payload_json = ?, classification_json = ?, "
                        "confidence = ?, flags_json = ?, updated_at = ? "
                        "WHERE id = ? AND updated_at = ?",
                        (
                            payload_json,
                            classification_json,
                            confidence,
                            flags_json,
                            updated_at,
                            question_id,
                            expected_updated_at,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "o acervo mudou durante a reclassificação; tente novamente"
                        )
                    self._audit(
                        connection,
                        question_id,
                        "classification_reprocessed",
                        "system",
                        {
                            "editorialFields": {
                                key: json.loads(cast(str, row["payload_json"])).get(key)
                                for key in ("discipline", "matter", "subject", "level")
                            },
                            "classification": json.loads(
                                cast(str, row["classification_json"])
                            ),
                        },
                        {
                            "editorialFields": {
                                "discipline": question.discipline,
                                "matter": question.matter,
                                "subject": question.subject,
                                "level": question.level,
                            },
                            "classification": classification.model_dump(mode="json"),
                            "taxonomyVersion": taxonomy_version,
                        },
                        f"Reclassificação local com taxonomia {taxonomy_version}.",
                    )
                    sync_canonical_editorial_from_question(
                        connection, question_id, changed_at=updated_at
                    )
                    changed += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return changed

    @staticmethod
    def _duplicate_question_id_in_connection(
        connection: sqlite3.Connection, fingerprint: str, *, exclude_id: str
    ) -> str | None:
        row = connection.execute(
            "SELECT id FROM questions WHERE fingerprint = ? AND id != ? LIMIT 1",
            (fingerprint, exclude_id),
        ).fetchone()
        return cast(str, row["id"]) if row is not None else None

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

    def answer_key_association(
        self, exam_version_id: str
    ) -> tuple[dict[str, Any] | None, DocumentAssociationDecision]:
        with closing(self._connect()) as connection:
            _, decision = decide_runtime_association(connection, exam_version_id)
        if decision.selected_version_id is None:
            return None, decision
        return self.answer_key_document(decision.selected_version_id), decision

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
                exam_row = connection.execute(
                    "SELECT profile_json FROM document_versions "
                    "WHERE id = ? AND document_role = 'exam'",
                    (exam_version_id,),
                ).fetchone()
                if exam_row is None:
                    connection.rollback()
                    return False
                _, current_decision = decide_runtime_association(
                    connection, exam_version_id
                )
                if current_decision.selected_version_id != answer_key_version_id:
                    connection.commit()
                    return False
                link_id = record_document_link(
                    connection,
                    exam_version_id,
                    answer_key_version_id,
                    current_decision,
                    _now(),
                )
                if link_id is None:
                    connection.rollback()
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
                    current_payload = json.loads(cast(str, row["payload_json"]))
                    if (
                        current_payload.get("answer_status") == update[0]
                        and current_payload.get("correct_answer") == update[1]
                        and row["answer_key_link_id"] == link_id
                    ):
                        continue
                    question = QuestionRecord.model_validate(
                        current_payload
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
                        "reviewer = ?, review_notes = ?, exported_at = ?, "
                        "answer_key_link_id = ?, answer_invalidated_at = NULL, "
                        "answer_invalidation_reason = NULL, updated_at = ? "
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
                            link_id,
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
                    connection.commit()
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
            effective_years = profile.identity.year.normalized_values
            if result.outcome != "uncertain" and len(effective_years) == 1:
                effective_year = effective_years[0]
                if isinstance(effective_year, int):
                    metadata = DesktopImportMetadata.model_validate(document["metadata"])
                    if metadata.year != effective_year:
                        metadata = metadata.model_copy(update={"year": effective_year})
                        connection.execute(
                            "UPDATE documents SET metadata_json = ?, updated_at = ? WHERE id = ?",
                            (_json(metadata.model_dump(mode="json")), _now(), document_id),
                        )
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
        parsing_result_json = payload.pop("parsing_result_json", None)
        payload["parsing_result"] = (
            json.loads(cast(str, parsing_result_json))
            if parsing_result_json is not None
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
            "parsing_result_json",
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
    ) -> IdentityResolution:
        return self.correct_document_identity(document_id, metadata, actor=actor)

    def correct_document_identity(
        self, document_id: str, metadata: DesktopImportMetadata, actor: str
    ) -> IdentityResolution:
        reviewer = actor.strip()
        if not reviewer:
            raise ValueError("ator da correção é obrigatório")
        validated = DesktopImportMetadata.model_validate(metadata)
        corrected_at = _now()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                document = connection.execute(
                    "SELECT id, document_version_id, normalized_json, metadata_json "
                    "FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
                if document is None:
                    raise ValueError("documento nao encontrado")
                version_id = cast(str | None, document["document_version_id"])
                if version_id is None or document["normalized_json"] is None:
                    self._update_document_metadata_in_connection(
                        connection,
                        (document_id,),
                        validated,
                        reviewer,
                        corrected_at,
                    )
                    result = IdentityResolution(
                        outcome="uncertain",
                        reason="metadados atualizados; identidade legada permanece desconhecida",
                    )
                    connection.commit()
                    return result

                normalized = NormalizedDocument.model_validate_json(
                    cast(str, document["normalized_json"])
                )
                declared_type = (
                    validated.document_type
                    if validated.document_type != "auto"
                    else normalized.declared_type
                )
                inspection_document = normalized.model_copy(
                    update={
                        "declared_type": declared_type,
                        "title": validated.document_title or normalized.title,
                    }
                )
                pages = [
                    (int(row["page_number"]), cast(str, row["text"]))
                    for row in connection.execute(
                        "SELECT page_number, text FROM pages WHERE document_id = ? "
                        "ORDER BY page_number",
                        (document_id,),
                    ).fetchall()
                ]
                overrides: dict[str, str | int] = {
                    target: value
                    for target, value in (
                        ("board", validated.board),
                        ("concurso", validated.concurso),
                        ("organization", validated.organization),
                        ("year", validated.year),
                        ("roles", validated.role),
                        ("stage", validated.stage),
                        ("turns", validated.turn),
                        ("variants", validated.variant),
                    )
                    if value is not None
                }
                profile = extract_semantic_profile(
                    inspection_document,
                    pages,
                    human_overrides=overrides,
                )
                if profile.has_conflict:
                    raise ValueError("perfil semântico conflitante")
                if profile.identity_key is None:
                    raise ValueError("identidade semântica insuficiente")

                previous_version = connection.execute(
                    "SELECT profile_json FROM document_versions WHERE id = ?",
                    (version_id,),
                ).fetchone()
                if previous_version is None:
                    raise RuntimeError("versão operacional ausente")
                old_profile = DocumentSemanticProfile.model_validate_json(
                    cast(str, previous_version["profile_json"])
                )

                before_graph = {
                    cast(str, row["id"]): (
                        row["identity_key"], row["document_role"], row["predecessor_version_id"]
                    )
                    for row in connection.execute(
                        "SELECT id, identity_key, document_role, predecessor_version_id "
                        "FROM document_versions"
                    ).fetchall()
                }

                result, _ = persist_identity_correction(
                    connection,
                    document_id=document_id,
                    profile=profile,
                    actor=reviewer,
                    corrected_at=corrected_at,
                )
                after_graph = {
                    cast(str, row["id"]): (
                        row["identity_key"], row["document_role"], row["predecessor_version_id"]
                    )
                    for row in connection.execute(
                        "SELECT id, identity_key, document_role, predecessor_version_id "
                        "FROM document_versions"
                    ).fetchall()
                }
                changed_version_ids = {
                    item
                    for item in before_graph.keys() | after_graph.keys()
                    if before_graph.get(item) != after_graph.get(item)
                }
                if changed_version_ids:
                    correction_event = connection.execute(
                        "SELECT event_key FROM document_identity_events "
                        "WHERE document_version_id = ? AND action = 'identity_corrected' "
                        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                        (version_id,),
                    ).fetchone()
                    if correction_event is None:
                        raise RuntimeError("evento de correção de identidade ausente")
                    reconcile_question_lineage_after_correction(
                        connection,
                        changed_version_ids=changed_version_ids,
                        correction_event_key=cast(str, correction_event["event_key"]),
                        corrected_at=corrected_at,
                    )
                linked_documents = tuple(
                    cast(str, row["id"])
                    for row in connection.execute(
                        "SELECT id FROM documents WHERE document_version_id = ? ORDER BY id",
                        (version_id,),
                    ).fetchall()
                )
                self._update_document_metadata_in_connection(
                    connection,
                    linked_documents,
                    validated,
                    reviewer,
                    corrected_at,
                    metadata_document_id=document_id,
                )
                self._reevaluate_corrected_links(
                    connection,
                    corrected_version_id=version_id,
                    old_profile=old_profile,
                    new_profile=profile,
                    corrected_at=corrected_at,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _update_document_metadata_in_connection(
        self,
        connection: sqlite3.Connection,
        document_ids: tuple[str, ...],
        metadata: DesktopImportMetadata,
        actor: str,
        updated_at: str,
        *,
        metadata_document_id: str | None = None,
    ) -> None:
        target_document = metadata_document_id or document_ids[0]
        metadata_json = _json(metadata.model_dump(mode="json"))
        connection.execute(
            "UPDATE documents SET metadata_json = ?, updated_at = ? "
            "WHERE id = ? AND metadata_json != ?",
            (metadata_json, updated_at, target_document, metadata_json),
        )
        if not document_ids:
            return
        placeholders = ",".join("?" for _ in document_ids)
        rows = connection.execute(
            f"SELECT * FROM questions WHERE document_id IN ({placeholders}) "  # noqa: S608
            "ORDER BY document_id, question_number",
            document_ids,
        ).fetchall()
        for row in rows:
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
            payload_json = _json(updated.model_dump(mode="json"))
            flags = question_quality_flags(updated, classification)
            duplicate = connection.execute(
                "SELECT 1 FROM questions WHERE fingerprint = ? AND id != ? LIMIT 1",
                (question_fingerprint(updated), row["id"]),
            ).fetchone()
            if duplicate is not None:
                flags.append("duplicate")
            flags_json = _json(list(dict.fromkeys(flags)))
            if payload_json == row["payload_json"] and flags_json == row["flags_json"]:
                continue
            connection.execute(
                "UPDATE questions SET payload_json = ?, flags_json = ?, updated_at = ? "
                "WHERE id = ?",
                (payload_json, flags_json, updated_at, row["id"]),
            )
            self._audit(
                connection,
                cast(str, row["id"]),
                "updated",
                actor,
                question.model_dump(mode="json"),
                updated.model_dump(mode="json"),
                "Metadados do documento corrigidos por revisão humana.",
            )

    def _reevaluate_corrected_links(
        self,
        connection: sqlite3.Connection,
        *,
        corrected_version_id: str,
        old_profile: DocumentSemanticProfile,
        new_profile: DocumentSemanticProfile,
        corrected_at: str,
    ) -> None:
        affected = affected_exam_documents_after_identity_correction(
            connection,
            corrected_version_id=corrected_version_id,
            old_profile=old_profile,
            new_profile=new_profile,
        )
        if old_profile.document_role == "exam" and new_profile.document_role != "exam":
            record_corrected_document_link(
                connection,
                corrected_version_id,
                select_answer_key(old_profile, []),
                corrected_at,
            )
            invalidate_answer_association(
                connection,
                exam_version_id=corrected_version_id,
                reason="Resposta invalidada após o documento deixar de ser uma prova.",
                changed_at=corrected_at,
            )
        for exam in affected:
            exam_version_id = cast(str, exam["exam_version_id"])
            document_id = cast(str | None, exam["document_id"])
            if document_id is None:
                continue
            exam_profile = DocumentSemanticProfile.model_validate_json(
                cast(str, exam["profile_json"])
            )
            _, decision = decide_runtime_association(connection, exam_version_id)
            if decision.selected_version_id is None:
                record_corrected_document_link(
                    connection, exam_version_id, decision, corrected_at
                )
                invalidate_answer_association(
                    connection,
                    exam_version_id=exam_version_id,
                    reason=(
                        "Resposta invalidada porque a correção semântica removeu "
                        "a associação de gabarito válida."
                    ),
                    changed_at=corrected_at,
                )
                continue
            key_document = connection.execute(
                "SELECT d.id FROM documents d WHERE d.document_version_id = ? "
                "ORDER BY (SELECT COALESCE(SUM(p.character_count), 0) FROM pages p "
                "WHERE p.document_id = d.id) DESC, d.created_at, d.id LIMIT 1",
                (decision.selected_version_id,),
            ).fetchone()
            if key_document is None:
                incomplete_decision = decision.model_copy(
                    update={
                        "outcome": "incomplete",
                        "selected_version_id": None,
                        "reason": "documento operacional do gabarito não localizado",
                    }
                )
                record_corrected_document_link(
                    connection, exam_version_id, incomplete_decision, corrected_at
                )
                invalidate_answer_association(
                    connection,
                    exam_version_id=exam_version_id,
                    reason="Resposta invalidada porque o gabarito não possui documento local.",
                    changed_at=corrected_at,
                )
                continue
            answer_text = "\n".join(
                cast(str, row["text"])
                for row in connection.execute(
                    "SELECT text FROM pages WHERE document_id = ? ORDER BY page_number",
                    (key_document["id"],),
                ).fetchall()
            )
            role = self._single_semantic_value(exam_profile.identity.roles)
            variant = self._single_semantic_value(exam_profile.identity.variants)
            turn = self._single_semantic_value(exam_profile.identity.turns)
            entries = parse_answer_key(
                answer_text,
                role=role,
                variant=variant,
                turn=turn,
            )
            updates = {
                number: (
                    "annulled" if entry.annulled else "matched",
                    None if entry.annulled else entry.answer,
                )
                for number, entry in entries.items()
            }
            self._apply_corrected_answer_updates(
                connection,
                document_id=document_id,
                exam_version_id=exam_version_id,
                decision=decision,
                updates=updates,
                corrected_at=corrected_at,
            )

    @staticmethod
    def _single_semantic_value(field: Any) -> str | None:
        if field.status != "known" or len(field.normalized_values) != 1:
            return None
        return str(field.normalized_values[0])

    def _apply_corrected_answer_updates(
        self,
        connection: sqlite3.Connection,
        *,
        document_id: str,
        exam_version_id: str,
        decision: Any,
        updates: dict[int, tuple[str, str | None]],
        corrected_at: str,
    ) -> None:
        link_id = record_corrected_document_link(
            connection,
            exam_version_id,
            decision,
            corrected_at,
        )
        if link_id is None:
            raise RuntimeError("a correção não produziu vínculo de gabarito ativo")
        rows = connection.execute(
            "SELECT * FROM questions WHERE document_id = ? ORDER BY question_number",
            (document_id,),
        ).fetchall()
        for row in rows:
            update = updates.get(int(row["question_number"]))
            if update is None:
                continue
            question = QuestionRecord.model_validate(
                json.loads(cast(str, row["payload_json"]))
            ).model_copy(update={"answer_status": update[0], "correct_answer": update[1]})
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
            payload_json = _json(question.model_dump(mode="json"))
            flags_json = _json(list(dict.fromkeys(flags)))
            if (
                row["payload_json"] == payload_json
                and row["fingerprint"] == fingerprint
                and row["decision_fingerprint"] == decision_fingerprint
                and float(row["confidence"]) == confidence
                and row["flags_json"] == flags_json
                and row["status"] == status
                and row["reviewer"] == reviewer
                and row["review_notes"] == review_notes
                and row["exported_at"] == exported_at
                and row["answer_key_link_id"] == link_id
            ):
                continue
            connection.execute(
                "UPDATE questions SET payload_json = ?, fingerprint = ?, "
                "decision_fingerprint = ?, confidence = ?, flags_json = ?, status = ?, "
                "reviewer = ?, review_notes = ?, exported_at = ?, answer_key_link_id = ?, "
                "answer_invalidated_at = NULL, answer_invalidation_reason = NULL, "
                "updated_at = ? WHERE id = ?",
                (
                    payload_json,
                    fingerprint,
                    decision_fingerprint,
                    confidence,
                    flags_json,
                    status,
                    reviewer,
                    review_notes,
                    exported_at,
                    link_id,
                    corrected_at,
                    row["id"],
                ),
            )
            if row["status"] in {"approved", "rejected", "exported"} and not decision_unchanged:
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
                    changed_at=corrected_at,
                )

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
            if existing is not None and existing["fingerprint"] != fingerprint:
                invalidate_canonical_classification(
                    connection,
                    question_id,
                    actor="system",
                    reason="reprocessamento alterou o conteúdo da questão canônica",
                    changed_at=created_at,
                )
                invalidate_question_equivalence(
                    connection,
                    question_id,
                    actor="system",
                    reason="reprocessamento alterou o conteúdo de uma ocorrência",
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
        row = next(
            iter(self._all_question_rows(question_id)),
            None,
        )
        if row is None:
            raise ValueError("questao nao encontrada")
        return self._question_view(row)

    def question_equivalence(self, question_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            return question_equivalence_view(connection, question_id)

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
        changed_at = _now()
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
                    changed_at,
                    question_id,
                ),
            )
            if before["fingerprint"] != fingerprint:
                invalidate_canonical_classification(
                    connection,
                    question_id,
                    actor=actor,
                    reason="conteúdo da questão canônica alterado; classificação exige revisão",
                    changed_at=changed_at,
                )
            invalidate_question_equivalence(
                connection,
                question_id,
                actor=actor,
                reason="conteúdo da ocorrência alterado; equivalência exige revalidação",
                changed_at=changed_at,
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
            equivalence = cast(dict[str, Any] | None, before.get("question_equivalence"))
            if equivalence and (
                equivalence.get("status") != "confirmed"
                or not equivalence.get("isRepresentative")
                or not equivalence.get("groupFresh")
            ):
                raise ValueError("revalide a equivalência antes de aprovar")
            if "duplicate" in before["flags"] and not equivalence:
                raise ValueError("resolva a duplicata antes de aprovar")
        with closing(self._connect()) as connection:
            changed_at = _now()
            connection.execute(
                """
                UPDATE questions SET status = ?, reviewer = ?, review_notes = ?,
                    updated_at = ?, exported_at = NULL WHERE id = ?
                """,
                (status, actor.strip(), notes, changed_at, question_id),
            )
            sync_canonical_editorial_from_question(
                connection, question_id, changed_at=changed_at
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
            equivalence = cast(dict[str, Any] | None, before.get("question_equivalence"))
            if equivalence and (
                equivalence.get("status") != "confirmed"
                or not equivalence.get("isRepresentative")
                or not equivalence.get("groupFresh")
            ):
                errors.append(f"questao {question.number}: equivalencia nao confirmada")
            if "duplicate" in before["flags"] and not equivalence:
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
                sync_canonical_editorial_from_question(
                    connection, question_id, changed_at=now
                )
            connection.commit()
        return len(before_views)

    @staticmethod
    def _classification_review_signature(
        classification: QuestionClassification,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        fields = {
            "discipline": classification.discipline,
            "matter": classification.subject,
            "subject": classification.topic,
        }
        suggestion = {
            name: str(value.value or "").strip() for name, value in fields.items()
        }
        if any(not value for value in suggestion.values()):
            raise ValueError("a revisão em lote exige sugestão completa de classificação")
        taxonomy = EditorialTaxonomy.load_default()
        taxonomy.ensure_known("discipline", suggestion["discipline"])
        taxonomy.ensure_known("matter", suggestion["matter"])
        taxonomy.ensure_known("subject", suggestion["subject"])
        valid_path = any(
            path.matter == suggestion["matter"] and path.subject == suggestion["subject"]
            for path in taxonomy.candidate_paths(discipline=suggestion["discipline"])
        )
        if not valid_path:
            raise ValueError("a sugestão não forma um caminho válido na taxonomia")
        evidence = {
            name: {
                "evidence": value.evidence,
                "source": value.source,
                "reason": value.reason,
                "provenance": value.provenance,
            }
            for name, value in fields.items()
        }
        if any(value.source == "human_review" for value in fields.values()):
            raise ValueError("uma decisão humana existente não pode ser sobrescrita em lote")
        return suggestion, evidence

    def preview_classification_batch(self, question_ids: list[str]) -> dict[str, Any]:
        normalized_ids = sorted(set(question_ids))
        if not normalized_ids:
            raise ValueError("selecione ao menos uma questão")
        if len(normalized_ids) > 1_000:
            raise ValueError("a revisão em lote aceita no máximo 1000 questões")
        views = [self.question(question_id) for question_id in normalized_ids]
        signatures: list[tuple[dict[str, str], dict[str, Any]]] = []
        snapshots: list[dict[str, Any]] = []
        for view in views:
            classification = QuestionClassification.model_validate(view["classification"])
            signatures.append(self._classification_review_signature(classification))
            snapshots.append(
                {
                    "questionId": view["id"],
                    "classification": classification.model_dump(mode="json"),
                }
            )
        first_suggestion, first_evidence = signatures[0]
        if any(
            suggestion != first_suggestion or evidence != first_evidence
            for suggestion, evidence in signatures[1:]
        ):
            raise ValueError(
                "selecione somente questões com a mesma sugestão e evidência"
            )
        token = hashlib.sha256(
            _json(
                {
                    "questions": snapshots,
                    "suggestion": first_suggestion,
                    "evidence": first_evidence,
                }
            ).encode("utf-8")
        ).hexdigest()
        return {
            "count": len(views),
            "questionIds": normalized_ids,
            "suggestion": first_suggestion,
            "evidence": first_evidence,
            "confirmationToken": token,
        }

    def confirm_classification_batch(
        self,
        question_ids: list[str],
        *,
        confirmation_token: str,
        actor: str,
    ) -> dict[str, Any]:
        if not confirmation_token.strip():
            raise ValueError("a alteração em lote exige confirmação explícita")
        reviewer = actor.strip()
        if not reviewer:
            raise ValueError("informe o responsável pela confirmação")
        try:
            preview = self.preview_classification_batch(question_ids)
        except ValueError as exc:
            raise ValueError(
                "a fila mudou depois da prévia; revise e confirme novamente"
            ) from exc
        if confirmation_token != preview["confirmationToken"]:
            raise ValueError("a fila mudou depois da prévia; revise e confirme novamente")
        batch_id = str(uuid.uuid4())
        now = _now()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = {
                cast(str, row["id"]): row
                for row in connection.execute(
                    "SELECT * FROM questions WHERE id IN ("
                    + ",".join("?" for _ in preview["questionIds"])
                    + ")",
                    tuple(preview["questionIds"]),
                ).fetchall()
            }
            if len(rows) != len(preview["questionIds"]):
                raise RuntimeError("a fila mudou durante a confirmação")
            current_snapshots = [
                {
                    "questionId": question_id,
                    "classification": json.loads(
                        cast(str, rows[question_id]["classification_json"])
                    ),
                }
                for question_id in preview["questionIds"]
            ]
            current_token = hashlib.sha256(
                _json(
                    {
                        "questions": current_snapshots,
                        "suggestion": preview["suggestion"],
                        "evidence": preview["evidence"],
                    }
                ).encode("utf-8")
            ).hexdigest()
            if current_token != confirmation_token:
                raise RuntimeError("a fila mudou durante a confirmação")
            connection.execute(
                "INSERT INTO classification_review_batches ("
                "id, confirmation_token, suggestion_json, evidence_json, status, actor, "
                "created_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (
                    batch_id,
                    confirmation_token,
                    _json(preview["suggestion"]),
                    _json(preview["evidence"]),
                    reviewer,
                    now,
                ),
            )
            for question_id in preview["questionIds"]:
                row = rows[question_id]
                before_json = cast(str, row["classification_json"])
                before = QuestionClassification.model_validate_json(before_json)
                updates: dict[str, Any] = {}
                for field_name in ("discipline", "subject", "topic"):
                    value = getattr(before, field_name)
                    updates[field_name] = value.model_copy(
                        update={
                            "confidence": 1.0,
                            "source": "human_review",
                            "reason": "Sugestão e evidências confirmadas em revisão assistida.",
                        }
                    )
                after = before.model_copy(update=updates)
                after_json = _json(after.model_dump(mode="json"))
                question = QuestionRecord.model_validate_json(cast(str, row["payload_json"]))
                flags = question_quality_flags(question, after)
                if "duplicate" in json.loads(cast(str, row["flags_json"])):
                    flags.append("duplicate")
                connection.execute(
                    "UPDATE questions SET classification_json=?, confidence=?, flags_json=?, "
                    "updated_at=? WHERE id=? AND classification_json=?",
                    (
                        after_json,
                        min(_classification_confidences(after), default=0),
                        _json(list(dict.fromkeys(flags))),
                        now,
                        question_id,
                        before_json,
                    ),
                )
                connection.execute(
                    "INSERT INTO classification_review_batch_items ("
                    "batch_id, question_id, before_classification_json, "
                    "after_classification_json) VALUES (?, ?, ?, ?)",
                    (batch_id, question_id, before_json, after_json),
                )
                self._audit(
                    connection,
                    question_id,
                    "classification_batch_confirmed",
                    reviewer,
                    {"classification": json.loads(before_json)},
                    {"classification": after.model_dump(mode="json"), "batchId": batch_id},
                    "Sugestão e evidências confirmadas em lote.",
                )
            connection.commit()
        return {"batchId": batch_id, "updated": len(preview["questionIds"])}

    def revert_classification_batch(self, batch_id: str, *, actor: str) -> dict[str, Any]:
        reviewer = actor.strip()
        if not reviewer:
            raise ValueError("informe o responsável pela correção")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT * FROM classification_review_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise ValueError("lote de revisão não encontrado")
            if batch["status"] != "active":
                raise ValueError("este lote já foi corrigido")
            items = connection.execute(
                "SELECT * FROM classification_review_batch_items WHERE batch_id=? "
                "ORDER BY question_id",
                (batch_id,),
            ).fetchall()
            current_rows = {
                cast(str, row["id"]): row
                for row in connection.execute(
                    "SELECT * FROM questions WHERE id IN ("
                    + ",".join("?" for _ in items)
                    + ")",
                    tuple(row["question_id"] for row in items),
                ).fetchall()
            }
            if any(
                item["question_id"] not in current_rows
                or current_rows[item["question_id"]]["classification_json"]
                != item["after_classification_json"]
                for item in items
            ):
                raise ValueError(
                    "uma classificação mudou após o lote; a correção automática foi bloqueada"
                )
            now = _now()
            for item in items:
                question_id = cast(str, item["question_id"])
                current = current_rows[question_id]
                before_json = cast(str, item["before_classification_json"])
                restored = QuestionClassification.model_validate_json(before_json)
                question = QuestionRecord.model_validate_json(
                    cast(str, current["payload_json"])
                )
                flags = question_quality_flags(question, restored)
                if "duplicate" in json.loads(cast(str, current["flags_json"])):
                    flags.append("duplicate")
                connection.execute(
                    "UPDATE questions SET classification_json=?, confidence=?, flags_json=?, "
                    "updated_at=? WHERE id=? AND classification_json=?",
                    (
                        before_json,
                        min(_classification_confidences(restored), default=0),
                        _json(list(dict.fromkeys(flags))),
                        now,
                        question_id,
                        item["after_classification_json"],
                    ),
                )
                self._audit(
                    connection,
                    question_id,
                    "classification_batch_reverted",
                    reviewer,
                    {"classification": json.loads(item["after_classification_json"])},
                    {"classification": restored.model_dump(mode="json"), "batchId": batch_id},
                    "Confirmação em lote corrigida sem alterar a decisão editorial.",
                )
            connection.execute(
                "UPDATE classification_review_batches SET status='reverted', "
                "reverted_at=? WHERE id=?",
                (now, batch_id),
            )
            connection.commit()
        return {"batchId": batch_id, "reverted": len(items)}

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

    def query(
        self,
        filters: DesktopFilterSet,
        *,
        include_equivalent_copies: bool = False,
    ) -> dict[str, Any]:
        rows = self._all_question_rows()
        all_views = [self._question_view(row) for row in rows]
        views = (
            all_views
            if include_equivalent_copies
            else [
                view
                for view in all_views
                if not (view.get("question_equivalence") or {}).get("groupId")
                or view["question_equivalence"].get("isRepresentative")
            ]
        )
        selected = [view for view in views if self._matches(view, filters)]
        return {
            "questions": selected,
            "total": len(selected),
            "summary": self._summary(views),
            "facets": self._facets(views, filters),
            "filters": filters.model_dump(mode="json"),
        }

    def export_candidates(self, filters: DesktopFilterSet) -> list[dict[str, Any]]:
        views = [self._question_view(row) for row in self._all_question_rows()]
        selected = [view for view in views if self._matches(view, filters)]
        representatives = {
            cast(str, view["question_equivalence"]["occurrenceId"]): view
            for view in views
            if view.get("question_equivalence")
            and view["question_equivalence"].get("isRepresentative")
        }
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for view in selected:
            equivalence = cast(dict[str, Any] | None, view.get("question_equivalence"))
            candidate = view
            key = f"occurrence:{view['id']}"
            if equivalence and equivalence.get("status") == "confirmed":
                representative_id = cast(
                    str | None, equivalence.get("representativeOccurrenceId")
                )
                candidate = representatives.get(representative_id or "", view)
                key = f"group:{equivalence['groupId']}"
            if key not in seen:
                seen.add(key)
                result.append(candidate)
        return result

    def _all_question_rows(self, question_id: str | None = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            where_clause = "WHERE q.id = ?" if question_id is not None else ""
            parameters = (question_id,) if question_id is not None else ()
            rows = list(
                connection.execute(
                    """
                    SELECT q.*, d.filename, d.local_path, d.sha256 AS document_sha256,
                           d.metadata_json, d.warnings_json, d.job_id,
                           d.size_bytes, d.created_at AS document_created_at,
                           d.document_version_id AS exam_version_id,
                           d.semantic_resolution,
                           d.canonical_contest_id, d.canonical_application_id,
                           d.canonical_document_id,
                           cc.canonical_key AS canonical_contest_key,
                           cc.display_name AS canonical_contest_name,
                           ea.canonical_key AS canonical_application_key,
                           ea.display_name AS canonical_application_name,
                           (SELECT json_group_array(cds.scope_id)
                            FROM canonical_document_scopes cds
                            WHERE cds.document_id = d.canonical_document_id)
                               AS canonical_scope_ids_json,
                           (SELECT json_group_array(ca.raw_value)
                            FROM contest_aliases ca
                            WHERE ca.contest_id = d.canonical_contest_id
                              AND ca.status = 'active') AS canonical_aliases_json,
                           (d.document_version_id IS NULL OR EXISTS(
                               SELECT 1 FROM document_links l
                               WHERE l.id = q.answer_key_link_id
                                 AND l.status = 'active'
                                 AND l.algorithm_version = 'semantic-association-v2'
                           )) AS valid_answer_association,
                           review.status AS answer_review_status,
                           review.reason AS answer_review_reason,
                           review.candidates_json AS answer_review_candidates_json,
                           (SELECT COALESCE(
                                json_extract(answer_document.metadata_json,
                                             '$.document_title'),
                                answer_document.filename)
                            FROM document_links answer_link
                            JOIN documents answer_document
                              ON answer_document.document_version_id =
                                 answer_link.answer_key_version_id
                            WHERE answer_link.id = q.answer_key_link_id
                            ORDER BY answer_document.created_at DESC LIMIT 1)
                               AS linked_answer_key_document,
                           qo.id AS equivalence_occurrence_id,
                           qeg.id AS equivalence_group_id,
                           qeg.status AS equivalence_group_status,
                           qeg.reason AS equivalence_reason,
                           qeg.expected_occurrences AS equivalence_expected_occurrences,
                           qeg.occurrence_count AS equivalence_occurrence_count,
                           qeg.has_statement_variants AS equivalence_has_statement_variants,
                           cq.id AS canonical_question_id,
                           COALESCE(cq.representative_occurrence_id,
                                    qeg.representative_occurrence_id)
                               AS representative_occurrence_id,
                           cq.editorial_status AS canonical_editorial_status,
                           NOT EXISTS (
                               SELECT 1 FROM question_group_occurrences fresh_go
                               JOIN question_occurrences fresh_o
                                 ON fresh_o.id = fresh_go.occurrence_id
                               JOIN questions fresh_q ON fresh_q.id = fresh_o.question_id
                               WHERE fresh_go.group_id = qeg.id AND fresh_go.status = 'active'
                                 AND (fresh_o.source_updated_at != fresh_q.updated_at
                                   OR fresh_o.answer_key_link_id IS NOT fresh_q.answer_key_link_id
                                   OR NOT EXISTS (
                                       SELECT 1 FROM document_links fresh_link
                                       WHERE fresh_link.id = fresh_q.answer_key_link_id
                                         AND fresh_link.status = 'active'
                                         AND fresh_link.algorithm_version =
                                             'semantic-association-v2'
                                   ))
                           ) AS equivalence_group_fresh
                    FROM questions q JOIN documents d ON d.id = q.document_id
                    LEFT JOIN canonical_contests cc ON cc.id = d.canonical_contest_id
                    LEFT JOIN exam_applications ea ON ea.id = d.canonical_application_id
                    LEFT JOIN association_review_queue review
                      ON review.exam_version_id = d.document_version_id
                    LEFT JOIN question_occurrences qo ON qo.question_id = q.id
                    LEFT JOIN question_group_occurrences qgo
                      ON qgo.occurrence_id = qo.id AND qgo.status = 'active'
                    LEFT JOIN question_equivalence_groups qeg ON qeg.id = qgo.group_id
                    LEFT JOIN canonical_questions cq ON cq.group_id = qeg.id
                    """
                    + where_clause
                    + " ORDER BY d.filename, q.question_number",
                    parameters,
                ).fetchall()
            )

            candidates_by_exam: dict[str, tuple[int, list[str]]] = {}
            enriched: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                exam_version_id = cast(str | None, item.get("exam_version_id"))
                if exam_version_id and exam_version_id not in candidates_by_exam:
                    candidates = active_answer_key_candidates(
                        connection,
                        exam_version_id,
                        include_scope_conflicts=True,
                    )
                    latest_by_identity: dict[str, dict[str, Any]] = {}
                    for candidate in candidates:
                        identity_key = cast(str, candidate["identity_key"])
                        current = latest_by_identity.get(identity_key)
                        if current is None or int(candidate["version_number"]) > int(
                            current["version_number"]
                        ):
                            latest_by_identity[identity_key] = candidate
                    candidates_by_exam[exam_version_id] = (
                        len(latest_by_identity),
                        sorted(
                            cast(str, candidate["answer_key_version_id"])
                            for candidate in latest_by_identity.values()
                        ),
                    )
                count, version_ids = candidates_by_exam.get(exam_version_id or "", (0, []))
                item["compatible_answer_key_count"] = count
                item["compatible_answer_key_version_ids"] = version_ids
                enriched.append(item)
            return enriched

    @staticmethod
    def _question_view(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        question = json.loads(cast(str, payload.pop("payload_json")))
        classification = json.loads(cast(str, payload.pop("classification_json")))
        flags = json.loads(cast(str, payload.pop("flags_json")))
        metadata = json.loads(cast(str, payload.pop("metadata_json")))
        warnings = json.loads(cast(str, payload.pop("warnings_json")))
        scope_ids = json.loads(payload.pop("canonical_scope_ids_json", None) or "[]")
        aliases = json.loads(payload.pop("canonical_aliases_json", None) or "[]")
        occurrence_id = cast(str | None, payload.pop("equivalence_occurrence_id", None))
        group_id = cast(str | None, payload.pop("equivalence_group_id", None))
        group_status = cast(str | None, payload.pop("equivalence_group_status", None))
        representative_id = cast(
            str | None, payload.pop("representative_occurrence_id", None)
        )
        equivalence = (
            {
                "occurrenceId": occurrence_id,
                "groupId": group_id,
                "status": group_status,
                "reason": payload.pop("equivalence_reason", None),
                "expectedOccurrences": payload.pop(
                    "equivalence_expected_occurrences", None
                ),
                "occurrenceCount": payload.pop("equivalence_occurrence_count", None),
                "hasStatementVariants": bool(
                    payload.pop("equivalence_has_statement_variants", False)
                ),
                "canonicalQuestionId": payload.pop("canonical_question_id", None),
                "representativeOccurrenceId": representative_id,
                "isRepresentative": bool(
                    occurrence_id and representative_id == occurrence_id
                ),
                "editorialStatus": payload.pop("canonical_editorial_status", None),
                "groupFresh": bool(payload.pop("equivalence_group_fresh", False)),
            }
            if occurrence_id is not None
            else None
        )
        question_record = QuestionRecord.model_validate(question)
        answer_key_diagnosis = diagnose_answer_key(
            AnswerKeyEvidence(
                answer_status=str(question.get("answer_status") or "missing"),
                answer_key_link_id=cast(str | None, payload.get("answer_key_link_id")),
                valid_answer_association=bool(payload.get("valid_answer_association")),
                exam_version_id=cast(str | None, payload.get("exam_version_id")),
                compatible_candidate_count=int(
                    payload.get("compatible_answer_key_count") or 0
                ),
                review_status=cast(str | None, payload.get("answer_review_status")),
                review_reason=cast(str | None, payload.get("answer_review_reason")),
                linked_answer_key_document=cast(
                    str | None, payload.get("linked_answer_key_document")
                ),
            )
        )
        source_document = str(metadata.get("document_title") or payload["filename"])
        import_diagnosis = diagnose_import_readiness(
            question_record,
            source_document=source_document,
            provider=cast(str | None, metadata.get("provider")),
            source_url=cast(str | None, metadata.get("source_url")),
            document_sha256=cast(str | None, payload.get("document_sha256")),
            flags=flags,
            document_warnings=warnings,
            semantic_resolution=cast(str | None, payload.get("semantic_resolution")),
        )
        diagnosis_issues = list(import_diagnosis.issues)
        origin_issues = [
            item.what for item in diagnosis_issues if item.code == "unproved_origin"
        ]
        duplicate_issues = [
            item.what
            for item in diagnosis_issues
            if item.code == "unresolved_duplicate"
        ]
        canonical_duplicate = bool(
            equivalence
            and equivalence["status"] == "confirmed"
            and equivalence["isRepresentative"]
            and equivalence["groupFresh"]
        )
        equivalence_ready = bool(
            equivalence is None
            or (
                equivalence["status"] == "confirmed"
                and equivalence["isRepresentative"]
                and equivalence["groupFresh"]
            )
        )
        if canonical_duplicate:
            duplicate_issues = []
            diagnosis_issues = [
                item for item in diagnosis_issues if item.code != "unresolved_duplicate"
            ]
        equivalence_issue: dict[str, Any] | None = None
        if equivalence is not None and not equivalence_ready:
            equivalence_issue = {
                "code": "unresolved_duplicate",
                "what": (
                    "Cópia repetida preservada"
                    if equivalence["status"] == "confirmed"
                    and not equivalence["isRepresentative"]
                    else "Agrupamento de cópias ainda não confirmado"
                ),
                "why": (
                    "Somente a cópia principal entra no app; esta cópia continua no banco "
                    "como evidência."
                    if equivalence["status"] == "confirmed"
                    and not equivalence["isRepresentative"]
                    else str(equivalence.get("reason") or "As evidências ainda divergem.")
                ),
                "how_to_resolve": (
                    "Abra os detalhes da cópia principal."
                    if equivalence["status"] == "confirmed"
                    and not equivalence["isRepresentative"]
                    else "Revise apenas este grupo; as demais questões continuam normalmente."
                ),
                "source_document": source_document,
                "missing": [],
            }
        effective_diagnosis_issues = [
            item.model_dump(mode="json") for item in diagnosis_issues
        ]
        if equivalence_issue is not None:
            effective_diagnosis_issues.append(equivalence_issue)
        effective_importable = not effective_diagnosis_issues
        import_issues = [
            cast(str, item["what"]) for item in effective_diagnosis_issues
        ]
        publication_issues = [
            *validate_editorial_question(question_record),
            *origin_issues,
            *duplicate_issues,
            *([] if equivalence_ready else ["equivalência canônica exige revalidação"]),
        ]
        readiness_states = ["importable" if effective_importable else "blocked"]
        if any(
            not str(question.get(field_name) or "").strip()
            for field_name in ("discipline", "matter", "subject")
        ):
            readiness_states.append("unclassified")
        payload.update(
            {
                "question": question,
                "classification": classification,
                "flags": flags,
                "metadata": metadata,
                "document_warnings": warnings,
                "importable": effective_importable,
                "import_issues": list(dict.fromkeys(import_issues)),
                "import_diagnosis": {
                    "importable": effective_importable,
                    "issues": effective_diagnosis_issues,
                },
                "block_reasons": [
                    cast(str, item["code"]) for item in effective_diagnosis_issues
                ],
                "readiness_states": readiness_states,
                "publication_ready": not publication_issues,
                "publication_issues": list(dict.fromkeys(publication_issues)),
                "question_equivalence": equivalence,
                "answer_key_state": answer_key_diagnosis["state"],
                "answer_key_diagnosis": answer_key_diagnosis,
                "answer_key_evidence": {
                    "examDocument": source_document,
                    "examVersionId": payload.get("exam_version_id"),
                    "answerKeyLinkId": payload.get("answer_key_link_id"),
                    "linkedAnswerKeyDocument": payload.get("linked_answer_key_document"),
                    "compatibleCandidateCount": payload.get(
                        "compatible_answer_key_count", 0
                    ),
                    "compatibleAnswerKeyVersionIds": payload.get(
                        "compatible_answer_key_version_ids", []
                    ),
                    "reviewStatus": payload.get("answer_review_status"),
                    "reviewReason": payload.get("answer_review_reason"),
                },
                "exportable": payload["status"] == "approved"
                and bool(payload.get("valid_answer_association"))
                and equivalence_ready
                and not validate_editorial_question(question_record)
                and ("duplicate" not in flags or canonical_duplicate),
            }
        )
        if payload.get("canonical_document_id"):
            payload["canonical_identity"] = {
                "contestId": payload.get("canonical_contest_id"),
                "contestKey": payload.get("canonical_contest_key"),
                "contestName": payload.get("canonical_contest_name"),
                "applicationId": payload.get("canonical_application_id"),
                "applicationKey": payload.get("canonical_application_key"),
                "applicationName": payload.get("canonical_application_name"),
                "documentId": payload.get("canonical_document_id"),
                "scopeIds": sorted(set(scope_ids)),
                "aliases": sorted(set(aliases)),
            }
        if document_title := metadata.get("document_title"):
            payload["stored_filename"] = payload["filename"]
            payload["filename"] = document_title
        return payload

    @staticmethod
    def _summary(views: list[dict[str, Any]]) -> dict[str, Any]:
        counts = Counter(cast(str, view["status"]) for view in views)
        for status in ("pending", "approved", "rejected", "exception", "exported"):
            counts.setdefault(status, 0)
        answer_states = Counter(
            cast(
                str,
                view.get("answer_key_state")
                or {
                    "matched": "official",
                    "annulled": "annulled",
                    "missing": "missing",
                }.get(cast(str, view["question"]["answer_status"]), "missing"),
            )
            for view in views
        )
        counts["answer_matched"] = answer_states["official"]
        counts["answer_annulled"] = answer_states["annulled"]
        counts["answer_official"] = (
            answer_states["official"] + answer_states["annulled"]
        )
        counts["answer_missing"] = answer_states["missing"]
        counts["exportable"] = sum(bool(view["exportable"]) for view in views)
        counts["importable"] = sum(bool(view["importable"]) for view in views)
        counts["publication_ready"] = sum(
            bool(view["publication_ready"]) for view in views
        )
        counts["unclassified"] = sum(
            "unclassified" in view["readiness_states"] for view in views
        )
        counts["blocked"] = sum("blocked" in view["readiness_states"] for view in views)
        counts["total"] = len(views)
        summary: dict[str, Any] = dict(counts)
        summary["import_block_reasons"] = dict(
            Counter(
                reason
                for view in views
                for reason in cast(list[str], view["block_reasons"])
            ).most_common()
        )
        summary["answer_key_states"] = dict(answer_states)
        summary["answer_key_diagnostics"] = dict(
            Counter(
                cast(str, view.get("answer_key_diagnosis", {}).get("diagnosticCode"))
                for view in views
                if view.get("answer_key_diagnosis", {}).get("diagnosticCode")
            ).most_common()
        )
        return summary

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
            if view["importable"]:
                actual_statuses.add("importable")
            if view["publication_ready"]:
                actual_statuses.add("publication_ready")
            if not actual_statuses.intersection(filters.statuses):
                return False
        if (
            skip != "answer_states"
            and filters.answer_states
            and view["answer_key_state"] not in filters.answer_states
        ):
            return False
        if (
            skip != "answer_diagnostics"
            and filters.answer_diagnostics
            and view["answer_key_diagnosis"].get("diagnosticCode")
            not in filters.answer_diagnostics
        ):
            return False
        if (
            skip != "readiness_states"
            and filters.readiness_states
            and not set(filters.readiness_states).intersection(
                cast(list[str], view["readiness_states"])
            )
        ):
            return False
        if (
            skip != "block_reasons"
            and filters.block_reasons
            and not set(filters.block_reasons).intersection(
                cast(list[str], view["block_reasons"])
            )
        ):
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
                [
                    view["status"],
                    *(["importable"] if view["importable"] else []),
                    *(["publication_ready"] if view["publication_ready"] else []),
                    *(["exportable"] if view["exportable"] else []),
                ]
            ),
            "answer_states": lambda view: view["answer_key_state"],
            "answer_diagnostics": lambda view: view["answer_key_diagnosis"].get(
                "diagnosticCode"
            ),
            "readiness_states": lambda view: view["readiness_states"],
            "block_reasons": lambda view: view["block_reasons"],
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
                    WHERE id IN ({placeholders}) AND (
                        EXISTS (
                            SELECT 1 FROM documents d
                            WHERE d.id = questions.document_id
                              AND d.document_version_id IS NULL
                        ) OR EXISTS (
                            SELECT 1 FROM document_links l
                            WHERE l.id = questions.answer_key_link_id
                              AND l.status = 'active'
                              AND l.algorithm_version = 'semantic-association-v2'
                        )
                    )""",  # noqa: S608
                (now, now, *question_ids),
            )
            for question_id in question_ids:
                sync_canonical_editorial_from_question(
                    connection, question_id, changed_at=now
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
