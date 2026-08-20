from __future__ import annotations

import hashlib
import json
import re
import socket
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from unittest.mock import patch

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


CaseExecutor = Callable[[CaseSpec, Mapping[str, Path]], dict[str, object]]


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_fixture(spec: FixtureSpec, root: Path) -> Path:
    path = (root / spec.path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RegressionError(f"fixture fora da raiz: {spec.id}") from exc
    if not path.is_file():
        raise RegressionError(f"fixture ausente: {spec.id} ({path})")
    size = path.stat().st_size
    if size != spec.size_bytes:
        raise RegressionError(
            f"tamanho divergente da fixture {spec.id}: {size} != {spec.size_bytes}"
        )
    digest = _sha256(path)
    if digest != spec.sha256:
        raise RegressionError(f"SHA-256 divergente da fixture {spec.id}: {digest}")
    if spec.format == "pdf":
        with path.open("rb") as handle:
            if not handle.read(5).startswith(b"%PDF-"):
                raise RegressionError(f"assinatura PDF inválida: {spec.id}")
    elif spec.format == "text":
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RegressionError(f"texto UTF-8 inválido: {spec.id}") from exc
    else:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RegressionError(f"JSON inválido: {spec.id}") from exc
    return path


@contextmanager
def _offline_guard() -> Iterator[None]:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise RegressionError("acesso à rede bloqueado durante a regressão offline")

    with (
        patch("socket.create_connection", side_effect=blocked),
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
    ):
        yield


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary_name = handle.name
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()


def _coverage_rows(
    manifest: RegressionManifest, case_results: Mapping[str, str]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for topic in manifest.coverage_topics:
        cases = [case for case in manifest.cases if topic in case.covers]
        supported = [case for case in cases if case.status == "supported"]
        if supported:
            state = (
                "passed"
                if all(case_results.get(case.id) == "passed" for case in supported)
                else "failed"
            )
        else:
            state = "planned"
        rows.append(
            {
                "topic": topic,
                "state": state,
                "cases": [case.id for case in cases],
            }
        )
    return rows


def run_regression(
    manifest_path: Path,
    report_path: Path,
    *,
    executors: Mapping[str, CaseExecutor] | None = None,
) -> dict[str, object]:
    manifest = load_regression_manifest(manifest_path)
    registry = dict(executors or {})
    fixture_paths = {
        fixture.id: validate_fixture(fixture, manifest.path.parent)
        for fixture in manifest.fixtures
    }
    case_rows: list[dict[str, object]] = []
    case_states: dict[str, str] = {}
    passed = 0
    planned = 0
    for case in manifest.cases:
        if case.status == "planned":
            planned += 1
            case_states[case.id] = "planned"
            case_rows.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "status": "planned",
                    "gap": case.gap,
                    "covers": list(case.covers),
                }
            )
            continue
        executor = registry.get(case.executor or "")
        if executor is None:
            raise RegressionError(f"executor desconhecido: {case.executor}")
        selected_fixtures = {item: fixture_paths[item] for item in case.fixtures}
        try:
            with _offline_guard():
                first = executor(case, selected_fixtures)
                second = executor(case, selected_fixtures)
        except RegressionError:
            raise
        except Exception as exc:
            raise RegressionError(f"caso {case.id} falhou: {type(exc).__name__}: {exc}") from exc
        if first != second:
            raise RegressionError(f"caso não determinístico: {case.id}")
        if first != case.expected:
            raise RegressionError(
                f"resultado inesperado no caso {case.id}: {first!r} != {case.expected!r}"
            )
        passed += 1
        case_states[case.id] = "passed"
        case_rows.append(
            {
                "id": case.id,
                "title": case.title,
                "status": "passed",
                "covers": list(case.covers),
                "result": first,
            }
        )

    report: dict[str, object] = {
        "schema_version": manifest.schema_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": str(manifest.path),
        "manifest_sha256": _sha256(manifest.path),
        "offline": True,
        "fixtures": [
            {
                "id": fixture.id,
                "kind": fixture.kind,
                "path": fixture.path.as_posix(),
                "size_bytes": fixture.size_bytes,
                "sha256": fixture.sha256,
            }
            for fixture in manifest.fixtures
        ],
        "cases": case_rows,
        "coverage": _coverage_rows(manifest, case_states),
        "summary": {
            "supported": len(manifest.cases) - planned,
            "passed": passed,
            "planned": planned,
        },
    }
    _write_report(report_path, report)
    return report
