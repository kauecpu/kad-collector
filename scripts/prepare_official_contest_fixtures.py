from __future__ import annotations

import argparse
import hashlib
import tempfile
import time
import urllib.robotparser
from collections.abc import Sequence
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from kad_collector.official_regression import (
    OfficialContestManifest,
    OfficialDocumentSpec,
    OfficialRegressionError,
    load_official_manifest,
    validate_official_fixture,
)

USER_AGENT = "KADCollector-OfficialRegressionFixturePreparation/1.0"


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: Message,
        new_url: str,
    ) -> Request | None:
        del request, file_pointer, code, message, headers
        raise OfficialRegressionError(f"redirect not allowed: {new_url}")


_URL_OPENER = build_opener(RejectRedirects())


def urlopen(request: Request, *, timeout: int) -> object:
    return _URL_OPENER.open(request, timeout=timeout)


def _apply_access_policy(manifest: OfficialContestManifest, document: OfficialDocumentSpec) -> None:
    if manifest.robots_policy == "ignore" and manifest.crawl_delay_policy == "ignore":
        return
    parsed = urlsplit(document.source_url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    request = Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            robots_text = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        if manifest.robots_policy == "enforce" or manifest.crawl_delay_policy == "enforce":
            raise OfficialRegressionError(
                f"could not apply robots policy: {document.id}"
            ) from exc
        print(f"OBSERVE: robots.txt unavailable for {document.id}: {exc}")
        return

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(robots_text.splitlines())
    allowed = parser.can_fetch(USER_AGENT, document.source_url)
    if manifest.robots_policy == "enforce" and not allowed:
        raise OfficialRegressionError(f"robots.txt blocks fixture: {document.id}")
    if manifest.robots_policy == "observe" and not allowed:
        print(f"OBSERVE: robots.txt would block {document.id}")

    crawl_delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*")
    if crawl_delay and manifest.crawl_delay_policy == "enforce":
        time.sleep(crawl_delay)
    elif crawl_delay and manifest.crawl_delay_policy == "observe":
        print(f"OBSERVE: Crawl-delay {crawl_delay} s for {document.id}")


def prepare_official_contest_fixtures(manifest_path: Path) -> list[Path]:
    loaded = load_official_manifest(manifest_path)
    root = loaded.path.parent
    prepared: list[Path] = []
    for document in loaded.spec.documents:
        if document.support_status != "supported":
            continue
        destination = (root / document.path).resolve()
        try:
            validate_official_fixture(document, root)
        except OfficialRegressionError:
            pass
        else:
            prepared.append(destination)
            continue

        _apply_access_policy(loaded.spec, document)
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
                request = Request(document.source_url, headers={"User-Agent": USER_AGENT})
                with urlopen(request, timeout=60) as response:
                    total = 0
                    while True:
                        read_size = min(1024 * 1024, document.size_bytes - total + 1)
                        chunk = response.read(max(1, read_size))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > document.size_bytes:
                            raise OfficialRegressionError(
                                f"download exceeds declared size: {document.id}"
                            )
                        output.write(chunk)
            if temporary_path.stat().st_size != document.size_bytes:
                raise OfficialRegressionError(f"download size mismatch: {document.id}")
            with temporary_path.open("rb") as handle:
                if not handle.read(5).startswith(b"%PDF-"):
                    raise OfficialRegressionError(f"invalid PDF signature: {document.id}")
            digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
            if digest != document.sha256:
                raise OfficialRegressionError(
                    f"download SHA-256 mismatch: {document.id} ({digest})"
                )
            temporary_path.replace(destination)
            temporary_path = None
            prepared.append(destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    return prepared


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepara os PDFs locais de uma regressão oficial versionada."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/regression/rfb22/manifest.v1.toml"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prepared = prepare_official_contest_fixtures(args.manifest)
    except (OSError, OfficialRegressionError) as exc:
        print(f"ERRO: {exc}")
        return 2
    for path in prepared:
        print(f"OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
