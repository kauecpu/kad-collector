from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .discovery import DiscoveredLink, safe_discovered_links
from .models import CollectionFilters, DocumentRecord, DocumentType, SourceDefinition
from .url_utils import canonicalize_url, redact_url_secrets

_ALIAS_GROUPS = (
    frozenset({"cebraspe", "cespe", "cespe unb", "cebraspe unb"}),
    frozenset({"policia federal", "pf", "departamento de policia federal"}),
    frozenset({"banco do brasil", "bb"}),
    frozenset({"fundacao cesgranrio", "cesgranrio"}),
    frozenset({"fundacao getulio vargas", "fgv"}),
)
_DISCOVERY_HINT = re.compile(
    r"\b(?:provas?|gabaritos?|cadernos?|questoes?|concursos?|selecao|selecoes|"
    r"arquivo|arquivos|acervo|edital|certame|vestibular|exame|exames)\b"
)
_DOCUMENT_SECTION = re.compile(r"\b(?:provas?|gabaritos?|cadernos?)\b")
_REJECTED_SECTION = re.compile(
    r"\b(?:resultados?|recursos?|inscricoes?|editais?|comunicados?|cronograma|"
    r"cartao de confirmacao|classificacao|convocacao)\b"
)


def normalize_discovery_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents.casefold()).split())


def expand_aliases(value: str) -> frozenset[str]:
    normalized = normalize_discovery_text(value)
    aliases = {normalized} if normalized else set()
    for group in _ALIAS_GROUPS:
        if normalized in group or any(item in normalized for item in group):
            aliases.update(group)
    return frozenset(aliases)


@dataclass(frozen=True)
class DiscoveryTarget:
    organizations: frozenset[str]
    boards: frozenset[str]
    roles: frozenset[str]
    years: frozenset[int]

    @property
    def terms(self) -> frozenset[str]:
        return self.organizations | self.boards | self.roles


def build_discovery_target(source: SourceDefinition, filters: CollectionFilters) -> DiscoveryTarget:
    def values(requested: list[str], metadata_key: str) -> frozenset[str]:
        raw = requested or [source.metadata.get(metadata_key, "")]
        return frozenset(alias for item in raw for alias in expand_aliases(item))

    years = set(filters.years)
    for metadata_key in ("ano", "ano_publicacao"):
        value = source.metadata.get(metadata_key, "")
        if value.isdigit():
            years.add(int(value))
    return DiscoveryTarget(
        organizations=values(filters.organizations, "orgao"),
        boards=values(filters.boards, "banca"),
        roles=values(filters.roles, "cargo"),
        years=frozenset(years),
    )


@dataclass(frozen=True)
class ContextualLink:
    url: str
    title: str
    section: str


class _ContextualLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section = ""
        self._heading_depth = 0
        self._heading_parts: list[str] = []
        self._href: str | None = None
        self._anchor_parts: list[str] = []
        self._text_window = ""
        self._published_year: int | None = None
        self.links: list[tuple[str, str, str, int | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if re.fullmatch(r"h[1-6]", tag):
            self._heading_depth += 1
            self._heading_parts = []
        if tag == "time":
            datetime_value = attributes.get("datetime") or ""
            year_match = re.search(r"\b((?:19|20)\d{2})\b", datetime_value)
            if year_match:
                self._published_year = int(year_match.group(1))
        if tag == "a":
            href = attributes.get("href")
            data_url = attributes.get("data-url")
            self._href = (
                data_url if data_url and (not href or href.startswith("javascript:")) else href
            )
            self._anchor_parts = []

    def handle_data(self, data: str) -> None:
        self._text_window = (self._text_window + " " + data)[-240:]
        published = re.search(
            r"publicado\s+em\s+\d{1,2}\s+\d{1,2}\s+((?:19|20)\d{2})",
            normalize_discovery_text(self._text_window),
        )
        if published:
            self._published_year = int(published.group(1))
        if self._heading_depth:
            self._heading_parts.append(data)
        if self._href is not None:
            self._anchor_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if re.fullmatch(r"h[1-6]", tag) and self._heading_depth:
            self.section = " ".join("".join(self._heading_parts).split())
            self._heading_depth -= 1
            self._heading_parts = []
        if tag == "a" and self._href is not None:
            title = " ".join("".join(self._anchor_parts).split())
            self.links.append((self._href, title, self.section, self._published_year))
            self._href = None
            self._anchor_parts = []


@dataclass(frozen=True)
class InventoryDocument:
    url: str
    title: str
    document_type: DocumentType
    variant: str | None
    group: str | None
    year: int | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "url": self.url,
            "title": self.title,
            "document_type": self.document_type,
            "variant": self.variant,
            "group": self.group,
            "year": str(self.year) if self.year is not None else None,
        }


@dataclass(frozen=True)
class PageInventory:
    page_url: str
    expected: tuple[InventoryDocument, ...]

    @property
    def exams(self) -> int:
        return sum(item.document_type == "exam" for item in self.expected)

    @property
    def answer_keys(self) -> int:
        return sum(item.document_type == "answer_key" for item in self.expected)


def _document_type(title: str, url: str, source: SourceDefinition) -> DocumentType:
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    query = urlsplit(url).query
    evidence = f"{title}\n{filename}\n{query}"
    normalized_title = normalize_discovery_text(title)
    numbered_variant = re.search(r"\bgabarito\s*[1-9]\d*\b", normalized_title)
    exam_container = re.search(r"(?:^|/)provas?(?:/|$)", query.casefold())
    if (
        numbered_variant
        and not normalized_title.startswith("gabarito")
        and ("prova" in normalized_title or exam_container)
    ):
        return "exam"
    if any(re.search(pattern, evidence) for pattern in source.answer_key_patterns):
        return "answer_key"
    if any(re.search(pattern, evidence) for pattern in source.exam_patterns):
        return "exam"
    return "other"


def _variant(title: str) -> str | None:
    normalized = normalize_discovery_text(title)
    match = re.search(r"\b(?:gabarito|tipo|caderno)\s*([1-9]\d*)\b", normalized)
    return f"Tipo {match.group(1)}" if match else None


def _group(title: str) -> str | None:
    normalized = normalize_discovery_text(title)
    match = re.search(r"\bprova\s+([a-z])\b", normalized)
    return f"Prova {match.group(1).upper()}" if match else None


def extract_page_inventory(html: str, page_url: str, source: SourceDefinition) -> PageInventory:
    parser = _ContextualLinkParser()
    parser.feed(html)
    page_years = {int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", page_url)}
    page_year = next(iter(page_years)) if len(page_years) == 1 else None
    expected: list[InventoryDocument] = []
    seen: set[str] = set()
    for href, title, section, year in parser.links:
        url = urljoin(page_url, href)
        section_text = normalize_discovery_text(section)
        candidate = f"{title}\n{url}\n{section}"
        if _REJECTED_SECTION.search(section_text) and not _DOCUMENT_SECTION.search(section_text):
            continue
        if source.exclude_patterns and any(
            re.search(pattern, candidate) for pattern in source.exclude_patterns
        ):
            continue
        if source.include_patterns and not any(
            re.search(pattern, value)
            for pattern in source.include_patterns
            for value in (url, candidate)
        ):
            continue
        document_type = _document_type(title, url, source)
        if document_type == "other":
            continue
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        expected.append(
            InventoryDocument(
                url=url,
                title=title or urlsplit(url).path.rsplit("/", 1)[-1],
                document_type=document_type,
                variant=_variant(title),
                group=_group(title),
                year=year or page_year,
            )
        )
    return PageInventory(page_url=page_url, expected=tuple(expected))


def navigation_relevance(
    item: DiscoveredLink,
    target: DiscoveryTarget,
    source: SourceDefinition,
) -> tuple[int, str]:
    parsed = urlsplit(item.url)
    if parsed.path.casefold().endswith(".pdf"):
        return 0, "document"
    raw_evidence = f"{item.title}\n{item.url}"
    configured_collection = any(
        re.search(pattern, raw_evidence) for pattern in source.collection_url_patterns
    )
    configured_pagination = any(
        re.search(pattern, raw_evidence) for pattern in source.pagination_patterns
    )
    suffix = Path(parsed.path).suffix.casefold()
    if suffix in {".ashx", ".aspx", ".cgi", ".doc", ".docx", ".php", ".zip"} and not (
        configured_collection or configured_pagination
    ):
        return 0, "action_or_asset"
    normalized_raw_evidence = normalize_discovery_text(raw_evidence)
    if (
        source.exclude_patterns
        and any(re.search(pattern, raw_evidence) for pattern in source.exclude_patterns)
        and not _DOCUMENT_SECTION.search(normalized_raw_evidence)
    ):
        return 0, "administrative"
    evidence = normalize_discovery_text(f"{item.title} {parsed.path} {parsed.query}")
    if not evidence:
        return 0, "empty"
    if _REJECTED_SECTION.search(evidence) and not _DOCUMENT_SECTION.search(evidence):
        return 0, "administrative"
    score = 0
    reasons: list[str] = []
    matched_organizations = sorted(
        term for term in target.organizations if term and term in evidence
    )
    matched_roles = sorted(term for term in target.roles if term and term in evidence)
    matched_boards = sorted(term for term in target.boards if term and term in evidence)
    if matched_organizations:
        score += 70
        reasons.append("organization=" + ",".join(matched_organizations[:3]))
    if matched_roles:
        score += 45
        reasons.append("role=" + ",".join(matched_roles[:3]))
    if matched_boards:
        score += 15
        reasons.append("board=" + ",".join(matched_boards[:3]))
    matched_years = sorted(year for year in target.years if str(year) in evidence)
    title_years = {int(value) for value in re.findall(r"\b((?:19|20)\d{2})\b", item.title)}
    if target.years and title_years and title_years.isdisjoint(target.years):
        return 0, "year_mismatch"
    if matched_years:
        score += 20
        reasons.append("year=" + ",".join(str(year) for year in matched_years))
    specific_target = bool(target.organizations or target.roles or target.years)
    specific_match = bool(matched_organizations or matched_roles or matched_years)
    if specific_target and not specific_match:
        return 0, "target_mismatch"
    if _DISCOVERY_HINT.search(evidence):
        score += 25
        reasons.append("archive_hint")
    if configured_collection:
        score += 40
        reasons.append("configured_collection")
    if configured_pagination:
        score += 30
        reasons.append("configured_pagination")
    if (
        suffix
        and suffix not in {".htm", ".html"}
        and not (configured_collection or configured_pagination or _DISCOVERY_HINT.search(evidence))
    ):
        return 0, "action_or_asset"
    return score, ";".join(reasons) or "low_relevance"


def select_relevant_navigation_links(
    links: list[DiscoveredLink],
    target: DiscoveryTarget,
    source: SourceDefinition,
    *,
    minimum_score: int = 50,
    limit: int = 200,
) -> list[tuple[str, str]]:
    selected: list[tuple[int, str, str]] = []
    for item in safe_discovered_links(links, source):
        score, reason = navigation_relevance(item, target, source)
        if score >= minimum_score:
            selected.append((score, item.url, reason))
    selected.sort(key=lambda item: (-item[0], item[1]))
    unique: dict[str, tuple[str, str]] = {}
    for _score, url, reason in selected:
        unique.setdefault(url.split("#", 1)[0], (url, reason))
        if len(unique) >= limit:
            break
    return list(unique.values())


def finalize_discovery_inventory(
    entries: list[dict[str, object]], documents: list[DocumentRecord]
) -> list[dict[str, object]]:
    downloaded_by_source: dict[str, set[str]] = {}
    for document in documents:
        urls = downloaded_by_source.setdefault(document.source_id, set())
        for value in (document.original_url, document.resolved_url):
            try:
                urls.add(canonicalize_url(value))
            except ValueError:
                continue

    finalized: list[dict[str, object]] = []
    for entry in entries:
        source_id = str(entry.get("source_id", ""))
        expected_value = entry.get("expected", [])
        expected = expected_value if isinstance(expected_value, list) else []
        downloaded: list[dict[str, object]] = []
        missing: list[dict[str, object]] = []
        known_urls = downloaded_by_source.get(source_id, set())
        for raw_item in expected:
            if not isinstance(raw_item, dict):
                continue
            item = {str(key): value for key, value in raw_item.items()}
            raw_url = str(item.get("url", ""))
            item["url"] = redact_url_secrets(raw_url)
            try:
                found = canonicalize_url(raw_url) in known_urls
            except ValueError:
                found = False
            item["status"] = "downloaded" if found else "missing"
            (downloaded if found else missing).append(item)
        total = len(downloaded) + len(missing)
        finalized.append(
            {
                **entry,
                "page_url": redact_url_secrets(str(entry.get("page_url", ""))),
                "expected": [*downloaded, *missing],
                "downloaded": len(downloaded),
                "missing": missing,
                "complete": total > 0 and not missing,
                "coverage_percent": round((len(downloaded) / total) * 100, 2) if total else None,
                "stop_reason": "inventory_complete"
                if total and not missing
                else ("missing_documents" if total else "no_inventory_evidence"),
            }
        )
    return finalized


def summarize_discovery_inventory(entries: list[dict[str, object]]) -> dict[str, object]:
    expected: dict[tuple[str, str], dict[str, object]] = {}
    for entry in entries:
        source_id = str(entry.get("source_id", ""))
        raw_expected = entry.get("expected", [])
        if not isinstance(raw_expected, list):
            continue
        for item in raw_expected:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            expected.setdefault((source_id, url), item)
    values = list(expected.values())
    downloaded = sum(item.get("status") == "downloaded" for item in values)
    missing = len(values) - downloaded
    expected_variants = {
        (str(item.get("group") or ""), str(item.get("variant") or ""))
        for item in values
        if item.get("document_type") == "exam" and item.get("variant")
    }
    downloaded_variants = {
        (str(item.get("group") or ""), str(item.get("variant") or ""))
        for item in values
        if item.get("document_type") == "exam"
        and item.get("variant")
        and item.get("status") == "downloaded"
    }
    return {
        "expected_documents": len(values),
        "downloaded_documents": downloaded,
        "missing_documents": missing,
        "coverage_percent": round((downloaded / len(values)) * 100, 2) if values else None,
        "expected_variants": len(expected_variants),
        "downloaded_variants": len(downloaded_variants),
        "variant_coverage_percent": (
            round((len(downloaded_variants) / len(expected_variants)) * 100, 2)
            if expected_variants
            else None
        ),
        "complete": bool(values) and not missing,
    }
