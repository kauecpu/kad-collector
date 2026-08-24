from __future__ import annotations

import json
import os
from typing import Any, cast

from .canonical_classification import (
    CANONICAL_AI_INSTRUCTIONS,
    CanonicalAIProvider,
    CanonicalAIRequest,
    CanonicalAIResponse,
    CanonicalAIResult,
    CanonicalClassificationError,
    canonical_ai_response_schema,
)


def canonical_ai_messages(request: CanonicalAIRequest) -> list[dict[str, str]]:
    """Build the production prompt used by providers and controlled benchmarks."""
    payload = {
        "outputSchema": canonical_ai_response_schema(request.requested_fields),
        "request": request.safe_payload(),
    }
    return [
        {
            "role": "system",
            "content": (
                f"{CANONICAL_AI_INSTRUCTIONS} "
                "Responda somente com um objeto JSON compatível com o schema fornecido."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _token_usage(completion: Any) -> tuple[int | None, int | None]:
    usage = getattr(completion, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    return (
        input_tokens if isinstance(input_tokens, int) else None,
        output_tokens if isinstance(output_tokens, int) else None,
    )


def _completion_payload(completion: Any, *, accept_parsed: bool = False) -> dict[str, Any]:
    choices = getattr(completion, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise CanonicalClassificationError("provedor retornou resposta sem alternativas")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise CanonicalClassificationError("provedor retornou resposta sem mensagem")

    if accept_parsed:
        parsed = getattr(message, "parsed", None)
        if isinstance(parsed, CanonicalAIResponse):
            return parsed.model_dump(mode="json")
        if isinstance(parsed, dict):
            return cast(dict[str, Any], parsed)

    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise CanonicalClassificationError("provedor retornou resposta vazia")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CanonicalClassificationError("provedor retornou JSON inválido") from exc
    if not isinstance(payload, dict):
        raise CanonicalClassificationError("provedor retornou JSON que não é um objeto")
    return cast(dict[str, Any], payload)


class _OpenAICompatibleCanonicalProvider:
    name = "compatible"
    default_model = ""
    model_env = ""
    api_key_envs: tuple[str, ...] = ()
    base_url_env = ""
    default_base_url = ""

    def __init__(
        self,
        model: str | None = None,
        *,
        client: Any | None = None,
        max_retries: int = 2,
    ) -> None:
        self.model = model or os.environ.get(self.model_env, self.default_model)
        self._configuration_error: str | None = None
        if client is not None:
            self._client = client
            return

        api_key = next(
            (os.environ[name] for name in self.api_key_envs if os.environ.get(name)),
            None,
        )
        base_url = os.environ.get(self.base_url_env, self.default_base_url)
        missing = [*self.api_key_envs] if not api_key else []
        if not base_url:
            missing.append(self.base_url_env)
        if missing:
            self._client = None
            names = " ou ".join(missing)
            self._configuration_error = (
                f"provedor {self.name} indisponível; configure {names}"
            )
            return
        try:
            from openai import OpenAI
        except ImportError:
            self._client = None
            self._configuration_error = (
                "dependência openai ausente; execute pip install -e ."
            )
            return
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=180.0,
            max_retries=max_retries,
        )

    def _require_client(self) -> Any:
        if self._client is None:
            raise CanonicalClassificationError(
                self._configuration_error or f"provedor {self.name} indisponível"
            )
        return self._client

    def _create_completion(self, request: CanonicalAIRequest) -> Any:
        raise NotImplementedError

    def enrich(self, request: CanonicalAIRequest) -> CanonicalAIResult:
        completion = self._create_completion(request)
        input_tokens, output_tokens = _token_usage(completion)
        return CanonicalAIResult(
            response=_completion_payload(completion),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


class GeminiCanonicalEnrichmentProvider(_OpenAICompatibleCanonicalProvider):
    name = "gemini"
    default_model = "gemini-3.7-flash"
    model_env = "GEMINI_MODEL"
    api_key_envs = ("GEMINI_API_KEY",)
    base_url_env = "GEMINI_BASE_URL"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

    def _create_completion(self, request: CanonicalAIRequest) -> Any:
        client = self._require_client()
        return client.beta.chat.completions.parse(
            model=self.model,
            messages=canonical_ai_messages(request),
            response_format=CanonicalAIResponse,
            reasoning_effort="low",
            max_tokens=2_000,
        )

    def enrich(self, request: CanonicalAIRequest) -> CanonicalAIResult:
        completion = self._create_completion(request)
        input_tokens, output_tokens = _token_usage(completion)
        return CanonicalAIResult(
            response=_completion_payload(completion, accept_parsed=True),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


class QwenCanonicalEnrichmentProvider(_OpenAICompatibleCanonicalProvider):
    name = "qwen"
    default_model = "qwen3.7-plus"
    model_env = "QWEN_MODEL"
    api_key_envs = ("DASHSCOPE_API_KEY", "QWEN_API_KEY")
    base_url_env = "QWEN_BASE_URL"
    default_base_url = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"

    def _create_completion(self, request: CanonicalAIRequest) -> Any:
        client = self._require_client()
        return client.chat.completions.create(
            model=self.model,
            messages=canonical_ai_messages(request),
            response_format={"type": "json_object"},
            max_tokens=2_000,
            extra_body={"enable_thinking": False},
        )


class DeepSeekCanonicalEnrichmentProvider(_OpenAICompatibleCanonicalProvider):
    name = "deepseek"
    default_model = "deepseek-v4-pro"
    model_env = "DEEPSEEK_MODEL"
    api_key_envs = ("DEEPSEEK_API_KEY",)
    base_url_env = "DEEPSEEK_BASE_URL"
    default_base_url = "https://api.deepseek.com"

    def _create_completion(self, request: CanonicalAIRequest) -> Any:
        client = self._require_client()
        return client.chat.completions.create(
            model=self.model,
            messages=canonical_ai_messages(request),
            response_format={"type": "json_object"},
            max_tokens=2_000,
            extra_body={"thinking": {"type": "disabled"}},
        )


def create_canonical_ai_provider(
    provider: str,
    model: str | None = None,
    *,
    max_retries: int = 2,
) -> CanonicalAIProvider:
    if provider == "ollama":
        from .ollama_ai_provider import OllamaCanonicalEnrichmentProvider

        return cast(
            CanonicalAIProvider,
            OllamaCanonicalEnrichmentProvider(model, max_retries=max_retries),
        )

    providers: dict[str, type[Any]] = {
        "gemini": GeminiCanonicalEnrichmentProvider,
        "qwen": QwenCanonicalEnrichmentProvider,
        "deepseek": DeepSeekCanonicalEnrichmentProvider,
    }
    provider_class = providers.get(provider)
    if provider_class is None:
        raise CanonicalClassificationError(f"provedor de IA desconhecido: {provider}")
    return cast(CanonicalAIProvider, provider_class(model, max_retries=max_retries))
