from __future__ import annotations

import hashlib
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .collection_state import CollectionStateStore
from .collection_transport import CollectionHttpClient, EngineDownload, EngineHttpResult
from .discovery import (
    BrowserUnavailableError,
    DiscoveredLink,
    ManualActionRequired,
    browser_discover,
    parse_feed,
    parse_json_links,
    parse_sitemap,
    safe_discovered_links,
)
from .filters import document_might_match_filters
from .json_utils import write_json
from .models import (
    AppConfig,
    CollectionFailure,
    CollectionFilters,
    CollectorSettings,
    DiscoveryRecord,
    DocumentRecord,
    DocumentType,
    DownloadManifest,
    SourceDefinition,
)
from .security import FetchError, HttpResult, SafeHttpClient, UnsafeUrlError, validate_public_url

_ORIGINAL_SAFE_HTTP_CLIENT = SafeHttpClient


class CollectorError(RuntimeError):
    """Falha controlada durante uma execucao do coletor."""


class CollectionPaused(CollectorError):
    """A coleta foi pausada e pode continuar a partir do checkpoint."""


class _HttpGetter(Protocol):
    def get(
        self, url: str, allowed_hosts: list[str], max_bytes: int
    ) -> HttpResult | EngineHttpResult: ...


class _LegacyClientAdapter:
    """Keeps injected legacy clients working for local fixtures and integrations."""

    def __init__(self, client: SafeHttpClient) -> None:
        self.client = client

    def get(
        self,
        url: str,
        allowed_hosts: list[str],
        max_bytes: int,
        **_options: object,
    ) -> EngineHttpResult:
        result = self.client.get(url, allowed_hosts, max_bytes)
        return EngineHttpResult(
            url=result.url,
            status_code=result.status_code,
            headers=result.headers,
            body=result.body,
            cache_status="disabled",
            attempt=1,
            duration_ms=0,
        )

    def remember_body(self, **_options: object) -> None:
        return

    def download(
        self,
        url: str,
        allowed_hosts: list[str],
        max_bytes: int,
        destination_dir: Path,
        **_options: object,
    ) -> EngineDownload:
        result = self.client.get(url, allowed_hosts, max_bytes)
        token = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        path = destination_dir / f".{token}.part"
        path.write_bytes(result.body)
        return EngineDownload(
            url=result.url,
            status_code=result.status_code,
            headers=result.headers,
            path=path,
            sha256=hashlib.sha256(result.body).hexdigest(),
            size_bytes=len(result.body),
            cache_status="disabled",
            resumed=False,
            attempt=1,
            duration_ms=0,
        )

    def remember_download(self, **_options: object) -> None:
        return

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        attributes = {name.lower(): value for name, value in attrs}
        style = (attributes.get("style") or "").replace(" ", "").casefold()
        hidden = (
            "hidden" in attributes
            or (attributes.get("aria-hidden") or "").casefold() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        href = attributes.get("href")
        if hidden:
            return
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            self.links.append((self._href, text))
            self._href = None
            self._text = []


def extract_links(html: str, page_url: str) -> list[tuple[str, str]]:
    parser = _LinkParser()
    parser.feed(html)
    return [(urljoin(page_url, href), title) for href, title in parser.links]


def _is_html_page(result: HttpResult | EngineHttpResult) -> bool:
    """Accept mislabeled HTML only when the body has an HTML document signature."""
    content_type = result.headers.get_content_type()
    if content_type in {"text/html", "application/xhtml+xml"}:
        return True
    if content_type not in {"text/plain", "application/octet-stream"}:
        return False
    prefix = result.body[:16_384]
    if b"\x00" in prefix:
        return False
    text = prefix.decode("utf-8", errors="ignore").lstrip("\ufeff\t\r\n ").casefold()
    return text.startswith("<!doctype html") or (
        "<html" in text and ("<head" in text or "<body" in text)
    )


def classify_document(url: str, title: str, source: SourceDefinition) -> DocumentType:
    candidate = f"{title}\n{url}"
    if any(re.search(pattern, candidate) for pattern in source.answer_key_patterns):
        return "answer_key"
    if any(re.search(pattern, candidate) for pattern in source.exam_patterns):
        return "exam"
    return "other"


def select_document_links(
    html: str, page_url: str, source: SourceDefinition
) -> list[tuple[str, str, DocumentType]]:
    selected: list[tuple[str, str, DocumentType]] = []
    seen: set[str] = set()
    for url, title in extract_links(html, page_url):
        candidate = f"{title}\n{url}"
        if source.exclude_patterns and any(
            re.search(pattern, candidate) for pattern in source.exclude_patterns
        ):
            continue
        if not any(re.search(pattern, candidate) for pattern in source.include_patterns):
            continue
        try:
            validate_public_url(url, source.allowed_hosts, resolve_dns=False)
        except UnsafeUrlError:
            continue
        if url in seen:
            continue
        seen.add(url)
        document_type = classify_document(url, title, source)
        if source.access_mode == "content" and document_type == "other":
            continue
        selected.append((url, title or Path(urlsplit(url).path).name, document_type))
    return selected


def _limit_document_links(
    links: list[tuple[str, str, DocumentType]], limit: int | None
) -> list[tuple[str, str, DocumentType]]:
    if limit is None or len(links) <= limit:
        return links
    exams = [item for item in links if item[2] == "exam"]
    answer_keys = sorted(
        (item for item in links if item[2] == "answer_key"),
        key=lambda item: ("definitiv" not in f"{item[1]} {item[0]}".casefold(), links.index(item)),
    )
    if limit == 1 or not answer_keys:
        return exams[:limit]
    if not exams:
        return answer_keys[:limit]
    key_slots = min(len(answer_keys), max(1, min(4, limit // 4)))
    return [*exams[: limit - key_slots], *answer_keys[:key_slots]]


def select_pagination_links(html: str, page_url: str, source: SourceDefinition) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for url, title in extract_links(html, page_url):
        candidate = f"{title}\n{url}"
        if not any(re.search(pattern, candidate) for pattern in source.pagination_patterns):
            continue
        try:
            validate_public_url(url, source.allowed_hosts, resolve_dns=False)
        except UnsafeUrlError:
            continue
        if url in seen:
            continue
        seen.add(url)
        selected.append(url)
    return selected


class RobotsPolicy:
    def __init__(
        self,
        client: _HttpGetter,
        user_agent: str,
        max_bytes: int = 1_000_000,
        *,
        robots_policy: str = "enforce",
        crawl_delay_policy: str = "enforce",
    ) -> None:
        self.client = client
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.robots_policy = robots_policy
        self.crawl_delay_policy = crawl_delay_policy
        self._cache: dict[tuple[str, str], RobotFileParser | None] = {}
        self.observations: list[str] = []

    def _observe(self, message: str) -> None:
        if message not in self.observations:
            self.observations.append(message)

    def can_fetch(self, url: str, allowed_hosts: list[str]) -> bool:
        if self.robots_policy == "ignore":
            self._observe("robots.txt ignorado por politica administrativa explicita")
            return True
        parsed = urlsplit(url)
        key = (parsed.scheme, parsed.netloc)
        if key not in self._cache:
            robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
            parser = RobotFileParser()
            parser.set_url(robots_url)
            try:
                result = self.client.get(robots_url, allowed_hosts, self.max_bytes)
            except FetchError as exc:
                if exc.status_code == 404:
                    self._cache[key] = None
                else:
                    self._observe(f"robots.txt indisponivel para {parsed.netloc}: {exc}")
                    return self.robots_policy == "observe"
            else:
                parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
                self._cache[key] = parser
        cached = self._cache[key]
        allowed = True if cached is None else cached.can_fetch(self.user_agent, url)
        if not allowed and self.robots_policy == "observe":
            self._observe(f"robots.txt bloquearia {url}; coleta mantida em modo observe")
            return True
        return allowed

    def crawl_delay(self, url: str) -> float | None:
        if self.crawl_delay_policy == "ignore":
            self._observe("Crawl-delay ignorado por politica administrativa explicita")
            return None
        parsed = urlsplit(url)
        cached = self._cache.get((parsed.scheme, parsed.netloc))
        if cached is None:
            return None
        value = cached.crawl_delay(self.user_agent) or cached.crawl_delay("*")
        delay = float(value) if value is not None else None
        if delay is not None and self.crawl_delay_policy == "observe":
            self._observe(f"Crawl-delay de {delay:g}s observado para {parsed.netloc}; nao aplicado")
            return None
        return delay


def _relative_or_absolute(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _is_pdf(result: HttpResult) -> bool:
    content_type = result.headers.get_content_type()
    return content_type == "application/pdf" or result.body[:1024].lstrip().startswith(b"%PDF-")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, FetchError):
        return (
            exc.status_code is None
            or exc.status_code in {408, 425, 429}
            or bool(exc.status_code and exc.status_code >= 500)
        )
    return isinstance(exc, OSError)


def _store_document(
    *,
    source: SourceDefinition,
    original_url: str,
    title: str,
    document_type: DocumentType,
    result: HttpResult,
    raw_dir: Path,
) -> DocumentRecord:
    body = result.body
    digest = hashlib.sha256(body).hexdigest()
    destination = raw_dir / f"{source.id}-{document_type}-{digest[:16]}.pdf"
    if not destination.exists():
        destination.write_bytes(body)
    content_type = result.headers.get_content_type()
    if content_type != "application/pdf":
        content_type = "application/pdf"
    return DocumentRecord(
        source_id=source.id,
        source_name=source.name,
        document_type=document_type,
        title=title,
        original_url=original_url,
        resolved_url=result.url,
        local_path=_relative_or_absolute(destination),
        sha256=digest,
        content_type=content_type,
        size_bytes=len(body),
        downloaded_at=datetime.now(UTC),
        authorization_basis=source.authorization_basis,
        terms_url=source.terms_url,
        metadata=source.metadata,
    )


def _store_engine_document(
    *,
    source: SourceDefinition,
    original_url: str,
    title: str,
    document_type: DocumentType,
    result: EngineDownload,
    raw_dir: Path,
    client: CollectionHttpClient | _LegacyClientAdapter,
) -> DocumentRecord:
    destination = raw_dir / f"{source.id}-{document_type}-{result.sha256[:16]}.pdf"
    if result.path.resolve() != destination.resolve():
        if destination.exists():
            if result.path.name.endswith(".part"):
                result.path.unlink(missing_ok=True)
        else:
            result.path.replace(destination)
    content_type = result.headers.get_content_type()
    if content_type != "application/pdf":
        content_type = "application/pdf"
    client.remember_download(
        original_url=original_url,
        result=result,
        final_path=destination,
        strategy="download",
    )
    return DocumentRecord(
        source_id=source.id,
        source_name=source.name,
        document_type=document_type,
        title=title,
        original_url=original_url,
        resolved_url=result.url,
        local_path=_relative_or_absolute(destination),
        sha256=result.sha256,
        content_type=content_type,
        size_bytes=result.size_bytes,
        downloaded_at=datetime.now(UTC),
        authorization_basis=source.authorization_basis,
        terms_url=source.terms_url,
        metadata=source.metadata,
    )


def _engine_is_pdf(result: EngineHttpResult) -> bool:
    return result.headers.get_content_type() == "application/pdf" or result.body[
        :1024
    ].lstrip().startswith(b"%PDF-")


def _store_engine_body_document(
    *,
    source: SourceDefinition,
    original_url: str,
    title: str,
    document_type: DocumentType,
    result: EngineHttpResult,
    raw_dir: Path,
) -> DocumentRecord:
    digest = hashlib.sha256(result.body).hexdigest()
    destination = raw_dir / f"{source.id}-{document_type}-{digest[:16]}.pdf"
    if not destination.exists():
        temporary = raw_dir / f".{digest}.tmp"
        temporary.write_bytes(result.body)
        temporary.replace(destination)
    return DocumentRecord(
        source_id=source.id,
        source_name=source.name,
        document_type=document_type,
        title=title,
        original_url=original_url,
        resolved_url=result.url,
        local_path=_relative_or_absolute(destination),
        sha256=digest,
        content_type="application/pdf",
        size_bytes=len(result.body),
        downloaded_at=datetime.now(UTC),
        authorization_basis=source.authorization_basis,
        terms_url=source.terms_url,
        metadata=source.metadata,
    )


def _within_limit(current: int, limit: int | None) -> bool:
    return limit is None or current < limit


def _download_pdf_candidate(
    *,
    source: SourceDefinition,
    url: str,
    title: str,
    document_type: DocumentType,
    client: CollectionHttpClient | _LegacyClientAdapter,
    robots: RobotsPolicy,
    settings: CollectorSettings,
    raw_dir: Path,
    interval_seconds: float,
) -> DocumentRecord:
    effective_interval = max(interval_seconds, robots.crawl_delay(url) or 0.0)
    result = client.download(
        url,
        source.allowed_hosts,
        settings.max_pdf_bytes,
        raw_dir,
        strategy="download",
        interval_seconds=effective_interval,
        resume=settings.resume_downloads,
    )
    with result.path.open("rb") as stream:
        header = stream.read(1024).lstrip()
    if result.headers.get_content_type() != "application/pdf" and not header.startswith(b"%PDF-"):
        if result.path.name.endswith(".part"):
            result.path.unlink(missing_ok=True)
        raise ValueError(f"link nao retornou PDF: {url}")
    return _store_engine_document(
        source=source,
        original_url=url,
        title=title,
        document_type=document_type,
        result=result,
        raw_dir=raw_dir,
        client=client,
    )


def _checkpoint_key(source: SourceDefinition) -> str:
    payload = f"{source.id}\n" + "\n".join(source.start_urls)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _matches_source_link(
    item: DiscoveredLink,
    source: SourceDefinition,
) -> tuple[str, str, DocumentType] | None:
    candidate = f"{item.title}\n{item.url}"
    if source.exclude_patterns and any(
        re.search(pattern, candidate) for pattern in source.exclude_patterns
    ):
        return None
    if not any(re.search(pattern, candidate) for pattern in source.include_patterns):
        return None
    try:
        validate_public_url(item.url, source.allowed_hosts, resolve_dns=False)
    except UnsafeUrlError:
        return None
    title = item.title or Path(urlsplit(item.url).path).name
    document_type = classify_document(item.url, title, source)
    if item.declared_type in {"exam", "answer_key", "other"}:
        document_type = item.declared_type  # type: ignore[assignment]
    return item.url, title, document_type


def collect_documents(
    config: AppConfig,
    filters: CollectionFilters | None = None,
    *,
    run_id: str | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[DownloadManifest, Path]:
    enabled_sources = [source for source in config.sources if source.enabled]
    if not enabled_sources:
        raise CollectorError("nenhuma fonte esta habilitada na configuracao")

    settings = config.collector
    data_dir = Path(settings.data_dir)
    raw_dir = data_dir / "raw"
    cache_dir = data_dir / "cache" / "objects"
    manifest_dir = data_dir / "manifests"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    state = CollectionStateStore(data_dir / "collection-engine.sqlite3")
    active_run_id = run_id or str(uuid.uuid4())
    active_filters = filters or CollectionFilters()
    documents: list[DocumentRecord] = []
    references: list[DiscoveryRecord] = []
    failures: list[CollectionFailure] = []
    warnings: list[str] = []
    filtered_out_documents = 0
    duplicate_documents = 0
    seen_digests: set[str] = set()

    for source in enabled_sources:
        interval = (
            source.request_interval_seconds
            if source.request_interval_seconds is not None
            else settings.request_interval_seconds
        )
        concurrency = source.max_concurrency or settings.max_concurrency
        if SafeHttpClient is _ORIGINAL_SAFE_HTTP_CLIENT:
            client: CollectionHttpClient | _LegacyClientAdapter = CollectionHttpClient(
                user_agent=settings.user_agent,
                timeout=settings.timeout_seconds,
                connect_timeout=settings.connect_timeout_seconds,
                interval_seconds=interval,
                max_concurrency=concurrency,
                max_retries=settings.max_retries,
                retry_max_delay_seconds=settings.retry_max_delay_seconds,
                state_store=state,
                run_id=active_run_id,
                source_id=source.id,
                conditional_cache=settings.conditional_cache,
                disk_quota_bytes=settings.disk_quota_bytes,
            )
        else:
            client = _LegacyClientAdapter(
                SafeHttpClient(
                    user_agent=settings.user_agent,
                    timeout=settings.timeout_seconds,
                    interval_seconds=interval,
                )
            )
        robots = RobotsPolicy(
            client,
            settings.user_agent,
            robots_policy=source.robots_policy,
            crawl_delay_policy=source.crawl_delay_policy,
        )
        source_links: list[tuple[str, str, DocumentType]] = []
        seen_links: set[str] = set()
        source_items = 0
        pagination_truncated = False
        checkpoint_key = _checkpoint_key(source)

        def check_paused(
            pending_pages: list[str],
            seen_pages: set[str],
            *,
            _checkpoint_key: str = checkpoint_key,
            _source: SourceDefinition = source,
            _source_links: list[tuple[str, str, DocumentType]] = source_links,
        ) -> None:
            if stop_event is None or not stop_event.is_set():
                return
            state.save_checkpoint(
                _checkpoint_key,
                _source.id,
                "paused",
                {
                    "pending_pages": pending_pages,
                    "seen_pages": sorted(seen_pages),
                    "source_links": _source_links,
                },
            )
            raise CollectionPaused(f"coleta de {_source.name} pausada com checkpoint preservado")

        def add_candidates(
            candidates: list[DiscoveredLink],
            *,
            _source: SourceDefinition = source,
            _seen_links: set[str] = seen_links,
            _source_links: list[tuple[str, str, DocumentType]] = source_links,
        ) -> None:
            nonlocal filtered_out_documents
            for candidate in safe_discovered_links(candidates, _source):
                selected = _matches_source_link(candidate, _source)
                if selected is None or selected[0] in _seen_links:
                    continue
                if not document_might_match_filters(
                    selected[1], selected[0], _source.metadata, active_filters
                ):
                    filtered_out_documents += 1
                    continue
                _seen_links.add(selected[0])
                _source_links.append(selected)

        try:
            checkpoint = state.load_checkpoint(checkpoint_key)
            checkpoint_payload = checkpoint["payload"] if checkpoint else {}
            restored_links = checkpoint_payload.get("source_links", [])
            for item in restored_links if isinstance(restored_links, list) else []:
                if isinstance(item, list) and len(item) == 3:
                    value = (str(item[0]), str(item[1]), str(item[2]))
                    if value[2] in {"exam", "answer_key", "other"}:
                        source_links.append(value)  # type: ignore[arg-type]
                        seen_links.add(value[0])

            if "html" in source.discovery_strategies:
                restored_pending = checkpoint_payload.get("pending_pages", [])
                pending_pages = (
                    [str(item) for item in restored_pending]
                    if isinstance(restored_pending, list) and restored_pending
                    else list(dict.fromkeys(source.start_urls))
                )
                restored_seen = checkpoint_payload.get("seen_pages", [])
                seen_pages = (
                    {str(item) for item in restored_seen}
                    if isinstance(restored_seen, list)
                    else set()
                )
                while pending_pages:
                    check_paused(pending_pages, seen_pages)
                    page_url = pending_pages.pop(0)
                    if page_url in seen_pages:
                        continue
                    if (
                        source.max_pages_per_run is not None
                        and len(seen_pages) >= source.max_pages_per_run
                    ):
                        pagination_truncated = True
                        break
                    seen_pages.add(page_url)
                    if not robots.can_fetch(page_url, source.allowed_hosts):
                        message = f"{source.id}: robots.txt bloqueou ou nao liberou {page_url}"
                        warnings.append(message)
                        failures.append(
                            CollectionFailure(
                                source_id=source.id,
                                url=page_url,
                                stage="robots",
                                message=message,
                            )
                        )
                        continue
                    effective_interval = max(interval, robots.crawl_delay(page_url) or 0.0)
                    try:
                        if (
                            urlsplit(page_url).path.casefold().endswith(".pdf")
                            and source.access_mode == "content"
                        ):
                            title = Path(urlsplit(page_url).path).name or source.name
                            document_type = classify_document(page_url, title, source)
                            if document_type == "other":
                                document_type = "exam"
                            if not document_might_match_filters(
                                title, page_url, source.metadata, active_filters
                            ):
                                filtered_out_documents += 1
                                continue
                            if not _within_limit(source_items, settings.max_files_per_source):
                                continue
                            record = _download_pdf_candidate(
                                source=source,
                                url=page_url,
                                title=title,
                                document_type=document_type,
                                client=client,
                                robots=robots,
                                settings=settings,
                                raw_dir=raw_dir,
                                interval_seconds=interval,
                            )
                            source_items += 1
                            if record.sha256 in seen_digests:
                                duplicate_documents += 1
                            else:
                                seen_digests.add(record.sha256)
                                documents.append(record)
                            continue
                        page = client.get(
                            page_url,
                            source.allowed_hosts,
                            max(settings.max_html_bytes, settings.max_pdf_bytes),
                            strategy="html",
                            interval_seconds=effective_interval,
                        )
                        client.remember_body(
                            original_url=page_url,
                            result=page,
                            cache_dir=cache_dir,
                            strategy="html",
                        )
                        if _engine_is_pdf(page):
                            title = Path(urlsplit(page.url).path).name or source.name
                            document_type = classify_document(page.url, title, source)
                            if document_type == "other":
                                document_type = "exam"
                            if source.access_mode == "reference_only":
                                references.append(
                                    DiscoveryRecord(
                                        source_id=source.id,
                                        source_name=source.name,
                                        title=title,
                                        url=page.url,
                                        discovered_at=datetime.now(UTC),
                                        authorization_basis=source.authorization_basis,
                                        written_authorization_reference=(
                                            source.written_authorization_reference
                                        ),
                                        terms_url=source.terms_url,
                                        metadata=source.metadata,
                                    )
                                )
                            elif _within_limit(source_items, settings.max_files_per_source):
                                record = _store_engine_body_document(
                                    source=source,
                                    original_url=page_url,
                                    title=title,
                                    document_type=document_type,
                                    result=page,
                                    raw_dir=raw_dir,
                                )
                                source_items += 1
                                if record.sha256 in seen_digests:
                                    duplicate_documents += 1
                                else:
                                    seen_digests.add(record.sha256)
                                    documents.append(record)
                            continue
                        content_type = page.headers.get_content_type()
                        if not _is_html_page(page):
                            raise ValueError(
                                f"pagina ignorada por Content-Type {content_type}: {page_url}"
                            )
                        charset = page.headers.get_content_charset() or "utf-8"
                        html = page.body.decode(charset, errors="replace")
                        add_candidates(
                            [
                                DiscoveredLink(url=url, title=title, declared_type=kind)
                                for url, title, kind in select_document_links(
                                    html, page.url, source
                                )
                            ]
                        )
                        for discovered_page in select_pagination_links(html, page.url, source):
                            if (
                                discovered_page not in seen_pages
                                and discovered_page not in pending_pages
                            ):
                                pending_pages.append(discovered_page)
                    except (FetchError, UnsafeUrlError, LookupError, OSError, ValueError) as exc:
                        message = f"{source.id}: falha ao ler {page_url}: {exc}"
                        warnings.append(message)
                        failures.append(
                            CollectionFailure(
                                source_id=source.id,
                                url=page_url,
                                stage="discovery",
                                message=message,
                                retryable=_is_retryable(exc),
                            )
                        )
                    state.save_checkpoint(
                        checkpoint_key,
                        source.id,
                        "running",
                        {
                            "pending_pages": pending_pages,
                            "seen_pages": sorted(seen_pages),
                            "source_links": source_links,
                        },
                    )

            if pagination_truncated:
                warnings.append(
                    f"{source.id}: paginacao limitada a {source.max_pages_per_run} paginas"
                )

            if "sitemap" in source.discovery_strategies:
                sitemap_queue = list(source.sitemap_urls)
                if not sitemap_queue:
                    for start_url in source.start_urls:
                        parsed = urlsplit(start_url)
                        sitemap_queue.append(
                            urlunsplit((parsed.scheme, parsed.netloc, "/sitemap.xml", "", ""))
                        )
                seen_sitemaps: set[str] = set()
                while sitemap_queue:
                    check_paused(sitemap_queue, seen_sitemaps)
                    sitemap_url = sitemap_queue.pop(0)
                    if sitemap_url in seen_sitemaps:
                        continue
                    seen_sitemaps.add(sitemap_url)
                    if not robots.can_fetch(sitemap_url, source.allowed_hosts):
                        continue
                    try:
                        result = client.get(
                            sitemap_url,
                            source.allowed_hosts,
                            settings.max_html_bytes,
                            strategy="sitemap",
                        )
                        urls, children = parse_sitemap(
                            result.body, result.url, max_bytes=settings.max_html_bytes
                        )
                        add_candidates([DiscoveredLink(url=item, title="") for item in urls])
                        for child in children:
                            if child not in seen_sitemaps:
                                sitemap_queue.append(child)
                    except (FetchError, UnsafeUrlError, OSError, ValueError) as exc:
                        warnings.append(f"{source.id}: sitemap ignorado: {exc}")

            if "feed" in source.discovery_strategies:
                for feed_url in source.feed_urls:
                    check_paused([], set())
                    if not robots.can_fetch(feed_url, source.allowed_hosts):
                        continue
                    try:
                        result = client.get(
                            feed_url,
                            source.allowed_hosts,
                            settings.max_html_bytes,
                            strategy="feed",
                        )
                        add_candidates(parse_feed(result.body, result.url))
                    except (FetchError, UnsafeUrlError, OSError, ValueError) as exc:
                        warnings.append(f"{source.id}: feed ignorado: {exc}")

            if "json" in source.discovery_strategies:
                for endpoint in source.json_endpoints:
                    json_page_url: str | None = endpoint.url
                    seen_json_pages: set[str] = set()
                    while json_page_url and json_page_url not in seen_json_pages:
                        if (
                            source.max_pages_per_run is not None
                            and len(seen_json_pages) >= source.max_pages_per_run
                        ):
                            warnings.append(
                                f"{source.id}: JSON limitado a {source.max_pages_per_run} paginas"
                            )
                            break
                        check_paused([], seen_json_pages)
                        seen_json_pages.add(json_page_url)
                        if not robots.can_fetch(json_page_url, source.allowed_hosts):
                            break
                        try:
                            result = client.get(
                                json_page_url,
                                source.allowed_hosts,
                                settings.max_html_bytes,
                                strategy="json",
                                extra_headers=endpoint.headers,
                            )
                            items, json_page_url = parse_json_links(
                                result.body, result.url, endpoint
                            )
                            add_candidates(items)
                        except (FetchError, UnsafeUrlError, OSError, ValueError) as exc:
                            warnings.append(f"{source.id}: endpoint JSON interrompido: {exc}")
                            break

            if "browser" in source.discovery_strategies:
                for browser_page_url in source.start_urls:
                    check_paused([], set())
                    if not robots.can_fetch(browser_page_url, source.allowed_hosts):
                        continue
                    try:
                        add_candidates(
                            browser_discover(
                                browser_page_url,
                                source,
                                timeout_seconds=settings.timeout_seconds,
                            )
                        )
                    except ManualActionRequired as exc:
                        message = f"{source.id}: {exc}"
                        warnings.append(message)
                        failures.append(
                            CollectionFailure(
                                source_id=source.id,
                                url=browser_page_url,
                                stage="discovery",
                                message=message,
                            )
                        )
                    except (BrowserUnavailableError, UnsafeUrlError, OSError, ValueError) as exc:
                        warnings.append(f"{source.id}: navegador indisponivel: {exc}")

            remaining = (
                None
                if settings.max_files_per_source is None
                else max(0, settings.max_files_per_source - source_items)
            )
            selected_links = _limit_document_links(source_links, remaining)
            if source.access_mode == "reference_only":
                for url, _title, _document_type in selected_links:
                    references.append(
                        DiscoveryRecord(
                            source_id=source.id,
                            source_name=source.name,
                            title=Path(urlsplit(url).path).name or "referencia",
                            url=url,
                            discovered_at=datetime.now(UTC),
                            authorization_basis=source.authorization_basis,
                            written_authorization_reference=(
                                source.written_authorization_reference
                            ),
                            terms_url=source.terms_url,
                            metadata=source.metadata,
                        )
                    )
                state.delete_checkpoint(checkpoint_key)
                continue

            downloadable: list[tuple[str, str, DocumentType]] = []
            for url, title, document_type in selected_links:
                if robots.can_fetch(url, source.allowed_hosts):
                    downloadable.append((url, title, document_type))
                else:
                    message = f"{source.id}: robots.txt bloqueou ou nao liberou {url}"
                    warnings.append(message)
                    failures.append(
                        CollectionFailure(
                            source_id=source.id,
                            url=url,
                            stage="robots",
                            message=message,
                        )
                    )

            def download_one(
                item: tuple[str, str, DocumentType],
                *,
                _interval: float = interval,
                _robots: RobotsPolicy = robots,
                _client: CollectionHttpClient | _LegacyClientAdapter = client,
                _source: SourceDefinition = source,
            ) -> DocumentRecord:
                url, title, document_type = item
                return _download_pdf_candidate(
                    source=_source,
                    url=url,
                    title=title,
                    document_type=document_type,
                    client=_client,
                    robots=_robots,
                    settings=settings,
                    raw_dir=raw_dir,
                    interval_seconds=_interval,
                )

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(download_one, item): item for item in downloadable}
                for future in as_completed(futures):
                    check_paused([], set())
                    url, _title, _kind = futures[future]
                    try:
                        record = future.result()
                        source_items += 1
                        if record.sha256 in seen_digests:
                            duplicate_documents += 1
                        else:
                            seen_digests.add(record.sha256)
                            documents.append(record)
                    except (FetchError, UnsafeUrlError, OSError, ValueError) as exc:
                        message = f"{source.id}: falha ao baixar {url}: {exc}"
                        warnings.append(message)
                        failures.append(
                            CollectionFailure(
                                source_id=source.id,
                                url=url,
                                stage="download",
                                message=message,
                                retryable=_is_retryable(exc),
                            )
                        )
            state.delete_checkpoint(checkpoint_key)
        finally:
            warnings.extend(f"{source.id}: {item}" for item in robots.observations)
            client.close()

    telemetry = state.events(active_run_id)
    manifest = DownloadManifest(
        created_at=datetime.now(UTC),
        documents=documents,
        references=references,
        filters=active_filters,
        filtered_out_documents=filtered_out_documents,
        duplicate_documents=duplicate_documents,
        failures=failures,
        warnings=list(dict.fromkeys(warnings)),
        telemetry=telemetry,
        collection_policy={
            "run_id": active_run_id,
            "capacity_profile": settings.capacity_profile,
            "request_interval_seconds": settings.request_interval_seconds,
            "max_concurrency": settings.max_concurrency,
            "max_files_per_source": settings.max_files_per_source,
            "max_retries": settings.max_retries,
            "conditional_cache": settings.conditional_cache,
            "resume_downloads": settings.resume_downloads,
            "source_policies": {
                source.id: {
                    "robots_policy": source.robots_policy,
                    "crawl_delay_policy": source.crawl_delay_policy,
                }
                for source in enabled_sources
            },
        },
    )
    timestamp = manifest.created_at.strftime("%Y%m%dT%H%M%SZ")
    manifest_path = manifest_dir / f"download-{timestamp}-{active_run_id[:8]}.json"
    write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest, manifest_path


def _collect_documents_legacy(
    config: AppConfig, filters: CollectionFilters | None = None
) -> tuple[DownloadManifest, Path]:
    enabled_sources = [source for source in config.sources if source.enabled]
    if not enabled_sources:
        raise CollectorError("nenhuma fonte esta habilitada na configuracao")

    settings = config.collector
    data_dir = Path(settings.data_dir)
    raw_dir = data_dir / "raw"
    manifest_dir = data_dir / "manifests"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = SafeHttpClient(
        user_agent=settings.user_agent,
        timeout=settings.timeout_seconds,
        interval_seconds=settings.request_interval_seconds,
    )
    robots = RobotsPolicy(client, settings.user_agent)
    documents: list[DocumentRecord] = []
    references: list[DiscoveryRecord] = []
    failures: list[CollectionFailure] = []
    warnings: list[str] = []
    active_filters = filters or CollectionFilters()
    filtered_out_documents = 0
    duplicate_documents = 0
    seen_digests: set[str] = set()

    for source in enabled_sources:
        source_links: list[tuple[str, str, DocumentType]] = []
        seen_links: set[str] = set()
        source_items = 0
        pending_pages = list(dict.fromkeys(source.start_urls))
        seen_pages: set[str] = set()
        pagination_truncated = False
        for page_url in pending_pages:
            if page_url in seen_pages:
                continue
            if source.pagination_patterns and len(seen_pages) >= (
                source.max_pages_per_run or 10_000
            ):
                pagination_truncated = True
                break
            seen_pages.add(page_url)
            try:
                if not robots.can_fetch(page_url, source.allowed_hosts):
                    message = f"{source.id}: robots.txt bloqueou ou nao liberou {page_url}"
                    warnings.append(message)
                    failures.append(
                        CollectionFailure(
                            source_id=source.id,
                            url=page_url,
                            stage="robots",
                            message=message,
                        )
                    )
                    continue
                page = client.get(
                    page_url,
                    source.allowed_hosts,
                    max(settings.max_html_bytes, settings.max_pdf_bytes),
                )
                if _is_pdf(page):
                    if len(page.body) > settings.max_pdf_bytes:
                        message = f"{source.id}: PDF excede o limite: {page_url}"
                        warnings.append(message)
                        failures.append(
                            CollectionFailure(
                                source_id=source.id,
                                url=page_url,
                                stage="download",
                                message=message,
                            )
                        )
                        continue
                    title = Path(urlsplit(page.url).path).name or source.name
                    document_type = classify_document(page.url, title, source)
                    if document_type == "other":
                        document_type = "exam"
                    if not document_might_match_filters(
                        title, page.url, source.metadata, active_filters
                    ):
                        filtered_out_documents += 1
                        continue
                    if source.access_mode == "reference_only":
                        references.append(
                            DiscoveryRecord(
                                source_id=source.id,
                                source_name=source.name,
                                title=title,
                                url=page.url,
                                discovered_at=datetime.now(UTC),
                                authorization_basis=source.authorization_basis,
                                written_authorization_reference=(
                                    source.written_authorization_reference
                                ),
                                terms_url=source.terms_url,
                                metadata=source.metadata,
                            )
                        )
                        continue
                    if source_items >= (settings.max_files_per_source or 10_000):
                        continue
                    record = _store_document(
                        source=source,
                        original_url=page_url,
                        title=title,
                        document_type=document_type,
                        result=page,
                        raw_dir=raw_dir,
                    )
                    source_items += 1
                    if record.sha256 in seen_digests:
                        duplicate_documents += 1
                    else:
                        seen_digests.add(record.sha256)
                        documents.append(record)
                    continue
                content_type = page.headers.get_content_type()
                if not _is_html_page(page):
                    message = (
                        f"{source.id}: pagina ignorada por Content-Type {content_type}: {page_url}"
                    )
                    warnings.append(message)
                    failures.append(
                        CollectionFailure(
                            source_id=source.id,
                            url=page_url,
                            stage="discovery",
                            message=message,
                        )
                    )
                    continue
                if len(page.body) > settings.max_html_bytes:
                    message = f"{source.id}: pagina HTML excede o limite: {page_url}"
                    warnings.append(message)
                    failures.append(
                        CollectionFailure(
                            source_id=source.id,
                            url=page_url,
                            stage="discovery",
                            message=message,
                        )
                    )
                    continue
                charset = page.headers.get_content_charset() or "utf-8"
                html = page.body.decode(charset, errors="replace")
                for item in select_document_links(html, page.url, source):
                    if item[0] in seen_links:
                        continue
                    seen_links.add(item[0])
                    if not document_might_match_filters(
                        item[1], item[0], source.metadata, active_filters
                    ):
                        filtered_out_documents += 1
                        continue
                    source_links.append(item)
                for discovered_page in select_pagination_links(html, page.url, source):
                    if discovered_page in seen_pages or discovered_page in pending_pages:
                        continue
                    if len(pending_pages) >= (source.max_pages_per_run or 10_000):
                        pagination_truncated = True
                        continue
                    pending_pages.append(discovered_page)
            except (FetchError, UnsafeUrlError, LookupError, OSError) as exc:
                message = f"{source.id}: falha ao ler {page_url}: {exc}"
                warnings.append(message)
                failures.append(
                    CollectionFailure(
                        source_id=source.id,
                        url=page_url,
                        stage="discovery",
                        message=message,
                        retryable=_is_retryable(exc),
                    )
                )

        if pagination_truncated:
            warnings.append(f"{source.id}: paginacao limitada a {source.max_pages_per_run} paginas")

        if source.access_mode == "reference_only":
            reference_links = _limit_document_links(
                source_links,
                (settings.max_files_per_source or 10_000) - source_items,
            )
            for url, _title, _document_type in reference_links:
                references.append(
                    DiscoveryRecord(
                        source_id=source.id,
                        source_name=source.name,
                        title=Path(urlsplit(url).path).name or "referencia",
                        url=url,
                        discovered_at=datetime.now(UTC),
                        authorization_basis=source.authorization_basis,
                        written_authorization_reference=source.written_authorization_reference,
                        terms_url=source.terms_url,
                        metadata=source.metadata,
                    )
                )
            if source_links:
                warnings.append(
                    f"{source.id}: "
                    f"{min(len(source_links), settings.max_files_per_source or 10_000)} "
                    "referencias registradas; conteudo nao foi baixado"
                )
            continue

        downloadable_links = _limit_document_links(
            source_links,
            (settings.max_files_per_source or 10_000) - source_items,
        )
        for url, title, document_type in downloadable_links:
            try:
                if not robots.can_fetch(url, source.allowed_hosts):
                    message = f"{source.id}: robots.txt bloqueou ou nao liberou {url}"
                    warnings.append(message)
                    failures.append(
                        CollectionFailure(
                            source_id=source.id,
                            url=url,
                            stage="robots",
                            message=message,
                        )
                    )
                    continue
                result = client.get(url, source.allowed_hosts, settings.max_pdf_bytes)
                if not _is_pdf(result):
                    message = f"{source.id}: link nao retornou PDF: {url}"
                    warnings.append(message)
                    failures.append(
                        CollectionFailure(
                            source_id=source.id,
                            url=url,
                            stage="download",
                            message=message,
                        )
                    )
                    continue
                record = _store_document(
                    source=source,
                    original_url=url,
                    title=title,
                    document_type=document_type,
                    result=result,
                    raw_dir=raw_dir,
                )
                source_items += 1
                if record.sha256 in seen_digests:
                    duplicate_documents += 1
                else:
                    seen_digests.add(record.sha256)
                    documents.append(record)
            except (FetchError, UnsafeUrlError, OSError) as exc:
                message = f"{source.id}: falha ao baixar {url}: {exc}"
                warnings.append(message)
                failures.append(
                    CollectionFailure(
                        source_id=source.id,
                        url=url,
                        stage="download",
                        message=message,
                        retryable=_is_retryable(exc),
                    )
                )

    manifest = DownloadManifest(
        created_at=datetime.now(UTC),
        documents=documents,
        references=references,
        filters=active_filters,
        filtered_out_documents=filtered_out_documents,
        duplicate_documents=duplicate_documents,
        failures=failures,
        warnings=warnings,
    )
    timestamp = manifest.created_at.strftime("%Y%m%dT%H%M%SZ")
    manifest_path = manifest_dir / f"download-{timestamp}.json"
    write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest, manifest_path
