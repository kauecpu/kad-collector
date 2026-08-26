from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from kad_collector.canonical_ai_benchmark import load_official_structure_references
from kad_collector.editorial_taxonomy import EditorialTaxonomy
from kad_collector.json_utils import read_json, write_json

PAF_EXPECTED = {
    "discipline": "Direito Tributário",
    "matter": "Contencioso Tributário",
    "subject": "Processo Administrativo Fiscal",
    "level": "Superior",
}
FRONTIER_EXPECTED = {
    "discipline": "Legislação Aduaneira",
    "matter": "Controle de Cargas",
    "subject": "Gestão de Fronteiras",
    "level": "Superior",
}
ANALYSIS_EXPECTED = {
    "discipline": "Fluência em Dados",
    "matter": "Ciência de Dados",
    "subject": "Análise de Dados",
    "level": "Superior",
}
DISPATCH_EXPECTED = {
    "discipline": "Legislação Aduaneira",
    "matter": "Despacho Aduaneiro",
    "subject": "Despacho de Importação e Exportação",
    "level": "Superior",
}

REPLACEMENTS: dict[str, dict[str, str]] = {
    # Processo Administrativo Fiscal
    "05e77eb69cf7986a24eaa43d1388c98562851c100aafe639d0a488412018be78": PAF_EXPECTED,
    "4541832c258d71efded13ca066e6d0960c7022b43f89b52377eb684bf919fe72": PAF_EXPECTED,
    "da410351a368309012acc36db3949f71534a86c97feab491f43059b5e5287552": PAF_EXPECTED,
    "a37131dcf720b6442b72cb2dc796567a178cea7f16d761ed2041dff78d5034bf": PAF_EXPECTED,
    "21fb209e386fc8b6a6e476e92ea3f9e2e6ea43e2524a51aa37f45df2095e84d6": PAF_EXPECTED,
    "62612f5f624d085c10fc151887d8077fc04de6f84f8fb7b23749d42c858b5ad5": PAF_EXPECTED,
    "e4d2fb6c63f8abed6f23fa65e7c948902f5d8537bd09f3b7cbb56c51a775143d": PAF_EXPECTED,
    "a27ffcc13fa376dfe239ebd048ee0b29c3c54eb6a4a44aabc0a5433f2bb7f1e7": PAF_EXPECTED,
    "cc4318abaefc69424dcb5c023e8b25e5b5f8f99bb0476ac3e82d9276aef6d488": PAF_EXPECTED,
    "20cd242eb1e66ed994de4de3bd7dd73e045e8c735a26ff26b5ec55fb9701faf5": PAF_EXPECTED,
    # Controle de Cargas / Gestão de Fronteiras
    "6b13f23a88fbd3c16913954e7a1e3fecfc1843245dffc86c277b54022d595d1e": FRONTIER_EXPECTED,
    "4be4a7a450a554f12b288832b2d6c6b3e87681e580ef1f1b95966bb34f27519f": FRONTIER_EXPECTED,
    "d69e13459b75498de7907a5016e6da745434cab59b804641d4da6de69fdee47d": FRONTIER_EXPECTED,
    "e836edd4b0f89f01d1045b9dd459ffefb0cfb37fd0a0ef285cae1324ded5ad72": FRONTIER_EXPECTED,
    "e6910993f4623af883e0040c8f2a2f735369ba3f9430cd553f7eca427ed2cf8a": FRONTIER_EXPECTED,
    "70e2aeb655b6e710c82d3eb8833474825b531b07b112547b3d8eea66d92d2514": FRONTIER_EXPECTED,
    "7380cf28d9d4c17efb8e0d28089f8b885afc6ef7283268d64708bb113d257c75": FRONTIER_EXPECTED,
    # Ciência de Dados / Análise de Dados
    "efe23394b40775d5ab1c274c5a8a0825c3d869129a466e5041d843f7be002c0f": ANALYSIS_EXPECTED,
    "459f5ab46bafca99741c2956c4b28e8cdef171b84459f5f656f19ad361afa044": ANALYSIS_EXPECTED,
    "35745cd561e9eea545ff493926df0b94bc2386a8befd57fc4f59edee1ede1505": ANALYSIS_EXPECTED,
    "95d0ee788f69e87af3e12878653d57fbc7c81a630db2b9ec93a7d6037120a8b3": ANALYSIS_EXPECTED,
    # Despacho de Importação e Exportação
    "c0149c4916188451d421677dfe4dd47e3233689c3406d0191ed46e97da5568e2": DISPATCH_EXPECTED,
    "7fe504d13f1d3c03441e02d5f2f6f63204090caea97b8a8661fbb5b5b146d32d": DISPATCH_EXPECTED,
    "9d445e7799ace5d6680e01635c1ac415242571b5674032def52aa9266bd7cf02": DISPATCH_EXPECTED,
    "9ac621145956fe9041cf8919643113371caa2fcd7f0cbb2a0fa820f21f109811": DISPATCH_EXPECTED,
    "482bac302c843b922f085d12201292fa28ceb351661a0e40f3db74c23bea70b4": DISPATCH_EXPECTED,
    "113f11ce21d055f3f95e999fc3708741e6a6d7b5b3dc6841831ff665ce4355f4": DISPATCH_EXPECTED,
}


def build_review(
    source_review_path: Path,
    database_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = read_json(source_review_path)
    records = list(source.get("records") or [])
    if len(records) != 200:
        raise ValueError("a revisão de origem deve conter os 200 registros congelados")

    taxonomy = EditorialTaxonomy.load_default()
    with sqlite3.connect(database_path) as connection:
        candidates, candidate_audit = load_official_structure_references(
            connection, taxonomy=taxonomy
        )
    candidates_by_fingerprint = {
        candidate.content_fingerprint: candidate for candidate in candidates
    }
    existing_fingerprints = {
        str(record.get("contentFingerprint") or "") for record in records
    }

    replacement_records: list[dict[str, Any]] = []
    for fingerprint, reviewed_expected in REPLACEMENTS.items():
        if fingerprint in existing_fingerprints:
            raise ValueError(f"replacement já existe na revisão de origem: {fingerprint}")
        candidate = candidates_by_fingerprint.get(fingerprint)
        if candidate is None:
            raise ValueError(f"replacement não existe no banco candidato: {fingerprint}")
        if candidate.expected != reviewed_expected:
            raise ValueError(f"rótulo estrutural mudou para replacement: {fingerprint}")
        replacement_records.append(
            {
                "sourceQuestionId": candidate.source_question_id,
                "contentFingerprint": fingerprint,
                "structuralExpected": candidate.expected,
                "status": "agent_reviewed_reference",
                "reviewedExpected": reviewed_expected,
                "reasonCode": "replacement_content_confirms_taxonomy_path",
            }
        )

    records.extend(replacement_records)
    statuses = Counter(str(record.get("status") or "") for record in records)
    available_fingerprints = set(candidates_by_fingerprint)
    usable_records = [
        record for record in records if record.get("status") == "agent_reviewed_reference"
    ]
    available_usable = [
        record
        for record in usable_records
        if record.get("contentFingerprint") in available_fingerprints
    ]
    unavailable_usable = [
        record
        for record in usable_records
        if record.get("contentFingerprint") not in available_fingerprints
    ]
    if len(available_usable) < 175 or len(unavailable_usable) != 5:
        raise ValueError(
            "a revisão v3 exige pelo menos 175 referências disponíveis "
            "e 5 históricas indisponíveis"
        )

    review = {
        "schemaVersion": 2,
        "kind": "canonical-ai-reference-review",
        "taxonomyVersion": taxonomy.version,
        "sourceBenchmarkId": source.get("sourceBenchmarkId"),
        "reviewMethod": "agent_content_taxonomy_review_with_fingerprint_replacements",
        "humanReview": False,
        "supersedes": source_review_path.name,
        "records": records,
    }
    audit = {
        "schemaVersion": 3,
        "kind": "canonical-ai-reference-review-audit",
        "sourceBenchmarkId": source.get("sourceBenchmarkId"),
        "taxonomyVersion": taxonomy.version,
        "examined": len(records),
        "statuses": dict(sorted(statuses.items())),
        "availableAgentReviewedReferences": len(available_usable),
        "unavailableHistoricalReferences": len(unavailable_usable),
        "replacementReferences": len(replacement_records),
        "readyForPreparation": len(available_usable) >= 175,
        "candidateAudit": candidate_audit,
        "replacementFingerprints": sorted(REPLACEMENTS),
        "networkCallsPerformed": 0,
        "modelInferencesPerformed": 0,
        "humanReview": False,
    }
    return review, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-review", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    review, audit = build_review(args.source_review, args.database)
    write_json(args.review, review)
    write_json(args.audit, audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
