from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from pydantic import Field

from .consolidated_review import (
    ConsolidatedReviewIndex,
    build_consolidated_review,
)
from .desktop_classifier import LocalRuleClassifier
from .desktop_models import ClassificationRequest, DesktopImportMetadata
from .editorial_export import build_editorial_record, stable_question_id
from .editorial_taxonomy import EditorialTaxonomy, TaxonomyPath
from .json_utils import read_json, write_json, write_json_lines
from .local_review import (
    question_content_sha256,
    save_review_session,
    update_review_question,
    verify_review_session,
)
from .models import LocalReviewSession, QuestionRecord, StrictModel
from .question_equivalence import question_fingerprints
from .validation import validate_editorial_question

CAMPAIGN_VERSION = "1.1"
CLASSIFICATION_NOTE_PREFIX = "Classificação automática:"
AUTOMATIC_BLOCKS = {
    "taxonomy_unresolved",
    "classification_level_unresolved",
    "difficulty_unresolved",
}


class EditorialCampaignSpec(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    corpus_spec: str
    qwen_endpoint: str = "http://127.0.0.1:11434"
    qwen_model: str = "qwen3:8b"
    qwen_batch_size: int = Field(default=40, ge=1, le=50)
    minimum_qwen_confidence: float = Field(default=0.78, ge=0, le=1)
    sample_size: int = Field(default=300, ge=1, le=2_000)


class CampaignCounts(StrictModel):
    raw_questions: int = Field(ge=0)
    unique_questions: int = Field(ge=0)
    duplicate_occurrences: int = Field(ge=0)
    structurally_accepted: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    rejected: int = Field(ge=0)
    deterministic_classifications: int = Field(ge=0)
    qwen_classifications: int = Field(ge=0)
    taxonomy_complete: int = Field(ge=0)
    taxonomy_coverage: float = Field(ge=0, le=1)
    taxonomy_field_coverage: dict[str, float] = Field(default_factory=dict)
    taxonomy_unresolved: int = Field(ge=0)
    human_decisions: int = Field(ge=0)
    waiting_human_review: int = Field(ge=0)
    ready_for_export: int = Field(ge=0)


class CampaignQwenStatus(StrictModel):
    enabled: bool
    available: bool
    endpoint: str
    model: str
    digest: str | None = None
    calls: int = Field(ge=0)
    questions_sent: int = Field(ge=0)
    accepted_suggestions: int = Field(ge=0)
    unresolved_suggestions: int = Field(ge=0)
    failures: int = Field(ge=0)


class CampaignSample(StrictModel):
    size: int = Field(ge=0)
    stable_ids: list[str]
    strata: dict[str, int]
    human_decisions: int = Field(ge=0)
    structural_precision: float | None = Field(default=None, ge=0, le=1)
    answer_precision: float | None = Field(default=None, ge=0, le=1)
    classification_precision: float | None = Field(default=None, ge=0, le=1)
    duplicate_fingerprints: int = Field(default=0, ge=0)
    structurally_accepted: int = Field(default=0, ge=0)
    quarantined: int = Field(default=0, ge=0)
    visual_dependencies: int = Field(default=0, ge=0)


class CampaignSampleEntry(StrictModel):
    stable_id: str
    semantic_fingerprint: str
    batch_id: str
    batch_path: str
    session_path: str
    question_number: int = Field(ge=1)
    board: str | None
    organization: str | None
    contest: str | None
    year: int | None
    role: str | None
    question_format: Literal["true_false", "multiple_choice"]
    structural_state: Literal["accepted", "quarantined", "rejected", "unknown"]
    classification_method: str
    classification_confidence: float | None = Field(default=None, ge=0, le=1)
    taxonomy_path_id: str | None
    visual_dependency: bool
    source_pages: list[int]
    exam_url: str | None
    answer_key_url: str | None


class EditorialCampaignReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: str
    campaign_version: str = CAMPAIGN_VERSION
    created_at: datetime
    corpus_index_path: str
    counts: CampaignCounts
    qwen: CampaignQwenStatus
    sample: CampaignSample
    audit_sample: list[CampaignSampleEntry] = Field(default_factory=list)
    grouped: dict[str, list[dict[str, Any]]]
    taxonomy_gaps: list[dict[str, Any]] = Field(default_factory=list)
    quarantine_reasons: dict[str, int]
    next_batch_id: str | None
    resumable: bool = True
    publication_status: Literal["draft"] = "draft"
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_seconds: float = Field(ge=0)
    limitations: list[str]


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve(parent: Path, value: str) -> Path:
    path = Path(value)
    return (parent / path).resolve() if not path.is_absolute() else path.resolve()


def _portable(path: str | Path) -> str:
    value = Path(path)
    try:
        return value.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return value.as_posix()


def _append_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        handle.write("\n")


def _classification_request(question: QuestionRecord) -> ClassificationRequest:
    return ClassificationRequest(
        question_number=question.number,
        statement=question.statement,
        alternatives=[item.text for item in question.alternatives],
        context=question.statement[:1_200],
    )


def _note_value(question: QuestionRecord, prefix: str) -> str | None:
    note = next(
        (item for item in question.review_notes if item.startswith(prefix)), None
    )
    if note is None:
        return None
    value = note[len(prefix) :].strip().removesuffix(".")
    return value or None


def _classification_metadata(question: QuestionRecord) -> DesktopImportMetadata:
    source_url = _note_value(question, "URL oficial da prova:")
    return DesktopImportMetadata(
        provider=question.board,
        source_url=source_url,
        canonical_url=source_url,
        document_title=question.role,
        document_type="exam",
        concurso=question.concurso,
        board=question.board,
        year=question.year,
        role=question.role,
        stage="objetiva",
        organization=question.organization,
    )


def _classification_fields(question: QuestionRecord) -> tuple[str | None, ...]:
    return (
        question.discipline,
        question.matter,
        question.subject,
        question.level,
        question.difficulty,
    )


def _classification_method(question: QuestionRecord) -> str:
    note = next(
        (item for item in question.review_notes if item.startswith(CLASSIFICATION_NOTE_PREFIX)),
        "",
    )
    if "método=qwen_unresolved;" in note:
        return "qwen_unresolved"
    if "método=qwen;" in note:
        return "qwen"
    if "método=deterministic" in note:
        return "deterministic"
    if all(_classification_fields(question)):
        return "human_or_existing"
    return "unresolved"


def _apply_classification(
    question: QuestionRecord,
    *,
    path: TaxonomyPath | None,
    level: str | None,
    difficulty: str | None,
    method: str,
    confidence: float,
    taxonomy_version: str,
    evidence: str | None = None,
) -> QuestionRecord:
    notes = [
        item
        for item in question.review_notes
        if not item.startswith(CLASSIFICATION_NOTE_PREFIX)
    ]
    values = {
        "discipline": question.discipline or (path.discipline if path else None),
        "matter": question.matter or (path.matter if path else None),
        "subject": question.subject or (path.subject if path else None),
        "level": question.level or level,
        "difficulty": question.difficulty or difficulty,
    }
    missing = [name for name, value in values.items() if value is None]
    notes.append(
        f"{CLASSIFICATION_NOTE_PREFIX} método={method}; confiança={confidence:.2f}; "
        f"taxonomia={taxonomy_version}; caminho={path.path_id if path else 'nenhum'}; "
        f"evidência={evidence or 'insuficiente'}; "
        f"pendências={','.join(missing) or 'nenhuma'}."
    )
    blocks = [item for item in question.editorial_blocks if item not in AUTOMATIC_BLOCKS]
    if any(values[name] is None for name in ("discipline", "matter", "subject")):
        blocks.append("taxonomy_unresolved")
    if values["level"] is None:
        blocks.append("classification_level_unresolved")
    if values["difficulty"] is None:
        blocks.append("difficulty_unresolved")
    return question.model_copy(
        update={**values, "review_notes": notes, "editorial_blocks": list(dict.fromkeys(blocks))}
    )


def _local_path(
    result: Any, taxonomy: EditorialTaxonomy
) -> tuple[TaxonomyPath | None, str | None, str | None, float, str | None]:
    classification = result.classification
    values = (
        classification.discipline.value,
        classification.subject.value,
        classification.topic.value,
    )
    if not all(isinstance(value, str) and value for value in values):
        return (
            None,
            classification.level.value,
            classification.difficulty.value,
            0,
            None,
        )
    path = next(
        (
            item
            for item in taxonomy.candidate_paths()
            if (item.discipline, item.matter, item.subject) == values
        ),
        None,
    )
    confidence = min(
        float(classification.discipline.confidence),
        float(classification.subject.confidence),
        float(classification.topic.confidence),
    )
    evidence = classification.topic.evidence or classification.subject.evidence
    return (
        path,
        classification.level.value,
        classification.difficulty.value,
        confidence if path is not None else 0,
        evidence,
    )


class OllamaTaxonomyClassifier:
    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        minimum_confidence: float,
        request: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.minimum_confidence = minimum_confidence
        self._request = request
        self.calls = 0
        self.questions_sent = 0
        self.accepted = 0
        self.unresolved = 0
        self.failures = 0
        self.digest: str | None = None

    def preflight(self) -> bool:
        if self._request is not None:
            self.digest = "fixture"
            return True
        try:
            response = httpx.get(f"{self.endpoint}/api/tags", timeout=5.0)
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return False
        selected = next(
            (
                item
                for item in models
                if item.get("name") == self.model or item.get("model") == self.model
            ),
            None,
        )
        if selected is None:
            return False
        self.digest = str(selected.get("digest") or "") or None
        return True

    @staticmethod
    def _schema(option_ids: list[str], stable_ids: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": len(stable_ids),
                    "maxItems": len(stable_ids),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "stable_id",
                            "option_id",
                            "level",
                            "difficulty",
                            "confidence",
                            "evidence",
                        ],
                        "properties": {
                            "stable_id": {"type": "string", "enum": stable_ids},
                            "option_id": {
                                "type": "string",
                                "enum": [*option_ids, "unresolved"],
                            },
                            "level": {
                                "type": "string",
                                "enum": ["Fundamental", "Médio", "Superior", "unresolved"],
                            },
                            "difficulty": {
                                "type": "string",
                                "enum": ["Fácil", "Média", "Difícil", "unresolved"],
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence": {"type": "string", "maxLength": 160},
                        },
                    },
                }
            },
        }

    def classify(
        self,
        questions: list[QuestionRecord],
        taxonomy: EditorialTaxonomy,
        trace_path: Path,
    ) -> dict[str, tuple[TaxonomyPath | None, str | None, str | None, float, str]]:
        paths = taxonomy.candidate_paths()
        options = {path.path_id: path for path in paths}
        stable_ids = {
            question.source_stable_id or question_content_sha256(question): question
            for question in questions
        }
        allowed_by_id = {
            stable_id: {
                option_id
                for option_id, path in options.items()
                if path.catalog_id
                in taxonomy.relevant_catalog_ids(_classification_metadata(question))
                if (question.discipline is None or path.discipline == question.discipline)
                and (question.matter is None or path.matter == question.matter)
                and (question.subject is None or path.subject == question.subject)
            }
            for stable_id, question in stable_ids.items()
        }
        user_content = json.dumps(
            {
                "taxonomy_version": taxonomy.version,
                "options": [
                    {
                        "id": option_id,
                        "taxonomy_path_id": path.path_id,
                        "discipline": path.discipline,
                        "matter": path.matter,
                        "subject": path.subject,
                    }
                    for option_id, path in sorted(options.items())
                ],
                "questions": [
                    {
                        "stable_id": stable_id,
                        "statement": question.statement[:1_200],
                        "alternatives": [
                            item.text[:350] for item in question.alternatives
                        ],
                        "board": question.board,
                        "organization": question.organization,
                        "contest": question.concurso,
                        "year": question.year,
                        "role": question.role,
                        "current_classification": {
                            "discipline": question.discipline,
                            "matter": question.matter,
                            "subject": question.subject,
                            "level": question.level,
                            "difficulty": question.difficulty,
                        },
                        "allowed_option_ids": sorted(allowed_by_id[stable_id]),
                    }
                    for stable_id, question in stable_ids.items()
                ],
            },
            ensure_ascii=False,
        )
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": self._schema(sorted(options), sorted(stable_ids)),
            "options": {"temperature": 0, "num_predict": max(2_048, len(questions) * 192)},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classifique o tema de cada questão somente pelas opções fornecidas. "
                        "Não resolva a questão e não use a veracidade da resposta como evidência. "
                        "Use unresolved e confiança 0 quando o enunciado não sustentar um tópico "
                        "exato. Produza exatamente um item para cada stable_id recebido, sem "
                        "duplicar nem omitir IDs. Evidence deve ter no máximo 15 palavras e "
                        "citar somente indícios temáticos. Não invente termos. Responda apenas "
                        "o JSON compacto."
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "keep_alive": "15m",
        }
        self.calls += 1
        self.questions_sent += len(questions)
        started = time.perf_counter()
        raw: dict[str, Any] | None = None
        error: str | None = None
        for attempt in range(2):
            try:
                if self._request is not None:
                    raw = self._request(payload)
                else:
                    response = httpx.post(
                        f"{self.endpoint}/api/chat", json=payload, timeout=180.0
                    )
                    response.raise_for_status()
                    raw = response.json()
                break
            except (httpx.HTTPError, ValueError) as exc:
                error = str(exc)
                if attempt == 1:
                    self.failures += 1
        output_text = ""
        if raw is not None:
            message = raw.get("message", raw)
            output_text = str(message.get("content", "")) if isinstance(message, dict) else ""
        accepted: dict[
            str, tuple[TaxonomyPath | None, str | None, str | None, float, str]
        ] = {}
        try:
            decoded = json.loads(output_text) if output_text else {"items": []}
            items = decoded.get("items", [])
            if not isinstance(items, list):
                raise ValueError("items não é uma lista")
            known_ids = set(stable_ids)
            response_ids = [
                str(item.get("stable_id") or "")
                for item in items
                if isinstance(item, dict)
            ]
            if len(response_ids) != len(known_ids) or set(response_ids) != known_ids:
                raise ValueError(
                    "resposta não contém exatamente uma decisão por stable_id"
                )
            for item in items:
                if not isinstance(item, dict):
                    continue
                stable_id = str(item.get("stable_id") or "")
                option_id = str(item.get("option_id") or "unresolved")
                confidence = float(item.get("confidence", 0))
                evidence = str(item.get("evidence") or "").strip()
                if stable_id not in known_ids or not evidence:
                    continue
                if confidence < self.minimum_confidence or option_id == "unresolved":
                    continue
                path = options.get(option_id)
                if path is None or option_id not in allowed_by_id[stable_id]:
                    continue
                level = str(item.get("level") or "unresolved")
                difficulty = str(item.get("difficulty") or "unresolved")
                accepted[stable_id] = (
                    path,
                    level if level != "unresolved" else None,
                    difficulty if difficulty != "unresolved" else None,
                    confidence,
                    evidence,
                )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            error = str(exc)
            self.failures += 1
        self.accepted += len(accepted)
        self.unresolved += len(questions) - len(accepted)
        _append_json_line(
            trace_path,
            {
                "created_at": datetime.now(UTC).isoformat(),
                "model": self.model,
                "digest": self.digest,
                "prompt_version": CAMPAIGN_VERSION,
                "input_sha256": _canonical_sha256(json.loads(user_content)),
                "duration_ms": round((time.perf_counter() - started) * 1_000),
                "question_count": len(questions),
                "accepted_count": len(accepted),
                "response": output_text,
                "error": error,
            },
        )
        return accepted


def _iter_sessions(index: ConsolidatedReviewIndex) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for batch in index.batches:
        if batch.session_path is not None:
            result.append((batch.batch_id, Path(batch.session_path)))
    return result


def _structural_state(question: QuestionRecord) -> str:
    value = _note_value(question, "Estado estrutural:")
    return value if value in {"accepted", "quarantined", "rejected"} else "unknown"


def _visual_dependency(question: QuestionRecord) -> bool:
    text = " ".join([*question.review_notes, *question.editorial_blocks]).casefold()
    return "visual" in text


def _classification_audit(question: QuestionRecord) -> tuple[float | None, str | None]:
    note = next(
        (
            item
            for item in question.review_notes
            if item.startswith(CLASSIFICATION_NOTE_PREFIX)
        ),
        "",
    )
    confidence_match = re.search(r"confiança=([0-9.]+)", note)
    path_match = re.search(r"caminho=([^;]+)", note)
    return (
        float(confidence_match.group(1)) if confidence_match else None,
        (
            path_match.group(1)
            if path_match and path_match.group(1) != "nenhum"
            else None
        ),
    )


def _semantic_counts(
    sessions: list[tuple[str, Path]],
) -> tuple[int, int, int, dict[str, list[str]]]:
    fingerprints: dict[str, list[str]] = defaultdict(list)
    raw = 0
    for _batch_id, session_path in sessions:
        session = LocalReviewSession.model_validate(read_json(session_path))
        for question in session.batch.questions:
            raw += 1
            fingerprint = question_fingerprints(
                question.model_dump(mode="json")
            ).exact
            fingerprints[fingerprint].append(
                question.source_stable_id or question_content_sha256(question)
            )
    unique = len(fingerprints)
    return raw, unique, raw - unique, {
        key: values for key, values in fingerprints.items() if len(values) > 1
    }


def _sample(
    sessions: list[tuple[str, Path]], *, sample_size: int
) -> tuple[CampaignSample, list[CampaignSampleEntry]]:
    rows: list[tuple[str, QuestionRecord, str, str, str, str]] = []
    decisions: dict[str, str] = {}
    seen_fingerprints: set[str] = set()
    for batch_id, source_session_path in sessions:
        session = LocalReviewSession.model_validate(read_json(source_session_path))
        by_number = {item.question_number: item.status for item in session.decisions}
        for question in session.batch.questions:
            stable_id = question.source_stable_id or question_content_sha256(question)
            fingerprint = question_fingerprints(question.model_dump(mode="json")).exact
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            rows.append(
                (
                    stable_id,
                    question,
                    _classification_method(question),
                    batch_id,
                    _portable(source_session_path),
                    fingerprint,
                )
            )
            decisions[stable_id] = by_number[question.number]

    selected: dict[str, tuple[QuestionRecord, str, str, str, str]] = {}
    dimensions: dict[
        str, dict[str, list[tuple[str, QuestionRecord, str, str, str, str]]]
    ] = {
        key: defaultdict(list)
        for key in (
            "board",
            "organization",
            "year",
            "role",
            "format",
            "method",
            "state",
            "visual",
        )
    }
    for stable_id, question, method, batch_id, session_ref, fingerprint in rows:
        values = {
            "board": question.board or "(não informado)",
            "organization": question.organization or "(não informado)",
            "year": str(question.year or "(não informado)"),
            "format": "true_false" if len(question.alternatives) == 2 else "multiple_choice",
            "method": method,
            "state": _structural_state(question),
            "visual": "sim" if _visual_dependency(question) else "não",
            "role": question.role or "(não informado)",
        }
        for dimension, value in values.items():
            dimensions[dimension][value].append(
                (stable_id, question, method, batch_id, session_ref, fingerprint)
            )
    for groups in dimensions.values():
        for candidates in groups.values():
            choice = min(candidates, key=lambda item: _canonical_sha256(item[0]))
            selected.setdefault(choice[0], choice[1:])
    for stable_id, question, method, batch_id, session_ref, fingerprint in sorted(
        rows, key=lambda item: _canonical_sha256(item[0])
    ):
        if len(selected) >= min(sample_size, len(rows)):
            break
        selected.setdefault(
            stable_id, (question, method, batch_id, session_ref, fingerprint)
        )

    strata: Counter[str] = Counter()
    for question, method, _batch_id, _session_path, _fingerprint in selected.values():
        strata.update(
            [
                f"banca:{question.board or '(não informado)'}",
                f"ano:{question.year or '(não informado)'}",
                "formato:"
                + (
                    "certo_errado"
                    if len(question.alternatives) == 2
                    else "multipla_escolha"
                ),
                f"classificação:{method}",
                f"estado:{_structural_state(question)}",
                f"visual:{'sim' if _visual_dependency(question) else 'não'}",
            ]
        )
    human_decisions = sum(
        decisions[stable_id] in {"approved", "rejected"} for stable_id in selected
    )
    entries: list[CampaignSampleEntry] = []
    for stable_id, (
        question,
        method,
        batch_id,
        session_ref,
        fingerprint,
    ) in sorted(selected.items()):
        confidence, path_id = _classification_audit(question)
        entries.append(
            CampaignSampleEntry(
                stable_id=stable_id,
                semantic_fingerprint=fingerprint,
                batch_id=batch_id,
                batch_path=_portable(
                    Path(session_ref).parent.parent / "batches" / f"{batch_id}.json"
                ),
                session_path=session_ref,
                question_number=question.number,
                board=question.board,
                organization=question.organization,
                contest=question.concurso,
                year=question.year,
                role=question.role,
                question_format=(
                    "true_false" if len(question.alternatives) == 2 else "multiple_choice"
                ),
                structural_state=cast(
                    Literal["accepted", "quarantined", "rejected", "unknown"],
                    _structural_state(question),
                ),
                classification_method=method,
                classification_confidence=confidence,
                taxonomy_path_id=path_id,
                visual_dependency=_visual_dependency(question),
                source_pages=question.source_pages,
                exam_url=_note_value(question, "URL oficial da prova:"),
                answer_key_url=_note_value(question, "URL oficial do gabarito:"),
            )
        )
    sample = CampaignSample(
        size=len(selected),
        stable_ids=sorted(selected),
        strata=dict(sorted(strata.items())),
        human_decisions=human_decisions,
        structural_precision=None,
        answer_precision=None,
        classification_precision=None,
        duplicate_fingerprints=len(entries)
        - len({entry.semantic_fingerprint for entry in entries}),
        structurally_accepted=sum(
            entry.structural_state == "accepted" for entry in entries
        ),
        quarantined=sum(entry.structural_state == "quarantined" for entry in entries),
        visual_dependencies=sum(entry.visual_dependency for entry in entries),
    )
    return sample, entries


def _grouped(sessions: list[tuple[str, Path]]) -> dict[str, list[dict[str, Any]]]:
    dimensions = (
        "banca",
        "órgão",
        "concurso",
        "ano",
        "cargo",
        "disciplina",
        "matéria",
        "assunto",
        "nível",
        "dificuldade",
        "estado",
    )
    counters: dict[str, Counter[str]] = {key: Counter() for key in dimensions}
    for _batch_id, session_path in sessions:
        session = LocalReviewSession.model_validate(read_json(session_path))
        decision_by_number = {
            item.question_number: item.status for item in session.decisions
        }
        for question in session.batch.questions:
            values = {
                "banca": question.board,
                "órgão": question.organization,
                "concurso": question.concurso,
                "ano": str(question.year) if question.year else None,
                "cargo": question.role,
                "disciplina": question.discipline,
                "matéria": question.matter,
                "assunto": question.subject,
                "nível": question.level,
                "dificuldade": question.difficulty,
                "estado": decision_by_number[question.number],
            }
            for key, value in values.items():
                counters[key][value or "(não informado)"] += 1
    return {
        key: [
            {"value": value, "questions": count}
            for value, count in sorted(counter.items())
        ]
        for key, counter in counters.items()
    }


def _taxonomy_gaps(
    sessions: list[tuple[str, Path]], *, limit: int = 30
) -> list[dict[str, Any]]:
    groups: Counter[tuple[str, str, str, str]] = Counter()
    for _batch_id, session_path in sessions:
        session = LocalReviewSession.model_validate(read_json(session_path))
        for question in session.batch.questions:
            if all((question.discipline, question.matter, question.subject)):
                continue
            groups[
                (
                    question.board or "(não informado)",
                    question.concurso or "(não informado)",
                    str(question.year or "(não informado)"),
                    question.role or "(não informado)",
                )
            ] += 1
    return [
        {
            "board": board,
            "contest": contest,
            "year": year,
            "role": role,
            "questions": count,
        }
        for (board, contest, year, role), count in groups.most_common(limit)
    ]


def _report_counts(
    index: ConsolidatedReviewIndex, sessions: list[tuple[str, Path]]
) -> CampaignCounts:
    raw, unique, duplicate_occurrences, _duplicates = _semantic_counts(sessions)
    methods: Counter[str] = Counter()
    taxonomy_unresolved = 0
    field_complete: Counter[str] = Counter()
    human_decisions = 0
    ready = 0
    waiting = 0
    for _batch_id, session_path in sessions:
        session = LocalReviewSession.model_validate(read_json(session_path))
        verify_review_session(session)
        decisions = {item.question_number: item for item in session.decisions}
        for question in session.batch.questions:
            methods[_classification_method(question)] += 1
            taxonomy_unresolved += int(
                any(
                    value is None
                    for value in (question.discipline, question.matter, question.subject)
                )
            )
            for field_name in ("discipline", "matter", "subject", "level", "difficulty"):
                field_complete[field_name] += int(bool(getattr(question, field_name)))
            decision = decisions[question.number]
            human_decisions += int(decision.status != "pending")
            waiting += int(decision.status in {"pending", "deferred"})
            ready += int(
                decision.status == "approved"
                and decision.content_sha256 == question_content_sha256(question)
                and not validate_editorial_question(question)
            )
    return CampaignCounts(
        raw_questions=raw,
        unique_questions=unique,
        duplicate_occurrences=duplicate_occurrences,
        structurally_accepted=index.total.structurally_accepted,
        quarantined=index.total.quarantined,
        rejected=index.total.rejected,
        deterministic_classifications=methods["deterministic"],
        qwen_classifications=methods["qwen"],
        taxonomy_complete=raw - taxonomy_unresolved,
        taxonomy_coverage=(raw - taxonomy_unresolved) / raw if raw else 0,
        taxonomy_field_coverage={
            field_name: field_complete[field_name] / raw if raw else 0
            for field_name in ("discipline", "matter", "subject", "level", "difficulty")
        },
        taxonomy_unresolved=taxonomy_unresolved,
        human_decisions=human_decisions,
        waiting_human_review=waiting,
        ready_for_export=ready,
    )


def _write_markdown(report: EditorialCampaignReport, path: Path) -> None:
    counts = report.counts
    qwen = report.qwen
    sample = report.sample
    lines = [
        "# Campanha editorial PF, BB e CORE-PI",
        "",
        f"- Campanha: `{report.campaign_id}`",
        f"- Versão: `{report.campaign_version}`",
        f"- Estado de publicação: `{report.publication_status}`",
        f"- Inventário: `{report.corpus_index_path}`",
        f"- Hash: `{report.content_sha256}`",
        "",
        "## Totais",
        "",
        "| Indicador | Total |",
        "|---|---:|",
        f"| Ocorrências brutas | {counts.raw_questions} |",
        f"| Questões únicas por conteúdo | {counts.unique_questions} |",
        f"| Ocorrências duplicadas | {counts.duplicate_occurrences} |",
        f"| Estruturalmente aceitas | {counts.structurally_accepted} |",
        f"| Em quarentena | {counts.quarantined} |",
        f"| Classificadas por regra | {counts.deterministic_classifications} |",
        f"| Sugeridas pelo Qwen | {counts.qwen_classifications} |",
        f"| Taxonomia completa | {counts.taxonomy_complete} |",
        f"| Cobertura taxonômica | {counts.taxonomy_coverage:.1%} |",
        f"| Taxonomia não resolvida | {counts.taxonomy_unresolved} |",
        f"| Decisões humanas | {counts.human_decisions} |",
        f"| Aguardando revisão humana | {counts.waiting_human_review} |",
        f"| Aptas para exportação | {counts.ready_for_export} |",
        "",
        "## Cobertura por campo",
        "",
        "| Campo | Cobertura |",
        "|---|---:|",
        *[
            f"| {field_name} | {coverage:.1%} |"
            for field_name, coverage in counts.taxonomy_field_coverage.items()
        ],
        "",
        "## Qwen local",
        "",
        "| Indicador | Resultado |",
        "|---|---:|",
        f"| Habilitado | {'sim' if qwen.enabled else 'não'} |",
        f"| Disponível | {'sim' if qwen.available else 'não'} |",
        f"| Modelo | {qwen.model} |",
        f"| Digest | {qwen.digest or 'indisponível'} |",
        f"| Chamadas | {qwen.calls} |",
        f"| Questões enviadas | {qwen.questions_sent} |",
        f"| Sugestões aceitas para revisão | {qwen.accepted_suggestions} |",
        f"| Sem sugestão segura | {qwen.unresolved_suggestions} |",
        f"| Falhas | {qwen.failures} |",
        "",
        "O Qwen não aprovou nem publicou questões. Todas as sugestões continuam "
        "pendentes de decisão humana.",
        "",
        "## Amostra estratificada",
        "",
        f"- Itens: {sample.size}",
        f"- Duplicatas semânticas na amostra: {sample.duplicate_fingerprints}",
        f"- Estruturalmente aceitas: {sample.structurally_accepted}",
        f"- Em quarentena estrutural: {sample.quarantined}",
        f"- Dependentes de elemento visual: {sample.visual_dependencies}",
        f"- Decisões humanas registradas: {sample.human_decisions}",
        "- Precisão estrutural: não medida sem revisão humana.",
        "- Precisão do gabarito: não medida sem revisão humana.",
        "- Precisão da classificação: não medida sem revisão humana.",
        "",
        "## Distribuição para revisão",
        "",
    ]
    for dimension in (
        "banca",
        "órgão",
        "concurso",
        "ano",
        "cargo",
        "disciplina",
        "matéria",
        "assunto",
        "nível",
        "dificuldade",
        "estado",
    ):
        lines.extend(
            [
                f"### {dimension.capitalize()}",
                "",
                "| Valor | Questões |",
                "|---|---:|",
                *[
                    f"| {item['value']} | {item['questions']} |"
                    for item in sorted(
                        report.grouped[dimension],
                        key=lambda item: (-int(item["questions"]), str(item["value"])),
                    )
                ],
                "",
            ]
        )
    if report.taxonomy_gaps:
        lines.extend(
            [
                "## Maiores lacunas taxonômicas",
                "",
                "| Banca | Concurso | Ano | Cargo/bloco | Questões |",
                "|---|---|---:|---|---:|",
                *[
                    "| {board} | {contest} | {year} | {role} | {questions} |".format(
                        **item
                    )
                    for item in report.taxonomy_gaps
                ],
                "",
            ]
        )
    lines.extend(
        [
            "## Limitações",
            "",
            *[f"- {item}" for item in report.limitations],
            "",
            "Nenhum dado foi enviado ao KAD ou ao Supabase.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_editorial_campaign(
    spec_path: Path,
    output_dir: Path,
    *,
    enable_qwen: bool = True,
    limit: int | None = None,
    report_json_path: Path | None = None,
    report_markdown_path: Path | None = None,
    qwen_request: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[EditorialCampaignReport, Path]:
    if limit is not None and limit < 1:
        raise ValueError("limit precisa ser positivo")
    started = time.perf_counter()
    spec_path = spec_path.resolve()
    output_dir = output_dir.resolve()
    spec = EditorialCampaignSpec.model_validate(read_json(spec_path))
    corpus_spec = _resolve(spec_path.parent, spec.corpus_spec)
    inventory, inventory_path = build_consolidated_review(corpus_spec, output_dir)
    sessions = _iter_sessions(inventory)
    taxonomy = EditorialTaxonomy.load_default()
    local = LocalRuleClassifier(taxonomy)
    trace_path = output_dir / "classification" / "qwen-traces.jsonl"
    qwen = OllamaTaxonomyClassifier(
        endpoint=spec.qwen_endpoint,
        model=spec.qwen_model,
        minimum_confidence=spec.minimum_qwen_confidence,
        request=qwen_request,
    )
    qwen_available = enable_qwen and qwen.preflight()
    processed = 0

    for _batch_id, session_path in sessions:
        session = LocalReviewSession.model_validate(read_json(session_path))
        decisions = {item.question_number: item for item in session.decisions}
        pending: list[QuestionRecord] = []
        for question in session.batch.questions:
            if limit is not None and processed >= limit:
                break
            decision = decisions[question.number]
            if decision.status != "pending":
                continue
            method = _classification_method(question)
            if method in {"qwen", "qwen_unresolved"} or all(
                _classification_fields(question)
            ):
                continue
            result = local.classify_many(
                [_classification_request(question)], _classification_metadata(question)
            )[0]
            path, level, difficulty, confidence, evidence = _local_path(result, taxonomy)
            updated = _apply_classification(
                question,
                path=path,
                level=level or question.level,
                difficulty=difficulty or question.difficulty,
                method="deterministic" if path is not None else "unresolved",
                confidence=confidence,
                taxonomy_version=taxonomy.version,
                evidence=evidence,
            )
            session = update_review_question(session, question.number, updated)
            if not all(_classification_fields(updated)):
                pending.append(updated)
            processed += 1
        if qwen_available:
            for offset in range(0, len(pending), spec.qwen_batch_size):
                chunk = pending[offset : offset + spec.qwen_batch_size]
                suggestions = qwen.classify(chunk, taxonomy, trace_path)
                for question in chunk:
                    stable_id = question.source_stable_id or question_content_sha256(question)
                    suggestion = suggestions.get(stable_id)
                    if suggestion is None:
                        updated = _apply_classification(
                            question,
                            path=None,
                            level=question.level,
                            difficulty=question.difficulty,
                            method="qwen_unresolved",
                            confidence=0,
                            taxonomy_version=taxonomy.version,
                            evidence=None,
                        )
                        session = update_review_question(
                            session, question.number, updated
                        )
                        continue
                    path, level, difficulty, confidence, evidence = suggestion
                    updated = _apply_classification(
                        question,
                        path=path,
                        level=level,
                        difficulty=difficulty,
                        method="qwen",
                        confidence=confidence,
                        taxonomy_version=taxonomy.version,
                        evidence=evidence,
                    )
                    session = update_review_question(session, question.number, updated)
        save_review_session(session, session_path)
        if limit is not None and processed >= limit:
            break

    # Refresh the derived index after checkpointing classifications so that a resumed
    # execution reports the same state as the run that completed the work.
    inventory, inventory_path = build_consolidated_review(corpus_spec, output_dir)
    sessions = _iter_sessions(inventory)
    counts = _report_counts(inventory, sessions)
    sample, sample_entries = _sample(sessions, sample_size=spec.sample_size)
    next_batch = next(
        (
            batch_id
            for batch_id, session_path in sessions
            if any(
                item.status in {"pending", "deferred"}
                for item in LocalReviewSession.model_validate(
                    read_json(session_path)
                ).decisions
            )
        ),
        None,
    )
    limitations = []
    if sample.human_decisions == 0:
        limitations.append(
            "A amostra ainda não recebeu revisão humana; as metas de precisão "
            "não podem ser alegadas."
        )
    if counts.ready_for_export == 0:
        limitations.append(
            "Nenhuma questão tem aprovação humana válida; a exportação real permanece bloqueada."
        )
    if enable_qwen and not qwen_available:
        limitations.append(
            "O Ollama ou o modelo qwen3:8b estava indisponível; regras locais e "
            "fila continuaram funcionando."
        )
    if counts.taxonomy_unresolved:
        limitations.append(
            f"{counts.taxonomy_unresolved} questões não receberam classificação "
            "completa na taxonomia atual."
        )
    if counts.taxonomy_coverage < 0.9:
        limitations.append(
            "A cobertura taxonômica ficou abaixo da meta de 90%; o acervo não pode "
            "ser declarado pronto para importação."
        )
    if qwen.failures:
        limitations.append(
            f"O contrato local do Qwen rejeitou {qwen.failures} resposta(s) inválida(s); "
            "nenhuma delas alterou decisões humanas ou foi publicada."
        )
    fingerprint = {
        "campaign_id": spec.campaign_id,
        "corpus": inventory.content_sha256,
        "counts": counts.model_dump(mode="json"),
        "sample_ids": sample.stable_ids,
        "qwen": {
            "model": spec.qwen_model,
            "digest": qwen.digest,
            "classified_questions": counts.qwen_classifications,
        },
    }
    report = EditorialCampaignReport(
        campaign_id=spec.campaign_id,
        created_at=datetime.now(UTC),
        corpus_index_path=_portable(inventory_path),
        counts=counts,
        qwen=CampaignQwenStatus(
            enabled=enable_qwen,
            available=qwen_available,
            endpoint=spec.qwen_endpoint,
            model=spec.qwen_model,
            digest=qwen.digest,
            calls=qwen.calls,
            questions_sent=qwen.questions_sent,
            accepted_suggestions=qwen.accepted,
            unresolved_suggestions=qwen.unresolved,
            failures=qwen.failures,
        ),
        sample=sample,
        audit_sample=sample_entries,
        grouped=_grouped(sessions),
        taxonomy_gaps=_taxonomy_gaps(sessions),
        quarantine_reasons=inventory.quarantine_reasons,
        next_batch_id=next_batch,
        content_sha256=_canonical_sha256(fingerprint),
        duration_seconds=round(time.perf_counter() - started, 3),
        limitations=limitations,
    )
    report_path = output_dir / "campaign-report.json"
    write_json(report_path, report.model_dump(mode="json"))
    write_json(output_dir / "campaign-sample.json", sample.model_dump(mode="json"))
    write_json(
        output_dir / "campaign-audit-sample.json",
        {
            "schema_version": "1.0",
            "campaign_id": spec.campaign_id,
            "publication_status": "draft",
            "human_review_required": True,
            "questions": [item.model_dump(mode="json") for item in sample_entries],
        },
    )
    _raw, _unique, _occurrences, duplicates = _semantic_counts(sessions)
    write_json(output_dir / "semantic-duplicates.json", duplicates)
    if report_json_path is not None:
        write_json(report_json_path, report.model_dump(mode="json"))
    if report_markdown_path is not None:
        _write_markdown(report, report_markdown_path)
    return report, report_path


def export_campaign_dry_run(index_path: Path, output_dir: Path) -> dict[str, Any]:
    index = ConsolidatedReviewIndex.model_validate(read_json(index_path))
    records: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()

    for batch in index.batches:
        if batch.session_path is None:
            continue
        session = LocalReviewSession.model_validate(read_json(Path(batch.session_path)))
        verify_review_session(session)
        decisions = {item.question_number: item for item in session.decisions}
        for question in session.batch.questions:
            decision = decisions[question.number]
            if decision.status != "approved":
                continue
            issues = validate_editorial_question(question)
            stable_id = stable_question_id(session.batch, question)
            if issues:
                exceptions.append(
                    {"stableId": stable_id, "issues": issues, "questionNumber": question.number}
                )
                continue
            record = build_editorial_record(session.batch, question)
            payload = record.model_dump(mode="json", by_alias=True, exclude_none=True)
            duplicate_issues = []
            if record.data.id in seen_ids:
                duplicate_issues.append("ID estável duplicado na campanha")
            if record.source.fingerprint in seen_fingerprints:
                duplicate_issues.append("identidade semântica duplicada na campanha")
            if duplicate_issues:
                exceptions.append(
                    {
                        "stableId": stable_id,
                        "issues": duplicate_issues,
                        "questionNumber": question.number,
                    }
                )
                continue
            seen_ids.add(record.data.id)
            seen_fingerprints.add(record.source.fingerprint)
            records.append(payload)
            lineage.append(
                {
                    "question_id": record.data.id,
                    "exam_url": session.batch.source_document.resolved_url,
                    "exam_sha256": session.batch.source_document.sha256,
                    "answer_key_url": (
                        session.batch.answer_key_document.resolved_url
                        if session.batch.answer_key_document is not None
                        else None
                    ),
                    "answer_key_sha256": (
                        session.batch.answer_key_document.sha256
                        if session.batch.answer_key_document is not None
                        else None
                    ),
                    "source_pages": question.source_pages,
                }
            )
    if not records:
        raise ValueError("nenhuma questão possui aprovação humana válida para exportação")

    output_dir.mkdir(parents=True, exist_ok=True)
    questions_path = output_dir / "questoes.jsonl"
    exceptions_path = output_dir / "excecoes.jsonl"
    lineage_path = output_dir / "linhagem.jsonl"
    write_json_lines(questions_path, records)
    write_json_lines(exceptions_path, exceptions)
    write_json_lines(lineage_path, lineage)
    files = []
    for path in (questions_path, exceptions_path, lineage_path):
        files.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "publicationStatus": "draft",
        "created_at": datetime.now(UTC).isoformat(),
        "questions": len(records),
        "exceptions": len(exceptions),
        "files": files,
        "content_sha256": _canonical_sha256(
            {"questions": records, "exceptions": exceptions, "lineage": lineage}
        ),
    }
    write_json(output_dir / "manifesto.json", manifest)
    return manifest
