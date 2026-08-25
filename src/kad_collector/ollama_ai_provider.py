from __future__ import annotations

import json
import os
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict

from .canonical_ai_providers import canonical_ai_messages
from .canonical_classification import (
    CanonicalAIHTTPError,
    CanonicalAIInvalidJSONError,
    CanonicalAIProviderUnavailableError,
    CanonicalAIRequest,
    CanonicalAIResult,
    CanonicalClassificationError,
    canonical_ai_response_schema,
)

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_CONTEXT_LENGTH = 4096
DEFAULT_OUTPUT_TOKENS = 512


class OllamaBlockingError(CanonicalAIProviderUnavailableError):
    """A local Ollama gate requires operator action before continuing."""


class OllamaUnavailableError(OllamaBlockingError):
    """The local Ollama service cannot complete the request now."""


class OllamaModelMissingError(OllamaBlockingError):
    """The selected Ollama model is not installed locally."""


class OllamaHardwareGateError(OllamaBlockingError):
    """The selected model does not satisfy the local hardware gate."""


class _OllamaMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    role: str
    content: str


class _OllamaChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    message: _OllamaMessage
    done: bool
    done_reason: str | None = None
    total_duration: int | None = None
    load_duration: int | None = None
    prompt_eval_count: int | None = None
    prompt_eval_duration: int | None = None
    eval_count: int | None = None
    eval_duration: int | None = None


def validate_ollama_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http":
        raise CanonicalClassificationError(
            "OLLAMA_BASE_URL deve usar HTTP em uma interface local"
        )
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise CanonicalClassificationError(
            "OLLAMA_BASE_URL deve apontar somente para loopback local"
        )
    if parsed.username is not None or parsed.password is not None:
        raise CanonicalClassificationError(
            "OLLAMA_BASE_URL não aceita credenciais na URL"
        )
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise CanonicalClassificationError(
            "OLLAMA_BASE_URL não aceita caminho, consulta ou fragmento"
        )
    return value.rstrip("/")


class OllamaCanonicalEnrichmentProvider:
    name = "ollama"

    def __init__(
        self,
        model: str | None = None,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 0,
    ) -> None:
        del max_retries
        self.model = model or os.environ.get("OLLAMA_MODEL", "")
        base_url = validate_ollama_base_url(
            os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
        )
        if client is not None:
            try:
                injected_base_url = validate_ollama_base_url(
                    str(client.base_url).rstrip("/")
                )
            except CanonicalClassificationError as exc:
                raise CanonicalClassificationError(
                    "cliente Ollama injetado deve usar loopback local"
                ) from exc
            if injected_base_url != base_url:
                raise CanonicalClassificationError(
                    "cliente Ollama deve usar o mesmo endpoint loopback configurado"
                )
        self._client = client
        if self._client is None and self.model:
            self._client = httpx.Client(
                base_url=base_url,
                timeout=180.0,
                trust_env=False,
            )

    def enrich(self, request: CanonicalAIRequest) -> CanonicalAIResult:
        if not self.model:
            raise CanonicalClassificationError(
                "modelo Ollama ausente; informe --model ou configure OLLAMA_MODEL"
            )
        if self._client is None:
            raise OllamaUnavailableError("cliente Ollama local indisponível")
        payload = {
            "model": self.model,
            "messages": canonical_ai_messages(request),
            "stream": False,
            "format": canonical_ai_response_schema(request),
            "think": False,
            "keep_alive": "5m",
            "options": {
                "temperature": 0,
                "num_ctx": DEFAULT_CONTEXT_LENGTH,
                "num_predict": DEFAULT_OUTPUT_TOKENS,
                "seed": 0,
            },
        }
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.TransportError as exc:
            raise OllamaUnavailableError(
                "Ollama local indisponível; confirme que o serviço está em execução"
            ) from exc

        if response.status_code >= 500:
            raise CanonicalAIHTTPError(
                f"Ollama local indisponível (HTTP {response.status_code})"
            )
        if response.status_code == 404:
            raise OllamaModelMissingError(
                f"modelo Ollama {self.model!r} não está instalado; "
                f"execute 'ollama pull {self.model}' somente após autorização"
            )
        if response.status_code >= 400:
            raise CanonicalAIHTTPError(
                f"Ollama recusou a solicitação (HTTP {response.status_code})"
            )

        try:
            chat = _OllamaChatResponse.model_validate(response.json())
            response_payload = json.loads(chat.message.content)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CanonicalAIInvalidJSONError(
                "Ollama retornou uma resposta inválida"
            ) from exc
        if not isinstance(response_payload, dict):
            raise CanonicalClassificationError(
                "Ollama retornou JSON que não é um objeto"
            )

        return CanonicalAIResult(
            response=cast(dict[str, Any], response_payload),
            input_tokens=chat.prompt_eval_count,
            output_tokens=chat.eval_count,
            estimated_cost=0.0,
            provider_metrics={
                "totalDurationNs": chat.total_duration,
                "loadDurationNs": chat.load_duration,
                "promptEvalDurationNs": chat.prompt_eval_duration,
                "evalDurationNs": chat.eval_duration,
                "doneReason": chat.done_reason,
            },
        )
