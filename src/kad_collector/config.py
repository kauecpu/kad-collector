from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from .models import AppConfig


class ConfigError(ValueError):
    """Configuracao ausente, invalida ou insegura."""


def load_config(path: Path) -> AppConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"arquivo de configuracao nao encontrado: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML invalido em {path}: {exc}") from exc

    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc

    for source in config.sources:
        for url in source.start_urls:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ConfigError(f"URL inicial invalida em {source.id}: {url}")
            if parsed.hostname.lower().rstrip(".") not in source.allowed_hosts:
                raise ConfigError(
                    f"host da URL inicial nao consta em allowed_hosts ({source.id}): {url}"
                )
        for pattern in (
            source.include_patterns
            + source.exclude_patterns
            + source.exam_patterns
            + source.answer_key_patterns
        ):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"regex invalida em {source.id}: {pattern!r}") from exc

    return config


def config_for_urls(config: AppConfig, urls: list[str]) -> AppConfig:
    """Restringe a execucao a links informados e a fontes de conteudo habilitadas."""

    requested_urls = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
    if not requested_urls:
        raise ConfigError("informe pelo menos um link para o fluxo semiautomatico")

    urls_by_source: dict[str, list[str]] = {}
    for url in requested_urls:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            raise ConfigError(f"URL informada e invalida: {url}")
        matching = [source for source in config.sources if host in source.allowed_hosts]
        if not matching:
            raise ConfigError(f"nenhuma fonte cadastrada permite o host {host}")
        enabled = [source for source in matching if source.enabled]
        if not enabled:
            raise ConfigError(f"a fonte cadastrada para {host} nao esta habilitada")
        content_sources = [source for source in enabled if source.access_mode == "content"]
        if not content_sources:
            raise ConfigError(f"a fonte {host} esta habilitada somente para referencias")
        if len(content_sources) > 1:
            raise ConfigError(
                f"mais de uma fonte de conteudo habilitada permite {host}; "
                "mantenha uma definicao inequivoca para links avulsos"
            )
        source = content_sources[0]
        urls_by_source.setdefault(source.id, []).append(url)

    selected_sources = [
        source.model_copy(update={"start_urls": urls_by_source[source.id]})
        for source in config.sources
        if source.id in urls_by_source
    ]
    return AppConfig(collector=config.collector, sources=selected_sources)
