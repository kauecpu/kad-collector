from __future__ import annotations

import math
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from .canonical_classification import (
    CANONICAL_ENRICHMENT_PROMPT_VERSION,
    CLASSIFICATION_FIELDS,
    CanonicalAIProvider,
    CanonicalAIRequest,
    CanonicalClassificationError,
    _validate_ai_response,
)
from .editorial_taxonomy import EditorialTaxonomy
from .json_utils import read_json, write_json
from .ollama_ai_provider import (
    DEFAULT_CONTEXT_LENGTH,
    OllamaCanonicalEnrichmentProvider,
    OllamaUnavailableError,
)
from .ollama_preflight import (
    OLLAMA_BENCHMARK_TARGETS,
    HttpOllamaAdminClient,
    OllamaAdminClient,
)
from .semantic_identity import stable_sha256

LOCAL_BENCHMARK_SCHEMA_VERSION = 1
LOCAL_BENCHMARK_ALGORITHM_VERSION = "ollama-local-ai-benchmark-v1"
LOCAL_BENCHMARK_SAMPLE_SIZE = 200
SMOKE_SIZE = 10
SMOKE_MAX_CALLS = SMOKE_SIZE * len(OLLAMA_BENCHMARK_TARGETS)
FULL_MAX_CALLS = (LOCAL_BENCHMARK_SAMPLE_SIZE - SMOKE_SIZE) * len(
    OLLAMA_BENCHMARK_TARGETS
)

LocalBenchmarkPhase = Literal["smoke", "full"]
LocalProviderFactory = Callable[[str], CanonicalAIProvider]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_item(item: Mapping[str, Any]) -> dict[str, Any]:
    names = (
        "referenceQuestionId",
        "sourceQuestionId",
        "contentFingerprint",
        "hiddenFields",
        "expected",
        "contest",
        "taxonomyVersion",
        "referenceKind",
    )
    try:
        return {name: item[name] for name in names}
    except KeyError as exc:
        raise CanonicalClassificationError(
            f"bundle canônico sem campo obrigatório: {exc.args[0]}"
        ) from exc


def _validated_preflight(path: Path) -> dict[str, Any]:
    preflight = cast(dict[str, Any], read_json(path))
    if preflight.get("kind") != "ollama-local-preflight-probe":
        raise CanonicalClassificationError("relatório informado não é um probe Ollama")
    if preflight.get("readyForBenchmark") is not True:
        raise CanonicalClassificationError("probe Ollama não liberou o benchmark")
    if preflight.get("networkScope") != "loopback":
        raise CanonicalClassificationError("probe Ollama não está restrito a loopback")
    models = preflight.get("models")
    if not isinstance(models, list):
        raise CanonicalClassificationError("probe Ollama sem modelos")
    by_tag = {
        str(item.get("tag")): item
        for item in models
        if isinstance(item, Mapping) and item.get("tag")
    }
    for target in OLLAMA_BENCHMARK_TARGETS:
        model = by_tag.get(target.tag)
        if model is None:
            raise CanonicalClassificationError(f"probe não contém o modelo {target.tag}")
        if model.get("processor") != "100% GPU":
            raise CanonicalClassificationError(f"{target.tag} não foi validado em 100% GPU")
        if model.get("structuredOutput") is not True:
            raise CanonicalClassificationError(
                f"{target.tag} não validou saída estruturada"
            )
        if model.get("contextLength") != DEFAULT_CONTEXT_LENGTH:
            raise CanonicalClassificationError(
                f"{target.tag} não foi validado com contexto {DEFAULT_CONTEXT_LENGTH}"
            )
        if model.get("quantization") != target.expected_quantization:
            raise CanonicalClassificationError(
                f"quantização do modelo {target.tag} divergiu do plano"
            )
        if not isinstance(model.get("digest"), str) or not model["digest"]:
            raise CanonicalClassificationError(f"probe não fixou o digest de {target.tag}")
    return preflight


def _manifest_core(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"benchmarkId", "manifestFingerprint", "createdAt"}
    }


def prepare_ollama_ai_benchmark(
    canonical_bundle_path: Path,
    *,
    preflight_path: Path,
    local_bundle_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    source = cast(dict[str, Any], read_json(canonical_bundle_path))
    source_manifest = cast(Mapping[str, Any], source.get("manifest") or {})
    source_items = cast(list[Mapping[str, Any]], source.get("items") or [])
    if len(source_items) != LOCAL_BENCHMARK_SAMPLE_SIZE:
        raise CanonicalClassificationError(
            f"benchmark local exige {LOCAL_BENCHMARK_SAMPLE_SIZE} questões"
        )
    safe_items = [_safe_item(item) for item in source_items]
    manifest_items = cast(list[Mapping[str, Any]], source_manifest.get("items") or [])
    if safe_items != manifest_items:
        raise CanonicalClassificationError("bundle local divergiu do manifesto canônico")
    source_fingerprint = stable_sha256(safe_items)
    if source_manifest.get("sampleFingerprint") != source_fingerprint:
        raise CanonicalClassificationError("fingerprint da amostra canônica não confere")
    if source_manifest.get("promptVersion") != CANONICAL_ENRICHMENT_PROMPT_VERSION:
        raise CanonicalClassificationError("prompt canônico mudou desde a preparação")

    preflight = _validated_preflight(preflight_path)
    probe_models = {
        str(item["tag"]): item
        for item in cast(list[Mapping[str, Any]], preflight["models"])
    }
    models = [
        {
            "tag": target.tag,
            "digest": probe_models[target.tag]["digest"],
            "quantization": probe_models[target.tag]["quantization"],
            "sizeBytes": probe_models[target.tag].get("sizeBytes"),
        }
        for target in OLLAMA_BENCHMARK_TARGETS
    ]
    ollama = cast(Mapping[str, Any], preflight.get("ollama") or {})
    core = {
        "schemaVersion": LOCAL_BENCHMARK_SCHEMA_VERSION,
        "algorithmVersion": LOCAL_BENCHMARK_ALGORITHM_VERSION,
        "sourceAlgorithmVersion": source_manifest.get("algorithmVersion"),
        "sourceBenchmarkId": source_manifest.get("benchmarkId"),
        "sampleFingerprint": source_fingerprint,
        "sampleSize": len(safe_items),
        "taxonomyVersion": source_manifest.get("taxonomyVersion"),
        "promptVersion": source_manifest.get("promptVersion"),
        "ollamaVersion": ollama.get("version"),
        "preflightProbeId": preflight.get("probeId"),
        "preflightReportId": preflight.get("probeReportId"),
        "networkScope": "loopback",
        "models": models,
        "parameters": {
            "concurrency": 1,
            "temperature": 0,
            "numCtx": DEFAULT_CONTEXT_LENGTH,
            "numPredict": 512,
            "thinking": False,
        },
        "phases": {
            "smokeMeasuredCalls": SMOKE_MAX_CALLS,
            "fullRemainderMeasuredCalls": FULL_MAX_CALLS,
            "maximumMeasuredCalls": LOCAL_BENCHMARK_SAMPLE_SIZE
            * len(OLLAMA_BENCHMARK_TARGETS),
        },
        "smokeReferenceQuestionIds": [
            item["referenceQuestionId"] for item in safe_items[:SMOKE_SIZE]
        ],
        "items": safe_items,
    }
    manifest_fingerprint = stable_sha256(core)
    manifest = {
        **core,
        "benchmarkId": f"ollama-local-{manifest_fingerprint[:16]}",
        "manifestFingerprint": manifest_fingerprint,
        "createdAt": _now(),
    }
    local_bundle = {"manifest": manifest, "items": source_items}
    write_json(local_bundle_path, local_bundle)
    write_json(manifest_path, manifest)
    return manifest


def _validate_local_bundle(
    local_bundle_path: Path, manifest_path: Path
) -> tuple[dict[str, Any], dict[str, Any], list[Mapping[str, Any]]]:
    bundle = cast(dict[str, Any], read_json(local_bundle_path))
    embedded = cast(dict[str, Any], bundle.get("manifest") or {})
    public = cast(dict[str, Any], read_json(manifest_path))
    if embedded != public:
        raise CanonicalClassificationError("manifesto público divergiu do bundle local")
    fingerprint = stable_sha256(_manifest_core(public))
    if public.get("manifestFingerprint") != fingerprint:
        raise CanonicalClassificationError("fingerprint do manifesto local não confere")
    if public.get("benchmarkId") != f"ollama-local-{fingerprint[:16]}":
        raise CanonicalClassificationError("identificador do benchmark local não confere")
    items = cast(list[Mapping[str, Any]], bundle.get("items") or [])
    if len(items) != LOCAL_BENCHMARK_SAMPLE_SIZE:
        raise CanonicalClassificationError("bundle local não contém 200 questões")
    if [_safe_item(item) for item in items] != public.get("items"):
        raise CanonicalClassificationError("questões locais divergiram do manifesto")
    return bundle, public, items


def _request_from_item(item: Mapping[str, Any]) -> CanonicalAIRequest:
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
            for key, value in cast(
                Mapping[str, Any], payload["knownEditorialFields"]
            ).items()
        },
        taxonomy_version=str(payload["taxonomyVersion"]),
        taxonomy_options=tuple(
            {str(key): str(value) for key, value in option.items()}
            for option in cast(list[Mapping[str, Any]], payload["taxonomyOptions"])
        ),
    )


def _checkpoint_key(
    benchmark_id: str,
    model: Mapping[str, Any],
    item: Mapping[str, Any],
) -> str:
    return stable_sha256(
        {
            "benchmark": benchmark_id,
            "model": model["tag"],
            "digest": model["digest"],
            "question": item["referenceQuestionId"],
            "fingerprint": item["contentFingerprint"],
            "fields": item["hiddenFields"],
        }
    )


def _load_checkpoint(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    if path.exists():
        checkpoint = cast(dict[str, Any], read_json(path))
        for name in ("benchmarkId", "manifestFingerprint", "sampleFingerprint"):
            if checkpoint.get(name) != manifest.get(name):
                raise CanonicalClassificationError(
                    f"checkpoint divergiu do manifesto em {name}"
                )
        return checkpoint
    return {
        "schemaVersion": LOCAL_BENCHMARK_SCHEMA_VERSION,
        "benchmarkId": manifest["benchmarkId"],
        "manifestFingerprint": manifest["manifestFingerprint"],
        "sampleFingerprint": manifest["sampleFingerprint"],
        "records": [],
        "warmups": [],
        "interruptions": [],
    }


def _default_provider_factory(model: str) -> CanonicalAIProvider:
    return OllamaCanonicalEnrichmentProvider(model)


def _integer(metrics: Mapping[str, Any], key: str) -> int | None:
    value = metrics.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _peak_vram(admin: OllamaAdminClient, model: str) -> int | None:
    for item in admin.running_models():
        name = item.get("name", item.get("model"))
        if name == model:
            size = item.get("size")
            value = item.get("size_vram")
            if not isinstance(size, int) or isinstance(size, bool):
                raise CanonicalClassificationError(
                    f"Ollama não informou o tamanho carregado de {model}"
                )
            if not isinstance(value, int) or isinstance(value, bool) or value < size:
                raise CanonicalClassificationError(
                    f"{model} deixou de executar integralmente na GPU"
                )
            return value
    return None


def _telemetry(result: Any, admin: OllamaAdminClient, model: str) -> dict[str, Any]:
    metrics = cast(Mapping[str, Any], result.provider_metrics)
    eval_duration = _integer(metrics, "evalDurationNs")
    output_tokens = result.output_tokens or 0
    tokens_per_second = (
        output_tokens * 1_000_000_000 / eval_duration
        if eval_duration is not None and eval_duration > 0
        else None
    )
    return {
        "inputTokens": result.input_tokens or 0,
        "outputTokens": output_tokens,
        "totalDurationNs": _integer(metrics, "totalDurationNs"),
        "loadDurationNs": _integer(metrics, "loadDurationNs"),
        "promptEvalDurationNs": _integer(metrics, "promptEvalDurationNs"),
        "evalDurationNs": eval_duration,
        "tokensPerSecond": round(tokens_per_second, 3)
        if tokens_per_second is not None
        else None,
        "peakVramBytes": _peak_vram(admin, model),
    }


def _error_category(exc: Exception) -> str:
    message = str(exc).casefold()
    if "proibid" in message:
        return "prohibited_field"
    if "taxon" in message:
        return "outside_taxonomy"
    if any(value in message for value in ("json", "schema", "campo", "resposta")):
        return "invalid_schema"
    return "provider_failure"


def _preflight_matches_manifest(
    preflight: Mapping[str, Any], manifest: Mapping[str, Any]
) -> None:
    if preflight.get("probeReportId") != manifest.get("preflightReportId"):
        raise CanonicalClassificationError("probe mudou desde a preparação do benchmark")
    probed = {
        str(item["tag"]): item
        for item in cast(list[Mapping[str, Any]], preflight["models"])
    }
    for model in cast(list[Mapping[str, Any]], manifest["models"]):
        current = probed.get(str(model["tag"]))
        if current is None or current.get("digest") != model.get("digest"):
            raise CanonicalClassificationError(
                f"digest de {model['tag']} mudou desde a preparação"
            )


def execute_ollama_ai_benchmark(
    local_bundle_path: Path,
    *,
    manifest_path: Path,
    preflight_path: Path,
    checkpoint_path: Path,
    phase: LocalBenchmarkPhase,
    approved_benchmark_id: str,
    max_new_calls: int,
    provider_factory: LocalProviderFactory = _default_provider_factory,
    admin_client: OllamaAdminClient | None = None,
) -> dict[str, Any]:
    if max_new_calls < 1:
        raise ValueError("max_new_calls deve ser positivo")
    phase_limit = SMOKE_MAX_CALLS if phase == "smoke" else FULL_MAX_CALLS
    if max_new_calls > phase_limit:
        raise CanonicalClassificationError(
            f"fase {phase} permite no máximo {phase_limit} novas chamadas medidas"
        )
    _, manifest, items = _validate_local_bundle(local_bundle_path, manifest_path)
    if approved_benchmark_id != manifest["benchmarkId"]:
        raise CanonicalClassificationError("aprovação não corresponde ao benchmark local")
    preflight = _validated_preflight(preflight_path)
    _preflight_matches_manifest(preflight, manifest)
    taxonomy = EditorialTaxonomy.load_default()
    if taxonomy.version != manifest["taxonomyVersion"]:
        raise CanonicalClassificationError("taxonomia mudou desde a preparação")
    checkpoint = _load_checkpoint(checkpoint_path, manifest)
    records = cast(list[dict[str, Any]], checkpoint["records"])
    warmups = cast(list[dict[str, Any]], checkpoint["warmups"])
    interruptions = cast(list[dict[str, Any]], checkpoint["interruptions"])
    models = cast(list[Mapping[str, Any]], manifest["models"])
    phase_items = items[:SMOKE_SIZE] if phase == "smoke" else items[SMOKE_SIZE:]
    completed = {str(record["key"]) for record in records}

    if phase == "full":
        smoke_keys = {
            _checkpoint_key(str(manifest["benchmarkId"]), model, item)
            for model in models
            for item in items[:SMOKE_SIZE]
        }
        successful_smoke = {
            str(record["key"])
            for record in records
            if record.get("phase") == "smoke" and record.get("status") == "completed"
        }
        if not smoke_keys.issubset(successful_smoke):
            raise CanonicalClassificationError(
                "smoke válido dos três modelos é obrigatório antes da fase full"
            )

    expected_phase_keys = {
        _checkpoint_key(str(manifest["benchmarkId"]), model, item)
        for model in models
        for item in phase_items
    }
    pending_exists = bool(expected_phase_keys - completed)
    if not pending_exists:
        return {
            "benchmarkId": manifest["benchmarkId"],
            "phase": phase,
            "status": "completed",
            "newCalls": 0,
            "totalCheckpointRecords": len(records),
        }

    admin = admin_client or HttpOllamaAdminClient()
    new_calls = 0
    paused = False
    session_id = stable_sha256(
        {"benchmark": manifest["benchmarkId"], "phase": phase, "startedAt": _now()}
    )[:20]

    for model in models:
        tag = str(model["tag"])
        pending_items = [
            item
            for item in phase_items
            if _checkpoint_key(str(manifest["benchmarkId"]), model, item) not in completed
        ]
        if not pending_items or new_calls >= max_new_calls:
            continue
        provider = provider_factory(tag)
        if provider.name != "ollama" or provider.model != tag:
            raise CanonicalClassificationError(
                f"provedor resolveu um modelo diferente do manifesto: {tag}"
            )
        try:
            warmup_started = time.perf_counter()
            try:
                warmup_result = provider.enrich(_request_from_item(pending_items[0]))
                _validate_ai_response(
                    warmup_result.response,
                    request=_request_from_item(pending_items[0]),
                    taxonomy=taxonomy,
                )
                warmups.append(
                    {
                        "sessionId": session_id,
                        "phase": phase,
                        "model": tag,
                        "digest": model["digest"],
                        "status": "completed",
                        "completedAt": _now(),
                        "wallLatencyMs": round(
                            (time.perf_counter() - warmup_started) * 1000, 3
                        ),
                        **_telemetry(warmup_result, admin, tag),
                    }
                )
                write_json(checkpoint_path, checkpoint)
            except (OllamaUnavailableError, KeyboardInterrupt) as exc:
                interruptions.append(
                    {
                        "sessionId": session_id,
                        "phase": phase,
                        "model": tag,
                        "stage": "warmup",
                        "interruptionType": type(exc).__name__,
                        "interruptedAt": _now(),
                    }
                )
                write_json(checkpoint_path, checkpoint)
                paused = True
                break
            except Exception as exc:
                warmups.append(
                    {
                        "sessionId": session_id,
                        "phase": phase,
                        "model": tag,
                        "digest": model["digest"],
                        "status": "failed",
                        "errorType": type(exc).__name__,
                        "errorCategory": _error_category(exc),
                        "completedAt": _now(),
                        "wallLatencyMs": round(
                            (time.perf_counter() - warmup_started) * 1000, 3
                        ),
                    }
                )
                write_json(checkpoint_path, checkpoint)

            for item in pending_items:
                if new_calls >= max_new_calls:
                    paused = True
                    break
                key = _checkpoint_key(str(manifest["benchmarkId"]), model, item)
                request = _request_from_item(item)
                started = time.perf_counter()
                record: dict[str, Any] = {
                    "key": key,
                    "phase": phase,
                    "model": tag,
                    "digest": model["digest"],
                    "referenceQuestionId": item["referenceQuestionId"],
                    "contentFingerprint": item["contentFingerprint"],
                    "requestedFields": item["hiddenFields"],
                    "completedAt": _now(),
                }
                try:
                    result = provider.enrich(request)
                    record.update(_telemetry(result, admin, tag))
                    record["rawResponse"] = result.response
                    record["jsonValid"] = isinstance(result.response, dict)
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
                            "schemaValid": True,
                            "suggestions": suggestions,
                            "fieldCorrect": {
                                field: suggestions.get(field, {}).get("value")
                                == expected[field]
                                for field in request.requested_fields
                            },
                            "allFieldsCorrect": all(
                                suggestions.get(field, {}).get("value")
                                == expected[field]
                                for field in request.requested_fields
                            ),
                        }
                    )
                except (OllamaUnavailableError, KeyboardInterrupt) as exc:
                    interruptions.append(
                        {
                            "sessionId": session_id,
                            "phase": phase,
                            "model": tag,
                            "stage": "measured",
                            "interruptionType": type(exc).__name__,
                            "referenceQuestionId": item["referenceQuestionId"],
                            "interruptedAt": _now(),
                        }
                    )
                    write_json(checkpoint_path, checkpoint)
                    paused = True
                    break
                except Exception as exc:
                    record.update(
                        {
                            "status": "failed",
                            "schemaValid": False,
                            "errorType": type(exc).__name__,
                            "errorCategory": _error_category(exc),
                        }
                    )
                record["wallLatencyMs"] = round(
                    (time.perf_counter() - started) * 1000, 3
                )
                records.append(record)
                completed.add(key)
                new_calls += 1
                write_json(checkpoint_path, checkpoint)
            if paused:
                break
        finally:
            admin.unload(tag)

    status = "completed" if expected_phase_keys.issubset(completed) else "paused"
    return {
        "benchmarkId": manifest["benchmarkId"],
        "phase": phase,
        "status": status,
        "newCalls": new_calls,
        "totalCheckpointRecords": len(records),
    }


def _percent(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 3) if denominator else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


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


def summarize_ollama_ai_benchmark(
    local_bundle_path: Path,
    *,
    manifest_path: Path,
    checkpoint_path: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    _, manifest, items = _validate_local_bundle(local_bundle_path, manifest_path)
    checkpoint = _load_checkpoint(checkpoint_path, manifest)
    records = cast(list[dict[str, Any]], checkpoint["records"])
    warmups = cast(list[dict[str, Any]], checkpoint["warmups"])
    interruptions = cast(list[dict[str, Any]], checkpoint["interruptions"])
    models = cast(list[Mapping[str, Any]], manifest["models"])
    indexed: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        indexed[str(record["model"])][str(record["referenceQuestionId"])] = record

    by_model: dict[str, dict[str, Any]] = {}
    for model in models:
        tag = str(model["tag"])
        model_records = [record for record in records if record.get("model") == tag]
        successful = [
            record for record in model_records if record.get("status") == "completed"
        ]
        failed = [record for record in model_records if record.get("status") == "failed"]
        field_totals: Counter[str] = Counter()
        field_correct: Counter[str] = Counter()
        accepted_total = 0
        accepted_correct = 0
        all_correct = 0
        for record in model_records:
            requested = cast(list[str], record.get("requestedFields") or [])
            field_totals.update(requested)
            if record.get("status") != "completed":
                continue
            correctness = cast(Mapping[str, bool], record.get("fieldCorrect") or {})
            suggestions = cast(
                Mapping[str, Mapping[str, Any]], record.get("suggestions") or {}
            )
            all_correct += int(bool(record.get("allFieldsCorrect")))
            for field in requested:
                field_correct[field] += int(bool(correctness.get(field)))
                suggestion = suggestions.get(field)
                if suggestion is not None and suggestion.get("accepted") is True:
                    accepted_total += 1
                    accepted_correct += int(bool(correctness.get(field)))
        requested_total = sum(field_totals.values())
        latencies = [float(record["wallLatencyMs"]) for record in model_records]
        token_rates = [
            float(record["tokensPerSecond"])
            for record in model_records
            if record.get("tokensPerSecond") is not None
        ]
        model_warmups = [item for item in warmups if item.get("model") == tag]
        warmup_load_ms = [
            float(value) / 1_000_000
            for item in model_warmups
            if (value := item.get("loadDurationNs")) is not None
        ]
        peak_vram = max(
            (int(record.get("peakVramBytes") or 0) for record in model_records),
            default=0,
        )
        prohibited = [
            record
            for record in failed
            if record.get("errorCategory") == "prohibited_field"
        ]
        outside_taxonomy = [
            record
            for record in failed
            if record.get("errorCategory") == "outside_taxonomy"
        ]
        schema_failures = [record for record in failed if not record.get("schemaValid")]
        model_interruptions = [
            item for item in interruptions if item.get("model") == tag
        ]
        by_model[tag] = {
            "digest": model["digest"],
            "quantization": model["quantization"],
            "calls": len(model_records),
            "successful": len(successful),
            "failures": len(failed),
            "interruptions": len(model_interruptions),
            "jsonValidPercent": _percent(
                sum(record.get("jsonValid") is True for record in model_records),
                len(model_records),
            ),
            "schemaFailures": len(schema_failures),
            "prohibitedFieldAttempts": len(prohibited),
            "outsideTaxonomy": len(outside_taxonomy),
            "fieldAccuracy": {
                field: {
                    "correct": field_correct[field],
                    "total": field_totals[field],
                    "percent": _percent(field_correct[field], field_totals[field]),
                    "confidence95": _wilson_95(
                        field_correct[field], field_totals[field]
                    ),
                }
                for field in CLASSIFICATION_FIELDS
            },
            "allRequestedFieldsAccuracy": {
                "correct": all_correct,
                "total": len(model_records),
                "percent": _percent(all_correct, len(model_records)),
                "confidence95": _wilson_95(all_correct, len(model_records)),
            },
            "acceptedPrecisionPercent": _percent(accepted_correct, accepted_total),
            "coverageAboveConfidencePercent": _percent(
                accepted_total, requested_total
            ),
            "reviewPercent": _percent(
                requested_total - accepted_total, requested_total
            ),
            "latencyMedianMs": round(statistics.median(latencies), 3)
            if latencies
            else 0.0,
            "latencyP95Ms": _percentile(latencies, 0.95),
            "inputTokens": sum(int(item.get("inputTokens") or 0) for item in model_records),
            "outputTokens": sum(
                int(item.get("outputTokens") or 0) for item in model_records
            ),
            "tokensPerSecondMedian": round(statistics.median(token_rates), 3)
            if token_rates
            else 0.0,
            "tokensPerSecondP95": _percentile(token_rates, 0.95),
            "warmupLoadDurationMedianMs": round(
                statistics.median(warmup_load_ms), 3
            )
            if warmup_load_ms
            else 0.0,
            "peakVramBytes": peak_vram,
        }

    paired: dict[str, dict[str, int]] = {}
    tags = [str(model["tag"]) for model in models]
    for index, left in enumerate(tags):
        for right in tags[index + 1 :]:
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
                "leftWins": left_wins,
                "rightWins": right_wins,
                "ties": ties,
            }

    maximum_records = len(items) * len(models)
    report = {
        "schemaVersion": LOCAL_BENCHMARK_SCHEMA_VERSION,
        "algorithmVersion": LOCAL_BENCHMARK_ALGORITHM_VERSION,
        "benchmarkId": manifest["benchmarkId"],
        "manifestFingerprint": manifest["manifestFingerprint"],
        "sampleFingerprint": manifest["sampleFingerprint"],
        "taxonomyVersion": manifest["taxonomyVersion"],
        "ollamaVersion": manifest["ollamaVersion"],
        "parameters": manifest["parameters"],
        "generatedAt": _now(),
        "records": len(records),
        "maximumRecords": maximum_records,
        "models": by_model,
        "pairedComparison": paired,
        "recommendation": (
            "Smoke incompleto; não executar a fase full."
            if len(records) < SMOKE_MAX_CALLS
            else (
                "Comparar os critérios editoriais antes de autorizar a fase full; "
                "nenhum vencedor foi escolhido."
            )
        ),
    }
    if report_path is not None:
        write_json(report_path, report)
    return report
