from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from .models import DocumentRecord, StrictModel

DocumentEntryMethod = Literal["automated_collection", "direct_import", "reprocessing"]
DeclaredDocumentType = Literal["auto", "exam", "answer_key", "other"]


class NormalizedDocument(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1)
    declared_type: DeclaredDocumentType = "auto"
    title: str = Field(min_length=1)
    original_url: str | None = None
    resolved_url: str | None = None
    source_page_url: str | None = None
    entry_method: DocumentEntryMethod
    metadata: dict[str, str | int] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    external_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    content_type: str | None = None
    acquired_at: datetime | None = None

    def validate_content(self, payload: bytes) -> None:
        actual_size = len(payload)
        if actual_size < 1:
            raise ValueError("arquivo local vazio")
        if actual_size != self.size_bytes:
            raise ValueError(
                f"tamanho local divergente: esperado {self.size_bytes}, encontrado {actual_size}"
            )
        if hashlib.sha256(payload).hexdigest() != self.sha256:
            raise ValueError("sha256 local divergente")

    def validate_local_file(self) -> None:
        path = Path(self.local_path)
        if not path.exists():
            raise ValueError(f"arquivo local nao existe: {self.local_path}")
        if not path.is_file():
            raise ValueError(f"caminho local nao e arquivo: {self.local_path}")

        self.validate_content(path.read_bytes())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_local_document(local_path: str | Path) -> NormalizedDocument:
    path = Path(local_path).resolve()
    if not path.exists():
        raise ValueError(f"arquivo local nao existe: {path}")
    if not path.is_file():
        raise ValueError(f"caminho local nao e arquivo: {path}")

    size_bytes = path.stat().st_size
    if size_bytes < 1:
        raise ValueError("arquivo local vazio")
    return NormalizedDocument(
        local_path=str(path),
        sha256=_sha256_file(path),
        size_bytes=size_bytes,
        title=path.name,
        entry_method="direct_import",
    )


def normalize_collected_document(
    record: DocumentRecord, *, source_page_url: str | None = None
) -> NormalizedDocument:
    evidence = [value.strip() for value in (record.authorization_basis, record.terms_url or "")]
    return NormalizedDocument(
        local_path=record.local_path,
        sha256=record.sha256,
        size_bytes=record.size_bytes,
        declared_type=record.document_type,
        title=record.title,
        original_url=record.original_url,
        resolved_url=record.resolved_url,
        source_page_url=source_page_url,
        entry_method="automated_collection",
        metadata=dict(record.metadata),
        evidence=[value for value in evidence if value],
        external_id=None,
        source_id=record.source_id,
        source_name=record.source_name,
        content_type=record.content_type,
        acquired_at=record.downloaded_at,
    )


def as_reprocessing_document(document: NormalizedDocument) -> NormalizedDocument:
    return document.model_copy(update={"entry_method": "reprocessing"})
