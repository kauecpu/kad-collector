from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from prepare_official_contest_fixtures import prepare_official_contest_fixtures

from kad_collector.real_homologation import (
    RealHomologationError,
    load_real_homologation,
    prepare_external_documents,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepara o corpus real de homologação.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/homologation/real-corpus.v1.toml"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        loaded = load_real_homologation(args.manifest)
        for manifest in loaded.spec.official_manifests:
            prepare_official_contest_fixtures((loaded.path.parent / manifest).resolve())
        prepared = prepare_external_documents(loaded)
    except (OSError, RealHomologationError) as exc:
        print(f"ERRO: {exc}")
        return 2
    print(f"OK: {len(prepared)} documentos externos validados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
