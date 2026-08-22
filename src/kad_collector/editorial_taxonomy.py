from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

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


@dataclass(frozen=True)
class SemanticMatch:
    path: TaxonomyPath
    score: int
    evidence: str


class EditorialTaxonomy:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.version = str(payload["version"])
        self.sources = tuple(str(item) for item in payload["sources"])
        self._disciplines = {
            str(item["name"]): tuple(item.get("topics", []))
            for item in payload["disciplines"]
        }
        self._sections = tuple(payload.get("sections", []))
        self._metadata_profiles = tuple(payload.get("metadata_profiles", []))
        self._profiles = tuple(payload.get("exam_profiles", []))
        self._known: dict[str, set[str]] = {
            "discipline": set(self._disciplines),
            "matter": set(),
            "subject": set(),
        }
        for topics in self._disciplines.values():
            for topic in topics:
                self._known["matter"].add(str(topic["matter"]))
                self._known["subject"].add(str(topic["subject"]))
        for section in self._sections:
            self._known["matter"].add(str(section["matter"]))
            self._known["subject"].add(str(section["subject"]))
        self._validate()

    @classmethod
    def load_default(cls) -> EditorialTaxonomy:
        resource = resources.files("kad_collector").joinpath("editorial_taxonomy.v1.json")
        return cls(json.loads(resource.read_text(encoding="utf-8")))

    def _validate(self) -> None:
        if not self.version or len(self.sources) < 1:
            raise ValueError("taxonomia editorial exige versão e fontes oficiais")
        for source in self.sources:
            if not source.startswith("https://"):
                raise ValueError("fontes da taxonomia devem usar HTTPS")
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

    def ensure_known(self, field: TaxonomyField, value: str) -> None:
        if value not in self._known[field]:
            raise ValueError(f"{field} '{value}' está fora da taxonomia {self.version}")

    def match_section(self, text: str | None) -> TaxonomyPath | None:
        if not text:
            return None
        normalized = normalize_taxonomy_text(text)
        matches: list[tuple[int, TaxonomyPath]] = []
        for section in self._sections:
            tokens = [normalize_taxonomy_text(str(item)) for item in section["tokens"]]
            if all(token in normalized for token in tokens):
                matches.append(
                    (
                        len(tokens),
                        TaxonomyPath(
                            discipline=str(section["discipline"]),
                            matter=str(section["matter"]),
                            subject=str(section["subject"]),
                        ),
                    )
                )
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        best_score = matches[0][0]
        best_paths = {item[1] for item in matches if item[0] == best_score}
        return next(iter(best_paths)) if len(best_paths) == 1 else None

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
                    return TaxonomyPath(discipline=str(discipline))
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
        self, text: str, *, discipline: str | None = None
    ) -> SemanticMatch | None:
        normalized = normalize_taxonomy_text(text)
        candidates: list[SemanticMatch] = []
        disciplines = (
            ((discipline, self._disciplines[discipline]),)
            if discipline in self._disciplines
            else self._disciplines.items()
        )
        for discipline_name, topics in disciplines:
            for topic in topics:
                matched = [
                    str(keyword)
                    for keyword in topic["keywords"]
                    if normalize_taxonomy_text(str(keyword)) in normalized
                ]
                if matched:
                    candidates.append(
                        SemanticMatch(
                            path=TaxonomyPath(
                                discipline=discipline_name,
                                matter=str(topic["matter"]),
                                subject=str(topic["subject"]),
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
