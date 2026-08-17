from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .answer_key import parse_answer_key
from .desktop_classifier import LocalRuleClassifier, build_classifier
from .desktop_limits import MAX_BATCH_PAGES, MAX_PDF_BYTES, MAX_PDF_PAGES
from .desktop_models import (
    ClassificationRequest,
    DesktopImportMetadata,
    QuestionClassification,
)
from .desktop_parser import parse_question_pages
from .desktop_store import DesktopStore
from .models import QuestionRecord


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _document_type(filename: str, metadata: DesktopImportMetadata) -> str:
    if metadata.document_type != "auto":
        return metadata.document_type
    normalized = filename.casefold()
    answer_markers = (
        "answer_key",
        "answer-key",
        "gabarito",
        "padrao-resposta",
        "padrao_resposta",
        "respostas",
        "solucao",
    )
    return "answer_key" if any(item in normalized for item in answer_markers) else "exam"


def _apply_classification(
    question: QuestionRecord,
    classification: QuestionClassification,
) -> QuestionRecord:
    values: dict[str, Any] = {}
    mapping = {
        "discipline": "discipline",
        "subject": "matter",
        "topic": "subject",
        "board": "board",
        "organization": "organization",
        "role": "role",
        "year": "year",
        "concurso": "concurso",
        "level": "level",
        "difficulty": "difficulty",
    }
    for source, target in mapping.items():
        candidate = getattr(classification, source)
        if candidate.value is not None and candidate.confidence >= 0.65:
            values[target] = candidate.value
    payload = question.model_dump(mode="json")
    payload.update(values)
    return QuestionRecord.model_validate(payload)


class DesktopProcessor:
    def __init__(self, store: DesktopStore) -> None:
        self.store = store
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start(self, job_id: str) -> None:
        with self._lock:
            existing = self._threads.get(job_id)
            if existing is not None and existing.is_alive():
                return
            event = threading.Event()
            thread = threading.Thread(
                target=self.run,
                args=(job_id, event),
                name=f"kad-collector-{job_id[:8]}",
                daemon=True,
            )
            self._cancel_events[job_id] = event
            self._threads[job_id] = thread
            thread.start()

    def cancel(self, job_id: str) -> None:
        event = self._cancel_events.get(job_id)
        if event is not None:
            event.set()
        self.store.update_job(job_id, status="cancelling", message="Pausando com segurança")

    def run(self, job_id: str, cancel_event: threading.Event | None = None) -> None:
        event = cancel_event or threading.Event()
        started = time.monotonic()
        self.store.update_job(
            job_id,
            status="running",
            message="Preparando documentos",
            error=None,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        try:
            documents = self.store.documents_for_job(job_id)
            total_pages = 0
            for document in documents:
                path = Path(cast(str, document["local_path"]))
                document_id = cast(str, document["id"])
                warnings = list(cast(list[str], document["warnings"]))
                try:
                    size = path.stat().st_size
                    if size > MAX_PDF_BYTES:
                        warnings.append(
                            f"PDF excede o limite de {MAX_PDF_BYTES // (1024 * 1024)} MB"
                        )
                        self.store.update_document(
                            document_id,
                            size_bytes=size,
                            status="exception",
                            warnings_json=json.dumps(warnings, ensure_ascii=False),
                        )
                        continue
                    page_count = int(document["page_count"])
                    if not page_count:
                        reader = PdfReader(path, strict=False)
                        page_count = len(reader.pages)
                except (OSError, PdfReadError, ValueError) as exc:
                    self.store.update_document(
                        document_id,
                        status="exception",
                        warnings_json=json.dumps([f"PDF ilegivel: {exc}"], ensure_ascii=False),
                    )
                    continue
                if page_count > MAX_PDF_PAGES:
                    warnings.append(f"PDF excede o limite de {MAX_PDF_PAGES} páginas")
                    self.store.update_document(
                        document_id,
                        size_bytes=size,
                        page_count=page_count,
                        status="exception",
                        warnings_json=json.dumps(warnings, ensure_ascii=False),
                    )
                    continue
                if total_pages + page_count > MAX_BATCH_PAGES:
                    warnings.append(f"lote excede o limite de {MAX_BATCH_PAGES} páginas")
                    self.store.update_document(
                        document_id,
                        size_bytes=size,
                        page_count=page_count,
                        status="exception",
                        warnings_json=json.dumps(warnings, ensure_ascii=False),
                    )
                    continue
                self.store.update_document(
                    document_id,
                    size_bytes=size,
                    page_count=page_count,
                )
                total_pages += page_count
            processed_pages = sum(
                len(self.store.pages(cast(str, item["id"]))) for item in documents
            )
            self.store.update_job(
                job_id,
                total_pages=total_pages,
                processed_pages=processed_pages,
                message="Extraindo texto dos PDFs",
            )

            for document in self.store.documents_for_job(job_id):
                if event.is_set():
                    self.store.update_job(
                        job_id, status="paused", message="Lote pausado; pronto para retomar"
                    )
                    return
                if document["status"] == "exception" and not self.store.pages(
                    cast(str, document["id"])
                ):
                    continue
                self._extract_document(job_id, document, event, started)
                if event.is_set():
                    self.store.update_job(
                        job_id, status="paused", message="Lote pausado; pronto para retomar"
                    )
                    return

            self.store.update_job(
                job_id, status="running", message="Separando e classificando questões"
            )
            self._structure_job(job_id, event)
            if event.is_set():
                self.store.update_job(
                    job_id, status="paused", message="Lote pausado; pronto para retomar"
                )
                return
            self.store.update_job(
                job_id,
                status="completed",
                processed_pages=total_pages,
                eta_seconds=0,
                current_file=None,
                message="Processamento concluído; revise as exceções",
            )
        except Exception as exc:
            self.store.update_job(
                job_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                message="O lote falhou sem perder os checkpoints",
            )

    def _extract_document(
        self,
        job_id: str,
        document: dict[str, Any],
        event: threading.Event,
        started: float,
    ) -> None:
        document_id = cast(str, document["id"])
        path = Path(cast(str, document["local_path"]))
        warnings = list(cast(list[str], document["warnings"]))
        self.store.update_job(job_id, current_file=path.name, message=f"Lendo {path.name}")
        try:
            size = path.stat().st_size
            if size > MAX_PDF_BYTES:
                warnings.append(f"PDF excede o limite de {MAX_PDF_BYTES // (1024 * 1024)} MB")
                self.store.update_document(
                    document_id,
                    size_bytes=size,
                    status="exception",
                    warnings_json=json.dumps(warnings, ensure_ascii=False),
                )
                return
            reader = PdfReader(path, strict=False)
            page_count = len(reader.pages)
            if page_count > MAX_PDF_PAGES:
                warnings.append(f"PDF excede o limite de {MAX_PDF_PAGES} páginas")
                self.store.update_document(
                    document_id,
                    size_bytes=size,
                    page_count=page_count,
                    status="exception",
                    warnings_json=json.dumps(warnings, ensure_ascii=False),
                )
                return
            if reader.is_encrypted and not reader.decrypt(""):
                warnings.append("PDF criptografado; extração bloqueada")
                self.store.update_document(
                    document_id,
                    status="exception",
                    needs_ocr=1,
                    warnings_json=json.dumps(warnings, ensure_ascii=False),
                )
                return
        except (OSError, PdfReadError) as exc:
            warnings.append(f"PDF ilegível: {exc}")
            self.store.update_document(
                document_id,
                status="exception",
                warnings_json=json.dumps(warnings, ensure_ascii=False),
            )
            return

        self.store.update_document(
            document_id,
            sha256=_sha256(path),
            size_bytes=size,
            page_count=page_count,
            status="processing",
        )
        for number, page in enumerate(reader.pages, start=1):
            if event.is_set():
                return
            if self.store.page_exists(document_id, number):
                continue
            error: str | None = None
            try:
                text = (page.extract_text() or "").replace("\x00", "").strip()
            except Exception as exc:
                text = ""
                error = f"{type(exc).__name__}: {exc}"
            status = "text" if len(text) >= 20 else "ocr_required"
            self.store.save_page(document_id, number, text, status=status, error=error)
            processed = int(self.store.job(job_id)["processed_pages"]) + 1
            total = max(1, int(self.store.job(job_id)["total_pages"]))
            elapsed = max(0.01, time.monotonic() - started)
            eta = round((total - processed) / (processed / elapsed)) if processed else None
            self.store.update_document(document_id, processed_pages=number)
            self.store.update_job(
                job_id,
                processed_pages=processed,
                eta_seconds=eta,
                message=f"Página {number} de {len(reader.pages)} em {path.name}",
            )

        pages = self.store.pages(document_id)
        ocr_pages = [int(page["page_number"]) for page in pages if page["status"] == "ocr_required"]
        non_empty = sum(int(page["character_count"]) >= 20 for page in pages)
        if ocr_pages:
            preview = ", ".join(str(number) for number in ocr_pages[:12])
            suffix = "…" if len(ocr_pages) > 12 else ""
            warnings.append(f"páginas sem texto, encaminhadas para exceções: {preview}{suffix}")
        mostly_ocr = not pages or non_empty < max(1, (len(pages) + 1) // 2)
        if mostly_ocr:
            warnings.append("documento sem camada textual suficiente; OCR necessário")
        self.store.update_document(
            document_id,
            status="exception" if mostly_ocr else "extracted",
            needs_ocr=1 if ocr_pages else 0,
            warnings_json=json.dumps(list(dict.fromkeys(warnings)), ensure_ascii=False),
        )

    def _structure_job(self, job_id: str, event: threading.Event) -> None:
        documents = self.store.documents_for_job(job_id)
        answer_keys: list[dict[int, Any]] = []
        exam_documents: list[dict[str, Any]] = []
        for document in documents:
            metadata = DesktopImportMetadata.model_validate(document["metadata"])
            if _document_type(cast(str, document["filename"]), metadata) == "answer_key":
                text = "\n".join(
                    str(page["text"]) for page in self.store.pages(cast(str, document["id"]))
                )
                entries = parse_answer_key(text)
                if entries:
                    answer_keys.append(entries)
                continue
            exam_documents.append(document)

        safe_answer_key = (
            answer_keys[0] if len(answer_keys) == 1 and len(exam_documents) == 1 else None
        )
        for document in exam_documents:
            if event.is_set():
                return
            if document["status"] == "exception" and not self.store.pages(
                cast(str, document["id"])
            ):
                continue
            document_id = cast(str, document["id"])
            pages = self.store.pages(document_id)
            questions, warnings = parse_question_pages(pages)
            existing_warnings = list(cast(list[str], document["warnings"]))
            existing_warnings.extend(warnings)
            if not questions:
                existing_warnings.append("nenhuma questão foi separada automaticamente")
                self.store.update_document(
                    document_id,
                    status="exception",
                    warnings_json=json.dumps(
                        list(dict.fromkeys(existing_warnings)), ensure_ascii=False
                    ),
                )
                continue
            metadata = DesktopImportMetadata.model_validate(document["metadata"])
            requests = [
                ClassificationRequest(
                    question_number=question.number,
                    statement=question.statement,
                    alternatives=[item.text for item in question.alternatives],
                )
                for question in questions
            ]
            provider_name = cast(str, self.store.job(job_id)["classifier_provider"])
            try:
                provider = build_classifier(provider_name)
                classified = provider.classify_many(requests, metadata)
            except Exception as exc:
                existing_warnings.append(
                    f"classificação {provider_name} indisponível; usado modo local: {exc}"
                )
                classified = LocalRuleClassifier().classify_many(requests, metadata)
            by_number = {item.question_number: item.classification for item in classified}
            for question in questions:
                classification = by_number.get(question.number, QuestionClassification())
                updated = _apply_classification(question, classification)
                if safe_answer_key is not None and question.number in safe_answer_key:
                    entry = safe_answer_key[question.number]
                    if entry.status == "matched":
                        payload = updated.model_dump(mode="json")
                        payload.update({"answer_status": "matched", "correct_answer": entry.answer})
                        updated = QuestionRecord.model_validate(payload)
                    elif entry.status == "annulled":
                        payload = updated.model_dump(mode="json")
                        payload.update({"answer_status": "annulled", "correct_answer": None})
                        updated = QuestionRecord.model_validate(payload)
                self.store.save_question(document_id, updated, classification)
            self.store.update_document(
                document_id,
                status="processed",
                warnings_json=json.dumps(
                    list(dict.fromkeys(existing_warnings)), ensure_ascii=False
                ),
            )
