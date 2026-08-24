from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .desktop_models import DesktopFilterSet
from .desktop_store import DesktopStore
from .editorial_export import build_editorial_record
from .json_utils import write_json, write_json_lines
from .models import DocumentRecord, QuestionBatch, QuestionRecord, ValidationState
from .validation import validate_editorial_question


@dataclass(frozen=True)
class DesktopExportResult:
    directory: Path
    questions_path: Path
    exceptions_path: Path
    report_path: Path
    exported_count: int
    exception_count: int


@dataclass(frozen=True)
class DesktopExportPreview:
    selected: int
    included_count: int
    exception_count: int
    questions: list[dict[str, Any]]
    exclusion_reasons: dict[str, int]


@dataclass(frozen=True)
class _DesktopExportEvaluation:
    selected: int
    records: list[dict[str, Any]]
    included_views: list[dict[str, Any]]
    exceptions: list[dict[str, Any]]
    exported_ids: list[str]
    evidence: dict[str, Path]
    reason_counts: Counter[str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _document_record(view: dict[str, Any]) -> DocumentRecord:
    metadata = cast(dict[str, Any], view["metadata"])
    provider = str(metadata.get("provider") or "").strip()
    source_url = str(metadata.get("source_url") or "").strip()
    digest = str(view.get("document_sha256") or "")
    if len(digest) != 64:
        raise ValueError("hash do PDF de origem ausente")
    return DocumentRecord(
        source_id=provider,
        source_name=provider,
        document_type="exam",
        title=cast(str, view["filename"]),
        original_url=source_url,
        resolved_url=source_url,
        local_path=cast(str, view["local_path"]),
        sha256=digest,
        content_type="application/pdf",
        size_bytes=int(view["size_bytes"]),
        downloaded_at=datetime.fromisoformat(cast(str, view["document_created_at"])),
        authorization_basis="Arquivo fornecido pelo operador para revisão editorial local",
        metadata={
            key: str(value)
            for key, value in metadata.items()
            if value is not None and key not in {"source_url", "provider"}
        },
    )


def _batch_for(view: dict[str, Any], question: QuestionRecord) -> QuestionBatch:
    document = _document_record(view)
    return QuestionBatch(
        batch_id=cast(str, view["document_id"]),
        created_at=datetime.now(UTC),
        model="kad-collector-desktop-v1",
        source_document=document,
        questions=[question],
        validation=ValidationState(valid=True),
    )


def _question_exception(view: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    return {
        "kind": "question",
        "questionId": view["id"],
        "documentId": view["document_id"],
        "questionNumber": view["question_number"],
        "status": view["status"],
        "issues": list(dict.fromkeys(issues)),
        "question": view["question"],
        "source": {
            "filename": view["filename"],
            "metadata": view["metadata"],
        },
    }


def _evaluate_filtered_questions(
    store: DesktopStore, filters: DesktopFilterSet
) -> _DesktopExportEvaluation:
    candidates = store.export_candidates(filters)
    records: list[dict[str, Any]] = []
    included_views: list[dict[str, Any]] = []
    selected_document_ids = {cast(str, item["document_id"]) for item in candidates}
    exceptions: list[dict[str, Any]] = store.document_exceptions(selected_document_ids)
    exported_ids: list[str] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    evidence: dict[str, Path] = {}
    reason_counts: Counter[str] = Counter()

    for view in candidates:
        question = QuestionRecord.model_validate(view["question"])
        issues = validate_editorial_question(question)
        if view["status"] != "approved":
            issues.append("questão ainda não aprovada na revisão editorial")
        if not view.get("valid_answer_association"):
            issues.append("resposta sem associação semantic-association-v2 ativa e válida")
        if "duplicate" in view["flags"]:
            issues.append("conteúdo duplicado; resolva antes da exportação")
        metadata = cast(dict[str, Any], view["metadata"])
        if not str(metadata.get("provider") or "").strip():
            issues.append("provider da origem não informado")
        source_url = str(metadata.get("source_url") or "").strip()
        if not source_url.startswith("https://"):
            issues.append("URL HTTPS da origem não informada")
        source_path = Path(cast(str, view["local_path"]))
        expected_sha = str(view.get("document_sha256") or "")
        if not source_path.is_file():
            issues.append("PDF de evidência não encontrado")
        elif _sha256(source_path) != expected_sha:
            issues.append("PDF de evidência diverge do hash processado")
        if issues:
            exceptions.append(_question_exception(view, issues))
            reason_counts.update(issues)
            continue
        try:
            record = build_editorial_record(
                _batch_for(view, question),
                question,
                canonical_identity=cast(dict[str, Any] | None, view.get("canonical_identity")),
            )
        except ValueError as exc:
            issues = [str(exc)]
            exceptions.append(_question_exception(view, issues))
            reason_counts.update(issues)
            continue
        if record.data.id in seen_ids:
            issues = ["ID estável duplicado no resultado filtrado"]
        elif record.source.fingerprint in seen_fingerprints:
            issues = ["conteúdo duplicado no resultado filtrado"]
        else:
            issues = []
        if issues:
            exceptions.append(_question_exception(view, issues))
            reason_counts.update(issues)
            continue
        seen_ids.add(record.data.id)
        seen_fingerprints.add(record.source.fingerprint)
        records.append(record.model_dump(mode="json", by_alias=True, exclude_none=True))
        included_views.append(view)
        exported_ids.append(cast(str, view["id"]))
        evidence[expected_sha] = source_path

    return _DesktopExportEvaluation(
        selected=len(candidates),
        records=records,
        included_views=included_views,
        exceptions=exceptions,
        exported_ids=exported_ids,
        evidence=evidence,
        reason_counts=reason_counts,
    )


def preview_filtered_questions(
    store: DesktopStore, filters: DesktopFilterSet
) -> DesktopExportPreview:
    """Evaluate the real export gates without files or database mutations."""

    evaluation = _evaluate_filtered_questions(store, filters)
    return DesktopExportPreview(
        selected=evaluation.selected,
        included_count=len(evaluation.records),
        exception_count=len(evaluation.exceptions),
        questions=[
            {
                "questionId": view["id"],
                "number": view["question"]["number"],
                "statement": view["question"]["statement"],
                "discipline": view["question"].get("discipline"),
                "matter": view["question"].get("matter"),
                "subject": view["question"].get("subject"),
                "sourceDocument": view["filename"],
            }
            for view in evaluation.included_views
        ],
        exclusion_reasons=dict(evaluation.reason_counts.most_common()),
    )


def export_filtered_questions(
    store: DesktopStore,
    filters: DesktopFilterSet,
    *,
    output_root: Path,
    now: datetime | None = None,
) -> DesktopExportResult:
    created_at = now or datetime.now(UTC)
    directory = output_root / f"KAD-export-{created_at.strftime('%Y%m%d-%H%M%S')}"
    questions_path = directory / "questoes.jsonl"
    exceptions_path = directory / "excecoes.jsonl"
    report_path = directory / "relatorio.json"
    evaluation = _evaluate_filtered_questions(store, filters)

    write_json_lines(questions_path, evaluation.records)
    write_json_lines(exceptions_path, evaluation.exceptions)
    evidence_paths: list[Path] = []
    for digest, source in evaluation.evidence.items():
        destination = directory / "fontes" / f"prova-{digest[:16]}.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        evidence_paths.append(destination)
    write_json(
        report_path,
        {
            "schemaVersion": 1,
            "createdAt": created_at.isoformat(),
            "filters": filters.model_dump(mode="json"),
            "selected": evaluation.selected,
            "exported": len(evaluation.records),
            "exceptions": len(evaluation.exceptions),
            "exclusionReasons": dict(evaluation.reason_counts.most_common()),
            "notes": [
                "Somente questões aprovadas, válidas e com origem HTTPS foram exportadas.",
                "PDFs são evidência; questoes.jsonl é o arquivo aceito pelo painel KAD.",
            ],
        },
    )
    manifest_files = [questions_path, exceptions_path, report_path, *evidence_paths]
    write_json(
        directory / "manifesto.json",
        {
            "schemaVersion": 1,
            "createdAt": created_at.isoformat(),
            "importFile": "questoes.jsonl",
            "files": [
                {
                    "path": str(path.relative_to(directory)).replace("\\", "/"),
                    "sha256": _sha256(path),
                    "sizeBytes": path.stat().st_size,
                }
                for path in manifest_files
            ],
        },
    )
    store.mark_exported(evaluation.exported_ids)
    return DesktopExportResult(
        directory=directory,
        questions_path=questions_path,
        exceptions_path=exceptions_path,
        report_path=report_path,
        exported_count=len(evaluation.records),
        exception_count=len(evaluation.exceptions),
    )
