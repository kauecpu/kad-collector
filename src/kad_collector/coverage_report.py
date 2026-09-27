"""Read-only coverage of the pages actually inspected, not of an entire portal."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .url_utils import redact_url_secrets


def build_coverage_report(
    manifest: dict[str, Any], package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pages = manifest.get("collection_policy", {}).get("discovery_inventory", [])
    documents = {
        (doc["source_id"], redact_url_secrets(url)): doc["sha256"]
        for doc in manifest.get("documents", [])
        for url in (doc["original_url"], doc["resolved_url"])
    }
    paired = {
        item[field] for section in ("accepted", "quarantined")
        for item in (package or {}).get(section, [])
        for field in ("exam_sha256", "answer_key_sha256")
    }
    pairing_errors = {
        item["document_id"] for item in (package or {}).get("errors", [])
        if item.get("stage") == "pairing"
    }
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for page in pages:
        for section in ("expected", "excluded"):
            for item in page.get(section, []):
                url = redact_url_secrets(item["url"])
                identity = (page["source_id"], page["page_url"], url)
                if identity in seen:
                    continue
                seen.add(identity)
                digest = documents.get((page["source_id"], url))
                status = item.get("status", "unknown")
                rows.append({
                    "source_id": page["source_id"],
                    "page_url": redact_url_secrets(page["page_url"]),
                    "url": url, "title": item.get("title", ""),
                    "document_type": item.get("document_type", "other"),
                    "status": status,
                    "reason": item.get("reason", "downloaded" if status == "downloaded"
                                       else "not_recorded"),
                    "sha256": digest,
                    "pairing": ("paired" if digest in paired else "pairing_error"
                                if digest in pairing_errors else "not_evaluated"),
                })
    return {
        "scope": "pages_recorded_in_manifest",
        "inventory_available": bool(pages),
        "counts": dict(Counter(row["reason"] for row in rows)),
        "documents": rows,
        "limitations": [
            "Não comprova a cobertura de páginas não visitadas.",
            "not_recorded: o manifesto não registrou a causa; não inferir teto ou exclusão.",
            "Pareamento só é avaliado quando um pacote estruturado é fornecido.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text("utf-8"))
    package = json.loads(args.package.read_text("utf-8")) if args.package else None
    print(json.dumps(build_coverage_report(manifest, package), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
