from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .json_utils import write_json
from .models import AppConfig, DocumentRecord, DocumentType, DownloadManifest, SourceDefinition
from .security import FetchError, SafeHttpClient, UnsafeUrlError, validate_public_url


class CollectorError(RuntimeError):
    """Falha controlada durante uma execucao do coletor."""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        href = next((value for name, value in attrs if name.lower() == "href"), None)
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
        selected.append(
            (
                url,
                title or Path(urlsplit(url).path).name,
                classify_document(url, title, source),
            )
        )
    return selected


class RobotsPolicy:
    def __init__(self, client: SafeHttpClient, user_agent: str, max_bytes: int = 1_000_000) -> None:
        self.client = client
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self._cache: dict[tuple[str, str], RobotFileParser | None] = {}

    def can_fetch(self, url: str, allowed_hosts: list[str]) -> bool:
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
                    return False
            else:
                parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
                self._cache[key] = parser
        cached = self._cache[key]
        return True if cached is None else cached.can_fetch(self.user_agent, url)


def _relative_or_absolute(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def collect_documents(config: AppConfig) -> tuple[DownloadManifest, Path]:
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
    warnings: list[str] = []

    for source in enabled_sources:
        source_links: list[tuple[str, str, DocumentType]] = []
        seen_links: set[str] = set()
        for page_url in source.start_urls:
            try:
                if not robots.can_fetch(page_url, source.allowed_hosts):
                    warnings.append(f"{source.id}: robots.txt bloqueou {page_url}")
                    continue
                page = client.get(page_url, source.allowed_hosts, settings.max_html_bytes)
                content_type = page.headers.get_content_type()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    warnings.append(f"{source.id}: pagina ignorada por Content-Type {content_type}")
                    continue
                charset = page.headers.get_content_charset() or "utf-8"
                html = page.body.decode(charset, errors="replace")
                for item in select_document_links(html, page.url, source):
                    if item[0] not in seen_links:
                        seen_links.add(item[0])
                        source_links.append(item)
            except (FetchError, UnsafeUrlError, LookupError) as exc:
                warnings.append(f"{source.id}: falha ao ler {page_url}: {exc}")

        for url, title, document_type in source_links[: settings.max_files_per_source]:
            try:
                if not robots.can_fetch(url, source.allowed_hosts):
                    warnings.append(f"{source.id}: robots.txt bloqueou {url}")
                    continue
                result = client.get(url, source.allowed_hosts, settings.max_pdf_bytes)
                content_type = result.headers.get_content_type()
                if content_type != "application/pdf" and not result.body[:1024].lstrip().startswith(
                    b"%PDF-"
                ):
                    warnings.append(f"{source.id}: link nao retornou PDF: {url}")
                    continue
                digest = hashlib.sha256(result.body).hexdigest()
                destination = raw_dir / f"{source.id}-{document_type}-{digest[:16]}.pdf"
                if not destination.exists():
                    destination.write_bytes(result.body)
                documents.append(
                    DocumentRecord(
                        source_id=source.id,
                        source_name=source.name,
                        document_type=document_type,
                        title=title,
                        original_url=url,
                        resolved_url=result.url,
                        local_path=_relative_or_absolute(destination),
                        sha256=digest,
                        content_type=content_type,
                        size_bytes=len(result.body),
                        downloaded_at=datetime.now(UTC),
                        authorization_basis=source.authorization_basis,
                        terms_url=source.terms_url,
                        metadata=source.metadata,
                    )
                )
            except (FetchError, UnsafeUrlError, OSError) as exc:
                warnings.append(f"{source.id}: falha ao baixar {url}: {exc}")

    manifest = DownloadManifest(
        created_at=datetime.now(UTC), documents=documents, warnings=warnings
    )
    timestamp = manifest.created_at.strftime("%Y%m%dT%H%M%SZ")
    manifest_path = manifest_dir / f"download-{timestamp}.json"
    write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest, manifest_path
