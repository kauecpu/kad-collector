from __future__ import annotations

import argparse
import tempfile
import time
import urllib.robotparser
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from kad_collector.regression import (
    FixtureSpec,
    RegressionError,
    load_regression_manifest,
    validate_fixture,
)

USER_AGENT = "KADCollector-RegressionFixturePreparation/1.0"


def _apply_access_policy(fixture: FixtureSpec) -> None:
    if fixture.robots_policy == "ignore" and fixture.crawl_delay_policy == "ignore":
        return
    source_url = fixture.source_url or ""
    parsed = urlsplit(source_url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            robots_text = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        if fixture.robots_policy == "enforce" or fixture.crawl_delay_policy == "enforce":
            raise RegressionError(
                f"não foi possível aplicar a política de robots.txt: {fixture.id}"
            ) from exc
        print(f"OBSERVE: robots.txt indisponível para {fixture.id}: {exc}")
        return

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(robots_text.splitlines())
    allowed = parser.can_fetch(USER_AGENT, source_url)
    if fixture.robots_policy == "enforce" and not allowed:
        raise RegressionError(f"robots.txt bloqueia a fixture: {fixture.id}")
    if fixture.robots_policy == "observe" and not allowed:
        print(f"OBSERVE: robots.txt bloquearia {fixture.id}")

    crawl_delay = parser.crawl_delay(USER_AGENT)
    if crawl_delay is None:
        crawl_delay = parser.crawl_delay("*")
    if crawl_delay and fixture.crawl_delay_policy == "enforce":
        time.sleep(crawl_delay)
    elif crawl_delay and fixture.crawl_delay_policy == "observe":
        print(f"OBSERVE: Crawl-delay de {crawl_delay} s para {fixture.id}")


def prepare_official_fixtures(manifest_path: Path) -> list[Path]:
    manifest = load_regression_manifest(manifest_path)
    root = manifest.path.parent
    prepared: list[Path] = []
    for fixture in manifest.fixtures:
        if fixture.kind != "official":
            continue
        destination = (root / fixture.path).resolve()
        try:
            validate_fixture(fixture, root)
        except RegressionError:
            pass
        else:
            prepared.append(destination)
            continue

        _apply_access_policy(fixture)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                request = Request(
                    fixture.source_url or "",
                    headers={"User-Agent": USER_AGENT},
                )
                with urlopen(request, timeout=60) as response:
                    total = 0
                    while True:
                        read_size = min(1024 * 1024, fixture.size_bytes - total + 1)
                        chunk = response.read(max(1, read_size))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > fixture.size_bytes:
                            raise RegressionError(
                                f"download excede o limite de tamanho: {fixture.id}"
                            )
                        output.write(chunk)
            temporary_spec = replace(
                fixture,
                path=temporary_path.relative_to(root),
            )
            validate_fixture(temporary_spec, root)
            temporary_path.replace(destination)
            temporary_path = None
            prepared.append(destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    return prepared


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepara os PDFs oficiais locais do pacote de regressão."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/regression/manifest.toml"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prepared = prepare_official_fixtures(args.manifest)
    except (OSError, RegressionError) as exc:
        print(f"ERRO: {exc}")
        return 2
    for path in prepared:
        print(f"OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
