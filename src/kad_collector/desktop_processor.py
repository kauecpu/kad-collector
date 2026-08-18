from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader
from pypdf.errors import DependencyError, PdfReadError

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


def _variant_number(document: dict[str, Any]) -> int | None:
    metadata = DesktopImportMetadata.model_validate(document["metadata"])
    candidate = " ".join(
        value
        for value in (
            metadata.variant,
            metadata.document_title,
            metadata.source_url,
            cast(str, document["filename"]),
        )
        if value
    )
    match = re.search(
        r"(?<![A-Z0-9])(?:V|TIPO|PROVA)[-_ ]*(?P<number>[1-9]\d*)(?!\d)",
        candidate,
        re.IGNORECASE,
    )
    return int(match.group("number")) if match else None


def _document_group(document: dict[str, Any]) -> tuple[str | None, str | None]:
    metadata = DesktopImportMetadata.model_validate(document["metadata"])
    return metadata.provider, metadata.concurso


def _years_in(value: str) -> set[int]:
    return {int(item) for item in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", value)}


def _periods_in(value: str) -> set[tuple[int, int]]:
    periods = {
        (int(year), int(term))
        for year, term in re.findall(r"(?<!\d)((?:19|20)\d{2})\s*/\s*([12])(?!\d)", value)
    }
    for month, year in re.findall(
        r"(?<!\d)\d{1,2}/(\d{1,2})/((?:19|20)\d{2})(?!\d)", value
    ):
        periods.add((int(year), 1 if int(month) <= 6 else 2))
    return periods


def _turn_from_text(value: str) -> str | None:
    match = re.search(r"\b(MANH[AÃ]|TARDE)\b", value[:20_000], re.IGNORECASE)
    return match.group(1) if match else None


def _canonical_exam_documents(
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    fuvest_groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for document in documents:
        metadata = DesktopImportMetadata.model_validate(document["metadata"])
        if metadata.provider != "fuvest_vestibular":
            selected.append(document)
            continue
        fuvest_groups.setdefault(_document_group(document), []).append(document)

    for group in fuvest_groups.values():
        ranked = sorted(
            group,
            key=lambda item: (
                _variant_number(item) is None,
                _variant_number(item) or 10_000,
                cast(str, item["filename"]),
            ),
        )
        selected.append(ranked[0])
        skipped.extend(ranked[1:])
    return selected, skipped


def _select_answer_key(
    exam: dict[str, Any], answer_keys: list[dict[str, Any]]
) -> dict[str, Any] | None:
    same_group = [item for item in answer_keys if _document_group(item) == _document_group(exam)]
    if len(same_group) == 1:
        return same_group[0]
    candidates = same_group or answer_keys
    if not candidates:
        return None
    exam_metadata = DesktopImportMetadata.model_validate(exam["metadata"])
    role_words = set(re.findall(r"[a-z0-9]+", (exam_metadata.role or "").casefold()))
    exam_text = cast(str, exam.get("exam_text", ""))[:20_000]
    exam_years = _years_in(
        " ".join(
            (
                exam_metadata.document_title or "",
                exam_metadata.source_url or "",
                exam_text,
            )
        )
    )
    exam_periods = _periods_in(exam_text)

    def score(item: dict[str, Any]) -> tuple[int, int, int, int]:
        metadata = DesktopImportMetadata.model_validate(item["metadata"])
        haystack = " ".join(
            (
                metadata.document_title or "",
                metadata.source_url or "",
                cast(str, item.get("answer_key_text", ""))[:20_000],
            )
        ).casefold()
        role_score = sum(word in haystack for word in role_words if len(word) > 2)
        definitive = int("definitiv" in haystack)
        candidate_years = _years_in(haystack)
        candidate_periods = _periods_in(haystack)
        period_score = int(bool(exam_periods & candidate_periods))
        year_score = int(bool(exam_years & candidate_years))
        if exam_metadata.year is not None and metadata.year == exam_metadata.year:
            year_score = 1
        return period_score, year_score, role_score, definitive

    selected = max(candidates, key=score)
    selected_score = score(selected)
    if selected_score[0] or selected_score[1] or selected_score[2] or len(candidates) == 1:
        return selected
    definitive = [item for item in candidates if score(item)[3]]
    return definitive[0] if len(definitive) == 1 else None


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
    def __init__(self, store: DesktopStore, *, max_workers: int = 2) -> None:
        self.store = store
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="kad-processor"
        )
        self._futures: dict[str, Future[None]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start(self, job_id: str) -> None:
        with self._lock:
            existing = self._futures.get(job_id)
            if existing is not None and not existing.done():
                return
            event = threading.Event()
            self._cancel_events[job_id] = event
            self._futures[job_id] = self._executor.submit(self.run, job_id, event)

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
                except (OSError, PdfReadError, DependencyError, ValueError) as exc:
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
        except (OSError, PdfReadError, DependencyError, ValueError) as exc:
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
        answer_keys: list[dict[str, Any]] = []
        exam_documents: list[dict[str, Any]] = []
        for document in documents:
            metadata = DesktopImportMetadata.model_validate(document["metadata"])
            if _document_type(cast(str, document["filename"]), metadata) == "answer_key":
                text = "\n".join(
                    str(page["text"]) for page in self.store.pages(cast(str, document["id"]))
                )
                if text.strip():
                    answer_keys.append({**document, "answer_key_text": text})
                continue
            exam_text = "\n".join(
                str(page["text"]) for page in self.store.pages(cast(str, document["id"]))
            )
            exam_documents.append({**document, "exam_text": exam_text})

        cached_hashes = {
            cast(str, item["sha256"])
            for item in answer_keys
            if item.get("sha256") is not None
        }
        groups = {_document_group(document) for document in exam_documents}
        for group_provider, concurso in groups:
            for cached in self.store.cached_answer_keys(
                provider=group_provider,
                concurso=concurso,
                exclude_job_id=job_id,
            ):
                digest = cast(str | None, cached.get("sha256"))
                if digest is not None and digest in cached_hashes:
                    continue
                answer_keys.append(cached)
                if digest is not None:
                    cached_hashes.add(digest)

        canonical_exams, skipped_exams = _canonical_exam_documents(exam_documents)
        for document in skipped_exams:
            metadata = DesktopImportMetadata.model_validate(document["metadata"])
            warnings = list(cast(list[str], document["warnings"]))
            warnings.append(
                f"versão {metadata.variant or 'alternativa'} preservada como origem; "
                "questões não duplicadas porque a menor versão do caderno é a canônica"
            )
            self.store.update_document(
                cast(str, document["id"]),
                status="processed",
                warnings_json=json.dumps(list(dict.fromkeys(warnings)), ensure_ascii=False),
            )

        for document in canonical_exams:
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
            answer_key = _select_answer_key(document, answer_keys)
            variant_number = _variant_number(document)
            variant = metadata.variant or (
                f"Tipo {variant_number}" if variant_number is not None else None
            )
            answer_entries = (
                parse_answer_key(
                    cast(str, answer_key["answer_key_text"]),
                    variant=variant,
                    role=metadata.role,
                    turn=_turn_from_text(cast(str, document.get("exam_text", ""))),
                )
                if answer_key is not None
                else {}
            )
            if answer_key is None and any(
                question.answer_status == "missing" for question in questions
            ):
                existing_warnings.append("nenhum gabarito correspondente foi localizado")
            elif not answer_entries:
                existing_warnings.append(
                    "gabarito localizado, mas sem respostas reconhecidas para "
                    f"{variant or 'a prova'}"
                )
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
            structured_questions: list[QuestionRecord] = []
            for question in questions:
                classification = by_number.get(question.number, QuestionClassification())
                updated = _apply_classification(question, classification)
                entry = answer_entries.get(question.number)
                if entry is not None:
                    payload = updated.model_dump(mode="json")
                    payload.update(
                        {
                            "answer_status": "annulled" if entry.annulled else "matched",
                            "correct_answer": None if entry.annulled else entry.answer,
                        }
                    )
                    updated = QuestionRecord.model_validate(payload)
                self.store.save_question(document_id, updated, classification)
                structured_questions.append(updated)
            missing_answers = sum(
                question.answer_status == "missing" for question in structured_questions
            )
            if missing_answers:
                existing_warnings.append(
                    f"{missing_answers} de {len(questions)} questões ficaram sem resposta oficial"
                )
            self.store.update_document(
                document_id,
                status="processed",
                warnings_json=json.dumps(
                    list(dict.fromkeys(existing_warnings)), ensure_ascii=False
                ),
            )
