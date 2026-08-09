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
