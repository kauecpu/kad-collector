from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMETER_NAMES = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "yclid",
        "_ga",
    }
)


def _is_tracking_parameter(name: str) -> bool:
    normalized = name.casefold()
    return normalized.startswith("utm_") or normalized in _TRACKING_PARAMETER_NAMES


def canonicalize_url(url: str) -> str:
    """Return a stable, audit-safe URL identity without changing its resource path.

    Fragments and known analytics parameters do not identify a downloaded resource. Other
    query parameters are preserved and sorted, because they may select a distinct page or
    document. The original URL remains available to callers for provenance.
    """

    raw = url.strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"URL HTTP(S) invalida para canonicalizacao: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL com credenciais nao pode ser canonicalizada")

    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"porta invalida na URL: {url}") from exc
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None or default_port else f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_parameter(key)
    ]
    query = urlencode(sorted(query_pairs, key=lambda pair: (pair[0], pair[1])), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))
