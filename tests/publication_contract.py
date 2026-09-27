"""Offline acceptance contract, not a substitute for executing KAD migrations.

Checked against KAD 70491bb, migration 20260828220803:
capture_question_answer_evidence and question_publication_blockers.
Publication still requires a separate authenticated editorial decision in KAD.
"""

from typing import Any


def publication_blockers(
    record: dict[str, Any],
    others: list[dict[str, Any]] | None = None,
) -> set[str]:
    data, source = record["data"], record["source"]
    blockers: set[str] = set()
    if len(data.get("statement", "").strip()) < 10:
        blockers.add("statement")
    if any(
        not str(data.get(field) or "").strip()
        for field in ("discipline", "subject", "topic", "level")
    ):
        blockers.add("taxonomy")
    alternatives = data.get("alternatives", [])
    ids = [item.get("id") for item in alternatives]
    if (
        not 2 <= len(alternatives) <= 5
        or len(set(ids)) != len(ids)
        or any(
            item.get("id") not in "ABCDE" or not item.get("text", "").strip()
            for item in alternatives
        )
        or data.get("correct") not in ids
    ):
        blockers.add("alternatives")
    if any(
        not str(source.get(field) or "").strip()
        for field in ("provider", "externalId", "url", "collectedAt", "fingerprint")
    ):
        blockers.add("source")
    provenances = (data.get("canonicalQuestion") or {}).get("provenances", [])
    if not provenances or any(
        not str(item.get("answerKeyLinkId") or "").strip()
        or item.get("answerStatus") != "matched"
        or item.get("answer") != data.get("correct")
        for item in provenances
    ):
        blockers.add("official_answer")
    if any(
        other["data"]["id"] != data["id"]
        and other["source"]["provider"] == source["provider"]
        and other["source"]["externalId"] == source["externalId"]
        for other in (others or [])
    ):
        blockers.add("duplicate_source")
    return blockers
