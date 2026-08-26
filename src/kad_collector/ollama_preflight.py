from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import httpx

from .canonical_classification import CanonicalClassificationError
from .ollama_ai_provider import (
    DEFAULT_CONTEXT_LENGTH,
    DEFAULT_OLLAMA_BASE_URL,
    OllamaUnavailableError,
    validate_ollama_base_url,
)
from .semantic_identity import canonical_json, stable_sha256

GIB = 1024**3
MINIMUM_FREE_BYTES = 35 * GIB


@dataclass(frozen=True)
class OllamaBenchmarkTarget:
    tag: str
    expected_quantization: str


OLLAMA_BENCHMARK_TARGETS = (
    OllamaBenchmarkTarget("qwen3:8b", "Q4_K_M"),
    OllamaBenchmarkTarget("qwen3:14b", "Q4_K_M"),
)

OllamaCommandRunner = Callable[[tuple[str, ...], Mapping[str, str]], str]


class OllamaAdminClient(Protocol):
    base_url: str

    def version(self) -> str: ...

    def tags(self) -> list[dict[str, Any]]: ...

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def running_models(self) -> list[dict[str, Any]]: ...

    def unload(self, model: str) -> None: ...


class HttpOllamaAdminClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = validate_ollama_base_url(
            base_url or os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=180.0,
            trust_env=False,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise OllamaUnavailableError("Ollama local indisponível durante o preflight") from exc
        if response.status_code >= 500:
            raise OllamaUnavailableError(
                f"Ollama local indisponível durante o preflight (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            raise CanonicalClassificationError(
                f"Ollama recusou o preflight (HTTP {response.status_code})"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CanonicalClassificationError("Ollama retornou JSON inválido") from exc
        if not isinstance(payload, dict):
            raise CanonicalClassificationError("Ollama retornou resposta administrativa inválida")
        return cast(dict[str, Any], payload)

    def version(self) -> str:
        value = self._request("GET", "/api/version").get("version")
        if not isinstance(value, str) or not value:
            raise CanonicalClassificationError("Ollama não informou sua versão")
        return value

    def tags(self) -> list[dict[str, Any]]:
        models = self._request("GET", "/api/tags").get("models")
        if not isinstance(models, list):
            raise CanonicalClassificationError("Ollama não informou os modelos instalados")
        return [cast(dict[str, Any], item) for item in models if isinstance(item, dict)]

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/chat", json=payload)

    def running_models(self) -> list[dict[str, Any]]:
        models = self._request("GET", "/api/ps").get("models")
        if not isinstance(models, list):
            raise CanonicalClassificationError("Ollama não informou os modelos carregados")
        return [cast(dict[str, Any], item) for item in models if isinstance(item, dict)]

    def unload(self, model: str) -> None:
        self._request(
            "POST",
            "/api/generate",
            json={"model": model, "prompt": "", "stream": False, "keep_alive": 0},
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _model_name(item: Mapping[str, Any]) -> str:
    value = item.get("name", item.get("model", ""))
    return value if isinstance(value, str) else ""


def _integer(item: Mapping[str, Any], key: str) -> int | None:
    value = item.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _details(item: Mapping[str, Any]) -> Mapping[str, Any]:
    value = item.get("details")
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _probe_material(report: Mapping[str, Any]) -> dict[str, Any]:
    disk = cast(Mapping[str, Any], report["disk"])
    return {
        "ollamaVersion": cast(Mapping[str, Any], report["ollama"])["version"],
        "baseUrl": cast(Mapping[str, Any], report["ollama"])["baseUrl"],
        "targets": report["targets"],
        "missingModels": report["missingModels"],
        "invalidModels": report["invalidModels"],
        "disk": {
            "minimumFreeBytes": disk["minimumFreeBytes"],
            "sufficient": disk["sufficient"],
        },
    }


def _probe_id(report: Mapping[str, Any]) -> str:
    digest = stable_sha256(canonical_json(_probe_material(report)))[:20]
    return f"ollama-probe-{digest}"


def ollama_probe_report_id(report: Mapping[str, Any]) -> str:
    material = {
        key: value for key, value in report.items() if key != "probeReportId"
    }
    return "ollama-probe-report-" + stable_sha256(canonical_json(material))[:20]


def validate_ollama_probe_report(report: Mapping[str, Any]) -> None:
    if report.get("probeReportId") != ollama_probe_report_id(report):
        raise CanonicalClassificationError("relatório de probe Ollama foi alterado")


def _default_model_volume() -> Path:
    configured = os.environ.get("OLLAMA_MODELS")
    return Path(configured) if configured else Path.home() / ".ollama" / "models"


def _free_bytes(path: Path) -> int:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return int(shutil.disk_usage(candidate).free)


def inspect_ollama_environment(
    *,
    client: OllamaAdminClient | None = None,
    free_bytes: int | None = None,
    model_volume: Path | None = None,
) -> dict[str, Any]:
    active_client = client or HttpOllamaAdminClient()
    base_url = validate_ollama_base_url(active_client.base_url)
    installed = {_model_name(item): item for item in active_client.tags()}
    target_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[str] = []

    for target in OLLAMA_BENCHMARK_TARGETS:
        model = installed.get(target.tag)
        if model is None:
            missing.append(target.tag)
            target_rows.append(
                {
                    "tag": target.tag,
                    "installed": False,
                    "digest": None,
                    "sizeBytes": None,
                    "quantization": None,
                    "expectedQuantization": target.expected_quantization,
                    "quantizationMatches": False,
                }
            )
            continue
        details = _details(model)
        quantization = details.get("quantization_level")
        matches = (
            isinstance(quantization, str)
            and quantization.casefold() == target.expected_quantization.casefold()
        )
        digest = model.get("digest") if isinstance(model.get("digest"), str) else None
        if not matches or not digest:
            invalid.append(target.tag)
        target_rows.append(
            {
                "tag": target.tag,
                "installed": True,
                "digest": digest,
                "sizeBytes": _integer(model, "size"),
                "format": details.get("format"),
                "family": details.get("family"),
                "parameterSize": details.get("parameter_size"),
                "quantization": quantization,
                "expectedQuantization": target.expected_quantization,
                "quantizationMatches": matches,
            }
        )

    volume = model_volume or _default_model_volume()
    available = free_bytes if free_bytes is not None else _free_bytes(volume)
    disk_sufficient = not missing or available >= MINIMUM_FREE_BYTES
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "ollama-local-preflight-inspection",
        "inspectedAt": _now(),
        "networkScope": "loopback",
        "ollama": {"baseUrl": base_url, "version": active_client.version()},
        "targets": target_rows,
        "missingModels": missing,
        "invalidModels": invalid,
        "pullCommands": (
            [f"ollama pull {tag}" for tag in missing] if disk_sufficient else []
        ),
        "downloadBlockedReason": (
            None if disk_sufficient else "insufficient_free_space"
        ),
        "disk": {
            "freeBytes": available,
            "minimumFreeBytes": MINIMUM_FREE_BYTES,
            "sufficient": disk_sufficient,
        },
        "readyForProbe": not missing and not invalid and disk_sufficient,
    }
    report["probeId"] = _probe_id(report)
    return report


def _default_command_runner(
    command: tuple[str, ...], environment: Mapping[str, str]
) -> str:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **environment},
    )
    if completed.returncode != 0:
        raise CanonicalClassificationError(
            f"{' '.join(command)} falhou com código {completed.returncode}"
        )
    return completed.stdout


def _default_windows_log_reader() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return ""
    path = Path(local_app_data) / "Ollama" / "server.log"
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 2_000_000))
        return handle.read().decode("utf-8", errors="replace")


def _processor_for_model(output: str, model: str) -> str | None:
    for line in output.splitlines():
        if model in line:
            match = re.search(r"(100%\s+GPU|\d+%\s+GPU(?:\s+\d+%\s+CPU)?)", line, re.I)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).upper()
    return None


def ollama_processor_for_model(
    model: str,
    *,
    base_url: str,
    command_runner: OllamaCommandRunner | None = None,
) -> str | None:
    active_base_url = validate_ollama_base_url(base_url)
    runner = command_runner or _default_command_runner
    return _processor_for_model(
        runner(("ollama", "ps"), {"OLLAMA_HOST": active_base_url}), model
    )


def _latest_layer_count(log_text: str) -> tuple[int | None, int | None]:
    matches = re.findall(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers", log_text, re.I)
    if not matches:
        return None, None
    loaded, total = matches[-1]
    return int(loaded), int(total)


def probe_ollama_models(
    *,
    preflight: Mapping[str, Any],
    approved_probe_id: str,
    client: OllamaAdminClient | None = None,
    command_runner: OllamaCommandRunner | None = None,
    windows_log_reader: Callable[[], str] | None = None,
) -> dict[str, Any]:
    expected_probe_id = _probe_id(preflight)
    if preflight.get("probeId") != expected_probe_id:
        raise CanonicalClassificationError("relatório de preflight foi alterado")
    if approved_probe_id != expected_probe_id:
        raise CanonicalClassificationError("aprovação do probe ausente ou divergente")
    if preflight.get("readyForProbe") is not True:
        raise CanonicalClassificationError("preflight ainda não está pronto para o probe")

    active_client = client or HttpOllamaAdminClient()
    active_base_url = validate_ollama_base_url(active_client.base_url)
    expected_base_url = validate_ollama_base_url(
        str(cast(Mapping[str, Any], preflight["ollama"])["baseUrl"])
    )
    if active_base_url != expected_base_url:
        raise CanonicalClassificationError(
            "cliente administrativo divergiu do endpoint loopback aprovado"
        )
    runner = command_runner or _default_command_runner
    read_log = windows_log_reader or _default_windows_log_reader
    expected_tags = [target.tag for target in OLLAMA_BENCHMARK_TARGETS]
    installed_rows = cast(list[dict[str, Any]], preflight["targets"])
    installed_by_tag = {str(item["tag"]): item for item in installed_rows}
    results: list[dict[str, Any]] = []
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean", "const": True}},
    }

    for tag in expected_tags:
        primary_error: BaseException | None = None
        try:
            response = active_client.chat(
                {
                    "model": tag,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Responda somente com o JSON solicitado.",
                        },
                        {"role": "user", "content": '{"ok":true}'},
                    ],
                    "stream": False,
                    "format": schema,
                    "think": False,
                    "keep_alive": "5m",
                    "options": {
                        "temperature": 0,
                        "num_ctx": DEFAULT_CONTEXT_LENGTH,
                        "num_predict": 32,
                        "seed": 0,
                    },
                }
            )
            message = response.get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            try:
                structured = json.loads(content) if isinstance(content, str) else None
            except json.JSONDecodeError as exc:
                raise CanonicalClassificationError(
                    f"probe estruturado falhou para {tag}"
                ) from exc
            if structured != {"ok": True}:
                raise CanonicalClassificationError(f"probe estruturado falhou para {tag}")

            running = next(
                (item for item in active_client.running_models() if _model_name(item) == tag),
                None,
            )
            if running is None:
                raise CanonicalClassificationError(f"{tag} não apareceu em /api/ps")
            processor = ollama_processor_for_model(
                tag,
                base_url=active_base_url,
                command_runner=runner,
            )
            size = _integer(running, "size")
            size_vram = _integer(running, "size_vram")
            if processor != "100% GPU":
                raise CanonicalClassificationError(
                    f"{tag} não atingiu o requisito de 100% GPU"
                )
            loaded_layers, total_layers = _latest_layer_count(read_log())
            model_info = installed_by_tag[tag]
            context_length = _integer(running, "context_length")
            if context_length != DEFAULT_CONTEXT_LENGTH:
                raise CanonicalClassificationError(
                    f"{tag} não carregou com contexto {DEFAULT_CONTEXT_LENGTH}"
                )
            installed_digest = model_info.get("digest")
            running_digest = running.get("digest")
            if (
                not isinstance(installed_digest, str)
                or not installed_digest
                or running_digest != installed_digest
            ):
                raise CanonicalClassificationError(
                    f"digest carregado de {tag} divergiu do modelo aprovado"
                )
            quantization = model_info.get("quantization")
            target = next(item for item in OLLAMA_BENCHMARK_TARGETS if item.tag == tag)
            if (
                not isinstance(quantization, str)
                or quantization.casefold()
                != target.expected_quantization.casefold()
            ):
                raise CanonicalClassificationError(
                    f"quantização carregada de {tag} divergiu do plano"
                )
            results.append(
                {
                    "tag": tag,
                    "digest": model_info.get("digest"),
                    "quantization": model_info.get("quantization"),
                    "structuredOutput": True,
                    "processor": processor,
                    "sizeBytes": size,
                    "sizeVramBytes": size_vram,
                    "contextLength": context_length,
                    "loadedLayers": loaded_layers,
                    "totalLayers": total_layers,
                    "layerDetailReason": (
                        None
                        if loaded_layers is not None
                        else "mensagem de offload não encontrada no log do Ollama"
                    ),
                    "totalDurationNs": _integer(response, "total_duration"),
                    "loadDurationNs": _integer(response, "load_duration"),
                    "promptTokens": _integer(response, "prompt_eval_count"),
                    "outputTokens": _integer(response, "eval_count"),
                }
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                active_client.unload(tag)
            except Exception as cleanup_error:
                if primary_error is None:
                    raise CanonicalClassificationError(
                        f"falha ao descarregar {tag} depois do probe"
                    ) from cleanup_error

    report = {
        "schemaVersion": 1,
        "kind": "ollama-local-preflight-probe",
        "probeId": expected_probe_id,
        "probedAt": _now(),
        "networkScope": "loopback",
        "ollama": preflight["ollama"],
        "models": results,
        "readyForBenchmark": len(results) == len(expected_tags),
    }
    report["probeReportId"] = ollama_probe_report_id(report)
    return report
