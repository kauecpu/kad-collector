from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, cast

from .desktop_models import DesktopFilterSet, DesktopOperationScope
from .desktop_store import DesktopStore
from .semantic_identity import canonical_json


@dataclass(frozen=True)
class ResolvedDesktopScope:
    contract: DesktopOperationScope
    question_ids: tuple[str, ...]
    items: tuple[dict[str, Any], ...]
    snapshot_hash: str

    def public(self) -> dict[str, Any]:
        return {
            "type": self.contract.type,
            "count": len(self.question_ids),
            "questionIds": list(self.question_ids),
            "filter": (
                self.contract.filter.model_dump(mode="json")
                if self.contract.filter is not None
                else None
            ),
            "allowOutOfScope": self.contract.allow_out_of_scope,
            "snapshotHash": self.snapshot_hash,
            "items": list(self.items),
        }


def resolve_desktop_scope(
    store: DesktopStore, scope: DesktopOperationScope
) -> ResolvedDesktopScope:
    all_views = store.query(
        DesktopFilterSet(), include_equivalent_copies=True
    )["questions"]
    by_id = {cast(str, view["id"]): view for view in all_views}
    if scope.type == "selected":
        missing = sorted(set(scope.question_ids) - set(by_id))
        if missing:
            raise ValueError(f"questões inexistentes no escopo: {', '.join(missing)}")
        question_ids = tuple(scope.question_ids)
    elif scope.type == "filter":
        assert scope.filter is not None
        question_ids = tuple(
            cast(str, view["id"])
            for view in store.query(scope.filter)["questions"]
        )
        if not question_ids:
            raise ValueError("o filtro atual não contém questões")
    else:
        question_ids = tuple(sorted(by_id))
        if not question_ids:
            raise ValueError("o banco não contém questões")
    items = tuple(_scope_item(by_id[question_id]) for question_id in question_ids)
    normalized = {
        "algorithm": "desktop-operation-scope-v1",
        "scope": scope.model_dump(mode="json", by_alias=True),
        "records": [
            {"id": item["id"], "updatedAt": item["updatedAt"]} for item in items
        ],
    }
    snapshot_hash = hashlib.sha256(canonical_json(normalized).encode()).hexdigest()
    return ResolvedDesktopScope(scope, question_ids, items, snapshot_hash)


def _scope_item(view: dict[str, Any]) -> dict[str, Any]:
    question = cast(dict[str, Any], view["question"])
    metadata = cast(dict[str, Any], view["metadata"])
    return {
        "id": view["id"],
        "number": question.get("number"),
        "source": metadata.get("provider") or "arquivo local",
        "exam": metadata.get("document_title") or view.get("filename"),
        "contest": question.get("concurso") or metadata.get("concurso"),
        "statement": question.get("statement"),
        "updatedAt": view.get("updated_at"),
    }
