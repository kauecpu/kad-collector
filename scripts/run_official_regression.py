from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from kad_collector.official_regression import OfficialRegressionError, run_official_regression


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa a regressão offline baseada em manifesto oficial."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/regression/rfb22/manifest.v1.toml"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("tests/regression/rfb22-report.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_official_regression(args.manifest, args.report)
    except (OSError, OfficialRegressionError) as exc:
        print(f"ERRO: {exc}")
        return 2
    summary = report["summary"]
    print(
        "OK: "
        f"{summary['passed']}/{summary['exam_cases']} cadernos; "
        f"{summary['supported_documents']} documentos oficiais verificados"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
