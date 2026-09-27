"""Offline publication evidence gate; never imports or publishes to KAD."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from .editorial_export import EditorialImportRecordV2

CONTRACT_REFERENCE = "KAD:20260828220803_controlled_question_publication"


def validate_package(records: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    ids: set[str] = set()
    sources: set[tuple[str, str]] = set()
    fingerprints: set[str] = set()
    if not records or len(records) > 5000:
        issues.append("package: expected 1..5000 records")
    for line, raw in enumerate(records, 1):
        prefix = f"line {line}"
        try:
            record = EditorialImportRecordV2.model_validate(raw)
        except ValidationError:
            issues.append(f"{prefix}: invalid v2 record")
            continue
        data, source = record.data, record.source
        if len(data.statement.strip()) < 10:
            issues.append(f"{prefix}: statement incomplete")
        if any(not value.strip() for value in (
            data.discipline, data.subject, data.topic, data.level,
            data.board, data.role, data.institution, data.concurso,
        )):
            issues.append(f"{prefix}: taxonomy/identity incomplete")
        url = urlsplit(source.url)
        if (not source.provider.strip() or not source.external_id.strip()
                or url.scheme != "https" or not url.hostname or url.username or url.password):
            issues.append(f"{prefix}: source incomplete")
        alternatives = data.alternatives
        letters = [item.id for item in alternatives]
        if (letters != list("ABCDE"[:len(letters)]) or data.correct not in letters
                or any(not item.text.strip() for item in alternatives)):
            issues.append(f"{prefix}: invalid alternatives/answer")
        canonical = data.canonical_question
        if canonical is None:
            issues.append(f"{prefix}: official answer evidence missing")
        else:
            evidence = canonical.provenances
            if (canonical.occurrence_count != len(evidence)
                    or len({p.occurrence_id for p in evidence}) != len(evidence)):
                issues.append(f"{prefix}: inconsistent occurrence count")
            if any(not (p.answer_key_link_id or "").strip()
                   or p.answer_status != "matched" or p.answer != data.correct for p in evidence):
                issues.append(f"{prefix}: official answer evidence incompatible")
        source_id = (source.provider, source.external_id)
        if data.id in ids or source_id in sources or source.fingerprint in fingerprints:
            issues.append(f"{prefix}: duplicate identity/source/fingerprint")
        ids.add(data.id)
        sources.add(source_id)
        fingerprints.add(source.fingerprint)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        records = [json.loads(line) for line in args.package.read_text("utf-8").splitlines()
                   if line.strip()]
        issues = validate_package(records)
    except (OSError, ValueError) as exc:
        issues = [str(exc)]
    print(json.dumps({"contract": CONTRACT_REFERENCE, "valid": not issues, "issues": issues}))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
