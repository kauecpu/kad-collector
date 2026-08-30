from __future__ import annotations

import hashlib
import os
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .collection_state import CollectionStateStore
from .discovery import detect_access_challenge
from .models import CollectionTelemetryEvent
from .scrapling_transport import (
    PersistentScraplingSession,
    ScraplingSessionError,
    ScraplingSessionFactory,
    ScraplingUnavailableError,
)
from .security import FetchError, validate_public_url
from .url_utils import canonicalize_url

_REDIRECTS = {301, 302, 303, 307, 308}
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)


class _ChallengePageDetected(Exception):
    """A download returned an HTML challenge page instead of the real file."""

    def __init__(self, content_type: str) -> None:
        super().__init__(content_type)
        self.content_type = content_type


def _content_type_of(headers: httpx.Headers) -> str:
    return str(headers.get("Content-Type", "")).split(";", 1)[0].strip().casefold()


def _message(headers: httpx.Headers) -> Message:
    result = Message()
    for name, value in headers.multi_items():
        result[name] = value
    return result


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        return max(0.0, (target - datetime.now(UTC)).total_seconds())


def _is_retryable_status(status_code: int | None) -> bool:
    return status_code in _RETRYABLE or bool(status_code and 500 <= status_code <= 599)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class EngineHttpResult:
    url: str
    status_code: int
    headers: Message
    body: bytes
    cache_status: str
    attempt: int
    duration_ms: int
    original_url: str | None = None
    canonical_url: str | None = None


@dataclass(frozen=True)
class EngineDownload:
    url: str
    status_code: int
    headers: Message
    path: Path
    sha256: str
    size_bytes: int
    cache_status: str
    resumed: bool
    attempt: int
    duration_ms: int
    original_url: str | None = None
    canonical_url: str | None = None


class HostScheduler:
    """Limits concurrency and request cadence independently for each host."""

    def __init__(self, interval_seconds: float, max_concurrency: int) -> None:
        self.interval_seconds = interval_seconds
        self.max_concurrency = max_concurrency
        self._guard = threading.Lock()
        self._slots: dict[str, threading.BoundedSemaphore] = {}
        self._rate_locks: dict[str, threading.Lock] = {}
        self._last_request: dict[str, float] = {}

    @contextmanager
    def slot(self, url: str, *, interval_seconds: float | None = None) -> Iterator[float]:
        host = (urlsplit(url).hostname or "").lower()
        with self._guard:
            semaphore = self._slots.setdefault(
                host, threading.BoundedSemaphore(self.max_concurrency)
            )
            rate_lock = self._rate_locks.setdefault(host, threading.Lock())
        semaphore.acquire()
        waited = 0.0
        try:
            with rate_lock:
                interval = self.interval_seconds if interval_seconds is None else interval_seconds
                remaining = interval - (time.monotonic() - self._last_request.get(host, 0.0))
                if remaining > 0:
                    time.sleep(remaining)
                    waited = remaining
                self._last_request[host] = time.monotonic()
            yield waited
        finally:
            semaphore.release()


class CollectionHttpClient:
    """Streaming HTTP client with retries, conditional cache and sanitized telemetry."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float,
        connect_timeout: float,
        interval_seconds: float,
        max_concurrency: int,
        max_retries: int,
        retry_max_delay_seconds: float,
        state_store: CollectionStateStore,
        run_id: str,
        source_id: str,
        conditional_cache: bool,
        disk_quota_bytes: int | None,
        development_cache: bool = False,
        random_source: random.Random | None = None,
        page_transport: Literal["http", "scrapling"] = "http",
        scrapling_session_factory: ScraplingSessionFactory | None = None,
        cloudflare_bypass_enabled: bool = True,
        progress_callback: Callable[[CollectionTelemetryEvent], None] | None = None,
    ) -> None:
        self.scheduler = HostScheduler(interval_seconds, max_concurrency)
        self.max_retries = max_retries
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self.state_store = state_store
        self.run_id = run_id
        self.source_id = source_id
        self.conditional_cache = conditional_cache
        self.development_cache = development_cache
        self.disk_quota_bytes = disk_quota_bytes
        self.random = random_source or random.Random()
        if page_transport not in {"http", "scrapling"}:
            raise ValueError(f"transporte de pagina desconhecido: {page_transport}")
        self.page_transport = page_transport
        self.progress_callback = progress_callback
        self.scrapling = (
            PersistentScraplingSession(
                user_agent=user_agent,
                timeout_seconds=timeout,
                session_factory=scrapling_session_factory,
                solve_cloudflare=cloudflare_bypass_enabled,
            )
            if page_transport == "scrapling"
            else None
        )
        self.client = httpx.Client(
            follow_redirects=False,
            http2=True,
            verify=True,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            limits=httpx.Limits(
                max_connections=max(4, max_concurrency * 2),
                max_keepalive_connections=max(2, max_concurrency),
            ),
            headers={
                "User-Agent": user_agent,
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
            },
        )

    def abort(self) -> None:
        """Best-effort immediate shutdown used by the desktop cancel supervisor."""

        self.close()

    def disable_scrapling(self) -> None:
        """Close the Patchright loop before another sync browser is started.

        Scrapling's persistent session owns a Patchright asyncio loop in the
        current thread. Starting Playwright's synchronous API in that same
        thread raises ``Sync API inside the asyncio loop``. HTML discovery has
        already completed by the time the optional browser discovery phase
        runs, so the session can be closed safely before that phase.
        """

        if self.scrapling is None:
            return
        try:
            self.scrapling.close()
        finally:
            self.scrapling = None

    def close(self) -> None:
        scrapling_error: ScraplingSessionError | None = None
        if self.scrapling is not None:
            try:
                self.scrapling.close()
            except ScraplingSessionError as exc:
                scrapling_error = exc
        self.client.close()
        if scrapling_error is not None:
            raise scrapling_error

    def __enter__(self) -> CollectionHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _event(
        self,
        *,
        url: str,
        strategy: str,
        outcome: str,
        status_code: int | None,
        duration_ms: int,
        bytes_received: int,
        attempt: int,
        wait_seconds: float,
        cache_status: str,
        detail: str | None = None,
    ) -> None:
        self.state_store.add_event(
            self.run_id,
            CollectionTelemetryEvent(
                occurred_at=datetime.now(UTC),
                source_id=self.source_id,
                url=_safe_url(url),
                strategy=strategy,
                outcome=outcome,
                status_code=status_code,
                duration_ms=duration_ms,
                bytes_received=bytes_received,
                attempt=attempt,
                wait_seconds=wait_seconds,
                cache_status=cache_status,  # type: ignore[arg-type]
                detail=detail,
            ),
        )
        if self.progress_callback is not None:
            self.progress_callback(
                CollectionTelemetryEvent(
                    occurred_at=datetime.now(UTC),
                    source_id=self.source_id,
                    url=_safe_url(url),
                    strategy=strategy,
                    outcome=outcome,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    bytes_received=bytes_received,
                    attempt=attempt,
                    wait_seconds=wait_seconds,
                    cache_status=cache_status,  # type: ignore[arg-type]
                    detail=detail,
                )
            )

    def _cache_headers(self, url: str) -> tuple[dict[str, str], dict[str, object] | None]:
        if not self.conditional_cache and not self.development_cache:
            return {}, None
        entry = self.state_store.cache_entry(url)
        if entry is None:
            return {}, None
        path = Path(str(entry["local_path"]))
        if (
            not path.is_file()
            or path.stat().st_size != int(entry["size_bytes"])
            or sha256_file(path) != entry["sha256"]
        ):
            self.state_store.invalidate_cache(url)
            return {}, None
        headers: dict[str, str] = {}
        if entry.get("etag"):
            headers["If-None-Match"] = str(entry["etag"])
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = str(entry["last_modified"])
        return headers, entry

    @staticmethod
    def _cached_path(entry: dict[str, object]) -> Path | None:
        path = Path(str(entry["local_path"]))
        try:
            valid = (
                path.is_file()
                and path.stat().st_size == int(str(entry["size_bytes"]))
                and sha256_file(path) == str(entry["sha256"])
            )
        except (OSError, ValueError):
            valid = False
        return path if valid else None

    def get(
        self,
        url: str,
        allowed_hosts: list[str],
        max_bytes: int,
        *,
        strategy: str = "html",
        interval_seconds: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> EngineHttpResult:
        conditional, entry = self._cache_headers(url)
        canonical_url = canonicalize_url(url)
        if self.development_cache and entry is not None:
            path = self._cached_path(entry)
            if path is not None:
                if int(str(entry["size_bytes"])) > max_bytes:
                    raise FetchError("resposta em cache excede o limite configurado")
                self.state_store.touch_cache(url, status_code=200)
                return EngineHttpResult(
                    url=str(entry["final_url"]),
                    status_code=200,
                    headers=_message(
                        httpx.Headers(
                            {
                                "Content-Type": str(entry["content_type"]),
                                "Content-Length": str(entry["size_bytes"]),
                            }
                        )
                    ),
                    body=path.read_bytes(),
                    cache_status="hit",
                    attempt=0,
                    duration_ms=0,
                    original_url=url,
                    canonical_url=canonical_url,
                )
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json,"
            "application/pdf;q=0.9,*/*;q=0.1",
            **(extra_headers or {}),
            **conditional,
        }
        is_robots_request = urlsplit(url).path.casefold().rstrip("/") == "/robots.txt"
        result = self._request_bytes(
            url,
            allowed_hosts,
            max_bytes,
            strategy=strategy,
            headers=headers,
            interval_seconds=interval_seconds,
            use_scrapling=(
                strategy == "html" and self.scrapling is not None and not is_robots_request
            ),
        )
        if result.status_code == 304 and entry is not None:
            path = Path(str(entry["local_path"]))
            body = path.read_bytes()
            self.state_store.touch_cache(url)
            return EngineHttpResult(
                url=str(entry["final_url"]),
                status_code=200,
                headers=result.headers,
                body=body,
                cache_status="revalidated",
                attempt=result.attempt,
                duration_ms=result.duration_ms,
                original_url=url,
                canonical_url=canonical_url,
            )
        return result

    def _scrapling_response(self, url: str, headers: dict[str, str]) -> httpx.Response:
        assert self.scrapling is not None
        page = self.scrapling.fetch(url, extra_headers=headers)
        final_url = str(page.url)
        return httpx.Response(
            status_code=int(page.status),
            headers={str(name): str(value) for name, value in page.headers.items()},
            content=bytes(page.body),
            request=httpx.Request("GET", final_url),
        )

    def _request_bytes(
        self,
        url: str,
        allowed_hosts: list[str],
        max_bytes: int,
        *,
        strategy: str,
        headers: dict[str, str],
        interval_seconds: float | None,
        use_scrapling: bool = False,
    ) -> EngineHttpResult:
        canonical_url = canonicalize_url(url)
        for attempt in range(1, self.max_retries + 2):
            started = time.monotonic()
            current_url = url
            waited = 0.0
            response: httpx.Response | None = None
            try:
                for _redirect in range(6):
                    validate_public_url(current_url, allowed_hosts)
                    with self.scheduler.slot(
                        current_url, interval_seconds=interval_seconds
                    ) as delay:
                        waited += delay
                        response = (
                            self._scrapling_response(current_url, headers)
                            if use_scrapling
                            else self.client.get(current_url, headers=headers)
                        )
                    validate_public_url(str(response.url), allowed_hosts)
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("Location")
                        if not location:
                            raise FetchError(
                                "redirecionamento sem cabecalho Location",
                                response.status_code,
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code == 304:
                        return EngineHttpResult(
                            url=str(response.url),
                            status_code=304,
                            headers=_message(response.headers),
                            body=b"",
                            cache_status="revalidated",
                            attempt=attempt,
                            duration_ms=int((time.monotonic() - started) * 1000),
                            original_url=url,
                            canonical_url=canonical_url,
                        )
                    if response.status_code >= 400:
                        raise FetchError(
                            f"HTTP {response.status_code} ao acessar {current_url}",
                            response.status_code,
                        )
                    body = response.content
                    if len(body) > max_bytes:
                        raise FetchError("resposta excede o limite configurado")
                    duration = int((time.monotonic() - started) * 1000)
                    self._event(
                        url=url,
                        strategy=strategy,
                        outcome="success",
                        status_code=response.status_code,
                        duration_ms=duration,
                        bytes_received=len(body),
                        attempt=attempt,
                        wait_seconds=waited,
                        cache_status="miss",
                    )
                    return EngineHttpResult(
                        url=str(response.url),
                        status_code=response.status_code,
                        headers=_message(response.headers),
                        body=body,
                        cache_status=(
                            "miss"
                            if self.conditional_cache or self.development_cache
                            else "disabled"
                        ),
                        attempt=attempt,
                        duration_ms=duration,
                        original_url=url,
                        canonical_url=canonical_url,
                    )
                raise FetchError("numero maximo de redirecionamentos excedido")
            except (
                httpx.TransportError,
                ScraplingSessionError,
                ScraplingUnavailableError,
                FetchError,
            ) as exc:
                status = exc.status_code if isinstance(exc, FetchError) else None
                retryable = (
                    not isinstance(exc, (FetchError, ScraplingUnavailableError))
                    or _is_retryable_status(status)
                )
                if not retryable or attempt > self.max_retries:
                    outcome = (
                        "access_denied"
                        if status == 403
                        else "retry_exhausted"
                        if retryable
                        else "failed"
                    )
                    self._event(
                        url=url,
                        strategy=strategy,
                        outcome=outcome,
                        status_code=status,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        bytes_received=0,
                        attempt=attempt,
                        wait_seconds=waited,
                        cache_status="miss",
                        detail=str(exc),
                    )
                    raise FetchError(
                        str(exc),
                        status,
                    ) from exc
                retry_after = _retry_after(
                    None if response is None else response.headers.get("Retry-After")
                )
                retry_delay: float = (
                    retry_after
                    if retry_after is not None
                    else float(2 ** (attempt - 1)) + self.random.random()
                )
                time.sleep(min(retry_delay, self.retry_max_delay_seconds))
        raise FetchError(f"tentativas esgotadas ao acessar {url}")

    def download(
        self,
        url: str,
        allowed_hosts: list[str],
        max_bytes: int,
        destination_dir: Path,
        *,
        strategy: str,
        interval_seconds: float | None = None,
        resume: bool = True,
    ) -> EngineDownload:
        destination_dir.mkdir(parents=True, exist_ok=True)
        canonical_url = canonicalize_url(url)
        token = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
        partial = destination_dir / f".{token}.part"
        legacy_token = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        legacy_partial = destination_dir / f".{legacy_token}.part"
        if not partial.exists() and legacy_partial.exists() and legacy_token != token:
            partial = legacy_partial
        other_disk_usage = (
            sum(path.stat().st_size for path in destination_dir.iterdir() if path != partial)
            if self.disk_quota_bytes is not None
            else 0
        )
        conditional, entry = self._cache_headers(url)

        if self.development_cache and entry is not None:
            cached = self._cached_path(entry)
            if cached is not None:
                if int(str(entry["size_bytes"])) > max_bytes:
                    raise FetchError("download em cache excede o limite configurado")
                partial.write_bytes(cached.read_bytes())
                self.state_store.touch_cache(url, status_code=200)
                return EngineDownload(
                    url=str(entry["final_url"]),
                    status_code=200,
                    headers=_message(
                        httpx.Headers(
                            {
                                "Content-Type": str(entry["content_type"]),
                                "Content-Length": str(entry["size_bytes"]),
                            }
                        )
                    ),
                    path=partial,
                    sha256=str(entry["sha256"]),
                    size_bytes=int(str(entry["size_bytes"])),
                    cache_status="hit",
                    resumed=False,
                    attempt=0,
                    duration_ms=0,
                    original_url=url,
                    canonical_url=canonical_url,
                )

        for attempt in range(1, self.max_retries + 2):
            started = time.monotonic()
            current_url = url
            waited = 0.0
            response_headers: httpx.Headers | None = None
            status: int | None = None
            try:
                for _redirect in range(6):
                    validate_public_url(current_url, allowed_hosts)
                    existing = partial.stat().st_size if resume and partial.exists() else 0
                    # PDF bytes must remain byte-for-byte stable when a .part
                    # file is resumed.  Do not let a proxy/CDN negotiate a
                    # compressed 206 response that could be appended to the
                    # partial file or decoded inconsistently by httpx.
                    headers = {
                        "Accept": "application/pdf,*/*;q=0.1",
                        "Accept-Encoding": "identity",
                        **conditional,
                    }
                    if existing:
                        headers["Range"] = f"bytes={existing}-"
                        validator = (
                            None
                            if entry is None
                            else (entry.get("etag") or entry.get("last_modified"))
                        )
                        if validator:
                            headers["If-Range"] = str(validator)
                    with self.scheduler.slot(
                        current_url, interval_seconds=interval_seconds
                    ) as delay:
                        waited += delay
                        with self.client.stream("GET", current_url, headers=headers) as response:
                            status = response.status_code
                            response_headers = response.headers
                            if status in _REDIRECTS:
                                location = response.headers.get("Location")
                                if not location:
                                    raise FetchError(
                                        "redirecionamento sem cabecalho Location", status
                                    )
                                current_url = urljoin(current_url, location)
                                continue
                            if status == 304 and entry is not None:
                                cached = Path(str(entry["local_path"]))
                                self.state_store.touch_cache(url)
                                return EngineDownload(
                                    url=str(entry["final_url"]),
                                    status_code=200,
                                    headers=_message(response.headers),
                                    path=cached,
                                    sha256=str(entry["sha256"]),
                                    size_bytes=int(str(entry["size_bytes"])),
                                    cache_status="revalidated",
                                    resumed=False,
                                    attempt=attempt,
                                    duration_ms=int((time.monotonic() - started) * 1000),
                                    original_url=url,
                                    canonical_url=canonical_url,
                                )
                            if status >= 400:
                                raise FetchError(f"HTTP {status} ao acessar {current_url}", status)
                            if self.scrapling is not None and "html" in _content_type_of(
                                response.headers
                            ):
                                raise _ChallengePageDetected(_content_type_of(response.headers))
                            resumed = existing > 0 and status == 206
                            if resumed:
                                content_range = response.headers.get("Content-Range", "")
                                match = _CONTENT_RANGE.fullmatch(content_range.strip())
                                if match is None or int(match.group(1)) != existing:
                                    raise FetchError("Content-Range invalido para retomada", status)
                            if not resumed:
                                existing = 0
                            declared = response.headers.get("Content-Length")
                            if declared and existing + int(declared) > max_bytes:
                                raise FetchError("resposta excede o limite configurado")
                            written = existing
                            with partial.open("ab" if resumed else "wb") as stream:
                                for chunk in response.iter_bytes(256 * 1024):
                                    written += len(chunk)
                                    if written > max_bytes:
                                        raise FetchError("resposta excede o limite configurado")
                                    if self.disk_quota_bytes is not None and (
                                        other_disk_usage + written > self.disk_quota_bytes
                                    ):
                                        raise FetchError("download excede a quota de disco")
                                    stream.write(chunk)
                                stream.flush()
                                os.fsync(stream.fileno())
                            digest = sha256_file(partial)
                            duration = int((time.monotonic() - started) * 1000)
                            self._event(
                                url=url,
                                strategy=strategy,
                                outcome="resumed" if resumed else "downloaded",
                                status_code=status,
                                duration_ms=duration,
                                bytes_received=written,
                                attempt=attempt,
                                wait_seconds=waited,
                                cache_status="miss",
                            )
                            return EngineDownload(
                                url=str(response.url),
                                status_code=status,
                                headers=_message(response.headers),
                                path=partial,
                                sha256=digest,
                                size_bytes=written,
                                cache_status="miss",
                                resumed=resumed,
                                attempt=attempt,
                                duration_ms=duration,
                                original_url=url,
                                canonical_url=canonical_url,
                            )
                raise FetchError("numero maximo de redirecionamentos excedido")
            except _ChallengePageDetected as exc:
                if self.scrapling is None:
                    raise FetchError(
                        f"resposta bloqueada por desafio ({exc.content_type}) ao acessar "
                        f"{current_url}",
                        status,
                    ) from exc
                scrapling_response = self._scrapling_response(current_url, headers)
                body = scrapling_response.content
                scrapling_content_type = _content_type_of(scrapling_response.headers)
                if "html" in scrapling_content_type:
                    challenge = detect_access_challenge(
                        "",
                        body[:20_000].decode("utf-8", errors="ignore"),
                        str(scrapling_response.url),
                    )
                    detail = challenge or "conteudo HTML inesperado"
                    duration = int((time.monotonic() - started) * 1000)
                    self._event(
                        url=url,
                        strategy=strategy,
                        outcome="access_denied",
                        status_code=scrapling_response.status_code,
                        duration_ms=duration,
                        bytes_received=0,
                        attempt=attempt,
                        wait_seconds=waited,
                        cache_status="miss",
                        detail=(
                            "desafio Cloudflare/Turnstile persistiu mesmo com "
                            f"solve_cloudflare habilitado: {detail}"
                        ),
                    )
                    raise FetchError(
                        "PCI/Cloudflare bloqueou o download mesmo com solve_cloudflare "
                        f"habilitado ({detail})",
                        scrapling_response.status_code,
                    ) from exc
                if len(body) > max_bytes:
                    raise FetchError("resposta excede o limite configurado") from exc
                partial.write_bytes(body)
                digest = sha256_file(partial)
                duration = int((time.monotonic() - started) * 1000)
                self._event(
                    url=url,
                    strategy=strategy,
                    outcome="downloaded",
                    status_code=scrapling_response.status_code,
                    duration_ms=duration,
                    bytes_received=len(body),
                    attempt=attempt,
                    wait_seconds=waited,
                    cache_status="miss",
                    detail=(
                        "baixado via Scrapling (solve_cloudflare) apos desafio "
                        "detectado na resposta HTTP direta"
                    ),
                )
                return EngineDownload(
                    url=str(scrapling_response.url),
                    status_code=scrapling_response.status_code,
                    headers=_message(scrapling_response.headers),
                    path=partial,
                    sha256=digest,
                    size_bytes=len(body),
                    cache_status="miss",
                    resumed=False,
                    attempt=attempt,
                    duration_ms=duration,
                    original_url=url,
                    canonical_url=canonical_url,
                )
            except (httpx.TransportError, FetchError, OSError) as exc:
                retryable = not isinstance(exc, FetchError) or _is_retryable_status(status)
                if not retryable or attempt > self.max_retries:
                    outcome = (
                        "access_denied"
                        if status == 403
                        else "retry_exhausted"
                        if retryable
                        else "failed"
                    )
                    self._event(
                        url=url,
                        strategy=strategy,
                        outcome=outcome,
                        status_code=status,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        bytes_received=0,
                        attempt=attempt,
                        wait_seconds=waited,
                        cache_status="miss",
                        detail=str(exc),
                    )
                    raise FetchError(str(exc), status) from exc
                parsed_delay = _retry_after(
                    None if response_headers is None else response_headers.get("Retry-After")
                )
                retry_delay: float = (
                    parsed_delay
                    if parsed_delay is not None
                    else float(2 ** (attempt - 1)) + self.random.random()
                )
                time.sleep(min(retry_delay, self.retry_max_delay_seconds))
        raise FetchError(f"tentativas esgotadas ao baixar {url}")

    def remember_download(
        self,
        *,
        original_url: str,
        result: EngineDownload,
        final_path: Path,
        strategy: str,
    ) -> None:
        if not self.conditional_cache and not self.development_cache:
            return
        self.state_store.store_cache(
            url=original_url,
            final_url=result.url,
            etag=result.headers.get("ETag"),
            last_modified=result.headers.get("Last-Modified"),
            sha256=result.sha256,
            content_type=result.headers.get_content_type(),
            size_bytes=result.size_bytes,
            local_path=final_path,
            status_code=result.status_code,
            strategy=strategy,
        )

    def remember_body(
        self,
        *,
        original_url: str,
        result: EngineHttpResult,
        cache_dir: Path,
        strategy: str,
    ) -> None:
        if (not self.conditional_cache and not self.development_cache) or result.cache_status in {
            "hit",
            "revalidated",
        }:
            return
        cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(result.body).hexdigest()
        destination = cache_dir / digest
        temporary = cache_dir / f".{digest}.tmp"
        if not destination.exists():
            temporary.write_bytes(result.body)
            temporary.replace(destination)
        self.state_store.store_cache(
            url=original_url,
            final_url=result.url,
            etag=result.headers.get("ETag"),
            last_modified=result.headers.get("Last-Modified"),
            sha256=digest,
            content_type=result.headers.get_content_type(),
            size_bytes=len(result.body),
            local_path=destination,
            status_code=result.status_code,
            strategy=strategy,
        )
