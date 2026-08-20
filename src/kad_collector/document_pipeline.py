from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from .desktop_limits import MAX_BATCH_PDFS, validate_pdf_batch
from .desktop_models import ClassifierProviderName, DesktopImportMetadata
from .desktop_store import DesktopStore
from .document_contract import NormalizedDocument, normalize_local_document


class InterpretationRunner(Protocol):
    def start(self, job_id: str) -> None: ...


def _editorial_metadata(document: NormalizedDocument) -> dict[str, str | int]:
    values = dict(document.metadata)
    if document.source_id is not None:
        values.setdefault("provider", document.source_id)
    if "board" not in values and "banca" in values:
        values["board"] = values["banca"]
    if "year" not in values and "ano" in values:
        values["year"] = values["ano"]
    if "role" not in values and "cargo" in values:
        values["role"] = values["cargo"]
    if "organization" not in values and "orgao" in values:
        values["organization"] = values["orgao"]
    return {key: value for key, value in values.items() if value is not None}


def _batch_key(document: NormalizedDocument) -> tuple[object, ...]:
    metadata = _editorial_metadata(document)
    return (
        metadata.get("provider"),
        metadata.get("concurso"),
        metadata.get("year"),
        metadata.get("role"),
    )


def processing_batches(documents: Iterable[NormalizedDocument]) -> list[list[NormalizedDocument]]:
    """Plan neutral processing batches without selecting an answer key."""
    all_documents = list(documents)
    answer_keys = [item for item in all_documents if item.declared_type == "answer_key"]
    candidates = [item for item in all_documents if item.declared_type != "answer_key"]
    if not candidates:
        return [
            answer_keys[start : start + MAX_BATCH_PDFS]
            for start in range(0, len(answer_keys), MAX_BATCH_PDFS)
        ]
    if len(answer_keys) >= MAX_BATCH_PDFS:
        raise ValueError("gabaritos candidatos excedem o limite de PDFs por lote")

    groups: dict[tuple[object, ...], list[NormalizedDocument]] = {}
    for document in candidates:
        groups.setdefault(_batch_key(document), []).append(document)

    capacity = MAX_BATCH_PDFS - len(answer_keys)
    batches: list[list[NormalizedDocument]] = []
    for group in groups.values():
        for start in range(0, len(group), capacity):
            batches.append([*group[start : start + capacity], *answer_keys])
    return batches


class DocumentPipeline:
    def __init__(self, store: DesktopStore, runner: InterpretationRunner) -> None:
        self.store = store
        self.runner = runner

    def submit(
        self,
        documents: list[NormalizedDocument],
        classifier_provider: ClassifierProviderName,
    ) -> list[str]:
        job_ids: list[str] = []
        for batch in processing_batches(documents):
            job_id = self.store.create_interpretation_job(batch, classifier_provider)
            self.runner.start(job_id)
            job_ids.append(job_id)
        return job_ids

    def import_paths(
        self,
        paths: list[Path],
        metadata: DesktopImportMetadata,
        classifier_provider: ClassifierProviderName,
    ) -> list[str]:
        documents: list[NormalizedDocument] = []
        for path in validate_pdf_batch(paths):
            document_metadata = metadata.model_copy(deep=True)
            document = normalize_local_document(path).model_copy(
                update={
                    "metadata": document_metadata.model_dump(
                        mode="json", exclude_none=True, exclude_defaults=True
                    )
                }
            )
            documents.append(document)
        return self.submit(documents, classifier_provider)
