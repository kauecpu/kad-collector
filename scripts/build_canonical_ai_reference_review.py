from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from kad_collector.canonical_ai_input import (
    CanonicalAIInputError,
    find_canonical_ai_artifacts,
    sanitize_canonical_ai_content,
)
from kad_collector.editorial_taxonomy import EditorialTaxonomy
from kad_collector.json_utils import read_json, write_json

# These decisions were made by reviewing the question content against the 2.0.1
# taxonomy. Indices refer only to the frozen v1 benchmark bundle; generated
# artifacts persist stable question identifiers rather than these positions.
STRUCTURAL_ONLY_INDICES = frozenset(
    {
        6,
        7,
        14,
        34,
        39,
        40,
        52,
        55,
        60,
        72,
        77,
        95,
        99,
        134,
        138,
        164,
        171,
        186,
        197,
    }
)
AMBIGUOUS_INDICES = frozenset({1, 38})
CORRECTED_SUBJECTS = {
    24: "IPI",
    79: "Processo Administrativo Fiscal",
    105: "Processo Administrativo Fiscal",
    130: "Processo Administrativo Fiscal",
    146: "Processo Administrativo Fiscal",
    160: "SPED",
    179: "Processo Administrativo Fiscal",
}


def _expected_for_subject(
    taxonomy: EditorialTaxonomy, subject: str, *, level: str
) -> dict[str, str]:
    paths = [path for path in taxonomy.candidate_paths() if path.subject == subject]
    if len(paths) != 1:
        raise ValueError(f"assunto não identifica um caminho único: {subject}")
    path = paths[0]
    return {
        "discipline": path.discipline,
        "matter": str(path.matter),
        "subject": str(path.subject),
        "level": level,
    }


def build_review(source_bundle: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = read_json(source_bundle)
    items = source.get("items")
    if not isinstance(items, list) or len(items) != 200:
        raise ValueError("a revisão exige o bundle v1 congelado com 200 questões")
    taxonomy = EditorialTaxonomy.load_default()
    records: list[dict[str, Any]] = []
    artifact_counts: Counter[str] = Counter()
    cleaned_questions = 0
    residue_questions = 0
    sanitization_rejections = 0
    official_headings = taxonomy.official_headings(catalog_ids=("fgv-rfb22",))
    for index, item in enumerate(items):
        question = item["request"]["question"]
        sanitization_failed = False
        try:
            sanitized = sanitize_canonical_ai_content(
                question["statement"],
                tuple(question["alternatives"]),
                official_headings=official_headings,
            )
        except CanonicalAIInputError:
            sanitization_failed = True
            sanitization_rejections += 1
        else:
            if sanitized.removed_artifacts:
                cleaned_questions += 1
                artifact_counts.update(sanitized.removed_artifacts)
            residue_questions += bool(
                find_canonical_ai_artifacts(
                    sanitized.statement,
                    sanitized.alternatives,
                    official_headings=official_headings,
                )
            )
        structural = dict(item["expected"])
        record: dict[str, Any] = {
            "sourceQuestionId": item["sourceQuestionId"],
            "contentFingerprint": item["contentFingerprint"],
            "structuralExpected": structural,
        }
        if sanitization_failed:
            record.update(
                {
                    "status": "rejected_reference",
                    "reasonCode": "sanitization_removed_required_content",
                }
            )
        elif index in STRUCTURAL_ONLY_INDICES:
            record.update(
                {
                    "status": "structural_only_reference",
                    "reasonCode": "taxonomy_lacks_specific_semantic_path",
                }
            )
        elif index in AMBIGUOUS_INDICES:
            record.update(
                {
                    "status": "ambiguous_reference",
                    "reasonCode": "multiple_plausible_taxonomy_paths",
                }
            )
        else:
            corrected_subject = CORRECTED_SUBJECTS.get(index)
            reviewed = (
                _expected_for_subject(taxonomy, corrected_subject, level=structural["level"])
                if corrected_subject is not None
                else structural
            )
            record.update(
                {
                    "status": "agent_reviewed_reference",
                    "reviewedExpected": reviewed,
                    "reasonCode": (
                        "content_supports_different_taxonomy_path"
                        if corrected_subject is not None
                        else "content_confirms_structural_reference"
                    ),
                }
            )
        records.append(record)

    statuses = Counter(str(record["status"]) for record in records)
    review = {
        "schemaVersion": 2,
        "kind": "canonical-ai-reference-review",
        "taxonomyVersion": taxonomy.version,
        "sourceBenchmarkId": source["manifest"]["benchmarkId"],
        "reviewMethod": "agent_content_taxonomy_review",
        "humanReview": False,
        "records": records,
    }
    audit = {
        "schemaVersion": 2,
        "kind": "canonical-ai-reference-review-audit",
        "sourceBenchmarkId": source["manifest"]["benchmarkId"],
        "taxonomyVersion": taxonomy.version,
        "examined": len(records),
        "statuses": dict(sorted(statuses.items())),
        "usableReferences": statuses["agent_reviewed_reference"],
        "requiredReferences": 200,
        "readyForPreparation": statuses["agent_reviewed_reference"] >= 200,
        "networkCallsPerformed": 0,
        "modelInferencesPerformed": 0,
        "humanReview": False,
        "sanitization": {
            "examined": len(items),
            "cleaned": cleaned_questions,
            "removedArtifacts": dict(sorted(artifact_counts.items())),
            "residuesAfterCleaning": residue_questions,
            "rejected": sanitization_rejections,
        },
        "blockedReason": (
            None
            if statuses["agent_reviewed_reference"] >= 200
            else "candidate_database_unavailable_for_replacement_references"
        ),
    }
    return review, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    review, audit = build_review(args.source_bundle)
    write_json(args.review, review)
    write_json(args.audit, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
