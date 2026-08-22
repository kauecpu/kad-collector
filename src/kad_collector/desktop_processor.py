from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from io import BytesIO
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
from .document_contract import NormalizedDocument
from .document_matching import (
    DocumentEvidence,
    normalize_text,
    structural_v_number,
)
from .models import QuestionRecord
from .semantic_identity import (
    AssociationCandidate,
    DocumentAssociationDecision,
    DocumentSemanticProfile,
    extract_semantic_profile,
)
from .semantic_resolution import select_answer_key


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


def _document_group(
    document: dict[str, Any],
) -> tuple[str | None, int | None, str | None, str | None]:
    metadata = DesktopImportMetadata.model_validate(document["metadata"])
    return (
        normalize_text(metadata.concurso) if metadata.concurso else None,
        metadata.year,
        normalize_text(metadata.role) if metadata.role else None,
        normalize_text(metadata.organization) if metadata.organization else None,
    )


def _turn_from_text(value: str) -> str | None:
    match = re.search(r"\b(MANH[AÃ]|TARDE)\b", value[:20_000], re.IGNORECASE)
    return match.group(1) if match else None


def _canonical_exam_documents(
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    groups: dict[tuple[str | None, int | None, str | None, str | None], list[dict[str, Any]]] = {}
    for document in documents:
        group_key = _document_group(document)
        if not any(value is not None for value in group_key):
            selected.append(document)
            continue
        groups.setdefault(group_key, []).append(document)

    for group_documents in groups.values():
        structural_variants = [
            (number, document)
            for document in group_documents
            if (number := _canonical_variant_number(document)) is not None
        ]
        if len(structural_variants) < 2:
            selected.extend(group_documents)
            continue
        structural_documents = {id(document) for _, document in structural_variants}
        selected.extend(
            document for document in group_documents if id(document) not in structural_documents
        )
        ranked = sorted(
            structural_variants,
            key=lambda item: (item[0], cast(str, item[1]["filename"])),
        )
        selected.append(ranked[0][1])
        skipped.extend(document for _, document in ranked[1:])
    return selected, skipped


def _canonical_variant_number(document: dict[str, Any]) -> int | None:
    metadata = DesktopImportMetadata.model_validate(document["metadata"])
    if metadata.variant and re.fullmatch(r"TIPO[-_ ]*[1-9]\d*", metadata.variant, re.IGNORECASE):
        return None
    return structural_v_number(
        " ".join(
            value
            for value in (
                metadata.variant,
                metadata.document_title,
                cast(str, document["filename"]),
            )
            if value
        )
    )


def _matching_evidence(document: dict[str, Any], text_field: str) -> DocumentEvidence:
    metadata = DesktopImportMetadata.model_validate(document["metadata"])
    return DocumentEvidence(
        title=metadata.document_title or cast(str, document["filename"]),
        content=cast(str, document.get(text_field, "")),
        concurso=metadata.concurso,
        year=metadata.year,
        role=metadata.role,
        organization=metadata.organization,
        variant=metadata.variant,
    )


def _select_answer_key(
    exam: dict[str, Any], answer_keys: list[dict[str, Any]]
) -> dict[str, Any] | None:
    exam_sha = cast(str, exam.get("sha256") or hashlib.sha256(
        cast(str, exam.get("exam_text", "")).encode("utf-8")
    ).hexdigest())
    metadata = DesktopImportMetadata.model_validate(exam["metadata"])
    exam_profile = extract_semantic_profile(
        NormalizedDocument(
            local_path=cast(str, exam["filename"]), sha256=exam_sha,
            size_bytes=int(exam.get("size_bytes", 1)),
            title=metadata.document_title or cast(str, exam["filename"]),
            entry_method="direct_import", metadata=metadata.model_dump(exclude_none=True),
            declared_type="exam",
        ), [(1, cast(str, exam.get("exam_text", "")))],
    )
    candidates: list[AssociationCandidate] = []
    for item in answer_keys:
        item_metadata = DesktopImportMetadata.model_validate(item["metadata"])
        item_sha = cast(str, item.get("sha256") or hashlib.sha256(
            cast(str, item.get("answer_key_text", "")).encode("utf-8")
        ).hexdigest())
        item_profile = extract_semantic_profile(
            NormalizedDocument(
                local_path=cast(str, item["filename"]), sha256=item_sha,
                size_bytes=int(item.get("size_bytes", 1)),
                title=item_metadata.document_title or cast(str, item["filename"]),
                entry_method="direct_import", metadata=item_metadata.model_dump(exclude_none=True),
                declared_type="answer_key",
            ), [(1, cast(str, item.get("answer_key_text", "")))],
        )
        candidates.append(AssociationCandidate(
            version_id=cast(str, item.get("version_id") or item.get("id") or item_sha),
            profile=item_profile,
            predecessor_version_id=cast(
                str | None,
                item.get("predecessor_version_id") or item.get("predecessorVersionId"),
            ),
        ))
    decision = select_answer_key(exam_profile, candidates)
    if decision.selected_version_id is None:
        return None
    return next(
        item for item in answer_keys
        if item.get("sha256") == decision.selected_version_id
        or item.get("version_id") == decision.selected_version_id
        or item.get("id") == decision.selected_version_id
        or hashlib.sha256(cast(str, item.get("answer_key_text", "")).encode("utf-8")).hexdigest()
        == decision.selected_version_id
    )


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

    def _read_validated_snapshot(
        self, document: dict[str, Any], warnings: list[str]
    ) -> bytes | None:
        normalized = cast(NormalizedDocument | None, document["normalized_document"])
        try:
            payload = Path(cast(str, document["local_path"])).read_bytes()
            if normalized is not None:
                normalized.validate_content(payload)
        except (OSError, ValueError) as exc:
            warnings.append(f"integridade local divergente: {exc}")
            self.store.update_document(
                cast(str, document["id"]),
                status="exception",
                warnings_json=json.dumps(warnings, ensure_ascii=False),
            )
            return None
        return payload

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
                document_id = cast(str, document["id"])
                warnings = list(cast(list[str], document["warnings"]))
                payload = self._read_validated_snapshot(document, warnings)
                if payload is None:
                    continue
                try:
                    size = len(payload)
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
                        reader = PdfReader(BytesIO(payload), strict=False)
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

            for document in self.store.documents_for_job(job_id):
                if event.is_set():
                    self.store.update_job(
                        job_id, status="paused", message="Lote pausado; pronto para retomar"
                    )
                    return
                document_id = cast(str, document["id"])
                try:
                    resolution = self.store.resolve_extracted_document(document_id)
                except Exception as exc:
                    warnings = list(cast(list[str], self.store.document(document_id)["warnings"]))
                    warnings.append(f"resolução semântica falhou: {type(exc).__name__}: {exc}")
                    self.store.update_document(
                        document_id,
                        status="exception",
                        warnings_json=json.dumps(warnings, ensure_ascii=False),
                    )
                    continue
                if resolution.outcome == "uncertain":
                    warnings = list(cast(list[str], self.store.document(document_id)["warnings"]))
                    warnings.append(resolution.reason)
                    self.store.update_document(
                        document_id,
                        status="exception",
                        warnings_json=json.dumps(warnings, ensure_ascii=False),
                    )
                elif resolution.outcome == "republication":
                    warnings = list(cast(list[str], self.store.document(document_id)["warnings"]))
                    warnings.append(
                        f"republicação vinculada à versão {resolution.document_version_id}; "
                        "questões não duplicadas"
                    )
                    self.store.update_document(
                        document_id,
                        status="processed",
                        warnings_json=json.dumps(warnings, ensure_ascii=False),
                    )

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
        payload = self._read_validated_snapshot(document, warnings)
        if payload is None:
            return
        try:
            size = len(payload)
            if size > MAX_PDF_BYTES:
                warnings.append(f"PDF excede o limite de {MAX_PDF_BYTES // (1024 * 1024)} MB")
                self.store.update_document(
                    document_id,
                    size_bytes=size,
                    status="exception",
                    warnings_json=json.dumps(warnings, ensure_ascii=False),
                )
                return
            reader = PdfReader(BytesIO(payload), strict=False)
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
            sha256=hashlib.sha256(payload).hexdigest(),
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
        new_answer_key_versions: list[str] = []
        exam_documents: list[dict[str, Any]] = []
        for document in documents:
            if document.get("semantic_resolution") not in {"new_identity", "new_version"}:
                continue
            document_id = cast(str, document["id"])
            semantic_role = self.store.semantic_document_view(document_id)["documentRole"]
            if semantic_role == "answer_key":
                version_id = cast(str | None, document.get("document_version_id"))
                if version_id is not None:
                    new_answer_key_versions.append(version_id)
                continue
            if semantic_role != "exam":
                continue
            exam_text = "\n".join(
                str(page["text"]) for page in self.store.pages(document_id)
            )
            exam_documents.append({**document, "exam_text": exam_text})

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
            if self.store.question_records(document_id):
                self._associate_exam(document)
                self.store.reconcile_question_lineage(document_id)
                continue
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
            structured_questions: list[QuestionRecord] = []
            for question in questions:
                classification = by_number.get(question.number, QuestionClassification())
                updated = _apply_classification(question, classification)
                self.store.save_question(document_id, updated, classification)
                structured_questions.append(updated)
            applied, located = self._associate_exam(document)
            self.store.reconcile_question_lineage(document_id)
            if not applied:
                existing_warnings.append(
                    "gabarito localizado, mas sem respostas reconhecidas para a prova"
                    if located else "nenhum gabarito correspondente foi localizado"
                )
            structured_questions = [
                question for question, _ in self.store.question_records(document_id)
            ]
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

        for answer_key_version_id in dict.fromkeys(new_answer_key_versions):
            if event.is_set():
                return
            self._reconcile_answer_key(answer_key_version_id)

    def _association_for_exam(
        self, exam: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, DocumentAssociationDecision]:
        exam_version_id = cast(str, exam["document_version_id"])
        exam_profile = DocumentSemanticProfile.model_validate_json(
            json.dumps(self.store.semantic_document_view(cast(str, exam["id"]))["profile"])
        )
        candidates = self.store.answer_key_candidates(exam_version_id)
        decision = select_answer_key(
            exam_profile,
            [
                AssociationCandidate(
                    version_id=cast(str, candidate["answer_key_version_id"]),
                    profile=DocumentSemanticProfile.model_validate_json(
                        json.dumps(candidate["profile"])
                    ),
                    predecessor_version_id=cast(
                        str | None, candidate["predecessor_version_id"]
                    ),
                )
                for candidate in candidates
            ],
        )
        if decision.selected_version_id is None:
            return None, decision
        return self.store.answer_key_document(decision.selected_version_id), decision

    def _associate_exam(self, exam: dict[str, Any]) -> tuple[bool, bool]:
        answer_key, decision = self._association_for_exam(exam)
        if answer_key is None:
            return False, False
        return self._apply_answer_key_to_exam(exam, answer_key, decision), True

    def _apply_answer_key_to_exam(
        self,
        exam: dict[str, Any],
        answer_key: dict[str, Any],
        decision: DocumentAssociationDecision,
    ) -> bool:
        exam_version_id = cast(str, exam["document_version_id"])
        answer_key_version_id = cast(str, answer_key["version_id"])
        if decision.selected_version_id != answer_key_version_id:
            return False
        metadata = DesktopImportMetadata.model_validate(exam["metadata"])
        variant_number = _variant_number(exam)
        variant = metadata.variant or (
            f"Tipo {variant_number}" if variant_number is not None else None
        )
        entries = parse_answer_key(
            cast(str, answer_key["answer_key_text"]),
            variant=variant,
            role=metadata.role,
            turn=_turn_from_text(cast(str, exam.get("exam_text", ""))),
        )
        updates = {
            number: (
                "annulled" if entry.annulled else "matched",
                None if entry.annulled else entry.answer,
            )
            for number, entry in entries.items()
        }
        return self.store.apply_answer_key_updates(
            cast(str, exam["id"]), exam_version_id, answer_key_version_id, decision, updates
        )

    def _reconcile_answer_key(self, answer_key_version_id: str) -> int:
        applied = 0
        for affected in self.store.exams_affected_by_answer_key(answer_key_version_id):
            document_id = cast(str, affected["id"])
            exam = self.store.document(document_id)
            exam["exam_text"] = "\n".join(
                str(page["text"]) for page in self.store.pages(document_id)
            )
            changed, _ = self._associate_exam(exam)
            self.store.reconcile_question_lineage(document_id)
            if changed:
                applied += 1
        return applied
