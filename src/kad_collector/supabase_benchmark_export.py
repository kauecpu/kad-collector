from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from .canonical_ai_benchmark import (
    _RFB22_OFFICIAL_HEADINGS,
    REFERENCE_KIND,
    load_official_structure_references,
)
from .editorial_taxonomy import EditorialTaxonomy
from .json_utils import read_json
from .models import QuestionRecord
from .ollama_local_paths import validate_local_artifact_path
from .question_equivalence import question_fingerprints

DEFAULT_REFERENCE_REVIEW = Path(
    "docs/benchmarks/canonical-ai-reference-review.v2.json"
)
DEFAULT_SNAPSHOT_PATH = Path(
    "data/benchmarks/local/canonical-ai/collector-copy.sqlite3"
)

_READ_ONLY_EXPORT_QUERY = """
SELECT DISTINCT ON (payload->'source'->>'externalId')
       payload
FROM private.editorial_import_items
WHERE kind = 'question'
  AND status IN ('imported', 'duplicate')
  AND payload->'source'->>'externalId' = ANY(%s::text[])
ORDER BY payload->'source'->>'externalId', imported_at DESC NULLS LAST, id DESC
"""


class SupabaseBenchmarkExportError(ValueError):
    """The remote snapshot cannot safely reproduce the reviewed benchmark source."""


@dataclass(frozen=True)
class SupabaseBenchmarkExportResult:
    executed: bool
    requested_questions: int
    exported_questions: int
    exported_documents: int
    output_path: Path
    sha256: str | None = None


RecordLoader = Callable[[str, Sequence[str]], list[Mapping[str, Any]]]


def _reviewed_records(path: Path) -> dict[str, Mapping[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise SupabaseBenchmarkExportError("artefato de revisão canônica inválido")
    reviewed: dict[str, Mapping[str, Any]] = {}
    for raw_record in cast(list[object], payload["records"]):
        if not isinstance(raw_record, Mapping) or raw_record.get("status") != REFERENCE_KIND:
            continue
        source_id = str(raw_record.get("sourceQuestionId") or "")
        fingerprint = str(raw_record.get("contentFingerprint") or "")
        expected = raw_record.get("reviewedExpected")
        if not source_id or len(fingerprint) != 64 or not isinstance(expected, Mapping):
            raise SupabaseBenchmarkExportError(
                "referência agent_reviewed_reference incompleta"
            )
        if source_id in reviewed:
            raise SupabaseBenchmarkExportError(f"referência duplicada: {source_id}")
        reviewed[source_id] = raw_record
    if not reviewed:
        raise SupabaseBenchmarkExportError("nenhuma referência revisada foi encontrada")
    return reviewed


def _load_import_records(database_url: str, source_ids: Sequence[str]) -> list[Mapping[str, Any]]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "dependência de banco ausente; execute pip install -e .[database]"
        ) from exc

    try:
        with (
            psycopg.connect(
                database_url, connect_timeout=10, row_factory=dict_row
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            cursor.execute(_READ_ONLY_EXPORT_QUERY, (list(source_ids),))
            rows = cursor.fetchall()
            connection.rollback()
    except psycopg.Error as exc:
        raise RuntimeError(
            "não foi possível ler o histórico editorial do Supabase; confira a conexão "
            "e a permissão SELECT em private.editorial_import_items"
        ) from exc
    return [cast(Mapping[str, Any], row) for row in rows]


def _json_object(value: object, *, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SupabaseBenchmarkExportError(f"{label} não contém JSON válido") from exc
    if not isinstance(value, Mapping):
        raise SupabaseBenchmarkExportError(f"{label} deve ser um objeto JSON")
    return {str(key): item for key, item in value.items()}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _official_heading(
    taxonomy: EditorialTaxonomy, expected: Mapping[str, Any]
) -> tuple[str, tuple[str, ...]]:
    wanted = (
        str(expected.get("discipline") or ""),
        str(expected.get("matter") or ""),
        str(expected.get("subject") or ""),
    )
    matches: list[tuple[str, tuple[str, ...]]] = []
    for heading in sorted(_RFB22_OFFICIAL_HEADINGS):
        path = taxonomy.match_section(heading, catalog_ids=("fgv-rfb22",))
        if path is None:
            continue
        if (path.discipline, path.matter, path.subject) == wanted:
            matches.append((heading, path.provenance))
    if not matches:
        raise SupabaseBenchmarkExportError(
            "a referência revisada não corresponde a um título oficial da taxonomia"
        )
    return matches[0]


def _source_provenance(
    record: Mapping[str, Any], *, source_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    data = _json_object(record.get("data"), label=f"data de {source_id}")
    canonical = _json_object(
        data.get("canonicalQuestion"), label=f"canonicalQuestion de {source_id}"
    )
    raw_provenances = canonical.get("provenances")
    if not isinstance(raw_provenances, list) or not raw_provenances:
        raise SupabaseBenchmarkExportError(
            f"a questão {source_id} não preserva proveniência canônica"
        )
    provenances = [
        _json_object(item, label=f"proveniência de {source_id}")
        for item in raw_provenances
    ]
    selected = next(
        (item for item in provenances if str(item.get("questionId") or "") == source_id),
        provenances[0],
    )
    return data, selected


def _question_payload(
    data: Mapping[str, Any], provenance: Mapping[str, Any], *, source_id: str
) -> dict[str, Any]:
    alternatives = data.get("alternatives")
    pages = provenance.get("pages")
    if not isinstance(alternatives, list) or not isinstance(pages, list):
        raise SupabaseBenchmarkExportError(
            f"a questão {source_id} não preserva alternativas e páginas"
        )
    correct = str(data.get("correct") or "")
    payload = {
        "number": provenance.get("questionNumber"),
        "statement": data.get("statement"),
        "alternatives": [
            {
                "letter": _json_object(item, label=f"alternativa de {source_id}").get("id"),
                "text": _json_object(item, label=f"alternativa de {source_id}").get("text"),
            }
            for item in alternatives
        ],
        "matter": data.get("subject"),
        "subject": data.get("topic"),
        "board": data.get("board"),
        "organization": data.get("institution"),
        "role": data.get("role"),
        "year": data.get("year"),
        "source_pages": pages,
        "discipline": data.get("discipline"),
        "concurso": data.get("concurso"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty"),
        "explanation": data.get("explanation"),
        "correct_answer": correct,
        "answer_status": "matched",
        "review_notes": [],
    }
    try:
        return QuestionRecord.model_validate(payload).model_dump(mode="json")
    except ValueError as exc:
        raise SupabaseBenchmarkExportError(
            f"payload remoto incompatível para a questão {source_id}: {exc}"
        ) from exc


def _classification_payload(
    data: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    heading: str,
    provenance: Sequence[str],
) -> dict[str, Any]:
    def value(
        raw: object, source: str, evidence: str, *, confidence: float = 1.0
    ) -> dict[str, Any]:
        return {
            "value": raw,
            "confidence": confidence,
            "evidence": evidence,
            "source": source,
            "reason": None,
            "provenance": list(provenance),
        }

    return {
        "concurso": value(data.get("concurso"), "imported_editorial_record", "Supabase"),
        "board": value(data.get("board"), "imported_editorial_record", "Supabase"),
        "year": value(data.get("year"), "imported_editorial_record", "Supabase"),
        "role": value(data.get("role"), "imported_editorial_record", "Supabase"),
        "organization": value(
            data.get("institution"), "imported_editorial_record", "Supabase"
        ),
        "level": value(
            expected.get("level"),
            "official_contest_requirement",
            "Edital oficial da Receita Federal",
        ),
        "discipline": value(expected.get("discipline"), "section_title", heading),
        "subject": value(expected.get("matter"), "section_title", heading),
        "topic": value(expected.get("subject"), "section_title", heading),
        "difficulty": value(
            data.get("difficulty"), "imported_editorial_record", "Supabase"
        ),
    }


def _normalized_record_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("payload")
    return _json_object(raw, label="payload da importação")


def _build_rows(
    remote_rows: Sequence[Mapping[str, Any]],
    reviewed: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    taxonomy = EditorialTaxonomy.load_default()
    by_source: dict[str, dict[str, Any]] = {}
    for row in remote_rows:
        record = _normalized_record_payload(row)
        source = _json_object(record.get("source"), label="source da importação")
        source_id = str(source.get("externalId") or "")
        if source_id not in reviewed:
            raise SupabaseBenchmarkExportError(
                f"o Supabase retornou uma questão não solicitada: {source_id or '<vazia>'}"
            )
        if source_id in by_source:
            raise SupabaseBenchmarkExportError(
                f"o Supabase retornou a questão {source_id} mais de uma vez"
            )
        by_source[source_id] = record

    missing = sorted(set(reviewed) - set(by_source))
    if missing:
        preview = ", ".join(missing[:3])
        suffix = "..." if len(missing) > 3 else ""
        raise SupabaseBenchmarkExportError(
            f"faltam {len(missing)} referências no Supabase: {preview}{suffix}"
        )

    documents: dict[str, dict[str, Any]] = {}
    questions: list[dict[str, Any]] = []
    for source_id in sorted(reviewed):
        record = by_source[source_id]
        source = _json_object(record.get("source"), label=f"source de {source_id}")
        data, source_provenance = _source_provenance(record, source_id=source_id)
        review = reviewed[source_id]
        expected = _json_object(
            review.get("reviewedExpected"), label=f"reviewedExpected de {source_id}"
        )
        heading, classification_provenance = _official_heading(taxonomy, expected)
        payload = _question_payload(data, source_provenance, source_id=source_id)
        actual_fingerprint = question_fingerprints(payload).invariant
        expected_fingerprint = str(review["contentFingerprint"])
        if actual_fingerprint != expected_fingerprint:
            raise SupabaseBenchmarkExportError(
                f"fingerprint divergente para {source_id}: o conteúdo remoto mudou"
            )

        sha256 = str(source_provenance.get("sha256") or "")
        source_url = str(source_provenance.get("url") or source.get("url") or "")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise SupabaseBenchmarkExportError(
                f"a questão {source_id} não preserva SHA-256 válido do documento"
            )
        if (urlparse(source_url).hostname or "").casefold() != "conhecimento.fgv.br":
            raise SupabaseBenchmarkExportError(
                f"a questão {source_id} não aponta para o host oficial da FGV"
            )
        document_id = str(source_provenance.get("documentId") or f"document:{sha256}")
        document = {
            "id": document_id,
            "metadata_json": _canonical_json(
                {
                    "canonical_url": source_url,
                    "source_url": source_url,
                    "export_source": "supabase-private-editorial-import",
                }
            ),
            "sha256": sha256,
        }
        previous_document = documents.get(document_id)
        if previous_document is not None and previous_document != document:
            raise SupabaseBenchmarkExportError(
                f"metadados conflitantes para o documento {document_id}"
            )
        documents[document_id] = document
        questions.append(
            {
                "id": source_id,
                "document_id": document_id,
                "payload_json": _canonical_json(payload),
                "classification_json": _canonical_json(
                    _classification_payload(
                        data,
                        expected,
                        heading=heading,
                        provenance=classification_provenance,
                    )
                ),
            }
        )
    return list(documents.values()), questions


def _write_snapshot(
    output_path: Path,
    *,
    documents: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with closing(sqlite3.connect(temporary_path)) as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL
                );
                CREATE TABLE questions (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    payload_json TEXT NOT NULL,
                    classification_json TEXT NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT INTO documents (id, metadata_json, sha256) VALUES (?, ?, ?)",
                [
                    (item["id"], item["metadata_json"], item["sha256"])
                    for item in documents
                ],
            )
            connection.executemany(
                """
                INSERT INTO questions (id, document_id, payload_json, classification_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["document_id"],
                        item["payload_json"],
                        item["classification_json"],
                    )
                    for item in questions
                ],
            )
            connection.commit()
            connection.row_factory = sqlite3.Row
            references, _ = load_official_structure_references(connection)
        expected = {
            (
                str(item["id"]),
                question_fingerprints(json.loads(str(item["payload_json"]))).invariant,
            )
            for item in questions
        }
        actual = {(item.source_question_id, item.content_fingerprint) for item in references}
        if actual != expected:
            raise SupabaseBenchmarkExportError(
                "a cópia SQLite não reproduziu todas as referências oficiais"
            )
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def export_supabase_benchmark_snapshot(
    *,
    reference_review_path: Path = DEFAULT_REFERENCE_REVIEW,
    output_path: Path = DEFAULT_SNAPSHOT_PATH,
    execute: bool = False,
    database_url: str | None = None,
    record_loader: RecordLoader = _load_import_records,
) -> SupabaseBenchmarkExportResult:
    resolved_output = validate_local_artifact_path(
        output_path, label="cópia SQLite do Supabase"
    )
    reviewed = _reviewed_records(reference_review_path)
    if not execute:
        return SupabaseBenchmarkExportResult(
            executed=False,
            requested_questions=len(reviewed),
            exported_questions=0,
            exported_documents=0,
            output_path=resolved_output,
        )

    resolved_database_url = database_url or os.environ.get("KAD_DATABASE_URL")
    if not resolved_database_url:
        raise RuntimeError(
            "defina KAD_DATABASE_URL com a conexão PostgreSQL do Supabase "
            "para executar a exportação"
        )
    remote_rows = record_loader(resolved_database_url, tuple(sorted(reviewed)))
    documents, questions = _build_rows(remote_rows, reviewed)
    _write_snapshot(resolved_output, documents=documents, questions=questions)
    digest = hashlib.sha256(resolved_output.read_bytes()).hexdigest()
    return SupabaseBenchmarkExportResult(
        executed=True,
        requested_questions=len(reviewed),
        exported_questions=len(questions),
        exported_documents=len(documents),
        output_path=resolved_output,
        sha256=digest,
    )
