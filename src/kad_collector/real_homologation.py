from __future__ import annotations

import hashlib
import json
import platform
import statistics
import tempfile
import threading
import time
import tomllib
import tracemalloc
import urllib.robotparser
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pypdf import PdfReader

from .desktop_models import DesktopImportMetadata
from .desktop_processor import DesktopProcessor
from .desktop_store import DesktopStore
from .document_triage import classify_document
from .official_regression import load_official_manifest

DocumentKind = Literal["exam", "answer_key", "other"]
TriageDecision = Literal["exam", "answer_key", "other", "review"]


class RealHomologationError(ValueError):
    """The real-document corpus or its execution is invalid."""


class ExternalDocumentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    bank: str = Field(min_length=1)
    kind: DocumentKind
    expected_triage: TriageDecision
    declared_type: Literal["auto", "exam", "answer_key", "other"] = "auto"
    filename: str = Field(min_length=1)
    source_url: str = Field(pattern=r"^https://")
    source_page_url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=50 * 1024 * 1024)
    page_count: int = Field(gt=0, le=1_000)
    title: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    contest: str = Field(min_length=1)
    year: int = Field(ge=1900, le=2100)
    role: str | None = None
    organization: str | None = None
    turn: str | None = None
    variant: str | None = None
    answer_key_id: str | None = None

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        path = Path(value)
        if path.name != value or path.suffix.casefold() != ".pdf":
            raise ValueError("filename must be a plain PDF filename")
        return value


class RealHomologationSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    cache_dir: Path
    official_manifests: tuple[Path, ...] = Field(min_length=1)
    documents: tuple[ExternalDocumentSpec, ...] = Field(min_length=1)

    @field_validator("cache_dir")
    @classmethod
    def safe_cache_dir(cls, value: Path) -> Path:
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("cache_dir must stay inside the repository")
        return value


@dataclass(frozen=True)
class LoadedRealHomologation:
    path: Path
    repo_root: Path
    spec: RealHomologationSpec


@dataclass(frozen=True)
class CorpusEntry:
    id: str
    bank: str
    kind: DocumentKind
    expected_triage: TriageDecision
    path: Path
    source_url: str
    source_page_url: str
    sha256: str
    size_bytes: int
    page_count: int
    metadata: DesktopImportMetadata
    answer_key_id: str | None = None


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: Message,
        new_url: str,
    ) -> Request | None:
        del request, file_pointer, code, message, headers
        raise RealHomologationError(f"redirect not allowed: {new_url}")


def load_real_homologation(path: Path) -> LoadedRealHomologation:
    resolved = path.resolve()
    try:
        payload = tomllib.loads(resolved.read_text(encoding="utf-8"))
        spec = RealHomologationSpec.model_validate(payload)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise RealHomologationError(f"invalid real homologation manifest: {exc}") from exc
    ids = [item.id for item in spec.documents]
    if len(ids) != len(set(ids)):
        raise RealHomologationError("external document IDs must be unique")
    known = set(ids)
    for document in spec.documents:
        if document.answer_key_id is not None and document.answer_key_id not in known:
            raise RealHomologationError(
                f"unknown external answer key: {document.id} -> {document.answer_key_id}"
            )
    repo_root = next(
        (
            parent
            for parent in (resolved.parent, *resolved.parents)
            if (parent / "pyproject.toml").is_file()
        ),
        None,
    )
    if repo_root is None:
        raise RealHomologationError("manifest is not inside a repository")
    for item in spec.official_manifests:
        candidate = (resolved.parent / item).resolve()
        if not candidate.is_relative_to(repo_root):
            raise RealHomologationError("official manifest must stay inside the repository")
    return LoadedRealHomologation(path=resolved, repo_root=repo_root, spec=spec)


def _official_entries(loaded: LoadedRealHomologation) -> list[CorpusEntry]:
    entries: list[CorpusEntry] = []
    for relative in loaded.spec.official_manifests:
        official = load_official_manifest((loaded.path.parent / relative).resolve())
        supported = {
            item.id: item
            for item in official.spec.documents
            if item.support_status == "supported"
        }
        for document in supported.values():
            role = document.roles[0]
            metadata = DesktopImportMetadata(
                provider="fgv_conhecimento",
                source_url=document.source_url,
                canonical_url=document.source_url,
                external_id=document.id,
                document_title=document.title,
                variant=(
                    f"Tipo {document.booklet_type}"
                    if document.booklet_type is not None
                    else None
                ),
                document_type="auto",
                concurso=document.contest_aliases[0],
                board=document.board,
                year=document.application_year,
                role=role,
                stage=document.stage,
                turn=document.shift,
                organization=document.organization,
            )
            entries.append(
                CorpusEntry(
                    id=document.id,
                    bank="FGV",
                    kind=cast(DocumentKind, document.kind),
                    expected_triage=cast(TriageDecision, document.kind),
                    path=(official.path.parent / document.path).resolve(),
                    source_url=document.source_url,
                    source_page_url=document.source_page_url,
                    sha256=document.sha256,
                    size_bytes=document.size_bytes,
                    page_count=document.page_count,
                    metadata=metadata,
                    answer_key_id=document.answer_key_id,
                )
            )
    return entries


def corpus_entries(loaded: LoadedRealHomologation) -> list[CorpusEntry]:
    entries = _official_entries(loaded)
    cache_root = (loaded.repo_root / loaded.spec.cache_dir).resolve()
    for document in loaded.spec.documents:
        metadata = DesktopImportMetadata(
            provider=document.provider,
            source_url=document.source_url,
            canonical_url=document.source_url,
            external_id=document.id,
            document_title=document.title,
            variant=document.variant,
            document_type=document.declared_type,
            concurso=document.contest,
            board=document.bank,
            year=document.year,
            role=document.role,
            turn=document.turn,
            organization=document.organization,
        )
        entries.append(
            CorpusEntry(
                id=document.id,
                bank=document.bank,
                kind=document.kind,
                expected_triage=document.expected_triage,
                path=(cache_root / document.filename).resolve(),
                source_url=document.source_url,
                source_page_url=document.source_page_url,
                sha256=document.sha256,
                size_bytes=document.size_bytes,
                page_count=document.page_count,
                metadata=metadata,
                answer_key_id=document.answer_key_id,
            )
        )
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise RealHomologationError("corpus document IDs must be unique")
    return entries


def validate_corpus_file(entry: CorpusEntry) -> None:
    try:
        size = entry.path.stat().st_size
        if size != entry.size_bytes:
            raise RealHomologationError(
                f"size mismatch: {entry.id} ({size} != {entry.size_bytes})"
            )
        with entry.path.open("rb") as handle:
            if not handle.read(5).startswith(b"%PDF-"):
                raise RealHomologationError(f"invalid PDF signature: {entry.id}")
            handle.seek(0)
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != entry.sha256:
            raise RealHomologationError(f"SHA-256 mismatch: {entry.id}")
        page_count = len(PdfReader(entry.path, strict=False).pages)
        if page_count != entry.page_count:
            raise RealHomologationError(
                f"page count mismatch: {entry.id} ({page_count} != {entry.page_count})"
            )
    except OSError as exc:
        raise RealHomologationError(f"missing corpus file: {entry.id}") from exc


def _check_download_policy(entry: CorpusEntry) -> None:
    parsed = urlsplit(entry.source_url)
    allowed_hosts = {"cdn.cebraspe.org.br", "www.concursosfcc.com.br"}
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise RealHomologationError(f"unexpected download host: {entry.id}")
    robots_url = f"https://{parsed.hostname}/robots.txt"
    opener = build_opener(_RejectRedirects())
    request = Request(robots_url, headers={"User-Agent": "KADCollector-RealHomologation/1.0"})
    with opener.open(request, timeout=30) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise RealHomologationError(f"robots.txt exceeds limit: {entry.id}")
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(payload.decode("utf-8", errors="replace").splitlines())
    agent = "KADCollector-RealHomologation/1.0"
    if not parser.can_fetch(agent, entry.source_url):
        raise RealHomologationError(f"robots.txt blocks download: {entry.id}")
    delay = int(parser.crawl_delay(agent) or parser.crawl_delay("*") or 0)
    if delay > 60:
        raise RealHomologationError(f"crawl delay requires a separate preparation: {entry.id}")
    time.sleep(max(1, delay))


def prepare_external_documents(
    loaded: LoadedRealHomologation,
    *,
    timeout_seconds: int = 60,
) -> list[Path]:
    opener = build_opener(_RejectRedirects())
    entries = {entry.id: entry for entry in corpus_entries(loaded)}
    prepared: list[Path] = []
    for document in loaded.spec.documents:
        entry = entries[document.id]
        try:
            validate_corpus_file(entry)
        except RealHomologationError:
            pass
        else:
            prepared.append(entry.path)
            continue
        _check_download_policy(entry)
        entry.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=entry.path.parent, prefix=f".{entry.path.name}.", delete=False
            ) as output:
                temporary = Path(output.name)
                request = Request(
                    entry.source_url,
                    headers={"User-Agent": "KADCollector-RealHomologation/1.0"},
                )
                with opener.open(request, timeout=timeout_seconds) as response:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > entry.size_bytes:
                            raise RealHomologationError(
                                f"download exceeds declared size: {entry.id}"
                            )
                        output.write(chunk)
            validate_corpus_file(replace(entry, path=temporary))
            temporary.replace(entry.path)
            temporary = None
            validate_corpus_file(entry)
            prepared.append(entry.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return prepared


def _entry_metrics(
    entry: CorpusEntry,
    store: DesktopStore,
    job_id: str,
    elapsed_seconds: float,
    peak_python_bytes: int | None,
) -> dict[str, Any]:
    documents = store.documents_for_job(job_id)
    document = next(
        item for item in documents if Path(cast(str, item["local_path"])).resolve() == entry.path
    )
    document_id = cast(str, document["id"])
    pages = store.pages(document_id)
    questions = [question for question, _ in store.question_records(document_id)]
    triage = cast(dict[str, Any] | None, document.get("triage")) or {}
    automatic = classify_document(
        filename=entry.path.name,
        title=entry.metadata.document_title,
        text="\n".join(str(page["text"]) for page in pages),
        declared_type="auto",
    )
    ocr_recovered = sum(page["status"] == "ocr_text" for page in pages)
    ocr_unreadable = sum(page["status"] == "ocr_required" for page in pages)
    matched = sum(question.answer_status == "matched" for question in questions)
    annulled = sum(question.answer_status == "annulled" for question in questions)
    status = cast(str, document["status"])
    return {
        "id": entry.id,
        "bank": entry.bank,
        "kind": entry.kind,
        "source_url": entry.source_url,
        "source_page_url": entry.source_page_url,
        "sha256": entry.sha256,
        "size_bytes": entry.size_bytes,
        "page_count": len(pages),
        "document_status": status,
        "job_status": store.job(job_id)["status"],
        "triage_decision": triage.get("decision"),
        "triage_confidence": triage.get("confidence"),
        "triage_reason": triage.get("reason"),
        "triage_source": triage.get("source"),
        "automatic_triage": automatic.model_dump(mode="json"),
        "classification_correct": automatic.decision == entry.expected_triage,
        "download_status": "cached_file_integrity_verified",
        "download_seconds": None,
        "text_pages": sum(page["status"] == "text" for page in pages),
        "ocr_pages_recovered": ocr_recovered,
        "ocr_pages_unreadable": ocr_unreadable,
        "questions_found": len(questions),
        "alternatives_found": sum(len(question.alternatives) for question in questions),
        "matched_answers": matched,
        "annulled_answers": annulled,
        "answer_key_found": bool(matched or annulled) if entry.kind == "exam" else None,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "peak_python_bytes": peak_python_bytes,
        "warnings": cast(list[str], document["warnings"]),
        "result": (
            "success"
            if status in {"processed", "excluded"}
            or (entry.kind == "answer_key" and status == "extracted")
            else "partial"
            if pages
            else "failed"
        ),
    }


class _TimedProcessor(DesktopProcessor):
    def __init__(self, store: DesktopStore) -> None:
        super().__init__(store)
        self.extraction_seconds = 0.0
        self.structuring_seconds = 0.0

    def _extract_document(
        self,
        job_id: str,
        document: dict[str, Any],
        event: threading.Event,
        started: float,
    ) -> None:
        begin = time.monotonic()
        try:
            super()._extract_document(job_id, document, event, started)
        finally:
            self.extraction_seconds += time.monotonic() - begin

    def _structure_job(self, job_id: str, event: threading.Event) -> None:
        begin = time.monotonic()
        try:
            super()._structure_job(job_id, event)
        finally:
            self.structuring_seconds += time.monotonic() - begin


def _process_entry(
    entry: CorpusEntry,
    entries: dict[str, CorpusEntry],
    *,
    measure_memory: bool = True,
) -> dict[str, Any]:
    selected = [entry]
    if entry.answer_key_id is not None:
        selected.append(entries[entry.answer_key_id])
    with tempfile.TemporaryDirectory(prefix="kad-real-homologation-") as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        processor = _TimedProcessor(store)
        try:
            paths = [item.path for item in selected]
            metadata = {str(item.path): item.metadata for item in selected}
            job_id = store.create_job(
                paths,
                DesktopImportMetadata(),
                "local",
                metadata_by_path=metadata,
            )
            if measure_memory:
                tracemalloc.start()
                tracemalloc.reset_peak()
            started = time.monotonic()
            try:
                processor.run(job_id)
                elapsed = time.monotonic() - started
                peak = tracemalloc.get_traced_memory()[1] if measure_memory else None
            finally:
                if measure_memory:
                    tracemalloc.stop()
            metrics = _entry_metrics(entry, store, job_id, elapsed, peak)
            metrics["stage_seconds"] = {
                "extraction_ocr_triage": round(processor.extraction_seconds, 3),
                "structuring_association": round(processor.structuring_seconds, 3),
                "other": round(
                    max(0, elapsed - processor.extraction_seconds - processor.structuring_seconds),
                    3,
                ),
            }
            metrics["timing_scope"] = "document_and_declared_answer_key"
            return metrics
        finally:
            processor.shutdown()


def _resume_probe(entry: CorpusEntry) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kad-real-resume-") as directory:
        store = DesktopStore(Path(directory) / "collector.sqlite3")
        processor = DesktopProcessor(store)
        try:
            job_id = store.create_job([entry.path], entry.metadata, "local")
            stopped = threading.Event()
            original_save = store.save_page

            def save_and_pause(*args: Any, **kwargs: Any) -> None:
                original_save(*args, **kwargs)
                if args[1] == 3:
                    stopped.set()

            store.save_page = save_and_pause  # type: ignore[method-assign]
            processor.run(job_id, stopped)
            paused_status = cast(str, store.job(job_id)["status"])
            document_id = cast(str, store.documents_for_job(job_id)[0]["id"])
            checkpoint = store.pages(document_id)
            processor.shutdown()
            store = DesktopStore(Path(directory) / "collector.sqlite3")
            processor = DesktopProcessor(store)
            processor.run(job_id)
            resumed_status = cast(str, store.job(job_id)["status"])
            resumed_pages = store.pages(document_id)
            preserved = all(
                any(
                    page["page_number"] == prior["page_number"]
                    and page["text"] == prior["text"]
                    for page in resumed_pages
                )
                for prior in checkpoint
                if prior["status"] == "text"
            )
            duplicate_blocked = False
            try:
                store.create_job([entry.path], entry.metadata, "local")
            except ValueError:
                duplicate_blocked = True
            return {
                "document_id": entry.id,
                "paused_status": paused_status,
                "resumed_status": resumed_status,
                "duplicate_blocked": duplicate_blocked,
                "checkpoint_pages": len(checkpoint),
                "resumed_pages": len(resumed_pages),
                "checkpoint_text_preserved": preserved,
                "new_store_and_processor": True,
                "passed": (
                    paused_status == "paused"
                    and resumed_status == "completed"
                    and duplicate_blocked
                    and len(checkpoint) == 3
                    and len(resumed_pages) == entry.page_count
                    and preserved
                ),
            }
        finally:
            processor.shutdown()


def _summary(results: list[dict[str, Any]], resume_probe: dict[str, Any]) -> dict[str, Any]:
    total = len(results)
    recovered = sum(int(item["ocr_pages_recovered"]) for item in results)
    unreadable = sum(int(item["ocr_pages_unreadable"]) for item in results)
    bank_names = {cast(str, item["bank"]) for item in results}
    bank_counts = {
        bank: sum(item["bank"] == bank for item in results) for bank in bank_names
    }
    return {
        "documents": total,
        "banks": dict(sorted(bank_counts.items())),
        "download_success_rate": None,
        "cached_integrity_rate": 1.0 if total else 0.0,
        "classification_accuracy": (
            sum(bool(item["classification_correct"]) for item in results) / total
            if total
            else 0.0
        ),
        "processed_without_failure_rate": (
            sum(item["result"] == "success" for item in results) / total if total else 0.0
        ),
        "ocr_recovery_rate": (
            recovered / (recovered + unreadable) if recovered + unreadable else None
        ),
        "resume_success_rate": 1.0 if resume_probe["passed"] else 0.0,
        "median_elapsed_seconds": (
            round(statistics.median(float(item["elapsed_seconds"]) for item in results), 3)
            if results
            else 0.0
        ),
        "max_peak_python_bytes": max(
            (
                int(item["peak_python_bytes"])
                for item in results
                if item["peak_python_bytes"] is not None
            ),
            default=None,
        ),
        "questions_found": sum(int(item["questions_found"]) for item in results),
        "failures": sum(item["result"] == "failed" for item in results),
        "partial": sum(item["result"] == "partial" for item in results),
    }


def _markdown_report(report: dict[str, Any]) -> str:
    summary = cast(dict[str, Any], report["summary"])
    recovery_rate = summary["ocr_recovery_rate"]
    recovery_line = (
        f"- Recuperação OCR: {recovery_rate:.1%}"
        if recovery_rate is not None
        else "- Recuperação OCR: sem páginas elegíveis"
    )
    lines = [
        "# Homologação real de PDFs",
        "",
        f"- Execução: `{report['generated_at']}`",
        f"- Commit: `{report['commit']}`",
        f"- Sistema: `{report['platform']}`",
        f"- Documentos: {summary['documents']}",
        f"- Bancas: {json.dumps(summary['banks'], ensure_ascii=False, sort_keys=True)}",
        "- Taxa de download: não medida; arquivos já presentes no cache",
        f"- Integridade do cache: {summary['cached_integrity_rate']:.1%}",
        f"- Triagem automática correta: {summary['classification_accuracy']:.1%}",
        f"- Documentos concluídos: {summary['processed_without_failure_rate']:.1%}",
        recovery_line,
        f"- Retomada: {summary['resume_success_rate']:.1%}",
        f"- Tempo mediano: {summary['median_elapsed_seconds']:.3f} s",
        f"- Pico aproximado de memória Python (bytes; None = não medido): "
        f"{summary['max_peak_python_bytes']}",
        "",
        "## Resultado por documento",
        "",
        "| Documento | Banca | Tipo | Triagem | Páginas | OCR | Questões | "
        "Respostas | Tempo | Resultado |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in cast(list[dict[str, Any]], report["documents"]):
        lines.append(
            "| {id} | {bank} | {kind} | {triage_decision} | {page_count} | "
            "{ocr_pages_recovered} | {questions_found} | {matched_answers} | "
            "{elapsed_seconds:.3f}s | {result} |".format(**item)
        )
    lines.extend(
        [
            "",
            "## Retomada e duplicação",
            "",
            f"```json\n{json.dumps(report['resume_probe'], ensure_ascii=False, indent=2)}\n```",
            "",
            "## Limites observados",
            "",
            "- `peak_python_bytes` mede alocações do Python e não inclui toda a "
            "memória nativa do OCR.",
            "- Prova e gabarito no mesmo PDF e detecção completa de republicações "
            "permanecem fora deste trabalho.",
            "- PDFs do corpus ficam no cache local e não entram no Git.",
            "- Taxa OCR inclui páginas visualmente vazias; "
            "recuperação não mede fidelidade textual.",
            "- Tempo e memória incluem o gabarito pareado quando houver.",
            "- Triagem automática é medida separadamente do tipo declarado pelo operador.",
            "",
        ]
    )
    return "\n".join(lines)


def run_real_homologation(
    manifest_path: Path,
    report_path: Path,
    markdown_path: Path,
    *,
    commit: str = "unknown",
    measure_memory: bool = True,
) -> dict[str, Any]:
    loaded = load_real_homologation(manifest_path)
    entries = corpus_entries(loaded)
    if not 20 <= len(entries) <= 50:
        raise RealHomologationError("real corpus must contain between 20 and 50 PDFs")
    banks = {entry.bank for entry in entries}
    if not {"FGV", "Cebraspe", "FCC"}.issubset(banks):
        raise RealHomologationError("real corpus must include FGV, Cebraspe, and FCC")
    for entry in entries:
        validate_corpus_file(entry)
    by_id = {entry.id: entry for entry in entries}
    results = []
    for index, entry in enumerate(entries, 1):
        print(f"[{index}/{len(entries)}] {entry.id}", flush=True)
        results.append(_process_entry(entry, by_id, measure_memory=measure_memory))
    resume_entry = next(entry for entry in entries if entry.kind == "exam")
    resume_probe = _resume_probe(resume_entry)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "offline_processing": True,
        "memory_tracing": measure_memory,
        "documents": results,
        "resume_probe": resume_probe,
        "summary": _summary(results, resume_probe),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    return report
