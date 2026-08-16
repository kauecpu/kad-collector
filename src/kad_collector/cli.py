from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ai_processor import process_extraction_manifest
from .answer_key import match_answer_key
from .automation import run_automatic
from .collector import collect_documents
from .config import load_config
from .database import stage_batch
from .guided_test import run_guided_test
from .json_utils import read_json
from .models import CollectionFilters, QuestionBatch
from .pdf_extractor import extract_manifest
from .promotion import build_promotion_package, dry_run_promotion
from .review import approve_batch
from .review_server import serve_review_application
from .validation import validate_questions
from .workflow import read_requested_urls, run_semiautomatic


def _path(value: str) -> Path:
    return Path(value)


def _add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--year", "--ano", dest="years", type=int, action="append")
    parser.add_argument("--board", "--banca", dest="boards", action="append")
    parser.add_argument("--organization", "--orgao", dest="organizations", action="append")
    parser.add_argument("--role", "--cargo", dest="roles", action="append")
    parser.add_argument("--matter", "--materia", dest="matters", action="append")
    parser.add_argument("--subject", "--assunto", dest="subjects", action="append")


def _filters_from_args(args: argparse.Namespace) -> CollectionFilters | None:
    filters = CollectionFilters(
        years=args.years or [],
        boards=args.boards or [],
        organizations=args.organizations or [],
        roles=args.roles or [],
        matters=args.matters or [],
        subjects=args.subjects or [],
    )
    return None if filters.is_empty() else filters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kad-collector",
        description="Coleta provas e prepara questoes para revisao editorial.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="localiza e baixa PDFs permitidos")
    collect.add_argument("--config", type=_path, default=Path("config/sources.toml"))
    _add_filter_arguments(collect)

    run = subparsers.add_parser(
        "run",
        aliases=["semi-auto"],
        help="executa links informados e gera um relatorio local organizado",
    )
    run.add_argument("--config", type=_path, default=Path("config/sources.toml"))
    run.add_argument("--url", action="append", default=[])
    run.add_argument("--urls-file", type=_path, action="append", default=[])
    run.add_argument("--output", type=_path)
    run.add_argument("--model")
    run.add_argument("--max-chars", type=int, default=40_000)
    run.add_argument("--overlap-chars", type=int, default=3_000)
    _add_filter_arguments(run)

    sync = subparsers.add_parser(
        "sync", help="processa somente novidades das fontes cadastradas e atualiza o estado"
    )
    sync.add_argument("--config", type=_path, default=Path("config/sources.toml"))
    sync.add_argument("--state", type=_path)
    sync.add_argument("--output", type=_path)
    sync.add_argument("--model")
    sync.add_argument("--max-chars", type=int, default=40_000)
    sync.add_argument("--overlap-chars", type=int, default=3_000)
    sync.add_argument("--max-attempts", type=int, default=3)
    sync.add_argument("--retry-delay-seconds", type=int, default=300)
    _add_filter_arguments(sync)

    guided_test = subparsers.add_parser(
        "test",
        aliases=["testar"],
        help="executa um teste reduzido e abre a fila de revisao automaticamente",
    )
    guided_test.add_argument(
        "--config", type=_path, default=Path("config/sources.test.toml")
    )
    guided_test.add_argument(
        "--state", type=_path, default=Path("data/state/teste-guiado.json")
    )
    guided_test.add_argument(
        "--output", type=_path, default=Path("data/results/teste-guiado.json")
    )
    guided_test.add_argument("--model")
    guided_test.add_argument("--port", type=int, default=8765)

    extract = subparsers.add_parser("extract", help="extrai texto dos PDFs de um manifesto")
    extract.add_argument("manifest", type=_path)
    extract.add_argument("--output", type=_path)

    process = subparsers.add_parser("process", help="estrutura questoes com IA")
    process.add_argument("extraction", type=_path)
    process.add_argument("--output-dir", type=_path, default=Path("data/processed"))
    process.add_argument("--model")
    process.add_argument("--max-chars", type=int, default=40_000)
    process.add_argument("--overlap-chars", type=int, default=3_000)
    _add_filter_arguments(process)

    answers = subparsers.add_parser("match-answers", help="relaciona um gabarito ao lote")
    answers.add_argument("batch", type=_path)
    answers.add_argument("answer_key", type=_path)
    answers.add_argument("--output", type=_path)

    validate = subparsers.add_parser("validate", help="valida um lote sem altera-lo")
    validate.add_argument("batch", type=_path)
    validate.add_argument("--require-answers", action="store_true")

    review = subparsers.add_parser(
        "review", help="abre a revisao editorial local por questao"
    )
    review.add_argument("batch", type=_path)
    review.add_argument("--session", type=_path)
    review.add_argument("--output", type=_path)
    review.add_argument("--port", type=int, default=8765)
    review.add_argument(
        "--open-browser",
        action="store_true",
        help="abre o navegador padrao depois que o servidor local iniciar",
    )

    approve = subparsers.add_parser("approve", help="registra revisao humana do lote")
    approve.add_argument("batch", type=_path)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--notes")
    approve.add_argument("--output", type=_path)

    stage = subparsers.add_parser("stage", help="envia lote aprovado para staging")
    stage.add_argument("batch", type=_path)
    stage.add_argument(
        "--execute",
        action="store_true",
        help="executa a escrita; sem esta opcao apenas mostra a previa",
    )

    package = subparsers.add_parser(
        "package", help="gera um pacote local com lotes aprovados para o KAD"
    )
    package.add_argument("batch", type=_path, nargs="+")
    package.add_argument("--output", type=_path)

    promote = subparsers.add_parser(
        "promote", help="valida e simula a promocao de um pacote, sem acessar o KAD"
    )
    promote.add_argument("package", type=_path)
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command in {"run", "semi-auto"}:
        urls = read_requested_urls(args.url, args.urls_file)
        semiautomatic_report, path = run_semiautomatic(
            config_path=args.config,
            urls=urls,
            filters=_filters_from_args(args),
            output_path=args.output,
            model=args.model,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
        )
        print(
            f"Resultado: {path} ({semiautomatic_report.metrics.ready_questions} prontas, "
            f"{semiautomatic_report.metrics.exception_questions} excecoes, "
            f"{semiautomatic_report.metrics.duplicate_questions} duplicatas removidas)"
        )
        for warning in semiautomatic_report.warnings:
            print(f"AVISO: {warning}", file=sys.stderr)
        return 0
    if args.command == "sync":
        automatic_report, path = run_automatic(
            config_path=args.config,
            filters=_filters_from_args(args),
            state_path=args.state,
            output_path=args.output,
            model=args.model,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            max_attempts=args.max_attempts,
            base_delay_seconds=args.retry_delay_seconds,
        )
        metrics = automatic_report.automatic_metrics
        print(
            f"Resultado automatico: {path} ({metrics.new_documents} novos documentos, "
            f"{metrics.known_documents} ja conhecidos, "
            f"{automatic_report.result.metrics.exception_questions} excecoes)"
        )
        for warning in automatic_report.result.warnings:
            print(f"AVISO: {warning}", file=sys.stderr)
        return 0
    if args.command in {"test", "testar"}:
        run_guided_test(
            config_path=args.config,
            state_path=args.state,
            output_path=args.output,
            model=args.model,
            preferred_port=args.port,
        )
        return 0
    if args.command == "collect":
        download_manifest, path = collect_documents(
            load_config(args.config), _filters_from_args(args)
        )
        print(
            f"Manifesto: {path} ({len(download_manifest.documents)} documentos, "
            f"{len(download_manifest.references)} referencias, "
            f"{download_manifest.filtered_out_documents} descartados por filtro)"
        )
        for warning in download_manifest.warnings:
            print(f"AVISO: {warning}", file=sys.stderr)
        return 0
    if args.command == "extract":
        extraction_manifest, path = extract_manifest(args.manifest, args.output)
        print(f"Extracao: {path} ({len(extraction_manifest.documents)} documentos)")
        return 0
    if args.command == "process":
        paths = process_extraction_manifest(
            args.extraction,
            args.output_dir,
            model=args.model,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            filters=_filters_from_args(args),
        )
        if not paths:
            print("Nenhuma prova textual elegivel; verifique tipos e avisos de OCR.")
        for path in paths:
            batch = QuestionBatch.model_validate(read_json(path))
            print(
                f"Lote pendente: {path} ({len(batch.questions)} questoes, "
                f"{batch.filtered_out_questions} descartadas por filtro)"
            )
        return 0
    if args.command == "match-answers":
        batch, path = match_answer_key(args.batch, args.answer_key, args.output)
        matched = sum(item.answer_status != "missing" for item in batch.questions)
        print(f"Lote para revisao: {path} ({matched}/{len(batch.questions)} respostas)")
        return 0
    if args.command == "validate":
        batch = QuestionBatch.model_validate(read_json(args.batch))
        validation = validate_questions(batch.questions, require_answers=args.require_answers)
        for error in validation.errors:
            print(f"ERRO: {error}")
        for warning in validation.warnings:
            print(f"AVISO: {warning}")
        print("Valido" if validation.valid else "Invalido")
        return 0 if validation.valid else 2
    if args.command == "review":
        serve_review_application(
            args.batch,
            session_path=args.session,
            output_path=args.output,
            port=args.port,
            open_browser=args.open_browser,
        )
        return 0
    if args.command == "package":
        package, path = build_promotion_package(args.batch, args.output)
        print(
            f"Pacote: {path} ({len(package.batches)} lotes, "
            f"{sum(len(batch.questions) for batch in package.batches)} questoes, "
            f"SHA-256 {package.content_sha256})"
        )
        return 0
    if args.command == "promote":
        result = dry_run_promotion(args.package)
        print(
            f"Simulacao valida: {result.package_id} ({result.batch_count} lotes, "
            f"{result.question_count} questoes, nenhuma escrita no KAD)"
        )
        return 0
    if args.command == "approve":
        batch, path = approve_batch(
            args.batch,
            args.reviewer,
            notes=args.notes,
            output_path=args.output,
        )
        package, package_path = build_promotion_package([path])
        print(
            f"Lote aprovado: {path} ({len(batch.questions)} questoes). "
            f"Pacote local: {package_path} (SHA-256 {package.content_sha256})"
        )
        return 0
    if args.command == "stage":
        stage_result = stage_batch(args.batch, execute=args.execute)
        if stage_result.executed:
            print(
                "Staging concluido: "
                f"{stage_result.inserted_count}/{stage_result.question_count} insercoes novas"
            )
        else:
            print(
                f"Previa valida: lote {stage_result.batch_id}, "
                f"{stage_result.question_count} questoes. "
                "Use --execute para gravar em staging."
            )
        return 0
    raise AssertionError(f"comando desconhecido: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except KeyboardInterrupt:
        print("\nTeste encerrado pelo usuario.")
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
