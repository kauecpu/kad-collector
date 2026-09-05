from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from pathlib import Path

from kad_collector.real_homologation import RealHomologationError, run_real_homologation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Executa a homologação local com PDFs reais.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("tests/homologation/real-corpus.v1.toml"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/homologation/real-report.json"),
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("data/homologation/real-report.md"),
    )
    parser.add_argument(
        "--without-memory-tracing", action="store_true",
        help="Desativa tracemalloc; memória fica não medida no relatório.",
    )
    return parser


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--always", "--dirty"], text=True, encoding="utf-8"
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_real_homologation(
            args.manifest,
            args.report,
            args.markdown,
            commit=_commit(),
            measure_memory=not args.without_memory_tracing,
        )
    except (OSError, RealHomologationError) as exc:
        print(f"ERRO: {exc}")
        return 2
    summary = report["summary"]
    print(
        f"OK: {summary['documents']} documentos; "
        f"{summary['processed_without_failure_rate']:.1%} concluídos; "
        f"{summary['classification_accuracy']:.1%} de triagem automática correta"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
