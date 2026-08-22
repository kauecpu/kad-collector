from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from typing import Any, cast

from .semantic_identity import (
    IDENTITY_ALGORITHM_VERSION,
    AssociationCandidate,
    AssociationOutcome,
    CandidateAssessment,
    DocumentAssociationDecision,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    IdentityResolution,
    KnownDocumentVersion,
    SemanticField,
    canonical_json,
    stable_sha256,
)

ASSOCIATION_ALGORITHM_VERSION = "semantic-association-v1"
MATCH_WEIGHTS = {
    "board": 12, "concurso": 12, "year": 12, "organization": 8,
    "role": 10, "stage": 8, "turn": 8, "variant": 8,
}
MINIMUM_SCORE = 36
MINIMUM_MARGIN = 8


def _field(profile: DocumentSemanticProfile, name: str) -> SemanticField:
    return cast(SemanticField, getattr(profile.identity, name))


def _candidate_field(candidate: DocumentSemanticProfile, name: str) -> SemanticField:
    if name in {"role", "stage", "turn", "variant"}:
        return cast(SemanticField, getattr(candidate.coverage, _field_name(name)))
    return _field(candidate, name)


def _field_name(name: str) -> str:
    return {"role": "roles", "turn": "turns", "variant": "variants"}.get(name, name)


def _title_bonus(exam: DocumentSemanticProfile, candidate: DocumentSemanticProfile) -> int:
    exam_values = {
        value for name in ExamSemanticIdentity.model_fields
        for field in (_field(exam, name),)
        for value in field.normalized_values
        if any(item.source == "document_title" for item in field.evidence)
    }
    candidate_values = {
        value for name in ExamSemanticIdentity.model_fields
        for field in (_field(candidate, name),)
        for value in field.normalized_values
        if any(item.source == "document_title" for item in field.evidence)
    }
    return min(2, len(exam_values & candidate_values))


def _weak_only(field: SemanticField) -> bool:
    return bool(field.evidence) and all(item.strength == "weak" for item in field.evidence)


def _assess(exam: DocumentSemanticProfile, item: AssociationCandidate) -> CandidateAssessment:
    candidate = item.profile
    conflicts: list[str] = []
    incomplete_scope = False
    matched: list[str] = []
    reasons: list[str] = []
    score = 0
    if candidate.has_conflict:
        conflicts.append("perfil do candidato contém conflito")
    for name, weight in MATCH_WEIGHTS.items():
        exam_field = _field(
            exam, name if name not in {"role", "turn", "variant"} else _field_name(name)
        )
        candidate_field = _candidate_field(candidate, name)
        if exam_field.status == "conflict" or candidate_field.status == "conflict":
            conflicts.append(f"{name}: conflito conhecido")
            continue
        if exam_field.status != "known":
            continue
        if _weak_only(exam_field):
            reasons.append(f"{name}: título fraco não participa da decisão")
            continue
        if candidate_field.status != "known":
            if name in {"role", "stage", "turn", "variant"}:
                reasons.append(f"{name}: cobertura desconhecida")
                incomplete_scope = True
            continue
        if _weak_only(candidate_field):
            reasons.append(f"{name}: título fraco não participa da decisão")
            if name in {"role", "stage", "turn", "variant"}:
                incomplete_scope = True
            continue
        if set(exam_field.normalized_values).isdisjoint(candidate_field.normalized_values):
            conflicts.append(f"{name}: valores incompatíveis")
            continue
        matched.append(name)
        exam_strong = any(evidence.strength == "strong" for evidence in exam_field.evidence)
        candidate_strong = any(
            evidence.strength == "strong" for evidence in candidate_field.evidence
        )
        if exam_strong and candidate_strong:
            score += weight
    score += _title_bonus(exam, candidate)
    strong = {"board", "concurso", "year"}
    def has_strong_evidence(field: Any) -> bool:
        return bool(field.evidence) and any(
            evidence.strength == "strong" for evidence in field.evidence
        )

    strong_ok = all(
        _field(exam, name).status == "known"
        and _candidate_field(candidate, name).status == "known"
        and not set(_field(exam, name).normalized_values).isdisjoint(
            _candidate_field(candidate, name).normalized_values
        )
        and has_strong_evidence(_field(exam, name))
        and has_strong_evidence(_candidate_field(candidate, name))
        for name in strong
    )
    if not strong_ok:
        reasons.append("banca, concurso e ano não formam três evidências fortes")
    if score > 0:
        reasons.append(f"pontuação semântica: {score}")
    return CandidateAssessment(
        version_id=item.version_id,
        compatible=not conflicts and not incomplete_scope and strong_ok,
        score=score,
        matched_fields=tuple(matched),
        conflicts=tuple(conflicts),
        reasons=tuple(reasons),
    )


def select_answer_key(
    exam_profile: DocumentSemanticProfile,
    candidates: Sequence[AssociationCandidate],
) -> DocumentAssociationDecision:
    assessments = tuple(sorted(
        (_assess(exam_profile, item) for item in candidates),
        key=lambda value: (-value.score, value.version_id),
    ))
    if not assessments:
        return DocumentAssociationDecision(
            outcome="missing", selected_version_id=None, assessments=(),
            minimum_score=MINIMUM_SCORE, minimum_margin=MINIMUM_MARGIN,
            achieved_margin=None, reason="nenhum gabarito candidato",
            algorithm_version=ASSOCIATION_ALGORITHM_VERSION,
        )
    compatible = [item for item in assessments if item.compatible and item.score >= MINIMUM_SCORE]
    if not compatible:
        outcome: AssociationOutcome = (
            "conflict" if any(item.conflicts for item in assessments)
            else "insufficient_evidence"
        )
        return DocumentAssociationDecision(
            outcome=outcome, selected_version_id=None, assessments=assessments,
            minimum_score=MINIMUM_SCORE, minimum_margin=MINIMUM_MARGIN,
            achieved_margin=None, reason=("candidato eliminado por conflito conhecido"
                                          if outcome == "conflict"
                                          else "evidência semântica insuficiente"),
            algorithm_version=ASSOCIATION_ALGORITHM_VERSION,
        )
    top = compatible[0]
    second = compatible[1] if len(compatible) > 1 else None
    def semantic_score(assessment: CandidateAssessment) -> int:
        candidate = next(item for item in candidates if item.version_id == assessment.version_id)
        return assessment.score - _title_bonus(exam_profile, candidate.profile)

    top = max(
        compatible,
        key=lambda item: (semantic_score(item), item.score, item.version_id),
    )
    second = next((item for item in compatible if item.version_id != top.version_id), None)
    best_semantic = semantic_score(top)
    tied = [item for item in compatible if semantic_score(item) == best_semantic]
    if len(tied) > 1:
        tied_candidates = [
            next(item for item in candidates if item.version_id == assessment.version_id)
            for assessment in tied
        ]
        definitives = [
            item for item in tied_candidates
            if item.profile.answer_key_state == "definitive"
        ]
        preliminaries = [
            item for item in tied_candidates
            if item.profile.answer_key_state == "preliminary"
        ]
        successors = [item for item in definitives if any(
            item.predecessor_version_id == preliminary.version_id for preliminary in preliminaries
        )]
        if (
            len(tied_candidates) == 2
            and len(definitives) == 1
            and len(preliminaries) == 1
            and len(successors) == 1
        ):
            definitive = successors[0]
            top = next(item for item in compatible if item.version_id == definitive.version_id)
            second = next((item for item in compatible if item.version_id != top.version_id), None)
        else:
            return DocumentAssociationDecision(
                outcome="ambiguous", selected_version_id=None, assessments=assessments,
                minimum_score=MINIMUM_SCORE, minimum_margin=MINIMUM_MARGIN,
                achieved_margin=0, reason="candidatos semanticamente equivalentes",
                algorithm_version=ASSOCIATION_ALGORITHM_VERSION,
            )
    margin = semantic_score(top) - semantic_score(second) if second is not None else None
    top_candidate = next(item for item in candidates if item.version_id == top.version_id)
    second_candidate = (
        next(item for item in candidates if item.version_id == second.version_id)
        if second is not None else None
    )
    predecessor_exception = (
        second_candidate is not None
        and top_candidate.profile.answer_key_state == "definitive"
        and second_candidate.profile.answer_key_state == "preliminary"
        and top_candidate.predecessor_version_id == second_candidate.version_id
    )
    if (
        second is not None and margin is not None and margin < MINIMUM_MARGIN
        and not predecessor_exception
    ):
        return DocumentAssociationDecision(
            outcome="ambiguous", selected_version_id=None, assessments=assessments,
            minimum_score=MINIMUM_SCORE, minimum_margin=MINIMUM_MARGIN,
            achieved_margin=margin, reason="margem entre candidatos insuficiente",
            algorithm_version=ASSOCIATION_ALGORITHM_VERSION,
        )
    return DocumentAssociationDecision(
        outcome="selected", selected_version_id=top.version_id, assessments=assessments,
        minimum_score=MINIMUM_SCORE, minimum_margin=MINIMUM_MARGIN,
        achieved_margin=margin, reason="candidato com evidência semântica suficiente",
        algorithm_version=ASSOCIATION_ALGORITHM_VERSION,
    )


def decide_document_version(
    profile: DocumentSemanticProfile,
    known_versions: tuple[KnownDocumentVersion, ...] | list[KnownDocumentVersion],
) -> IdentityResolution:
    """Purely classify a profile against versions already known for the registry."""
    if profile.identity_key is None or profile.has_conflict:
        return IdentityResolution(
            outcome="uncertain",
            profile=profile,
            reason="identidade semântica insuficiente ou conflitante",
        )
    if profile.content_fingerprint.character_count <= 0:
        return IdentityResolution(
            outcome="uncertain",
            profile=profile,
            reason="documento sem texto utilizável",
        )
    if profile.document_role not in {"exam", "answer_key"}:
        return IdentityResolution(
            outcome="uncertain",
            profile=profile,
            reason="papel semântico do documento não suportado",
        )
    same_identity_and_role = [
        item
        for item in known_versions
        if item.identity_key == profile.identity_key and item.document_role == profile.document_role
    ]
    same_content = [
        item
        for item in same_identity_and_role
        if item.content_sha256 == profile.content_fingerprint.sha256
    ]
    if same_content:
        winner = same_content[-1]
        return IdentityResolution(
            outcome="republication",
            profile=profile,
            document_version_id=winner.version_id,
            predecessor_version_id=winner.predecessor_version_id,
            version_number=winner.version_number,
            reason="conteúdo equivalente já registrado",
        )
    if same_identity_and_role:
        predecessor = same_identity_and_role[-1]
        return IdentityResolution(
            outcome="new_version",
            profile=profile,
            predecessor_version_id=predecessor.version_id,
            version_number=predecessor.version_number + 1,
            reason="mesma identidade e papel, conteúdo alterado",
        )
    return IdentityResolution(
        outcome="new_identity",
        profile=profile,
        version_number=1,
        reason="primeira versão para identidade e papel",
    )


def _known_versions(
    connection: sqlite3.Connection, profile: DocumentSemanticProfile
) -> tuple[KnownDocumentVersion, ...]:
    if profile.identity_key is None:
        return ()
    rows = connection.execute(
        "SELECT id, identity_key, document_role, content_sha256, version_number, "
        "predecessor_version_id FROM document_versions "
        "WHERE identity_key = ? AND document_role = ? ORDER BY version_number",
        (profile.identity_key, profile.document_role),
    ).fetchall()
    return tuple(
        KnownDocumentVersion(
            version_id=row["id"],
            identity_key=row["identity_key"],
            document_role=row["document_role"],
            content_sha256=row["content_sha256"],
            version_number=row["version_number"],
            predecessor_version_id=row["predecessor_version_id"],
        )
        for row in rows
    )


def _version_id(profile: DocumentSemanticProfile) -> str:
    assert profile.identity_key is not None
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{profile.identity_key}:{profile.document_role}:{profile.content_fingerprint.sha256}",
        )
    )


def _event(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    version_id: str | None,
    action: str,
    payload: dict[str, Any],
    resolved_at: str,
) -> None:
    key = stable_sha256({"action": action, "document_id": document_id, "version_id": version_id})
    connection.execute(
        "INSERT OR IGNORE INTO document_identity_events "
        "(event_key, document_id, document_version_id, action, actor, algorithm_version, "
        "payload_json, created_at) "
        "VALUES (?, ?, ?, ?, 'system', ?, ?, ?)",
        (
            key,
            document_id,
            version_id,
            action,
            IDENTITY_ALGORITHM_VERSION,
            canonical_json(payload),
            resolved_at,
        ),
    )


def resolve_document_version(
    connection: sqlite3.Connection,
    document_id: str,
    profile: DocumentSemanticProfile,
    resolved_at: str,
) -> IdentityResolution:
    """Resolve and persist one extracted document atomically and idempotently."""
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT semantic_resolution, document_version_id FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if existing is None:
            raise ValueError("documento não encontrado")
        existing_outcome = existing["semantic_resolution"]
        if existing_outcome in {"uncertain", "new_identity", "new_version", "republication"}:
            existing_version = existing["document_version_id"]
            final_event = connection.execute(
                "SELECT 1 FROM document_identity_events "
                "WHERE document_id = ? AND action = ? "
                "AND ((document_version_id = ?) OR (document_version_id IS NULL AND ? IS NULL))",
                (document_id, existing_outcome, existing_version, existing_version),
            ).fetchone()
            if final_event is None:
                existing_outcome = None
        if existing_outcome in {"uncertain", "new_identity", "new_version", "republication"}:
            if existing_outcome == "uncertain":
                result = IdentityResolution(
                    outcome="uncertain",
                    profile=profile,
                    reason="identidade semântica insuficiente ou conflitante",
                )
            else:
                version = connection.execute(
                    "SELECT version_number, predecessor_version_id "
                    "FROM document_versions WHERE id = ?",
                    (existing_version,),
                ).fetchone()
                if version is None:
                    raise RuntimeError("documento resolvido aponta para versão ausente")
                result = IdentityResolution(
                    outcome=existing_outcome,
                    profile=profile,
                    document_version_id=existing_version,
                    predecessor_version_id=version["predecessor_version_id"],
                    version_number=version["version_number"],
                    reason="resolução já persistida para este documento",
                )
            if owns_transaction:
                connection.commit()
            return result
        decision = decide_document_version(profile, _known_versions(connection, profile))
        if decision.outcome == "uncertain":
            connection.execute(
                "UPDATE documents SET semantic_resolution = ?, updated_at = ? WHERE id = ?",
                (decision.outcome, resolved_at, document_id),
            )
            connection.execute(
                "UPDATE document_observations SET resolution_status = ? WHERE document_id = ?",
                (decision.outcome, document_id),
            )
            _event(
                connection,
                document_id=document_id,
                version_id=None,
                action="uncertain",
                payload={
                    "reason": decision.reason,
                    "identity": profile.identity.model_dump(mode="json"),
                    "evidence": {
                        name: getattr(profile.identity, name).model_dump(mode="json")
                        for name in ExamSemanticIdentity.model_fields
                    },
                },
                resolved_at=resolved_at,
            )
            if owns_transaction:
                connection.commit()
            return decision

        assert profile.identity_key is not None
        identity_json = canonical_json(profile.identity.model_dump(mode="json"))
        evidence = {
            name: getattr(profile.identity, name).model_dump(mode="json")
            for name in ExamSemanticIdentity.model_fields
        }
        connection.execute(
            "INSERT OR IGNORE INTO semantic_identities "
            "(identity_key, schema_version, algorithm_version, identity_json, evidence_json, "
            "created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?)",
            (
                profile.identity_key,
                IDENTITY_ALGORITHM_VERSION,
                identity_json,
                canonical_json(evidence),
                resolved_at,
                resolved_at,
            ),
        )
        version_id = decision.document_version_id or _version_id(profile)
        predecessor = decision.predecessor_version_id
        version_number = decision.version_number or 1
        if decision.outcome == "new_version":
            connection.execute(
                "INSERT OR IGNORE INTO document_versions "
                "(id, identity_key, document_role, answer_key_state, coverage_json, profile_json, "
                "content_sha256, content_normalizer_version, version_number, "
                "predecessor_version_id, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    profile.identity_key,
                    profile.document_role,
                    profile.answer_key_state,
                    canonical_json(profile.coverage.model_dump(mode="json")),
                    canonical_json(profile.model_dump(mode="json")),
                    profile.content_fingerprint.sha256,
                    profile.content_fingerprint.normalizer_version,
                    version_number,
                    predecessor,
                    resolved_at,
                    resolved_at,
                ),
            )
        elif decision.outcome == "new_identity":
            connection.execute(
                "INSERT OR IGNORE INTO document_versions "
                "(id, identity_key, document_role, answer_key_state, coverage_json, profile_json, "
                "content_sha256, content_normalizer_version, version_number, created_at, "
                "updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    profile.identity_key,
                    profile.document_role,
                    profile.answer_key_state,
                    canonical_json(profile.coverage.model_dump(mode="json")),
                    canonical_json(profile.model_dump(mode="json")),
                    profile.content_fingerprint.sha256,
                    profile.content_fingerprint.normalizer_version,
                    version_number,
                    resolved_at,
                    resolved_at,
                ),
            )
        winner = connection.execute(
            "SELECT id, version_number, predecessor_version_id FROM document_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if winner is None:
            raise RuntimeError("versão sem registro após resolução")
        version_id = winner["id"]
        version_number = winner["version_number"]
        predecessor = winner["predecessor_version_id"]
        connection.execute(
            "UPDATE documents SET document_version_id = ?, semantic_resolution = ?, "
            "updated_at = ? WHERE id = ?",
            (version_id, decision.outcome, resolved_at, document_id),
        )
        connection.execute(
            "UPDATE document_observations SET document_id = ?, document_version_id = ?, "
            "resolution_status = ? "
            "WHERE document_id = ? OR id = (SELECT observation_id FROM documents WHERE id = ?)",
            (document_id, version_id, decision.outcome, document_id, document_id),
        )
        _event(
            connection,
            document_id=document_id,
            version_id=version_id,
            action=decision.outcome,
            payload={"identityKey": profile.identity_key, "versionNumber": version_number},
            resolved_at=resolved_at,
        )
        result = decision.model_copy(
            update={
                "document_version_id": version_id,
                "version_number": version_number,
                "predecessor_version_id": predecessor,
            }
        )
        if owns_transaction:
            connection.commit()
        return result
    except Exception:
        if owns_transaction:
            connection.rollback()
        raise
