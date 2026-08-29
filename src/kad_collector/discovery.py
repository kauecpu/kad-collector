# mypy: disable-error-code=import-not-found
from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from .models import JsonDiscoveryEndpoint, SourceDefinition
from .security import UnsafeUrlError, validate_public_url


class BrowserUnavailableError(RuntimeError):
    """Playwright or a compatible browser is not available."""


class ManualActionRequired(RuntimeError):
    """The source presented authentication, CAPTCHA or an explicit challenge."""


@dataclass(frozen=True)
class DiscoveredLink:
    url: str
    title: str
    declared_type: str | None = None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _decode_xml(body: bytes, max_bytes: int) -> bytes:
    if body[:2] == b"\x1f\x8b":
        try:
            value = gzip.decompress(body)
        except OSError as exc:
            raise ValueError("sitemap gzip invalido") from exc
        if len(value) > max_bytes:
            raise ValueError("sitemap descompactado excede o limite")
        return value
    return body


def parse_sitemap(body: bytes, page_url: str, *, max_bytes: int) -> tuple[list[str], list[str]]:
    payload = _decode_xml(body, max_bytes)
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("sitemap XML invalido") from exc
    urls: list[str] = []
    children: list[str] = []
    is_index = _local_name(root.tag) == "sitemapindex"
    for node in root.iter():
        if _local_name(node.tag) != "loc" or not (node.text or "").strip():
            continue
        value = urljoin(page_url, (node.text or "").strip())
        (children if is_index else urls).append(value)
    return list(dict.fromkeys(urls)), list(dict.fromkeys(children))


def parse_feed(body: bytes, page_url: str) -> list[DiscoveredLink]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError("feed XML invalido") from exc
    links: list[DiscoveredLink] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = ""
        candidates: list[str] = []
        for child in node.iter():
            name = _local_name(child.tag)
            if name == "title" and not title:
                title = " ".join((child.text or "").split())
            if name == "link":
                href = child.attrib.get("href") or (child.text or "").strip()
                if href:
                    candidates.append(urljoin(page_url, href))
            if name == "enclosure" and child.attrib.get("url"):
                candidates.append(urljoin(page_url, child.attrib["url"]))
        links.extend(DiscoveredLink(url=value, title=title) for value in candidates)
    unique: dict[str, DiscoveredLink] = {}
    for link in links:
        unique.setdefault(link.url, link)
    return list(unique.values())


def _json_path(payload: Any, path: str) -> Any:
    current = payload
    for part in (item for item in path.split(".") if item):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(f"caminho JSON inexistente: {path}")
    return current


def parse_json_links(
    body: bytes,
    page_url: str,
    endpoint: JsonDiscoveryEndpoint,
) -> tuple[list[DiscoveredLink], str | None]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("endpoint JSON retornou conteudo invalido") from exc
    items = _json_path(payload, endpoint.items_path)
    if not isinstance(items, list):
        raise ValueError("items_path do endpoint JSON nao aponta para uma lista")
    links: list[DiscoveredLink] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(endpoint.url_field), str):
            continue
        raw_title = item.get(endpoint.title_field, "")
        raw_type = item.get(endpoint.type_field) if endpoint.type_field else None
        links.append(
            DiscoveredLink(
                url=urljoin(page_url, item[endpoint.url_field]),
                title=str(raw_title or ""),
                declared_type=str(raw_type) if raw_type is not None else None,
            )
        )
    next_page: str | None = None
    if endpoint.next_page_field:
        try:
            value = _json_path(payload, endpoint.next_page_field)
        except ValueError:
            value = None
        if isinstance(value, str) and value.strip():
            next_page = urljoin(page_url, value.strip())
    return links, next_page


def _looks_blocked(title: str, content: str, url: str) -> str | None:
    page = f"{title}\n{content[:20_000]}".casefold()
    if any(value in page for value in ("captcha", "cf-turnstile")):
        return "captcha"
    if any(
        value in page
        for value in (
            "just a moment...",
            "checking your browser",
            "cloudflare ray id",
            "cf-chl-",
        )
    ):
        return "cloudflare_challenge"
    if any(
        value in page
        for value in (
            "reference #18.",
            "errors.edgesuite.net",
            "akamai bot manager",
            "akamai ghost",
        )
    ):
        return "akamai_challenge"
    if any(value in page for value in ("access denied", "acesso negado", "request blocked")):
        return "access_denied"
    path = urlsplit(url).path.casefold()
    login_path = re.search(r"(?:^|/)(?:login|signin|auth)(?:/|$)", path) is not None
    password_field = re.search(
        r"<input\b[^>]*\btype\s*=\s*['\"]?password\b", content, re.IGNORECASE
    )
    login_form = re.search(
        r"<form\b[^>]*(?:action\s*=\s*['\"][^'\"]*(?:login|signin|auth)|"
        r"id\s*=\s*['\"][^'\"]*login)",
        content,
        re.IGNORECASE,
    )
    if login_path or password_field or login_form:
        return "login"
    return None


def detect_access_challenge(title: str, content: str, url: str) -> str | None:
    """Return a manual-action reason without attempting to bypass the challenge."""

    return _looks_blocked(title, content, url)


def browser_discover(
    page_url: str,
    source: SourceDefinition,
    *,
    timeout_seconds: float,
) -> list[DiscoveredLink]:
    if not source.browser_enabled:
        raise BrowserUnavailableError("a fonte nao habilitou o navegador JavaScript")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailableError(
            "Playwright nao esta instalado; instale o extra browser do KAD Collector"
        ) from exc

    timeout_ms = int(timeout_seconds * 1000)
    with sync_playwright() as playwright:
        browser = None
        launch_errors: list[str] = []
        for channel in ("msedge", None):
            try:
                browser = playwright.chromium.launch(headless=True, channel=channel)
                break
            except PlaywrightError as exc:
                launch_errors.append(str(exc))
        if browser is None:
            raise BrowserUnavailableError("; ".join(launch_errors[-2:]))
        try:
            context = browser.new_context(
                user_agent="KADCollector/0.3 (+https://github.com/kauecpu/kad-collector)",
                locale="pt-BR",
                accept_downloads=False,
            )
            page = context.new_page()

            def guard_route(route: Any) -> None:
                requested = route.request.url
                parsed = urlsplit(requested)
                if parsed.scheme in {"data", "blob", "about"}:
                    route.continue_()
                    return
                try:
                    validate_public_url(requested, source.allowed_hosts)
                except UnsafeUrlError:
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", guard_route)
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(min(2_000, timeout_ms // 4))
            content = page.content()
            reason = _looks_blocked(page.title(), content, page.url)
            if reason:
                raise ManualActionRequired(f"acao manual necessaria: {reason}")
            result: list[DiscoveredLink] = []
            for locator in page.locator("a:visible[href]").all():
                href = locator.get_attribute("href")
                if not href:
                    continue
                text = " ".join(locator.inner_text().split())
                result.append(DiscoveredLink(url=urljoin(page.url, href), title=text))
            unique: dict[str, DiscoveredLink] = {}
            for item in result:
                unique.setdefault(item.url, item)
            context.close()
            return list(unique.values())
        finally:
            browser.close()


def safe_discovered_links(
    links: list[DiscoveredLink], source: SourceDefinition
) -> list[DiscoveredLink]:
    safe: list[DiscoveredLink] = []
    blocked_paths = re.compile(r"(?i)(?:^|/)(?:login|logout|signin|admin)(?:/|$)")
    for item in links:
        parsed = urlsplit(item.url)
        if parsed.scheme not in {"http", "https"} or blocked_paths.search(parsed.path):
            continue
        try:
            validate_public_url(item.url, source.allowed_hosts, resolve_dns=False)
        except UnsafeUrlError:
            continue
        safe.append(item)
    return safe
