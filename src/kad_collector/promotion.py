from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .json_utils import read_json, write_json
from .models import PromotionPackage, QuestionBatch
from .reporting import question_fingerprint
from .validation import verify_approved_batch


@dataclass(frozen=True)
class PromotionDryRun:
    package_id: str
    batch_count: int
    question_count: int
    content_sha256: str
    executed: bool = False


def _content_sha256(batches: list[QuestionBatch]) -> str:
    content = {
        "target": "kad",
        "batches": [batch.model_dump(mode="json") for batch in batches],
    }
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_promotion_package(
    batch_paths: list[Path],
    output_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> tuple[PromotionPackage, Path]:
    if not batch_paths:
        raise ValueError("informe ao menos um lote aprovado")
    batches: list[QuestionBatch] = []
    fingerprints: set[str] = set()

    for batch_path in batch_paths:
        batch = QuestionBatch.model_validate(read_json(batch_path))
        verify_approved_batch(batch)
        if any(item.batch_id == batch.batch_id for item in batches):
            raise ValueError(f"lote repetido no pacote: {batch.batch_id}")
        for question in batch.questions:
            fingerprint = question_fingerprint(question)
            if fingerprint in fingerprints:
                raise ValueError(
                    f"questao duplicada entre lotes aprovados: {question.number}"
                )
            fingerprints.add(fingerprint)
        batches.append(batch)

    digest = _content_sha256(batches)
    package = PromotionPackage(
        package_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"kad-promotion:{digest}")),
        created_at=now or datetime.now(UTC),
        batches=batches,
        content_sha256=digest,
    )
    if output_path is None:
        output_path = Path("data/promotion") / f"{package.package_id}.json"
    write_json(output_path, package.model_dump(mode="json"))
    return package, output_path


def verify_promotion_package(package: PromotionPackage) -> None:
    batch_ids = [batch.batch_id for batch in package.batches]
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError("o pacote possui batch_ids duplicados")
    fingerprints: set[str] = set()
    for batch in package.batches:
        verify_approved_batch(batch)
        for question in batch.questions:
            fingerprint = question_fingerprint(question)
            if fingerprint in fingerprints:
                raise ValueError("o pacote contem questoes duplicadas")
            fingerprints.add(fingerprint)
    expected = _content_sha256(package.batches)
    if package.content_sha256 != expected:
        raise ValueError("o conteudo do pacote nao corresponde ao hash registrado")
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"kad-promotion:{expected}"))
    if package.package_id != expected_id:
        raise ValueError("o identificador do pacote nao corresponde ao conteudo")


def dry_run_promotion(package_path: Path) -> PromotionDryRun:
    package = PromotionPackage.model_validate(read_json(package_path))
    verify_promotion_package(package)
    return PromotionDryRun(
        package_id=package.package_id,
        batch_count=len(package.batches),
        question_count=sum(len(batch.questions) for batch in package.batches),
        content_sha256=package.content_sha256,
    )
