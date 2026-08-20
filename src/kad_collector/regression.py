from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

FixtureKind = Literal["official", "synthetic"]
FixtureFormat = Literal["pdf", "text", "json"]
CaseStatus = Literal["supported", "planned"]

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RegressionError(ValueError):
    """Invalid regression configuration or failed regression assertion."""


@dataclass(frozen=True)
class FixtureSpec:
    id: str
    kind: FixtureKind
    path: Path
    format: FixtureFormat
    size_bytes: int
    sha256: str
    description: str
    source_url: str | None = None


@dataclass(frozen=True)
class CaseSpec:
    id: str
    title: str
    status: CaseStatus
    executor: str | None
    fixtures: tuple[str, ...]
    covers: tuple[str, ...]
    expected: dict[str, object]
    gap: str | None = None


@dataclass(frozen=True)
class RegressionManifest:
    path: Path
    schema_version: int
    coverage_topics: tuple[str, ...]
    fixtures: tuple[FixtureSpec, ...]
    cases: tuple[CaseSpec, ...]


def _require_table_rows(payload: object, field: str) -> list[dict[str, object]]:
    if payload is None:
        return []
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RegressionError(f"{field} deve ser uma lista de tabelas TOML")
    return cast(list[dict[str, object]], payload)


def _string_list(payload: object, field: str) -> tuple[str, ...]:
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise RegressionError(f"{field} deve ser uma lista de textos")
    return tuple(cast(list[str], payload))


def _parse_fixture(row: dict[str, object]) -> FixtureSpec:
    try:
        fixture_id = str(row["id"])
        raw_kind = str(row["kind"])
        raw_format = str(row["format"])
        path = Path(str(row["path"]))
        size_bytes = int(cast(int, row["size_bytes"]))
        sha256 = str(row["sha256"]).casefold()
        description = str(row["description"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RegressionError(f"fixture incompleta: {exc}") from exc
    if raw_kind not in {"official", "synthetic"}:
        raise RegressionError(f"tipo de fixture desconhecido: {raw_kind}")
    if raw_format not in {"pdf", "text", "json"}:
        raise RegressionError(f"formato de fixture desconhecido: {raw_format}")
    source_url = row.get("source_url")
    return FixtureSpec(
        id=fixture_id,
        kind=cast(FixtureKind, raw_kind),
        path=path,
        format=cast(FixtureFormat, raw_format),
        size_bytes=size_bytes,
        sha256=sha256,
        description=description,
        source_url=str(source_url) if source_url is not None else None,
    )


def _parse_case(row: dict[str, object]) -> CaseSpec:
    try:
        case_id = str(row["id"])
        title = str(row["title"])
        raw_status = str(row["status"])
        fixtures = _string_list(row.get("fixtures", []), f"fixtures do caso {case_id}")
        covers = _string_list(row.get("covers", []), f"covers do caso {case_id}")
    except (KeyError, TypeError, ValueError) as exc:
        raise RegressionError(f"caso incompleto: {exc}") from exc
    if raw_status not in {"supported", "planned"}:
        raise RegressionError(f"status de caso desconhecido: {raw_status}")
    executor = row.get("executor")
    gap = row.get("gap")
    expected = row.get("expected", {})
    if not isinstance(expected, dict):
        raise RegressionError(f"expected do caso {case_id} deve ser uma tabela")
    return CaseSpec(
        id=case_id,
        title=title,
        status=cast(CaseStatus, raw_status),
        executor=str(executor) if executor is not None else None,
        fixtures=fixtures,
        covers=covers,
        expected=cast(dict[str, object], expected),
        gap=str(gap) if gap is not None else None,
    )


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _validate_manifest(manifest: RegressionManifest) -> None:
    errors: list[str] = []
    if manifest.schema_version != 1:
        errors.append(f"schema_version não suportado: {manifest.schema_version}")

    fixture_ids = [item.id for item in manifest.fixtures]
    fixture_paths = [item.path.as_posix().casefold() for item in manifest.fixtures]
    for duplicate in sorted(_duplicates(fixture_ids)):
        errors.append(f"fixture duplicada: {duplicate}")
    for duplicate in sorted(_duplicates(fixture_paths)):
        errors.append(f"caminho de fixture duplicado: {duplicate}")

    known_fixtures = set(fixture_ids)
    for fixture in manifest.fixtures:
        if not fixture.id.strip():
            errors.append("fixture exige id")
        if fixture.path.is_absolute() or ".." in fixture.path.parts:
            errors.append(f"caminho de fixture deve ser relativo: {fixture.path}")
        if fixture.size_bytes <= 0:
            errors.append(f"tamanho inválido: {fixture.id}")
        if _SHA256_PATTERN.fullmatch(fixture.sha256) is None:
            errors.append(f"SHA-256 inválido: {fixture.id}")
        if fixture.kind == "official" and not (fixture.source_url or "").startswith("https://"):
            errors.append(f"origem oficial deve usar HTTPS: {fixture.id}")
        if fixture.format == "pdf" and fixture.path.suffix.casefold() != ".pdf":
            errors.append(f"fixture PDF deve usar extensão .pdf: {fixture.id}")

    case_ids = [item.id for item in manifest.cases]
    for duplicate in sorted(_duplicates(case_ids)):
        errors.append(f"caso duplicado: {duplicate}")
    known_topics = set(manifest.coverage_topics)
    covered: set[str] = set()
    for case in manifest.cases:
        if case.status == "supported" and not case.executor:
            errors.append(f"caso supported exige executor: {case.id}")
        if case.status == "planned" and not (case.gap or "").strip():
            errors.append(f"caso planned exige gap: {case.id}")
        for fixture_id in case.fixtures:
            if fixture_id not in known_fixtures:
                errors.append(f"fixture desconhecida: {fixture_id}")
        for topic in case.covers:
            if topic not in known_topics:
                errors.append(f"tópico desconhecido no caso {case.id}: {topic}")
            covered.add(topic)
    for topic in manifest.coverage_topics:
        if topic not in covered:
            errors.append(f"cobertura sem caso: {topic}")
    if errors:
        raise RegressionError("manifesto de regressão inválido:\n- " + "\n- ".join(errors))


def load_regression_manifest(path: Path) -> RegressionManifest:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RegressionError(f"não foi possível ler o manifesto {path}: {exc}") from exc
    try:
        schema_version = int(cast(int, payload["schema_version"]))
        topics = _string_list(payload["coverage_topics"], "coverage_topics")
    except (KeyError, TypeError, ValueError) as exc:
        raise RegressionError(f"cabeçalho do manifesto inválido: {exc}") from exc
    fixtures = tuple(
        _parse_fixture(row) for row in _require_table_rows(payload.get("fixtures"), "fixtures")
    )
    cases = tuple(_parse_case(row) for row in _require_table_rows(payload.get("cases"), "cases"))
    manifest = RegressionManifest(
        path=path.resolve(),
        schema_version=schema_version,
        coverage_topics=topics,
        fixtures=fixtures,
        cases=cases,
    )
    _validate_manifest(manifest)
    return manifest
