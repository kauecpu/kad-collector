from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .consolidated_review import ConsolidatedReviewIndex
from .editorial_campaign import (
    CAMPAIGN_VERSION,
    _classification_audit,
    _classification_method,
    _note_value,
    _structural_state,
    _visual_dependency,
)
from .editorial_export import build_editorial_record
from .editorial_taxonomy import EditorialTaxonomy
from .json_utils import read_json, write_json, write_json_lines
from .local_review import (
    question_content_sha256,
    save_review_session,
    update_review_question,
    verify_review_session,
)
from .models import DocumentRecord, LocalReviewSession, QuestionRecord, StrictModel
from .question_equivalence import question_fingerprints

APPROVAL_RULE_VERSION = "1.0.0"
APPROVAL_SCHEMA_VERSION = "1.0"

EditorialState = Literal[
    "auto_ready",
    "audit_sample",
    "needs_review",
    "quarantined",
    "approved_for_staging",
    "rejected",
]
GroupStatus = Literal["pending_audit", "approved", "blocked"]
AuditStatus = Literal["approved", "rejected", "deferred"]


class ApprovalConfig(StrictModel):
    sample_rate: float = Field(default=0.02, gt=0, le=1)
    minimum_sample: int = Field(default=50, ge=1)
    maximum_sample: int = Field(default=300, ge=1)
    minimum_taxonomy_confidence: float = Field(default=0.84, ge=0, le=1)
    minimum_ocr_confidence: float = Field(default=0.90, ge=0, le=1)
    minimum_structural_precision: float = Field(default=0.98, ge=0, le=1)
    minimum_answer_precision: float = Field(default=0.99, ge=0, le=1)
    minimum_taxonomy_precision: float = Field(default=0.95, ge=0, le=1)
    authorized_hosts: list[str] = Field(
        default_factory=lambda: [
            "cdn.cebraspe.org.br",
            "inscricao.cesgranrio.com.br",
            "cesgranrio.org.br",
            "quadrix.org.br",
            "anexos-r2.selecao.net.br",
            "anexos.cdn.selecao.net.br",
        ]
    )

    @model_validator(mode="after")
    def sample_limits_are_ordered(self) -> ApprovalConfig:
        if self.minimum_sample > self.maximum_sample:
            raise ValueError("minimum_sample não pode exceder maximum_sample")
        return self


class GateDimension(StrictModel):
    score: float = Field(ge=0, le=1)
    passed: bool
    critical: bool = False
    evidence: list[str] = Field(default_factory=list)


class AuditDecision(StrictModel):
    status: AuditStatus
    reviewer: str = Field(min_length=2)
    reviewed_at: datetime
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    structural_correct: bool | None = None
    answer_correct: bool | None = None
    taxonomy_correct: bool | None = None
    critical_errors: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def completed_audit_requires_dimensions(self) -> AuditDecision:
        if self.status in {"approved", "rejected"} and any(
            value is None
            for value in (
                self.structural_correct,
                self.answer_correct,
                self.taxonomy_correct,
            )
        ):
            raise ValueError("auditoria concluída exige avaliação das três dimensões")
        if self.status == "rejected" and not (self.notes or self.critical_errors):
            raise ValueError("rejeição da amostra exige motivo")
        return self


class ApprovalQuestion(StrictModel):
    stable_id: str
    semantic_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    group_id: str
    batch_id: str
    session_path: str
    question_number: int = Field(ge=1)
    state: EditorialState
    eligible_for_auto: bool
    dimensions: dict[str, GateDimension]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    board: str | None = None
    organization: str | None = None
    contest: str | None = None
    year: int | None = None
    role: str | None = None
    question_format: Literal["true_false", "multiple_choice"]
    extraction_method: str
    classification_method: str
    classification_confidence: float | None = Field(default=None, ge=0, le=1)
    taxonomy_path_id: str | None = None
    exam_url: str | None = None
    answer_key_url: str | None = None
    exam_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    answer_key_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    audit_decision: AuditDecision | None = None


class ApprovalGroup(StrictModel):
    group_id: str
    key: dict[str, str]
    question_ids: list[str]
    sample_ids: list[str] = Field(default_factory=list)
    status: GroupStatus = "pending_audit"
    structural_precision: float | None = Field(default=None, ge=0, le=1)
    answer_precision: float | None = Field(default=None, ge=0, le=1)
    taxonomy_precision: float | None = Field(default=None, ge=0, le=1)
    critical_errors: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    reviewed_items: int = Field(default=0, ge=0)
    required_items: int = Field(default=0, ge=0)
    manually_blocked_by: str | None = None
    manual_block_reason: str | None = None
    manually_blocked_at: datetime | None = None


class ApprovalSummary(StrictModel):
    raw_occurrences: int = Field(ge=0)
    unique_questions: int = Field(ge=0)
    duplicate_occurrences: int = Field(ge=0)
    auto_ready: int = Field(ge=0)
    audit_sample: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    approved_for_staging: int = Field(ge=0)
    rejected: int = Field(ge=0)
    groups: int = Field(ge=0)
    groups_approved: int = Field(ge=0)
    groups_blocked: int = Field(ge=0)
    groups_pending: int = Field(ge=0)
    processed_without_intervention_percent: float = Field(ge=0, le=1)
    auto_ready_percent: float = Field(ge=0, le=1)


class ApprovalCampaignState(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: str
    rule_version: str = APPROVAL_RULE_VERSION
    taxonomy_version: str
    campaign_version: str = CAMPAIGN_VERSION
    created_at: datetime
    updated_at: datetime
    source_index_path: str
    config: ApprovalConfig
    questions: list[ApprovalQuestion]
    groups: list[ApprovalGroup]
    summary: ApprovalSummary
    blocker_distribution: dict[str, int]
    duration_seconds: float = Field(ge=0)
    seconds_per_thousand: float = Field(ge=0)
    qwen_calls: int = Field(default=0, ge=0)
    qwen_failures: int = Field(default=0, ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    publication_status: Literal["draft"] = "draft"


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _host_allowed(url: str, hosts: list[str]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    hostname = parsed.hostname.casefold()
    return any(
        hostname == host.casefold() or hostname.endswith(f".{host.casefold()}") for host in hosts
    )


def _document_integrity(
    document: DocumentRecord | None,
    cache: dict[str, tuple[bool, list[str]]],
) -> tuple[bool, list[str]]:
    if document is None:
        return False, ["documento ausente"]
    if document.sha256 in cache:
        return cache[document.sha256]
    path = Path(document.local_path)
    if not path.is_file():
        result = (False, ["arquivo local ausente"])
        cache[document.sha256] = result
        return result
    if document.content_type != "application/pdf":
        result = (False, ["tipo de conteúdo não é PDF"])
        cache[document.sha256] = result
        return result
    if path.stat().st_size != document.size_bytes:
        result = (False, ["tamanho local diverge do manifesto"])
        cache[document.sha256] = result
        return result
    if _file_sha256(path) != document.sha256:
        result = (False, ["SHA-256 local diverge do manifesto"])
        cache[document.sha256] = result
        return result
    result = (True, [f"PDF íntegro: {document.sha256}"])
    cache[document.sha256] = result
    return result


def _extraction_method(question: QuestionRecord) -> str:
    note = _note_value(question, "Extração:") or "não informada"
    return note.split(";", 1)[0].strip().casefold()


def _ocr_confidence(question: QuestionRecord) -> float | None:
    text = " ".join(question.review_notes)
    match = re.search(r"OCR[^.;]*confian(?:ça|ca)[=: ]+([0-9]+(?:[.,][0-9]+)?)", text, re.I)
    if match is None:
        return None
    value = float(match.group(1).replace(",", "."))
    return value / 100 if value > 1 else value


def _question_format(question: QuestionRecord) -> Literal["true_false", "multiple_choice"]:
    normalized = [item.text.strip().casefold() for item in question.alternatives]
    return "true_false" if normalized == ["certo", "errado"] else "multiple_choice"


def _group_key(
    question: QuestionRecord, extraction: str, method: str, path_id: str | None
) -> dict[str, str]:
    return {
        "board": question.board or "(não informado)",
        "contest": question.concurso or "(não informado)",
        "year": str(question.year or "(não informado)"),
        "role": question.role or "(não informado)",
        "format": _question_format(question),
        "extraction": extraction,
        "classification_rule": path_id or method,
    }


def _dimension(
    score: float,
    evidence: list[str],
    *,
    passed: bool | None = None,
    critical: bool = False,
) -> GateDimension:
    gate_passed = score >= 1 if passed is None else passed
    return GateDimension(
        score=score,
        passed=gate_passed,
        critical=critical and not gate_passed,
        evidence=evidence,
    )


def _evaluate_question(
    session: LocalReviewSession,
    question: QuestionRecord,
    *,
    config: ApprovalConfig,
    seen_fingerprints: set[str],
    document_cache: dict[str, tuple[bool, list[str]]],
) -> ApprovalQuestion:
    exam = session.batch.source_document
    answer_key = session.batch.answer_key_document
    fingerprint = question_fingerprints(question.model_dump(mode="json")).exact
    stable_id = question.source_stable_id or question_content_sha256(question)
    content_sha = question_content_sha256(question)
    method = _classification_method(question)
    confidence, path_id = _classification_audit(question)
    extraction = _extraction_method(question)
    letters = [item.letter for item in question.alternatives]
    expected_letters = list("ABCDE"[: len(letters)])

    exam_ok, exam_evidence = _document_integrity(exam, document_cache)
    answer_ok, answer_evidence = _document_integrity(answer_key, document_cache)
    origin_ok = exam_ok and _host_allowed(exam.resolved_url, config.authorized_hosts)
    origin_evidence = [*exam_evidence]
    if not _host_allowed(exam.resolved_url, config.authorized_hosts):
        origin_evidence.append("host da prova não autorizado")

    structure_ok = (
        _structural_state(question) == "accepted"
        and len(question.statement.strip()) >= 10
        and 2 <= len(question.alternatives) <= 5
        and letters == expected_letters
        and len(set(letters)) == len(letters)
        and all(item.text.strip() for item in question.alternatives)
        and bool(question.source_pages)
    )
    structure_evidence = [
        f"estado={_structural_state(question)}",
        f"alternativas={','.join(letters)}",
    ]

    ocr_confidence = _ocr_confidence(question)
    if extraction == "text":
        extraction_ok = True
        extraction_score = 1.0
        extraction_evidence = ["texto nativo; OCR dispensado"]
    elif extraction in {"ocr", "mixed"}:
        extraction_ok = (
            ocr_confidence is not None and ocr_confidence >= config.minimum_ocr_confidence
        )
        extraction_score = ocr_confidence or 0
        extraction_evidence = [
            f"OCR confiança={ocr_confidence if ocr_confidence is not None else 'ausente'}"
        ]
    else:
        extraction_ok = False
        extraction_score = 0
        extraction_evidence = [f"método de extração={extraction}"]

    association_note = _note_value(question, "Associação de gabarito:")
    ambiguous = bool(
        association_note and re.search(r"amb[ií]gu|incert|múltipl", association_note, re.I)
    )
    answer_ok = (
        answer_ok
        and question.answer_status == "matched"
        and question.correct_answer in letters
        and bool(association_note)
        and not ambiguous
        and answer_key is not None
        and _host_allowed(answer_key.resolved_url, config.authorized_hosts)
    )
    answer_evidence = [*answer_evidence, association_note or "associação não informada"]

    taxonomy_complete = all(
        isinstance(value, str) and len(value.strip()) >= 2
        for value in (question.discipline, question.matter, question.subject, question.level)
    )
    taxonomy_ok = (
        taxonomy_complete
        and method in {"deterministic", "human_or_existing"}
        and (
            method == "human_or_existing" or (confidence or 0) >= config.minimum_taxonomy_confidence
        )
        and method != "qwen"
    )
    taxonomy_score = (
        1.0 if method == "human_or_existing" and taxonomy_complete else (confidence or 0)
    )
    taxonomy_evidence = [
        f"método={method}",
        f"confiança={confidence if confidence is not None else 'ausente'}",
        f"caminho={path_id or 'ausente'}",
    ]

    visual_ok = not _visual_dependency(question)
    duplicate_ok = fingerprint not in seen_fingerprints
    seen_fingerprints.add(fingerprint)
    dimensions = {
        "origin": _dimension(1.0 if origin_ok else 0.0, origin_evidence, critical=True),
        "structure": _dimension(1.0 if structure_ok else 0.0, structure_evidence, critical=True),
        "extraction": _dimension(
            extraction_score,
            extraction_evidence,
            passed=extraction_ok,
        ),
        "answer_association": _dimension(
            1.0 if answer_ok else 0.0,
            answer_evidence,
            critical=True,
        ),
        "taxonomy": _dimension(taxonomy_score, taxonomy_evidence, passed=taxonomy_ok),
        "visual_dependency": _dimension(
            1.0 if visual_ok else 0.0,
            ["sem dependência visual"] if visual_ok else ["elemento visual requer conferência"],
            critical=True,
        ),
        "deduplication": _dimension(
            1.0 if duplicate_ok else 0.0,
            ["fingerprint único"] if duplicate_ok else ["fingerprint já encontrado"],
        ),
    }
    blockers = [name for name, value in dimensions.items() if not value.passed]
    if (
        _structural_state(question) in {"quarantined", "rejected"}
        or not origin_ok
        or not structure_ok
    ):
        state: EditorialState = "quarantined"
    elif blockers:
        state = "needs_review"
    else:
        state = "auto_ready"
    key = _group_key(question, extraction, method, path_id)
    group_id = f"grp-{_canonical_sha256(key)[:20]}"
    return ApprovalQuestion(
        stable_id=stable_id,
        semantic_fingerprint=fingerprint,
        content_sha256=content_sha,
        group_id=group_id,
        batch_id=session.batch.batch_id,
        session_path=str(Path(session.batch.source_document.local_path).resolve().parent),
        question_number=question.number,
        state=state,
        eligible_for_auto=not blockers,
        dimensions=dimensions,
        blockers=blockers,
        warnings=list(dict.fromkeys(question.editorial_blocks)),
        board=question.board,
        organization=question.organization,
        contest=question.concurso,
        year=question.year,
        role=question.role,
        question_format=_question_format(question),
        extraction_method=extraction,
        classification_method=method,
        classification_confidence=confidence,
        taxonomy_path_id=path_id,
        exam_url=exam.resolved_url,
        answer_key_url=answer_key.resolved_url if answer_key else None,
        exam_sha256=exam.sha256,
        answer_key_sha256=answer_key.sha256 if answer_key else None,
    )


def _select_sample(questions: list[ApprovalQuestion], config: ApprovalConfig) -> set[str]:
    eligible = [item for item in questions if item.state == "auto_ready"]
    if not eligible:
        return set()
    target = min(
        config.maximum_sample,
        len(eligible),
        max(config.minimum_sample, math.ceil(len(eligible) * config.sample_rate)),
    )
    by_group: dict[str, list[ApprovalQuestion]] = defaultdict(list)
    for item in eligible:
        by_group[item.group_id].append(item)
    selected: dict[str, ApprovalQuestion] = {}
    for group_id, items in sorted(by_group.items()):
        choice = min(items, key=lambda item: _canonical_sha256([group_id, item.stable_id]))
        selected[choice.stable_id] = choice
        if len(selected) >= target:
            return set(selected)
    dimensions = (
        "board",
        "organization",
        "contest",
        "year",
        "role",
        "question_format",
        "extraction_method",
        "classification_method",
    )
    for dimension in dimensions:
        strata: dict[str, list[ApprovalQuestion]] = defaultdict(list)
        for item in eligible:
            strata[str(getattr(item, dimension) or "(não informado)")].append(item)
        for value, items in sorted(strata.items()):
            choice = min(
                items, key=lambda item: _canonical_sha256([dimension, value, item.stable_id])
            )
            selected[choice.stable_id] = choice
            if len(selected) >= target:
                return set(selected)
    for item in sorted(eligible, key=lambda value: _canonical_sha256(value.stable_id)):
        selected[item.stable_id] = item
        if len(selected) >= target:
            break
    return set(selected)


def _refresh_groups(
    questions: list[ApprovalQuestion],
    config: ApprovalConfig,
    previous_groups: list[ApprovalGroup] | None = None,
) -> list[ApprovalGroup]:
    previous_by_id = {item.group_id: item for item in previous_groups or []}
    grouped: dict[str, list[ApprovalQuestion]] = defaultdict(list)
    for item in questions:
        if item.eligible_for_auto:
            grouped[item.group_id].append(item)
    groups: list[ApprovalGroup] = []
    for group_id, items in sorted(grouped.items()):
        sample = [item for item in items if item.state == "audit_sample" or item.audit_decision]
        completed = [
            item
            for item in sample
            if item.audit_decision and item.audit_decision.status in {"approved", "rejected"}
        ]
        critical_errors = sorted(
            {
                error
                for item in completed
                for error in (item.audit_decision.critical_errors if item.audit_decision else [])
            }
        )
        structural = [
            item.audit_decision.structural_correct for item in completed if item.audit_decision
        ]
        answers = [item.audit_decision.answer_correct for item in completed if item.audit_decision]
        taxonomy = [
            item.audit_decision.taxonomy_correct for item in completed if item.audit_decision
        ]
        structural_precision = (
            sum(value is True for value in structural) / len(structural) if structural else None
        )
        answer_precision = (
            sum(value is True for value in answers) / len(answers) if answers else None
        )
        taxonomy_precision = (
            sum(value is True for value in taxonomy) / len(taxonomy) if taxonomy else None
        )
        blockers: list[str] = []
        if not sample:
            blockers.append("auditoria_sem_amostra")
        if len(completed) < len(sample):
            blockers.append("auditoria_incompleta")
        if critical_errors:
            blockers.append("erro_critico_na_amostra")
        if (
            structural_precision is not None
            and structural_precision < config.minimum_structural_precision
        ):
            blockers.append("precisao_estrutural_insuficiente")
        if answer_precision is not None and answer_precision < config.minimum_answer_precision:
            blockers.append("precisao_gabarito_insuficiente")
        if (
            taxonomy_precision is not None
            and taxonomy_precision < config.minimum_taxonomy_precision
        ):
            blockers.append("precisao_taxonomia_insuficiente")
        previous_group = previous_by_id.get(group_id)
        manually_blocked = bool(previous_group and previous_group.manual_block_reason)
        if manually_blocked:
            blockers.append("bloqueio_manual")
        status: GroupStatus
        if manually_blocked:
            status = "blocked"
        elif not sample or len(completed) < len(sample):
            status = "pending_audit"
        elif blockers:
            status = "blocked"
        else:
            status = "approved"
        key = {
            "board": items[0].board or "(não informado)",
            "contest": items[0].contest or "(não informado)",
            "year": str(items[0].year or "(não informado)"),
            "role": items[0].role or "(não informado)",
            "format": items[0].question_format,
            "extraction": items[0].extraction_method,
            "classification_rule": items[0].taxonomy_path_id or items[0].classification_method,
        }
        groups.append(
            ApprovalGroup(
                group_id=group_id,
                key=key,
                question_ids=sorted(item.stable_id for item in items),
                sample_ids=sorted(item.stable_id for item in sample),
                status=status,
                structural_precision=structural_precision,
                answer_precision=answer_precision,
                taxonomy_precision=taxonomy_precision,
                critical_errors=critical_errors,
                blockers=list(dict.fromkeys(blockers)),
                reviewed_items=len(completed),
                required_items=len(sample),
                manually_blocked_by=(
                    previous_group.manually_blocked_by if previous_group else None
                ),
                manual_block_reason=(
                    previous_group.manual_block_reason if previous_group else None
                ),
                manually_blocked_at=(
                    previous_group.manually_blocked_at if previous_group else None
                ),
            )
        )
    status_by_group = {item.group_id: item.status for item in groups}
    for question in questions:
        if status_by_group.get(question.group_id) == "approved" and question.state in {
            "auto_ready",
            "audit_sample",
        }:
            question.state = "approved_for_staging"
        elif status_by_group.get(question.group_id) == "blocked" and question.state in {
            "auto_ready",
            "audit_sample",
            "approved_for_staging",
        }:
            question.state = "needs_review"
            question.blockers = list(dict.fromkeys([*question.blockers, "group_audit_failed"]))
    return groups


def _summary(questions: list[ApprovalQuestion], groups: list[ApprovalGroup]) -> ApprovalSummary:
    states = Counter(item.state for item in questions)
    fingerprints = Counter(item.semantic_fingerprint for item in questions)
    unique = len(fingerprints)
    return ApprovalSummary(
        raw_occurrences=len(questions),
        unique_questions=unique,
        duplicate_occurrences=len(questions) - unique,
        auto_ready=states["auto_ready"],
        audit_sample=states["audit_sample"],
        needs_review=states["needs_review"],
        quarantined=states["quarantined"],
        approved_for_staging=states["approved_for_staging"],
        rejected=states["rejected"],
        groups=len(groups),
        groups_approved=sum(item.status == "approved" for item in groups),
        groups_blocked=sum(item.status == "blocked" for item in groups),
        groups_pending=sum(item.status == "pending_audit" for item in groups),
        processed_without_intervention_percent=1.0 if questions else 0.0,
        auto_ready_percent=(
            states["auto_ready"] + states["audit_sample"] + states["approved_for_staging"]
        )
        / len(questions)
        if questions
        else 0,
    )


def _state_hash(state: ApprovalCampaignState) -> str:
    payload = state.model_dump(
        mode="json",
        exclude={
            "created_at",
            "updated_at",
            "duration_seconds",
            "seconds_per_thousand",
            "content_sha256",
        },
    )
    return _canonical_sha256(payload)


def _portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build_approval_campaign(
    index_path: Path,
    output_path: Path,
    *,
    config: ApprovalConfig | None = None,
) -> ApprovalCampaignState:
    started = time.perf_counter()
    index_path = index_path.resolve()
    index = ConsolidatedReviewIndex.model_validate(read_json(index_path))
    config = config or ApprovalConfig()
    previous: ApprovalCampaignState | None = None
    if output_path.is_file():
        previous = ApprovalCampaignState.model_validate(read_json(output_path))
    previous_decisions = {
        item.stable_id: item.audit_decision
        for item in (previous.questions if previous else [])
        if item.audit_decision is not None
    }
    previous_content = {
        item.stable_id: item.content_sha256 for item in (previous.questions if previous else [])
    }
    questions: list[ApprovalQuestion] = []
    seen_fingerprints: set[str] = set()
    document_cache: dict[str, tuple[bool, list[str]]] = {}
    for batch in index.batches:
        if batch.session_path is None:
            continue
        session_path = Path(batch.session_path)
        session = LocalReviewSession.model_validate(read_json(session_path))
        verify_review_session(session)
        local_decisions = {item.question_number: item for item in session.decisions}
        for question in session.batch.questions:
            evaluated = _evaluate_question(
                session,
                question,
                config=config,
                seen_fingerprints=seen_fingerprints,
                document_cache=document_cache,
            )
            evaluated.session_path = str(session_path.resolve())
            decision = previous_decisions.get(evaluated.stable_id)
            if decision and previous_content.get(evaluated.stable_id) == evaluated.content_sha256:
                evaluated.audit_decision = decision
                if decision.status == "rejected":
                    evaluated.state = "rejected"
                elif decision.status == "deferred":
                    evaluated.state = "audit_sample"
            else:
                local_decision = local_decisions.get(question.number)
                if (
                    local_decision
                    and local_decision.status != "pending"
                    and local_decision.content_sha256 == evaluated.content_sha256
                    and local_decision.reviewed_by
                    and local_decision.reviewed_at
                ):
                    if local_decision.status == "approved":
                        evaluated.audit_decision = AuditDecision(
                            status="approved",
                            reviewer=local_decision.reviewed_by,
                            reviewed_at=local_decision.reviewed_at,
                            content_sha256=evaluated.content_sha256,
                            structural_correct=True,
                            answer_correct=True,
                            taxonomy_correct=True,
                            notes=local_decision.notes,
                        )
                    elif local_decision.status == "rejected":
                        evaluated.audit_decision = AuditDecision(
                            status="rejected",
                            reviewer=local_decision.reviewed_by,
                            reviewed_at=local_decision.reviewed_at,
                            content_sha256=evaluated.content_sha256,
                            structural_correct=False,
                            answer_correct=False,
                            taxonomy_correct=False,
                            critical_errors=["decisao_humana_anterior"],
                            notes=local_decision.notes,
                        )
                        evaluated.state = "rejected"
                    else:
                        evaluated.audit_decision = AuditDecision(
                            status="deferred",
                            reviewer=local_decision.reviewed_by,
                            reviewed_at=local_decision.reviewed_at,
                            content_sha256=evaluated.content_sha256,
                            notes=local_decision.notes,
                        )
            questions.append(evaluated)
    selected = _select_sample(questions, config)
    for item in questions:
        if item.stable_id in selected and item.state == "auto_ready":
            item.state = "audit_sample"
    groups = _refresh_groups(questions, config, previous.groups if previous else None)
    duration = time.perf_counter() - started
    blocker_distribution = Counter(blocker for item in questions for blocker in item.blockers)
    now = datetime.now(UTC)
    campaign_report_path = index_path.parent.parent / "campaign-report.json"
    qwen_calls = 0
    qwen_failures = 0
    if campaign_report_path.is_file():
        campaign_report = read_json(campaign_report_path)
        qwen_calls = int(campaign_report.get("qwen", {}).get("calls", 0))
        qwen_failures = int(campaign_report.get("qwen", {}).get("failures", 0))
    state = ApprovalCampaignState(
        campaign_id=f"{index.corpus_id}-approval",
        taxonomy_version=EditorialTaxonomy.load_default().version,
        created_at=previous.created_at if previous else now,
        updated_at=now,
        source_index_path=_portable(index_path),
        config=config,
        questions=questions,
        groups=groups,
        summary=_summary(questions, groups),
        blocker_distribution=dict(sorted(blocker_distribution.items())),
        duration_seconds=round(duration, 3),
        seconds_per_thousand=round(duration / len(questions) * 1000, 3) if questions else 0,
        qwen_calls=qwen_calls,
        qwen_failures=qwen_failures,
        content_sha256="0" * 64,
    )
    state.content_sha256 = _state_hash(state)
    write_json(output_path, state.model_dump(mode="json"))
    return state


def decide_audit_item(
    state_path: Path,
    stable_id: str,
    *,
    reviewer: str,
    status: AuditStatus,
    structural_correct: bool | None,
    answer_correct: bool | None,
    taxonomy_correct: bool | None,
    critical_errors: list[str] | None = None,
    notes: str | None = None,
) -> ApprovalCampaignState:
    state = ApprovalCampaignState.model_validate(read_json(state_path))
    question = next((item for item in state.questions if item.stable_id == stable_id), None)
    if question is None:
        raise ValueError("questão não encontrada na campanha")
    if question.state not in {"audit_sample", "approved_for_staging", "needs_review"}:
        raise ValueError("questão não pertence à amostra auditável")
    question.audit_decision = AuditDecision(
        status=status,
        reviewer=reviewer,
        reviewed_at=datetime.now(UTC),
        content_sha256=question.content_sha256,
        structural_correct=structural_correct,
        answer_correct=answer_correct,
        taxonomy_correct=taxonomy_correct,
        critical_errors=critical_errors or [],
        notes=notes,
    )
    if status == "rejected":
        question.state = "rejected"
    elif status == "deferred":
        question.state = "audit_sample"
    else:
        question.state = "audit_sample"
    state.groups = _refresh_groups(state.questions, state.config, state.groups)
    state.summary = _summary(state.questions, state.groups)
    state.blocker_distribution = dict(
        sorted(Counter(blocker for item in state.questions for blocker in item.blockers).items())
    )
    state.updated_at = datetime.now(UTC)
    state.content_sha256 = _state_hash(state)
    write_json(state_path, state.model_dump(mode="json"))
    return state


def reprocess_group(state_path: Path, group_id: str) -> ApprovalCampaignState:
    state = ApprovalCampaignState.model_validate(read_json(state_path))
    if not any(item.group_id == group_id for item in state.questions):
        raise ValueError("grupo não encontrado")
    previous_sample_ids = next(
        group.sample_ids for group in state.groups if group.group_id == group_id
    )
    for group in state.groups:
        if group.group_id == group_id:
            group.manually_blocked_by = None
            group.manual_block_reason = None
            group.manually_blocked_at = None
    for item in state.questions:
        if item.group_id != group_id:
            continue
        item.audit_decision = None
        if item.eligible_for_auto:
            item.state = "audit_sample" if item.stable_id in previous_sample_ids else "auto_ready"
        item.blockers = [blocker for blocker in item.blockers if blocker != "group_audit_failed"]
    state.groups = _refresh_groups(state.questions, state.config, state.groups)
    state.summary = _summary(state.questions, state.groups)
    state.updated_at = datetime.now(UTC)
    state.content_sha256 = _state_hash(state)
    write_json(state_path, state.model_dump(mode="json"))
    return state


def block_approval_group(
    state_path: Path,
    group_id: str,
    *,
    reviewer: str,
    reason: str,
) -> ApprovalCampaignState:
    state = ApprovalCampaignState.model_validate(read_json(state_path))
    reviewer = reviewer.strip()
    reason = reason.strip()
    if len(reviewer) < 2:
        raise ValueError("informe o nome ou identificador do revisor")
    if len(reason) < 5:
        raise ValueError("informe o motivo do bloqueio")
    group = next((item for item in state.groups if item.group_id == group_id), None)
    if group is None:
        raise ValueError("grupo não encontrado")
    group.manually_blocked_by = reviewer
    group.manual_block_reason = reason
    group.manually_blocked_at = datetime.now(UTC)
    state.groups = _refresh_groups(state.questions, state.config, state.groups)
    state.summary = _summary(state.questions, state.groups)
    state.updated_at = datetime.now(UTC)
    state.content_sha256 = _state_hash(state)
    write_json(state_path, state.model_dump(mode="json"))
    return state


def approve_approval_group(state_path: Path, group_id: str) -> ApprovalCampaignState:
    state = ApprovalCampaignState.model_validate(read_json(state_path))
    state.groups = _refresh_groups(state.questions, state.config, state.groups)
    group = next((item for item in state.groups if item.group_id == group_id), None)
    if group is None:
        raise ValueError("grupo não encontrado")
    if group.status != "approved":
        raise ValueError("o grupo não atingiu os critérios: " + ", ".join(group.blockers))
    state.summary = _summary(state.questions, state.groups)
    state.updated_at = datetime.now(UTC)
    state.content_sha256 = _state_hash(state)
    write_json(state_path, state.model_dump(mode="json"))
    return state


def correct_approval_question(
    state_path: Path,
    stable_id: str,
    question: QuestionRecord,
) -> ApprovalCampaignState:
    state = ApprovalCampaignState.model_validate(read_json(state_path))
    target = next((item for item in state.questions if item.stable_id == stable_id), None)
    if target is None:
        raise ValueError("questão não encontrada na campanha")
    session_path = Path(target.session_path)
    session = LocalReviewSession.model_validate(read_json(session_path))
    updated = update_review_question(session, target.question_number, question)
    save_review_session(updated, session_path)
    source_index = Path(state.source_index_path)
    return build_approval_campaign(source_index, state_path, config=state.config)


def export_staging_package(state_path: Path, output_dir: Path) -> dict[str, Any]:
    state = ApprovalCampaignState.model_validate(read_json(state_path))
    approved_groups = {item.group_id for item in state.groups if item.status == "approved"}
    eligible = [
        item
        for item in state.questions
        if item.state == "approved_for_staging" and item.group_id in approved_groups
    ]
    if not eligible:
        raise ValueError("nenhum grupo auditado foi aprovado para staging")
    session_cache: dict[str, LocalReviewSession] = {}
    records: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    group_by_id = {item.group_id: item for item in state.groups}
    for item in eligible:
        session = session_cache.setdefault(
            item.session_path,
            LocalReviewSession.model_validate(read_json(Path(item.session_path))),
        )
        question = next(
            value for value in session.batch.questions if value.number == item.question_number
        )
        if question_content_sha256(question) != item.content_sha256:
            exceptions.append(
                {"stableId": item.stable_id, "issues": ["conteúdo mudou após avaliação"]}
            )
            continue
        try:
            record = build_editorial_record(session.batch, question)
        except ValueError as exc:
            exceptions.append({"stableId": item.stable_id, "issues": [str(exc)]})
            continue
        if record.data.id in seen_ids or record.source.fingerprint in seen_fingerprints:
            exceptions.append(
                {"stableId": item.stable_id, "issues": ["duplicata no pacote de staging"]}
            )
            continue
        seen_ids.add(record.data.id)
        seen_fingerprints.add(record.source.fingerprint)
        records.append(record.model_dump(mode="json", by_alias=True, exclude_none=True))
        group = group_by_id[item.group_id]
        lineage.append(
            {
                "stableId": item.stable_id,
                "groupId": item.group_id,
                "examUrl": item.exam_url,
                "examSha256": item.exam_sha256,
                "answerKeyUrl": item.answer_key_url,
                "answerKeySha256": item.answer_key_sha256,
                "taxonomyVersion": state.taxonomy_version,
                "ruleVersion": state.rule_version,
                "audit": {
                    "sampleIds": group.sample_ids,
                    "structuralPrecision": group.structural_precision,
                    "answerPrecision": group.answer_precision,
                    "taxonomyPrecision": group.taxonomy_precision,
                },
            }
        )
    if not records:
        raise ValueError("nenhuma questão válida permaneceu no pacote de staging")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "questions": output_dir / "questoes.jsonl",
        "lineage": output_dir / "linhagem.jsonl",
        "exceptions": output_dir / "excecoes.jsonl",
    }
    write_json_lines(paths["questions"], records)
    write_json_lines(paths["lineage"], lineage)
    write_json_lines(paths["exceptions"], exceptions)
    files = [
        {
            "path": path.name,
            "sha256": _file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in paths.values()
    ]
    manifest = {
        "schema_version": "1.0",
        "publicationStatus": "draft",
        "sourceApprovalState": _portable(state_path),
        "approvalContentSha256": state.content_sha256,
        "taxonomyVersion": state.taxonomy_version,
        "ruleVersion": state.rule_version,
        "questions": len(records),
        "exceptions": len(exceptions),
        "files": files,
        "content_sha256": _canonical_sha256(
            {"questions": records, "lineage": lineage, "exceptions": exceptions}
        ),
    }
    write_json(output_dir / "manifesto.json", manifest)
    return manifest


def approval_report(state: ApprovalCampaignState) -> dict[str, Any]:
    sampled_ids = {stable_id for group in state.groups for stable_id in group.sample_ids}
    sample = [item for item in state.questions if item.stable_id in sampled_ids]
    strata_fields = (
        "board",
        "organization",
        "contest",
        "year",
        "role",
        "question_format",
        "extraction_method",
        "classification_method",
    )
    strata = {
        field: dict(
            sorted(
                Counter(str(getattr(item, field) or "(não informado)") for item in sample).items()
            )
        )
        for field in strata_fields
    }
    return {
        "schema_version": "1.0",
        "campaign_id": state.campaign_id,
        "rule_version": state.rule_version,
        "taxonomy_version": state.taxonomy_version,
        "publication_status": state.publication_status,
        "automatic_approval_rules": {
            "critical_gates": [
                "origin",
                "structure",
                "answer_association",
                "visual_dependency",
            ],
            "other_gates": ["extraction", "taxonomy", "deduplication"],
            "minimum_taxonomy_confidence": state.config.minimum_taxonomy_confidence,
            "minimum_ocr_confidence": state.config.minimum_ocr_confidence,
            "qwen_only_can_auto_approve": False,
            "authorized_hosts": state.config.authorized_hosts,
        },
        "sampling": {
            "rate": state.config.sample_rate,
            "minimum": state.config.minimum_sample,
            "maximum": state.config.maximum_sample,
            "selected": len(sample),
            "unique_fingerprints": len({item.semantic_fingerprint for item in sample}),
            "strata": strata,
        },
        "group_thresholds": {
            "structural_precision": state.config.minimum_structural_precision,
            "answer_precision": state.config.minimum_answer_precision,
            "taxonomy_precision": state.config.minimum_taxonomy_precision,
            "critical_errors_allowed": 0,
            "all_sample_items_must_be_decided": True,
        },
        "summary": state.summary.model_dump(mode="json"),
        "blocker_distribution": state.blocker_distribution,
        "groups": [
            {
                "group_id": group.group_id,
                "key": group.key,
                "questions": len(group.question_ids),
                "sample": len(group.sample_ids),
                "reviewed": group.reviewed_items,
                "status": group.status,
                "structural_precision": group.structural_precision,
                "answer_precision": group.answer_precision,
                "taxonomy_precision": group.taxonomy_precision,
                "blockers": group.blockers,
            }
            for group in state.groups
        ],
        "duration_seconds": state.duration_seconds,
        "seconds_per_thousand": state.seconds_per_thousand,
        "qwen_calls": state.qwen_calls,
        "qwen_failures": state.qwen_failures,
        "content_sha256": state.content_sha256,
        "automatic_evidence": {
            "processed_without_intervention": state.summary.raw_occurrences,
            "groups_released_without_human_audit": 0,
            "staging_writes": 0,
            "production_writes": 0,
        },
        "corrections_implemented": [
            "limiares de OCR e taxonomia são avaliados contra a configuração",
            "integridade do mesmo PDF é calculada uma vez por execução",
            "grupos sem amostra nunca são aprovados",
            "decisões humanas são preservadas somente enquanto o conteúdo não muda",
            "falha em um critério crítico não pode ser compensada por outras dimensões",
        ],
        "validation_commands": [
            "python -m pytest",
            "python -m ruff check .",
            "python -m mypy src/kad_collector",
            "python -m build",
        ],
        "limitations": [
            "Nenhum grupo segue para staging antes da auditoria completa da amostra.",
            (
                "A precisão observada permanece indisponível enquanto a amostra não receber "
                "decisões humanas."
            ),
        ],
    }


def write_approval_report(
    state: ApprovalCampaignState, json_path: Path, markdown_path: Path
) -> None:
    report = approval_report(state)
    write_json(json_path, report)
    summary = state.summary
    lines = [
        "# Aprovação editorial escalável",
        "",
        f"- Campanha: `{state.campaign_id}`",
        f"- Regras: `{state.rule_version}`",
        f"- Taxonomia: `{state.taxonomy_version}`",
        f"- Estado de publicação: `{state.publication_status}`",
        "",
        "## Resultado",
        "",
        "| Indicador | Total |",
        "|---|---:|",
        f"| Ocorrências | {summary.raw_occurrences} |",
        f"| Questões únicas | {summary.unique_questions} |",
        f"| Duplicatas | {summary.duplicate_occurrences} |",
        (
            "| Elegíveis automaticamente | "
            f"{summary.auto_ready + summary.audit_sample + summary.approved_for_staging} |"
        ),
        f"| Amostra de auditoria | {summary.audit_sample} |",
        f"| Precisam de revisão | {summary.needs_review} |",
        f"| Quarentena | {summary.quarantined} |",
        f"| Aprovadas para staging | {summary.approved_for_staging} |",
        f"| Grupos | {summary.groups} |",
        f"| Grupos aprovados | {summary.groups_approved} |",
        f"| Grupos bloqueados | {summary.groups_blocked} |",
        f"| Grupos aguardando auditoria | {summary.groups_pending} |",
        f"| Processado sem intervenção | {summary.processed_without_intervention_percent:.1%} |",
        f"| Tempo total | {state.duration_seconds:.3f} s |",
        f"| Tempo por mil questões | {state.seconds_per_thousand:.3f} s |",
        f"| Chamadas ao Qwen | {state.qwen_calls} |",
        f"| Falhas do Qwen | {state.qwen_failures} |",
        "",
        "## Regras de aprovação",
        "",
        (
            "A origem, a estrutura, a associação do gabarito e a dependência visual "
            "são travas críticas: uma falha não pode ser compensada por outra dimensão."
        ),
        (
            f"OCR exige confiança mínima de {state.config.minimum_ocr_confidence:.0%}; "
            "a taxonomia determinística exige "
            f"{state.config.minimum_taxonomy_confidence:.0%}. Sugestão isolada do Qwen "
            "nunca recebe aprovação automática."
        ),
        "",
        "## Auditoria",
        "",
        (
            f"A amostra padrão usa {state.config.sample_rate:.0%}, mínimo de "
            f"{state.config.minimum_sample} e máximo de {state.config.maximum_sample}. "
            f"Foram selecionados {summary.audit_sample} fingerprints distintos."
        ),
        (
            "Um grupo só é liberado com toda a amostra decidida, nenhum erro crítico e "
            f"precisões mínimas de {state.config.minimum_structural_precision:.0%} "
            f"(estrutura), {state.config.minimum_answer_precision:.0%} (gabarito) e "
            f"{state.config.minimum_taxonomy_precision:.0%} (taxonomia)."
        ),
        "",
        "## Bloqueios",
        "",
        "| Motivo | Questões |",
        "|---|---:|",
        *[f"| {reason} | {count} |" for reason, count in state.blocker_distribution.items()],
        "",
        "## Segurança",
        "",
        (
            "A aprovação automática apenas seleciona candidatos. A auditoria libera grupos "
            "para um pacote local com estado draft. O fluxo não escreve no KAD nem no Supabase."
        ),
        "",
        "## Limitação atual",
        "",
        (
            "Nenhuma precisão pode ser alegada antes das decisões humanas sobre a amostra. "
            "Grupos incompletos continuam bloqueados."
        ),
        "",
        "## Artefatos locais",
        "",
        f"- Estado retomável: `{state.source_index_path}` → campanha local",
        "- Pacote de staging: ainda não gerado; não há grupos auditados.",
        "- Nenhum PDF, enunciado completo ou resposta bruta do Qwen foi versionado.",
        "",
        "## Validação",
        "",
        "- `python -m pytest`",
        "- `python -m ruff check .`",
        "- `python -m mypy src/kad_collector`",
        "- `python -m build`",
    ]
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
