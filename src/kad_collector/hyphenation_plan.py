"""Explicit, source-bound proposals for breaks the automatic repair cannot decide.

This changes extraction text, not PDF files, answers or editorial approvals.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import ExtractedDocument, StrictModel
from .pdf_text import _LINE_HYPHEN


class HyphenationJoin(StrictModel):
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_number: int = Field(ge=1)
    before: str
    after: str
    reason: str = Field(min_length=10)

    @model_validator(mode="after")
    def only_join_a_physical_break(self) -> HyphenationJoin:
        match = _LINE_HYPHEN.fullmatch(self.before)
        if match is None or self.after != match["left"] + match["right"]:
            raise ValueError("a proposta só pode remover hífen e quebra de linha entre letras")
        if not match["right"][0].islower():
            raise ValueError("continuação deve começar com letra minúscula")
        return self


class HyphenationPlan(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    prepared_by: str = Field(min_length=2)
    status: Literal["proposed"] = "proposed"
    joins: list[HyphenationJoin] = Field(min_length=1)

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


def apply_hyphenation_plan(
    documents: list[ExtractedDocument], plan: HyphenationPlan
) -> list[ExtractedDocument]:
    """Fail closed for stale/ambiguous proposals, including cached extractions."""
    result = [document.model_copy(deep=True) for document in documents]
    verified: set[str] = set()
    for join in plan.joins:
        matches = [doc for doc in result if doc.document.sha256 == join.document_sha256]
        if len(matches) != 1:
            raise ValueError("documento da proposta ausente ou duplicado")
        document = matches[0]
        if join.document_sha256 not in verified:
            with Path(document.document.local_path).open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != join.document_sha256:
                raise ValueError("PDF da proposta mudou: SHA-256 divergente")
            verified.add(join.document_sha256)
        pages = [page for page in document.pages if page.number == join.page_number]
        if len(pages) != 1:
            raise ValueError("página da proposta ausente ou duplicada")
        page = pages[0]
        # Do not match a substring of another word or a larger compound.
        pattern = re.compile(r"(?<![\w-])" + re.escape(join.before) + r"(?![\w-])")
        if len(list(pattern.finditer(page.text))) != 1:
            raise ValueError("trecho da proposta ausente ou ambíguo na página")
        page.text = pattern.sub(join.after, page.text)
        page.character_count = len(page.text)
    for document in result:
        if document.document.sha256 in verified:
            document.text = "\n\n".join(
                f"--- Pagina {page.number} ---\n{page.text}"
                for page in document.pages if page.text
            )
    return result
