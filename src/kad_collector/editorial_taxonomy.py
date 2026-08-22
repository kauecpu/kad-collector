from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

from .desktop_models import DesktopImportMetadata

TaxonomyField = Literal["discipline", "matter", "subject"]


def normalize_taxonomy_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    alphanumeric = "".join(
        character if character.isalnum() else " " for character in plain
    )
    return " ".join(alphanumeric.casefold().split())


@dataclass(frozen=True)
class TaxonomyPath:
    discipline: str
    matter: str | None = None
    subject: str | None = None
    catalog_id: str | None = None
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticMatch:
    path: TaxonomyPath
    score: int
    evidence: str


@dataclass(frozen=True)
class OfficialProgramEntry:
    heading: str
    path: TaxonomyPath
    source_url: str
    line_number: int


class EditorialTaxonomy:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._initialize([payload], version=str(payload["version"]))

    @classmethod
    def _from_catalogs(
        cls, payloads: list[dict[str, Any]], *, version: str
    ) -> EditorialTaxonomy:
        taxonomy = cls.__new__(cls)
        taxonomy._initialize(payloads, version=version)
        return taxonomy

    @classmethod
    def load_default(cls) -> EditorialTaxonomy:
        package = resources.files("kad_collector")
        bundle = json.loads(
            package.joinpath("editorial_taxonomy.bundle.v2.json").read_text(
                encoding="utf-8"
            )
        )
        payloads = [
            json.loads(
                package.joinpath(*str(resource_name).split("/")).read_text(
                    encoding="utf-8"
                )
            )
            for resource_name in bundle["catalogs"]
        ]
        return cls._from_catalogs(payloads, version=str(bundle["version"]))

    @classmethod
    def load_directory(cls, directory: Path, *, version: str) -> EditorialTaxonomy:
        paths = sorted(directory.glob("*.json"))
        if not paths:
            raise ValueError("diretório de taxonomia não contém catálogos JSON")
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        return cls._from_catalogs(payloads, version=version)

    def _initialize(self, payloads: list[dict[str, Any]], *, version: str) -> None:
        self.version = version
        self.catalog_ids = tuple(
            str(payload.get("id") or f"legacy-catalog-{index}")
            for index, payload in enumerate(payloads, start=1)
        )
        if len(set(self.catalog_ids)) != len(self.catalog_ids):
            raise ValueError("identificador de catálogo duplicado")

        self._catalog_sources: dict[str, tuple[str, ...]] = {}
        self._catalog_matches: dict[str, dict[str, tuple[str, ...]]] = {}
        self._disciplines: dict[str, list[dict[str, Any]]] = {}
        self._sections: list[dict[str, Any]] = []
        self._metadata_profiles: list[dict[str, Any]] = []
        self._profiles: list[dict[str, Any]] = []
        self._known: dict[str, set[str]] = {
            "discipline": set(),
            "matter": set(),
            "subject": set(),
        }
        self._aliases: dict[str, dict[str, str | None]] = {
            "discipline": {},
            "matter": {},
            "subject": {},
        }
        self._heading_paths: list[tuple[str, TaxonomyPath]] = []

        all_sources: list[str] = []
        for catalog_id, payload in zip(self.catalog_ids, payloads, strict=True):
            catalog_version = str(payload.get("version") or "")
            if re.fullmatch(r"\d+\.\d+\.\d+", catalog_version) is None:
                raise ValueError(f"catálogo {catalog_id} exige versão semântica")
            source_urls = self._source_urls(payload.get("sources"), catalog_id)
            self._catalog_sources[catalog_id] = source_urls
            self._catalog_matches[catalog_id] = self._match_rules(
                payload.get("match"), catalog_id
            )
            all_sources.extend(source_urls)
            provenance = (catalog_id, *source_urls)

            for discipline in cast(list[dict[str, Any]], payload.get("disciplines", [])):
                discipline_name = str(discipline["name"])
                self._register_name(
                    "discipline",
                    discipline_name,
                    cast(list[object], discipline.get("aliases", [])),
                )
                discipline_path = TaxonomyPath(
                    discipline=discipline_name,
                    catalog_id=catalog_id,
                    provenance=provenance,
                )
                self._register_heading(discipline_name, discipline_path)
                for alias in cast(list[object], discipline.get("aliases", [])):
                    self._register_heading(str(alias), discipline_path)

                topics = self._disciplines.setdefault(discipline_name, [])
                for raw_topic in cast(list[dict[str, Any]], discipline.get("topics", [])):
                    topic = dict(raw_topic)
                    topic["_catalog_id"] = catalog_id
                    topic["_provenance"] = provenance
                    matter = str(topic["matter"])
                    subject = str(topic["subject"])
                    self._register_name(
                        "matter",
                        matter,
                        cast(list[object], topic.get("matter_aliases", [])),
                    )
                    self._register_name(
                        "subject",
                        subject,
                        cast(list[object], topic.get("subject_aliases", [])),
                    )
                    path = TaxonomyPath(
                        discipline=discipline_name,
                        matter=matter,
                        subject=subject,
                        catalog_id=catalog_id,
                        provenance=provenance,
                    )
                    for heading in cast(list[object], topic.get("headings", [])):
                        self._register_heading(str(heading), path)
                    topics.append(topic)

            for raw_section in cast(list[dict[str, Any]], payload.get("sections", [])):
                section = dict(raw_section)
                section["_catalog_id"] = catalog_id
                section["_provenance"] = provenance
                path = TaxonomyPath(
                    discipline=str(section["discipline"]),
                    matter=str(section["matter"]),
                    subject=str(section["subject"]),
                    catalog_id=catalog_id,
                    provenance=provenance,
                )
                headings = [
                    *cast(list[object], section.get("headings", [])),
                    " ".join(str(item) for item in section.get("tokens", [])),
                ]
                for heading in headings:
                    if str(heading).strip():
                        self._register_heading(str(heading), path)
                self._sections.append(section)

            for raw_profile in cast(
                list[dict[str, Any]], payload.get("metadata_profiles", [])
            ):
                profile = dict(raw_profile)
                profile["_catalog_id"] = catalog_id
                profile["_provenance"] = provenance
                self._metadata_profiles.append(profile)
            for raw_profile in cast(
                list[dict[str, Any]], payload.get("exam_profiles", [])
            ):
                profile = dict(raw_profile)
                profile["_catalog_id"] = catalog_id
                profile["_provenance"] = provenance
                self._profiles.append(profile)

        self.sources = tuple(dict.fromkeys(all_sources))
        self._validate()

    @staticmethod
    def _source_urls(raw_sources: object, catalog_id: str) -> tuple[str, ...]:
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError(f"catálogo {catalog_id} exige fontes oficiais")
        urls: list[str] = []
        for item in raw_sources:
            if isinstance(item, str):
                url = item
            elif isinstance(item, dict):
                url = str(item.get("url") or "")
                if not str(item.get("id") or "").strip() or not str(
                    item.get("title") or ""
                ).strip():
                    raise ValueError(
                        f"fonte estruturada do catálogo {catalog_id} exige id e título"
                    )
            else:
                raise ValueError(f"fonte inválida no catálogo {catalog_id}")
            if not url.startswith("https://"):
                raise ValueError("fontes da taxonomia devem usar HTTPS")
            urls.append(url)
        return tuple(dict.fromkeys(urls))

    @staticmethod
    def _match_rules(
        raw_match: object, catalog_id: str
    ) -> dict[str, tuple[str, ...]]:
        if raw_match is None:
            return {}
        if not isinstance(raw_match, dict):
            raise ValueError(f"match inválido no catálogo {catalog_id}")
        allowed = {
            "concurso_contains",
            "organization_contains",
            "source_contains",
        }
        unknown = set(raw_match) - allowed
        if unknown:
            raise ValueError(
                f"critérios de match desconhecidos no catálogo {catalog_id}: "
                + ", ".join(sorted(unknown))
            )
        rules: dict[str, tuple[str, ...]] = {}
        for field, raw_values in raw_match.items():
            if not isinstance(raw_values, list) or not raw_values:
                raise ValueError(
                    f"critério {field} do catálogo {catalog_id} exige uma lista"
                )
            values = tuple(
                normalized
                for value in raw_values
                if (normalized := normalize_taxonomy_text(str(value)))
            )
            if not values:
                raise ValueError(
                    f"critério {field} do catálogo {catalog_id} está vazio"
                )
            rules[field] = values
        return rules

    def _register_name(
        self, field: TaxonomyField, canonical: str, aliases: list[object]
    ) -> None:
        self._known[field].add(canonical)
        for candidate in (canonical, *(str(item) for item in aliases)):
            normalized = normalize_taxonomy_text(candidate)
            if not normalized:
                raise ValueError(f"alias vazio para {field} {canonical}")
            existing = self._aliases[field].get(normalized)
            if existing is not None and existing != canonical:
                self._aliases[field][normalized] = None
            elif normalized not in self._aliases[field]:
                self._aliases[field][normalized] = canonical

    def _register_heading(self, heading: str, path: TaxonomyPath) -> None:
        normalized = normalize_taxonomy_text(heading)
        if normalized:
            self._heading_paths.append((normalized, path))

    def _validate(self) -> None:
        if re.fullmatch(r"\d+\.\d+\.\d+", self.version) is None:
            raise ValueError("taxonomia editorial exige versão semântica")
        if not self.sources:
            raise ValueError("taxonomia editorial exige fontes oficiais")
        for section in self._sections:
            for field in ("discipline", "matter", "subject"):
                self.ensure_known(field, str(section[field]))
        for profile in self._profiles:
            for start, end, discipline in profile["ranges"]:
                if int(start) < 1 or int(end) < int(start):
                    raise ValueError("intervalo inválido na taxonomia editorial")
                self.ensure_known("discipline", str(discipline))
        for profile in self._metadata_profiles:
            if profile.get("level") not in {"Fundamental", "Médio", "Superior"}:
                raise ValueError("nível inválido na taxonomia editorial")

    def canonical_name(self, field: TaxonomyField, value: str) -> str:
        canonical = self._aliases[field].get(normalize_taxonomy_text(value))
        if canonical is None:
            raise ValueError(f"{field} '{value}' está fora da taxonomia {self.version}")
        return canonical

    def ensure_known(self, field: TaxonomyField, value: str) -> None:
        self.canonical_name(field, value)

    @staticmethod
    def _path_specificity(path: TaxonomyPath) -> int:
        return 1 + int(path.matter is not None) + int(path.subject is not None)

    def relevant_catalog_ids(
        self, metadata: DesktopImportMetadata
    ) -> tuple[str, ...]:
        values = {
            "concurso_contains": normalize_taxonomy_text(metadata.concurso or ""),
            "organization_contains": normalize_taxonomy_text(
                metadata.organization or ""
            ),
            "source_contains": normalize_taxonomy_text(
                " ".join(
                    value
                    for value in (
                        metadata.source_url,
                        metadata.canonical_url,
                        metadata.document_title,
                        metadata.external_id,
                    )
                    if value
                )
            ),
        }
        matched = tuple(
            catalog_id
            for catalog_id in self.catalog_ids
            if any(
                candidate in values[field]
                for field, candidates in self._catalog_matches[catalog_id].items()
                for candidate in candidates
                if values[field]
            )
        )
        if matched:
            return matched
        if not any(values.values()):
            return self.catalog_ids
        return ()

    @staticmethod
    def _catalog_allowed(
        path: TaxonomyPath, catalog_ids: frozenset[str] | None
    ) -> bool:
        return catalog_ids is None or path.catalog_id in catalog_ids

    def _match_text(
        self,
        text: str | None,
        *,
        source_url: str | None = None,
        catalog_ids: Iterable[str] | None = None,
    ) -> TaxonomyPath | None:
        if not text:
            return None
        normalized = normalize_taxonomy_text(text)
        allowed_catalogs = (
            frozenset(catalog_ids) if catalog_ids is not None else None
        )
        padded_text = f" {normalized} "
        matches: list[tuple[int, int, TaxonomyPath]] = []
        for heading, path in self._heading_paths:
            if not self._catalog_allowed(path, allowed_catalogs):
                continue
            if source_url is not None and source_url not in path.provenance:
                continue
            if heading == normalized or f" {heading} " in padded_text:
                matches.append((self._path_specificity(path), len(heading), path))
        for section in self._sections:
            path = TaxonomyPath(
                discipline=str(section["discipline"]),
                matter=str(section["matter"]),
                subject=str(section["subject"]),
                catalog_id=str(section["_catalog_id"]),
                provenance=cast(tuple[str, ...], section["_provenance"]),
            )
            if not self._catalog_allowed(path, allowed_catalogs):
                continue
            if source_url is not None and source_url not in path.provenance:
                continue
            tokens = [
                normalize_taxonomy_text(str(item)) for item in section.get("tokens", [])
            ]
            if tokens and all(f" {token} " in padded_text for token in tokens):
                matches.append((3, sum(len(token) for token in tokens), path))
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_rank = matches[0][:2]
        best = [item[2] for item in matches if item[:2] == best_rank]
        canonical_paths = {
            (item.discipline, item.matter, item.subject) for item in best
        }
        if len(canonical_paths) != 1:
            return None
        selected = best[0]
        provenance = tuple(
            dict.fromkeys(value for item in best for value in item.provenance)
        )
        return TaxonomyPath(
            discipline=selected.discipline,
            matter=selected.matter,
            subject=selected.subject,
            catalog_id=selected.catalog_id,
            provenance=provenance,
        )

    def match_heading(
        self, text: str | None, *, catalog_ids: Iterable[str] | None = None
    ) -> TaxonomyPath | None:
        if not text:
            return None
        heading = text.split(":", 1)[0].strip()
        normalized = normalize_taxonomy_text(heading)
        allowed_catalogs = (
            frozenset(catalog_ids) if catalog_ids is not None else None
        )
        exact = [
            path
            for candidate, path in self._heading_paths
            if candidate == normalized
            and self._catalog_allowed(path, allowed_catalogs)
        ]
        if not exact:
            return None
        best_specificity = max(self._path_specificity(path) for path in exact)
        best = [path for path in exact if self._path_specificity(path) == best_specificity]
        canonical_paths = {
            (item.discipline, item.matter, item.subject) for item in best
        }
        return best[0] if len(canonical_paths) == 1 else None

    def match_section(
        self, text: str | None, *, catalog_ids: Iterable[str] | None = None
    ) -> TaxonomyPath | None:
        return self._match_text(text, catalog_ids=catalog_ids)

    def match_context_heading(
        self, text: str | None, *, catalog_ids: Iterable[str] | None = None
    ) -> TaxonomyPath | None:
        if not text:
            return None
        lines = [" ".join(raw_line.split()) for raw_line in text.splitlines()]
        lines = [line for line in lines if line]
        for width in range(1, min(3, len(lines)) + 1):
            for start in range(0, len(lines) - width + 1):
                candidate = " ".join(lines[start : start + width])
                exact = self.match_heading(candidate, catalog_ids=catalog_ids)
                if exact is not None:
                    return exact
                words = normalize_taxonomy_text(candidate).split()
                if (
                    len(candidate) > 160
                    or len(words) > 12
                    or any(mark in candidate for mark in (".", "?", "!", ";"))
                ):
                    continue
                section = self.match_section(candidate, catalog_ids=catalog_ids)
                if (
                    section is not None
                    and section.matter is not None
                    and section.subject is not None
                ):
                    return section
        return None

    def parse_official_program(
        self, text: str, *, source_url: str
    ) -> list[OfficialProgramEntry]:
        if source_url not in self.sources:
            raise ValueError(f"origem oficial não registrada: {source_url}")
        entries: list[OfficialProgramEntry] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = " ".join(raw_line.split())
            if not line:
                continue
            if ":" not in line and self.match_heading(line) is None:
                continue
            path = self._match_text(line, source_url=source_url)
            if path is None:
                continue
            heading = line.split(":", 1)[0].strip()
            entries.append(
                OfficialProgramEntry(
                    heading=heading,
                    path=path,
                    source_url=source_url,
                    line_number=line_number,
                )
            )
        return entries

    def match_official_range(
        self, metadata: DesktopImportMetadata, question_number: int
    ) -> TaxonomyPath | None:
        source = normalize_taxonomy_text(metadata.source_url or "")
        role = normalize_taxonomy_text(metadata.role or "")
        stage = normalize_taxonomy_text(metadata.stage or "")
        for profile in self._profiles:
            if normalize_taxonomy_text(str(profile["source_contains"])) not in source:
                continue
            if normalize_taxonomy_text(str(profile["role_contains"])) not in role:
                continue
            if normalize_taxonomy_text(str(profile["stage_contains"])) not in stage:
                continue
            for start, end, discipline in profile["ranges"]:
                if int(start) <= question_number <= int(end):
                    return TaxonomyPath(
                        discipline=str(discipline),
                        catalog_id=str(profile["_catalog_id"]),
                        provenance=cast(tuple[str, ...], profile["_provenance"]),
                    )
        return None

    def official_level(self, metadata: DesktopImportMetadata) -> str | None:
        concurso = normalize_taxonomy_text(metadata.concurso or "")
        role = normalize_taxonomy_text(metadata.role or "")
        for profile in self._metadata_profiles:
            if normalize_taxonomy_text(str(profile["concurso_contains"])) not in concurso:
                continue
            if normalize_taxonomy_text(str(profile["role_contains"])) not in role:
                continue
            return str(profile["level"])
        return None

    def semantic_match(
        self,
        text: str,
        *,
        discipline: str | None = None,
        catalog_ids: Iterable[str] | None = None,
    ) -> SemanticMatch | None:
        normalized = normalize_taxonomy_text(text)
        allowed_catalogs = (
            frozenset(catalog_ids) if catalog_ids is not None else None
        )
        candidates: list[SemanticMatch] = []
        canonical_discipline: str | None = None
        if discipline is not None:
            try:
                canonical_discipline = self.canonical_name("discipline", discipline)
            except ValueError:
                return None
        disciplines = (
            ((canonical_discipline, self._disciplines[canonical_discipline]),)
            if canonical_discipline in self._disciplines
            else self._disciplines.items()
        )
        for discipline_name, topics in disciplines:
            for topic in topics:
                if (
                    allowed_catalogs is not None
                    and str(topic["_catalog_id"]) not in allowed_catalogs
                ):
                    continue
                matched = [
                    str(keyword)
                    for keyword in topic.get("keywords", [])
                    if normalize_taxonomy_text(str(keyword)) in normalized
                ]
                if matched:
                    candidates.append(
                        SemanticMatch(
                            path=TaxonomyPath(
                                discipline=discipline_name,
                                matter=str(topic["matter"]),
                                subject=str(topic["subject"]),
                                catalog_id=str(topic["_catalog_id"]),
                                provenance=cast(
                                    tuple[str, ...], topic["_provenance"]
                                ),
                            ),
                            score=len(matched),
                            evidence=", ".join(matched[:3]),
                        )
                    )
        candidates.sort(key=lambda item: item.score, reverse=True)
        if not candidates:
            return None
        if len(candidates) > 1 and candidates[0].score == candidates[1].score:
            return None
        if discipline is None and candidates[0].score < 2:
            return None
        return candidates[0]
