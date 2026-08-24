from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

from .canonical_ai_providers import (
    canonical_ai_messages,
    create_canonical_ai_provider,
)
from .canonical_classification import (
    CANONICAL_ENRICHMENT_PROMPT_VERSION,
    CLASSIFICATION_FIELDS,
    CanonicalAIProvider,
    CanonicalAIRequest,
    CanonicalClassificationError,
    _validate_ai_response,
)
from .editorial_taxonomy import (
    EditorialTaxonomy,
    normalize_taxonomy_text,
)
from .json_utils import read_json, write_json
from .models import QuestionRecord
from .question_equivalence import question_fingerprints
from .semantic_identity import stable_sha256

BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_ALGORITHM_VERSION = "canonical-ai-benchmark-v1"
DEFAULT_SAMPLE_SIZE = 200
DEFAULT_SEED = 20260824
PILOT_SIZE = 10
PROVIDERS = ("gemini", "qwen", "deepseek")
REFERENCE_KIND = "official_structure_reference"

BenchmarkPhase = Literal["pilot", "full"]
ProviderFactory = Callable[[str, str], CanonicalAIProvider]

# These are exact headings observed in the official RFB22 documents. A loose token
# match is deliberately insufficient because answer text can contain the same words.
_RFB22_OFFICIAL_HEADINGS = frozenset(
    normalize_taxonomy_text(value)
    for value in (
        "Administração Aduaneira e Modelo de Controle - MCA",
        "Controle de Carga, Fluxo de Informações e Gestão Coordenada de Fronteiras - CCA",
        "Despacho Aduaneiro Operacional - DDA",
        "Gestão do Crédito Tributário",
        "Arrecadação, Cobrança e Controle do Crédito Tributário",
        "Atendimento ao Contribuinte",
        "Estado, Sociedade e Transformação Digital",
        "Tecnologia da Informação e Fluência em Dados II",
        "Sigilo Fiscal",
        "Sistema Público de Escriturações Digitais - SPED",
        "Tributação e Contencioso",
    )
)

OFFICIAL_PRICE_SNAPSHOT: dict[str, Any] = {
    "checkedAt": "2026-08-24",
    "currency": "USD",
    "unit": "1M tokens",
    "providers": {
        "gemini": {
            "model": "gemini-3.7-flash",
            "inputUsd": 0.75,
            "outputUsd": 3.75,
            "basis": "paid tier introductory price through 2026-12-31",
            "sourceUrl": "https://ai.google.dev/gemini-api/docs/pricing",
        },
        "qwen": {
            "model": "qwen3.7-plus",
            "inputUsd": 0.40,
            "outputUsd": 1.60,
            "basis": "international list price, non-thinking, request up to 256K tokens",
            "sourceUrl": "https://www.alibabacloud.com/help/en/model-studio/model-pricing",
        },
        "deepseek": {
            "model": "deepseek-v4-pro",
            "inputUsd": 0.435,
            "outputUsd": 0.87,
            "basis": "cache-miss input and non-thinking output",
            "sourceUrl": "https://api-docs.deepseek.com/quick_start/pricing/",
        },
    },
    "usdBrl": {
        "rate": 5.1625,
        "rateDate": "2026-08-21",
        "basis": "PTAX venda do último dia útil disponível",
        "sourceUrl": "https://www.bcb.gov.br/",
    },
}


@dataclass(frozen=True)
class ReferenceCandidate:
    source_question_id: str
    content_fingerprint: str
    question: QuestionRecord
    expected: dict[str, str]
    contest: str
    catalog_id: str
    heading: str
    document_sha256: str

    @property
    def stratum(self) -> tuple[str, str, str, str, str]:
        return (
            self.expected["discipline"],
            self.expected["matter"],
            self.expected["subject"],
            self.expected["level"],
            self.contest,
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _classification_value(
    classification: Mapping[str, Any], key: str
) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:
    raw = classification.get(key)
    if not isinstance(raw, Mapping):
        return None, None, None, ()
    provenance = raw.get("provenance")
    return (
        str(raw["value"]) if raw.get("value") is not None else None,
        str(raw["source"]) if raw.get("source") is not None else None,
        str(raw["evidence"]) if raw.get("evidence") is not None else None,
        tuple(str(item) for item in provenance)
        if isinstance(provenance, list)
        else (),
    )


def _official_document(metadata: Mapping[str, Any], sha256: str | None) -> bool:
    if not sha256 or len(sha256) != 64:
        return False
    url = str(metadata.get("canonical_url") or metadata.get("source_url") or "")
    host = (urlparse(url).hostname or "").casefold()
    return host == "conhecimento.fgv.br"


def _reference_candidate(
    row: sqlite3.Row,
    *,
    taxonomy: EditorialTaxonomy,
) -> ReferenceCandidate | None:
    try:
        classification = cast(dict[str, Any], json.loads(str(row["classification_json"])))
        metadata = cast(dict[str, Any], json.loads(str(row["metadata_json"])))
        payload = cast(dict[str, Any], json.loads(str(row["payload_json"])))
        question = QuestionRecord.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    discipline, discipline_source, evidence, provenance = _classification_value(
        classification, "discipline"
    )
    matter, matter_source, matter_evidence, matter_provenance = _classification_value(
        classification, "subject"
    )
    subject, subject_source, subject_evidence, subject_provenance = _classification_value(
        classification, "topic"
    )
    level, level_source, level_evidence, _ = _classification_value(classification, "level")
    contest, _, _, _ = _classification_value(classification, "concurso")
    if not all((discipline, matter, subject, level, contest, evidence)):
        return None
    if (discipline_source, matter_source, subject_source) != (
        "section_title",
        "section_title",
        "section_title",
    ):
        return None
    if level_source != "official_contest_requirement":
        return None
    if evidence != matter_evidence or evidence != subject_evidence:
        return None
    if normalize_taxonomy_text(cast(str, evidence)) not in _RFB22_OFFICIAL_HEADINGS:
        return None
    if "edital oficial" not in normalize_taxonomy_text(level_evidence or ""):
        return None
    if not _official_document(metadata, cast(str | None, row["sha256"])):
        return None
    if not all("conhecimento.fgv.br" in value for value in provenance[1:]):
        return None
    if provenance != matter_provenance or provenance != subject_provenance:
        return None

    catalog_ids = tuple(value for value in provenance if value in taxonomy.catalog_ids)
    if catalog_ids != ("fgv-rfb22",):
        return None
    matched = taxonomy.match_section(cast(str, evidence), catalog_ids=catalog_ids)
    expected_path = (discipline, matter, subject)
    if matched is None or (matched.discipline, matched.matter, matched.subject) != expected_path:
        return None
    for field, value in zip(("discipline", "matter", "subject"), expected_path, strict=True):
        try:
            taxonomy.ensure_known(cast(Any, field), cast(str, value))
        except ValueError:
            return None

    fingerprints = question_fingerprints(payload)
    return ReferenceCandidate(
        source_question_id=str(row["id"]),
        content_fingerprint=fingerprints.invariant,
        question=question,
        expected={
            "discipline": cast(str, discipline),
            "matter": cast(str, matter),
            "subject": cast(str, subject),
            "level": cast(str, level),
        },
        contest=cast(str, contest),
        catalog_id="fgv-rfb22",
        heading=cast(str, evidence),
        document_sha256=str(row["sha256"]),
    )


def load_official_structure_references(
    connection: sqlite3.Connection,
    *,
    taxonomy: EditorialTaxonomy | None = None,
) -> tuple[list[ReferenceCandidate], dict[str, int]]:
    active_taxonomy = taxonomy or EditorialTaxonomy.load_default()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT q.id,q.payload_json,q.classification_json,d.metadata_json,d.sha256 "
        "FROM questions q JOIN documents d ON d.id=q.document_id ORDER BY q.id"
    ).fetchall()
    accepted_by_fingerprint: dict[str, ReferenceCandidate] = {}
    rejected = 0
    duplicates = 0
    for row in rows:
        candidate = _reference_candidate(row, taxonomy=active_taxonomy)
        if candidate is None:
            rejected += 1
            continue
        if candidate.content_fingerprint in accepted_by_fingerprint:
            duplicates += 1
            continue
        accepted_by_fingerprint[candidate.content_fingerprint] = candidate
    return list(accepted_by_fingerprint.values()), {
        "examined": len(rows),
        "accepted": len(accepted_by_fingerprint),
        "rejected": rejected,
        "duplicateOccurrences": duplicates,
    }


def observed_missing_patterns(connection: sqlite3.Connection) -> Counter[tuple[str, ...]]:
    patterns: Counter[tuple[str, ...]] = Counter()
    rows = connection.execute("SELECT classification_json FROM questions").fetchall()
    mapping = {
        "discipline": "discipline",
        "matter": "subject",
        "subject": "topic",
        "level": "level",
    }
    for row in rows:
        try:
            classification = cast(dict[str, Any], json.loads(str(row[0])))
        except (json.JSONDecodeError, TypeError):
            continue
        missing = tuple(
            field
            for field in CLASSIFICATION_FIELDS
            if _classification_value(classification, mapping[field])[0] is None
        )
        patterns[missing] += 1
    return patterns


def _allocate_strata(
    groups: Mapping[tuple[str, ...], Sequence[ReferenceCandidate]], sample_size: int
) -> dict[tuple[str, ...], int]:
    total = sum(len(items) for items in groups.values())
    if total < sample_size:
        raise CanonicalClassificationError(
            f"referências oficiais insuficientes: {total} disponíveis, {sample_size} exigidas"
        )
    exact = {key: sample_size * len(items) / total for key, items in groups.items()}
    allocation = {key: math.floor(value) for key, value in exact.items()}
    remaining = sample_size - sum(allocation.values())
    ranked = sorted(
        groups,
        key=lambda key: (exact[key] - allocation[key], len(groups[key]), key),
        reverse=True,
    )
    for key in ranked[:remaining]:
        allocation[key] += 1
    return allocation


def select_reference_sample(
    candidates: Sequence[ReferenceCandidate],
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
) -> list[ReferenceCandidate]:
    groups: dict[tuple[str, ...], list[ReferenceCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.stratum].append(candidate)
    allocation = _allocate_strata(groups, sample_size)
    selected: list[ReferenceCandidate] = []
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda item: item.content_fingerprint)
        random.Random(f"{seed}:{'|'.join(key)}").shuffle(items)
        selected.extend(items[: allocation[key]])
    selected.sort(
        key=lambda item: stable_sha256(
            {"seed": seed, "contentFingerprint": item.content_fingerprint}
        )
    )
    return selected


def benchmark_masks(sample_size: int, *, seed: int) -> list[tuple[str, ...]]:
    primary = ("matter", "subject")
    diagnostics = (
        ("subject",),
        ("discipline", "matter", "subject"),
        ("level",),
        tuple(CLASSIFICATION_FIELDS),
    )
    diagnostic_count = sample_size // 20
    masks: list[tuple[str, ...]] = [primary] * (
        sample_size - diagnostic_count * len(diagnostics)
    )
    for pattern in diagnostics:
        masks.extend([pattern] * diagnostic_count)
    random.Random(f"{seed}:missing-fields").shuffle(masks)
    return masks


def assign_benchmark_masks(
    sample: Sequence[ReferenceCandidate],
    masks: Sequence[tuple[str, ...]],
    *,
    seed: int,
) -> list[tuple[str, ...]]:
    if len(sample) != len(masks):
        raise ValueError("a quantidade de máscaras deve ser igual à amostra")
    available = set(range(len(sample)))
    assignments: dict[int, tuple[str, ...]] = {}
    ordered_masks = sorted(
        enumerate(masks),
        key=lambda item: (len(item[1]), item[1], item[0]),
    )
    for mask_index, mask in ordered_masks:
        compatible = []
        for candidate_index in available:
            expected = sample[candidate_index].expected
            known_values = {
                value for field, value in expected.items() if field not in mask
            }
            if any(expected[field] in known_values for field in mask):
                continue
            compatible.append(candidate_index)
        if not compatible:
            raise CanonicalClassificationError(
                "não foi possível ocultar campos sem vazamento de rótulo"
            )
        compatible.sort(
            key=lambda index: stable_sha256(
                {
                    "seed": seed,
                    "maskIndex": mask_index,
                    "fingerprint": sample[index].content_fingerprint,
                }
            )
        )
        selected_index = compatible[0]
        assignments[selected_index] = mask
        available.remove(selected_index)
    return [assignments[index] for index in range(len(sample))]


def _taxonomy_options(
    taxonomy: EditorialTaxonomy,
    *,
    catalog_id: str,
    known: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    paths = taxonomy.candidate_paths(
        catalog_ids=(catalog_id,), discipline=known.get("discipline")
    )
    options: list[dict[str, str]] = []
    for path in paths:
        if known.get("matter") and path.matter != known["matter"]:
            continue
        if known.get("subject") and path.subject != known["subject"]:
            continue
        options.append(
            {
                "discipline": path.discipline,
                "matter": str(path.matter),
                "subject": str(path.subject),
            }
        )
    return tuple(options)


def _request_for(
    candidate: ReferenceCandidate,
    hidden_fields: tuple[str, ...],
    *,
    taxonomy: EditorialTaxonomy,
) -> CanonicalAIRequest:
    known = {
        field: value
        for field, value in candidate.expected.items()
        if field not in hidden_fields
    }
    request = CanonicalAIRequest(
        canonical_question_id=candidate.source_question_id,
        content_fingerprint=candidate.content_fingerprint,
        requested_fields=hidden_fields,
        statement=candidate.question.statement,
        alternatives=tuple(item.text for item in candidate.question.alternatives),
        known_fields=known,
        taxonomy_version=taxonomy.version,
        taxonomy_options=_taxonomy_options(
            taxonomy, catalog_id=candidate.catalog_id, known=known
        ),
    )
    leaked = {
        field
        for field in hidden_fields
        if field in request.known_fields
        or candidate.expected[field] in request.known_fields.values()
    }
    if leaked:
        raise CanonicalClassificationError(
            "campos ocultados vazaram para metadados conhecidos: "
            + ", ".join(sorted(leaked))
        )
    return request


def _estimated_tokens(text: str) -> int:
    # A conservative language-agnostic estimate for preflight budgeting. Providers
    # remain the source of truth for actual token usage in paid phases.
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


def _request_estimates(
    request: CanonicalAIRequest, expected: Mapping[str, str]
) -> tuple[int, int, int]:
    messages = canonical_ai_messages(request)
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    simulated_response = {
        "suggestions": [
            {
                "field": field,
                "value": expected[field],
                "confidence": 0.90,
                "evidence": "evidência textual suficiente para justificar a classificação",
            }
            for field in request.requested_fields
        ]
    }
    output = json.dumps(simulated_response, ensure_ascii=False, separators=(",", ":"))
    return len(serialized.encode("utf-8")), _estimated_tokens(serialized), _estimated_tokens(output)


def _price_for_tokens(price: Mapping[str, Any], input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * float(price["inputUsd"])
        + output_tokens * float(price["outputUsd"])
    ) / 1_000_000


def _distribution(items: Iterable[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        if field == "contest":
            counts[str(item["contest"])] += 1
        else:
            expected = cast(Mapping[str, str], item["expected"])
            counts[expected[field]] += 1
    return dict(sorted(counts.items()))


def prepare_canonical_ai_benchmark(
    database_path: Path,
    *,
    local_bundle_path: Path,
    manifest_path: Path,
    report_path: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    price_snapshot: Mapping[str, Any] = OFFICIAL_PRICE_SNAPSHOT,
) -> dict[str, Any]:
    if sample_size < PILOT_SIZE:
        raise ValueError(f"sample_size deve ser pelo menos {PILOT_SIZE}")
    taxonomy = EditorialTaxonomy.load_default()
    with closing(sqlite3.connect(database_path)) as connection:
        candidates, audit = load_official_structure_references(
            connection, taxonomy=taxonomy
        )
        missing_patterns = observed_missing_patterns(connection)
    sample = select_reference_sample(candidates, sample_size=sample_size, seed=seed)
    masks = assign_benchmark_masks(
        sample,
        benchmark_masks(sample_size, seed=seed),
        seed=seed,
    )

    local_items: list[dict[str, Any]] = []
    manifest_items: list[dict[str, Any]] = []
    for candidate, hidden_fields in zip(sample, masks, strict=True):
        request = _request_for(candidate, hidden_fields, taxonomy=taxonomy)
        payload_bytes, input_tokens, output_tokens = _request_estimates(
            request, candidate.expected
        )
        reference_id = stable_sha256(
            {
                "kind": REFERENCE_KIND,
                "sourceQuestionId": candidate.source_question_id,
                "contentFingerprint": candidate.content_fingerprint,
            }
        )
        safe_item = {
            "referenceQuestionId": reference_id,
            "sourceQuestionId": candidate.source_question_id,
            "contentFingerprint": candidate.content_fingerprint,
            "hiddenFields": list(hidden_fields),
            "expected": candidate.expected,
            "contest": candidate.contest,
            "taxonomyVersion": taxonomy.version,
            "referenceKind": REFERENCE_KIND,
        }
        manifest_items.append(safe_item)
        local_items.append(
            {
                **safe_item,
                "request": request.safe_payload(),
                "estimatedPayloadBytes": payload_bytes,
                "estimatedInputTokens": input_tokens,
                "estimatedOutputTokens": output_tokens,
            }
        )

    sample_fingerprint = stable_sha256(manifest_items)
    benchmark_id = f"canonical-ai-{sample_fingerprint[:16]}"
    created_at = _now()
    manifest = {
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "algorithmVersion": BENCHMARK_ALGORITHM_VERSION,
        "benchmarkId": benchmark_id,
        "sampleFingerprint": sample_fingerprint,
        "referenceKind": REFERENCE_KIND,
        "referenceLimitation": (
            "Referência estrutural oficial validada automaticamente; não equivale a revisão humana."
        ),
        "taxonomyVersion": taxonomy.version,
        "promptVersion": CANONICAL_ENRICHMENT_PROMPT_VERSION,
        "seed": seed,
        "createdAt": created_at,
        "items": manifest_items,
    }
    local_bundle = {
        "manifest": manifest,
        "priceSnapshot": dict(price_snapshot),
        "items": local_items,
    }

    total_input = sum(int(item["estimatedInputTokens"]) for item in local_items)
    total_output = sum(int(item["estimatedOutputTokens"]) for item in local_items)
    provider_costs: dict[str, dict[str, Any]] = {}
    providers = cast(Mapping[str, Mapping[str, Any]], price_snapshot["providers"])
    usd_brl = float(cast(Mapping[str, Any], price_snapshot["usdBrl"])["rate"])
    for provider in PROVIDERS:
        price = providers[provider]
        cost_usd = _price_for_tokens(price, total_input, total_output)
        provider_costs[provider] = {
            "model": price["model"],
            "plannedCalls": sample_size,
            "estimatedInputTokens": total_input,
            "estimatedOutputTokens": total_output,
            "estimatedCostUsd": round(cost_usd, 6),
            "estimatedCostBrl": round(cost_usd * usd_brl, 4),
            "pilotEstimatedCostUsd": round(
                _price_for_tokens(
                    price,
                    sum(
                        int(item["estimatedInputTokens"])
                        for item in local_items[:PILOT_SIZE]
                    ),
                    sum(
                        int(item["estimatedOutputTokens"])
                        for item in local_items[:PILOT_SIZE]
                    ),
                ),
                6,
            ),
        }
    total_cost_usd = sum(item["estimatedCostUsd"] for item in provider_costs.values())
    report = {
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "phase": "offline-preflight",
        "status": "awaiting-paid-pilot-approval",
        "benchmarkId": benchmark_id,
        "sampleFingerprint": sample_fingerprint,
        "createdAt": created_at,
        "sample": {
            "requested": sample_size,
            "availableOfficialStructureReferences": audit["accepted"],
            "selected": len(local_items),
            "referenceAudit": audit,
            "referenceKind": REFERENCE_KIND,
            "distributions": {
                field: _distribution(local_items, field)
                for field in CLASSIFICATION_FIELDS
            },
            "contests": _distribution(local_items, "contest"),
            "hiddenFields": dict(
                sorted(
                    Counter(
                        "+".join(cast(list[str], item["hiddenFields"]))
                        for item in local_items
                    ).items()
                )
            ),
            "observedMissingPatterns": {
                "+".join(pattern) if pattern else "complete": count
                for pattern, count in sorted(missing_patterns.items())
            },
        },
        "payload": {
            "estimatedBytesTotalPerProvider": sum(
                int(item["estimatedPayloadBytes"]) for item in local_items
            ),
            "estimatedInputTokensPerProvider": total_input,
            "estimatedOutputTokensPerProvider": total_output,
            "estimationMethod": "UTF-8 bytes divided by 3, rounded up",
        },
        "pricing": price_snapshot,
        "providers": provider_costs,
        "cost": {
            "estimatedTotalUsd": round(total_cost_usd, 6),
            "estimatedTotalBrl": round(total_cost_usd * usd_brl, 4),
            "suggestedMaximumUsdWith25PercentMargin": round(total_cost_usd * 1.25, 6),
            "suggestedMaximumBrlWith25PercentMargin": round(
                total_cost_usd * usd_brl * 1.25, 4
            ),
        },
        "plannedCalls": {
            "pilot": PILOT_SIZE * len(PROVIDERS),
            "fullRemainder": (sample_size - PILOT_SIZE) * len(PROVIDERS),
            "maximum": sample_size * len(PROVIDERS),
        },
        "networkCallsPerformed": 0,
    }
    write_json(local_bundle_path, local_bundle)
    write_json(manifest_path, manifest)
    write_json(report_path, report)
    return report


def _request_from_local_item(item: Mapping[str, Any]) -> CanonicalAIRequest:
    payload = cast(Mapping[str, Any], item["request"])
    question = cast(Mapping[str, Any], payload["question"])
    return CanonicalAIRequest(
        canonical_question_id=str(item["referenceQuestionId"]),
        content_fingerprint=str(item["contentFingerprint"]),
        requested_fields=tuple(cast(list[str], payload["requestedFields"])),
        statement=str(question["statement"]),
        alternatives=tuple(cast(list[str], question["alternatives"])),
        known_fields={
            str(key): str(value)
            for key, value in cast(Mapping[str, Any], payload["knownEditorialFields"]).items()
        },
        taxonomy_version=str(payload["taxonomyVersion"]),
        taxonomy_options=tuple(
            {str(key): str(value) for key, value in option.items()}
            for option in cast(list[Mapping[str, Any]], payload["taxonomyOptions"])
        ),
    )


def _default_provider_factory(provider: str, model: str) -> CanonicalAIProvider:
    return create_canonical_ai_provider(provider, model, max_retries=0)


def _checkpoint_key(
    benchmark_id: str,
    provider: str,
    model: str,
    item: Mapping[str, Any],
) -> str:
    return stable_sha256(
        {
            "benchmark": benchmark_id,
            "provider": provider,
            "model": model,
            "question": item["referenceQuestionId"],
            "fingerprint": item["contentFingerprint"],
            "fields": item["hiddenFields"],
        }
    )


def _load_checkpoint(path: Path, bundle: Mapping[str, Any]) -> dict[str, Any]:
    manifest = cast(Mapping[str, Any], bundle["manifest"])
    manifest_items = cast(list[Mapping[str, Any]], manifest.get("items") or [])
    if manifest_items:
        calculated_fingerprint = stable_sha256(manifest_items)
        if calculated_fingerprint != manifest["sampleFingerprint"]:
            raise CanonicalClassificationError("fingerprint do manifesto não confere")
        local_items = cast(list[Mapping[str, Any]], bundle.get("items") or [])
        safe_local_items = [
            {
                "referenceQuestionId": item["referenceQuestionId"],
                "sourceQuestionId": item["sourceQuestionId"],
                "contentFingerprint": item["contentFingerprint"],
                "hiddenFields": item["hiddenFields"],
                "expected": item["expected"],
                "contest": item["contest"],
                "taxonomyVersion": item["taxonomyVersion"],
                "referenceKind": item["referenceKind"],
            }
            for item in local_items
        ]
        if safe_local_items != manifest_items:
            raise CanonicalClassificationError("bundle local divergiu do manifesto aprovado")
    if path.exists():
        checkpoint = cast(dict[str, Any], read_json(path))
        if checkpoint.get("benchmarkId") != manifest["benchmarkId"]:
            raise CanonicalClassificationError("checkpoint pertence a outro benchmark")
        if checkpoint.get("sampleFingerprint") != manifest["sampleFingerprint"]:
            raise CanonicalClassificationError("fingerprint da amostra mudou")
        if checkpoint.get("taxonomyVersion") != manifest["taxonomyVersion"]:
            raise CanonicalClassificationError("taxonomia mudou desde a preparação")
        return checkpoint
    return {
        "benchmarkId": manifest["benchmarkId"],
        "sampleFingerprint": manifest["sampleFingerprint"],
        "taxonomyVersion": manifest["taxonomyVersion"],
        "records": [],
    }


def execute_canonical_ai_benchmark(
    local_bundle_path: Path,
    *,
    checkpoint_path: Path,
    phase: BenchmarkPhase,
    approved_benchmark_id: str,
    max_cost_usd: float,
    provider_factory: ProviderFactory = _default_provider_factory,
    providers: Sequence[str] = PROVIDERS,
) -> dict[str, Any]:
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd deve ser positivo")
    bundle = cast(dict[str, Any], read_json(local_bundle_path))
    manifest = cast(Mapping[str, Any], bundle["manifest"])
    if approved_benchmark_id != manifest["benchmarkId"]:
        raise CanonicalClassificationError("aprovação não corresponde ao benchmark preparado")
    items = cast(list[Mapping[str, Any]], bundle["items"])
    phase_items = items[:PILOT_SIZE] if phase == "pilot" else items[PILOT_SIZE:]
    checkpoint = _load_checkpoint(checkpoint_path, bundle)
    records = cast(list[dict[str, Any]], checkpoint["records"])
    completed_keys = {str(record["key"]) for record in records}
    price_snapshot = cast(Mapping[str, Any], bundle["priceSnapshot"])
    prices = cast(Mapping[str, Mapping[str, Any]], price_snapshot["providers"])
    taxonomy = EditorialTaxonomy.load_default()
    if taxonomy.version != manifest["taxonomyVersion"]:
        raise CanonicalClassificationError("taxonomia mudou desde a preparação")

    if phase == "full":
        required_pilot_keys = {
            _checkpoint_key(
                str(manifest["benchmarkId"]),
                provider,
                str(prices[provider]["model"]),
                item,
            )
            for provider in providers
            for item in items[:PILOT_SIZE]
        }
        if not required_pilot_keys.issubset(completed_keys):
            raise CanonicalClassificationError("piloto completo é obrigatório antes da fase final")

    spent_usd = sum(float(record.get("costUsd") or 0) for record in records)
    phase_new_calls = 0
    for provider_name in providers:
        if provider_name not in PROVIDERS:
            raise CanonicalClassificationError(f"provedor fora do benchmark: {provider_name}")
        price = prices[provider_name]
        model = str(price["model"])
        provider = provider_factory(provider_name, model)
        if provider.name != provider_name or provider.model != model:
            raise CanonicalClassificationError(
                f"modelo resolvido divergiu do aprovado para {provider_name}"
            )
        attempted = 0
        failures = 0
        for item in phase_items:
            key = _checkpoint_key(
                str(manifest["benchmarkId"]), provider_name, model, item
            )
            if key in completed_keys:
                continue
            planned_cost = _price_for_tokens(
                price,
                int(item["estimatedInputTokens"]),
                int(item["estimatedOutputTokens"]),
            )
            if spent_usd + planned_cost * 1.25 > max_cost_usd:
                write_json(checkpoint_path, checkpoint)
                raise CanonicalClassificationError(
                    "teto de custo atingido antes da próxima chamada"
                )
            request = _request_from_local_item(item)
            started = time.perf_counter()
            record: dict[str, Any] = {
                "key": key,
                "phase": phase,
                "provider": provider_name,
                "model": model,
                "referenceQuestionId": item["referenceQuestionId"],
                "contentFingerprint": item["contentFingerprint"],
                "requestedFields": item["hiddenFields"],
                "completedAt": _now(),
            }
            try:
                result = provider.enrich(request)
                input_tokens = result.input_tokens or int(item["estimatedInputTokens"])
                output_tokens = result.output_tokens or int(item["estimatedOutputTokens"])
                cost_usd = _price_for_tokens(price, input_tokens, output_tokens)
                accepted, low_confidence = _validate_ai_response(
                    result.response, request=request, taxonomy=taxonomy
                )
                suggestions = {
                    field: {
                        "value": value,
                        "confidence": confidence,
                        "evidence": evidence,
                        "accepted": True,
                    }
                    for field, (value, confidence, evidence, _) in accepted.items()
                }
                suggestions.update(
                    {
                        suggestion.field: {
                            "value": suggestion.value,
                            "confidence": suggestion.confidence,
                            "evidence": suggestion.evidence,
                            "accepted": False,
                        }
                        for suggestion in low_confidence
                    }
                )
                expected = cast(Mapping[str, str], item["expected"])
                record.update(
                    {
                        "status": "completed",
                        "rawResponse": result.response,
                        "suggestions": suggestions,
                        "fieldCorrect": {
                            field: suggestions.get(field, {}).get("value") == expected[field]
                            for field in request.requested_fields
                        },
                        "allFieldsCorrect": all(
                            suggestions.get(field, {}).get("value") == expected[field]
                            for field in request.requested_fields
                        ),
                        "inputTokens": input_tokens,
                        "outputTokens": output_tokens,
                        "tokenUsageEstimated": (
                            result.input_tokens is None or result.output_tokens is None
                        ),
                        "costUsd": cost_usd,
                    }
                )
            except Exception as exc:
                failures += 1
                record.update(
                    {
                        "status": "failed",
                        "errorType": type(exc).__name__,
                        "error": str(exc),
                        "inputTokens": 0,
                        "outputTokens": 0,
                        "costUsd": planned_cost,
                    }
                )
            record["latencyMs"] = round((time.perf_counter() - started) * 1000, 3)
            records.append(record)
            completed_keys.add(key)
            phase_new_calls += 1
            attempted += 1
            spent_usd += float(record["costUsd"])
            write_json(checkpoint_path, checkpoint)
            if phase == "full" and attempted and failures / attempted > 0.05:
                raise CanonicalClassificationError(
                    f"taxa de falha de {provider_name} ultrapassou 5%"
                )
    return {
        "benchmarkId": manifest["benchmarkId"],
        "phase": phase,
        "newCalls": phase_new_calls,
        "totalCheckpointRecords": len(records),
        "costUsd": round(spent_usd, 6),
        "checkpoint": str(checkpoint_path),
    }


def _percent(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 3) if denominator else 0.0


def _wilson_95(successes: int, total: int) -> dict[str, float]:
    if total == 0:
        return {"lowPercent": 0.0, "highPercent": 0.0}
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
        / denominator
    )
    return {
        "lowPercent": round(max(0.0, center - margin) * 100, 3),
        "highPercent": round(min(1.0, center + margin) * 100, 3),
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def summarize_canonical_ai_benchmark(
    local_bundle_path: Path,
    *,
    checkpoint_path: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    bundle = cast(dict[str, Any], read_json(local_bundle_path))
    checkpoint = _load_checkpoint(checkpoint_path, bundle)
    manifest = cast(Mapping[str, Any], bundle["manifest"])
    records = cast(list[dict[str, Any]], checkpoint["records"])
    by_provider: dict[str, dict[str, Any]] = {}
    indexed: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        indexed[str(record["provider"])][str(record["referenceQuestionId"])] = record

    for provider in PROVIDERS:
        provider_records = [
            record for record in records if record.get("provider") == provider
        ]
        successful = [
            record for record in provider_records if record.get("status") == "completed"
        ]
        failed = [record for record in provider_records if record.get("status") == "failed"]
        field_totals: Counter[str] = Counter()
        field_correct: Counter[str] = Counter()
        accepted_total = 0
        accepted_correct = 0
        review_fields = 0
        all_correct = 0
        for record in successful:
            requested = cast(list[str], record.get("requestedFields") or [])
            correctness = cast(Mapping[str, bool], record.get("fieldCorrect") or {})
            suggestions = cast(Mapping[str, Mapping[str, Any]], record.get("suggestions") or {})
            all_correct += int(bool(record.get("allFieldsCorrect")))
            for field in requested:
                field_totals[field] += 1
                field_correct[field] += int(bool(correctness.get(field)))
                suggestion = suggestions.get(field)
                if suggestion is not None and bool(suggestion.get("accepted")):
                    accepted_total += 1
                    accepted_correct += int(bool(correctness.get(field)))
                else:
                    review_fields += 1
        requested_total = sum(field_totals.values())
        input_tokens = sum(int(record.get("inputTokens") or 0) for record in provider_records)
        output_tokens = sum(int(record.get("outputTokens") or 0) for record in provider_records)
        cost_usd = sum(float(record.get("costUsd") or 0) for record in provider_records)
        latencies = [float(record.get("latencyMs") or 0) for record in provider_records]
        invalid = [
            record
            for record in failed
            if record.get("errorType") == "CanonicalClassificationError"
        ]
        prohibited = [
            record for record in invalid if "proibid" in str(record.get("error") or "")
        ]
        by_provider[provider] = {
            "model": (
                provider_records[0].get("model")
                if provider_records
                else cast(Mapping[str, Any], bundle["priceSnapshot"])["providers"][
                    provider
                ]["model"]
            ),
            "calls": len(provider_records),
            "successful": len(successful),
            "failures": len(failed),
            "apiFailures": len(failed) - len(invalid),
            "invalidResponses": len(invalid),
            "prohibitedFieldAttempts": len(prohibited),
            "fieldAccuracy": {
                field: {
                    "correct": field_correct[field],
                    "total": field_totals[field],
                    "percent": _percent(field_correct[field], field_totals[field]),
                    "confidence95": _wilson_95(field_correct[field], field_totals[field]),
                }
                for field in CLASSIFICATION_FIELDS
            },
            "allRequestedFieldsAccuracy": {
                "correct": all_correct,
                "total": len(successful),
                "percent": _percent(all_correct, len(successful)),
                "confidence95": _wilson_95(all_correct, len(successful)),
            },
            "acceptedPrecisionPercent": _percent(accepted_correct, accepted_total),
            "coverageAboveConfidencePercent": _percent(accepted_total, requested_total),
            "reviewPercent": _percent(review_fields, requested_total),
            "validPathPercent": _percent(len(successful), len(provider_records)),
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "costUsd": round(cost_usd, 6),
            "costPerQuestionUsd": round(cost_usd / len(provider_records), 8)
            if provider_records
            else 0.0,
            "costPerCorrectAcceptedFieldUsd": round(cost_usd / accepted_correct, 8)
            if accepted_correct
            else 0.0,
            "latencyMedianMs": round(statistics.median(latencies), 3)
            if latencies
            else 0.0,
            "latencyP95Ms": _percentile(latencies, 0.95),
        }

    paired: dict[str, dict[str, int]] = {}
    for left_index, left in enumerate(PROVIDERS):
        for right in PROVIDERS[left_index + 1 :]:
            common = set(indexed[left]).intersection(indexed[right])
            left_wins = right_wins = ties = 0
            for reference_id in common:
                left_correct = bool(indexed[left][reference_id].get("allFieldsCorrect"))
                right_correct = bool(indexed[right][reference_id].get("allFieldsCorrect"))
                if left_correct and not right_correct:
                    left_wins += 1
                elif right_correct and not left_correct:
                    right_wins += 1
                else:
                    ties += 1
            paired[f"{left}_vs_{right}"] = {
                "pairedQuestions": len(common),
                f"{left}Wins": left_wins,
                f"{right}Wins": right_wins,
                "ties": ties,
            }

    report = {
        "schemaVersion": BENCHMARK_SCHEMA_VERSION,
        "benchmarkId": manifest["benchmarkId"],
        "sampleFingerprint": manifest["sampleFingerprint"],
        "taxonomyVersion": manifest["taxonomyVersion"],
        "generatedAt": _now(),
        "records": len(records),
        "providers": by_provider,
        "pairedComparison": paired,
        "recommendation": (
            "Benchmark incompleto; não selecionar provedor."
            if len(records) < len(PROVIDERS) * len(cast(list[Any], bundle["items"]))
            else "Aplicar os critérios editoriais documentados antes de selecionar o provedor."
        ),
    }
    if report_path is not None:
        write_json(report_path, report)
    return report
