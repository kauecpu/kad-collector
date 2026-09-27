"""Isolated PF 2021 taxonomy pilot. Produces review evidence, never approvals."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import Field

from .editorial_taxonomy import EditorialTaxonomy, normalize_taxonomy_text
from .models import StrictModel
from .structured_questions import StructuredQuestion, StructuredQuestionPackage


class PilotScope(StrictModel):
    role: str
    block: str | None
    allowed_path_ids: list[str] = Field(default_factory=list)


class PilotSpec(StrictModel):
    catalog: dict[str, Any]
    scopes: list[PilotScope]
    program_sha256: str = ""
    reviewed_by: str = ""
    reviewed_digest: str = ""

    def digest(self) -> str:
        return _digest(self.model_dump(exclude={"reviewed_by", "reviewed_digest"}))


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()


def _pf21(question: StructuredQuestion) -> bool:
    return (question.year == 2021
            and normalize_taxonomy_text(question.organization) == "policia federal"
            and normalize_taxonomy_text(question.board) in {"cebraspe", "cespe", "cespe unb"})


def _sample(package: StructuredQuestionPackage) -> list[StructuredQuestion]:
    groups: dict[tuple[str, str], list[StructuredQuestion]] = defaultdict(list)
    unique = {q.stable_id: q for q in package.accepted if _pf21(q)}
    for question in sorted(unique.values(), key=lambda q: q.stable_id):
        groups[(question.role, question.block or "")].append(question)
    # Round robin prevents a large cargo/block from consuming the entire pilot.
    result: list[StructuredQuestion] = []
    while len(result) < 100 and any(groups.values()):
        for key in sorted(groups):
            if groups[key] and len(result) < 100:
                result.append(groups[key].pop(0))
    return result


def draft_spec(package: StructuredQuestionPackage, program: bytes = b"") -> PilotSpec:
    scopes = sorted({(q.role, q.block or "") for q in package.accepted if _pf21(q)})
    return PilotSpec(
        catalog={"id": "cebraspe-pf21-pilot", "version": "1.0.0",
                 "sources": [], "disciplines": []},
        scopes=[PilotScope(role=role, block=block or None) for role, block in scopes],
        program_sha256=hashlib.sha256(program).hexdigest() if program else "",
    )


def run_pilot(
    package: StructuredQuestionPackage, spec: PilotSpec, *, program: bytes = b"",
    reviews: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    if not program or hashlib.sha256(program).hexdigest() != spec.program_sha256:
        issues.append("official_program_missing_or_changed")
    if not spec.reviewed_by.strip() or spec.reviewed_digest != spec.digest():
        issues.append("catalog_requires_human_confirmation")
    taxonomy: EditorialTaxonomy | None = None
    try:
        taxonomy = EditorialTaxonomy(spec.catalog)
    except (ValueError, KeyError, TypeError):
        issues.append("invalid_or_incomplete_catalog")
    paths = {p.path_id: p for p in taxonomy.candidate_paths()} if taxonomy else {}
    scopes: dict[tuple[str, str | None], set[str]] = {}
    for scope in spec.scopes:
        key = (scope.role, scope.block)
        if key in scopes:
            issues.append("duplicate_scope")
        scopes[key] = set(scope.allowed_path_ids)
        if not scope.allowed_path_ids or any(path not in paths for path in scope.allowed_path_ids):
            issues.append("scope_has_missing_or_unknown_paths")
    if not taxonomy or not taxonomy.sources:
        issues.append("official_program_source_not_registered")
    rows: list[dict[str, Any]] = []
    for question in _sample(package):
        allowed = scopes.get((question.role, question.block), set())
        candidate_id: str | None = None
        if not issues and allowed and taxonomy is not None:
            candidates = {
                match.path.path_id
                for discipline in {paths[path].discipline for path in allowed}
                if (match := taxonomy.semantic_match(
                    question.statement + "\n" + (question.supporting_text or ""),
                    discipline=discipline,
                )) is not None and match.path.path_id in allowed
            }
            if len(candidates) == 1:
                candidate_id = next(iter(candidates))
        rows.append({
            "stable_id": question.stable_id, "role": question.role, "block": question.block,
            "number": question.original_number, "statement": question.statement,
            "supporting_text": question.supporting_text,
            "question_sha256": _digest(question.model_dump(mode="json")),
            "allowed_path_ids": sorted(allowed), "suggested_path_id": candidate_id,
            "status": ("blocked_catalog" if issues else "scope_missing" if not allowed
                       else "needs_human_review" if candidate_id else "unclassified"),
        })
    submitted: dict[str, dict[str, str]] = {}
    for review in reviews or []:
        if review.get("stable_id") in submitted:
            issues.append("duplicate_review")
        submitted[review.get("stable_id", "")] = review
    correct = reviewed = 0
    for row in rows:
        review = submitted.get(row["stable_id"], {})
        if (review.get("reviewer", "").strip()
                and review.get("spec_sha256") == spec.digest()
                and review.get("question_sha256") == row["question_sha256"]
                and review.get("path_id") in row["allowed_path_ids"]):
            reviewed += 1
            correct += review["path_id"] == row["suggested_path_id"]
    return {
        "scope": "PF 2021; accepted only; isolated pilot", "spec_sha256": spec.digest(),
        "issues": sorted(set(issues)), "sample_size": len(rows), "reviewed": reviewed,
        "correct_full_paths": correct, "required_correct": 95,
        "ready_for_next_pilot": not issues and len(rows) == reviewed == 100 and correct >= 95,
        "publication_authorized": False, "qwen_calls": 0, "sample": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--program", type=Path, help="Local edital text, never downloaded")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args()
    package = StructuredQuestionPackage.model_validate_json(args.package.read_text("utf-8"))
    program = args.program.read_bytes() if args.program else b""
    if args.draft:
        print(draft_spec(package, program).model_dump_json(indent=2))
        return 0
    if not args.spec:
        parser.error("--spec is required unless --draft")
    spec = PilotSpec.model_validate_json(args.spec.read_text("utf-8"))
    reviews = json.loads(args.reviews.read_text("utf-8")) if args.reviews else []
    report = run_pilot(package, spec, program=program, reviews=reviews)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_for_next_pilot"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
