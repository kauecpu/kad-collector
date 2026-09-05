from __future__ import annotations

import tomllib
from pathlib import Path

from kad_collector import __version__


def test_runtime_version_matches_package_metadata() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert __version__ == project["project"]["version"]
