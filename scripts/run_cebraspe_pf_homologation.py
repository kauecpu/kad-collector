from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from kad_collector.collector import collect_documents
from kad_collector.config import load_config
from kad_collector.models import AppConfig, DownloadManifest, SourceDefinition
from kad_collector.pdf_extractor import extract_manifest

SOURCE_ID = "cebraspe_policia_federal"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
FORBIDDEN = re.compile(
    r"(?i)\b(edital|resultado|comunicado|convoca[cç][aã]o|prova discursiva|prova oral)\b"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Homologa Cebraspe/PF com fontes reais.")
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("tests/homologation/cebraspe-pf.v1.json"),
    )
    parser.add_argument("--config", type=Path, default=Path("config/sources.official.toml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--sample", action="append", default=[])
    parser.add_argument("--ollama-offline", action="store_true")
    parser.add_argument("--repeat-sample")
    parser.add_argument("--skip-extraction", action="store_true")
    return parser


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--always", "--dirty"],
            text=True,
            encoding="utf-8",
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load_matrix(path: Path) -> dict[str, Any]:
    decoded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("matriz Cebraspe/PF inválida")
    payload = {str(key): value for key, value in decoded.items()}
    if (
        payload.get("schema_version") != 1
        or payload.get("source_id") != SOURCE_ID
        or not isinstance(payload.get("samples"), list)
    ):
        raise ValueError("matriz Cebraspe/PF inválida")
    return payload


def _filename(url: str) -> str:
    return unquote(Path(urlsplit(url).path).name).casefold()


def _ollama_status(*, offline: bool) -> dict[str, Any]:
    if offline:
        return {"available": False, "qwen3_8b": False, "mode": "offline_test"}
    try:
        request = urllib.request.Request(  # noqa: S310
            OLLAMA_TAGS_URL,
            headers={"User-Agent": "KADCollectorHomologation/1.0"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            payload = json.load(response)
        models = {
            str(item.get("name") or "").casefold()
            for item in payload.get("models", [])
            if isinstance(item, dict)
        }
        return {
            "available": True,
            "qwen3_8b": any(
                value == "qwen3:8b" or value.startswith("qwen3:8b-") for value in models
            ),
            "mode": "online",
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "qwen3_8b": False,
            "mode": "online",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _actual_items(manifest: DownloadManifest) -> list[dict[str, Any]]:
    return [
        {
            "kind": item.document_type,
            "title": item.title,
            "url": item.resolved_url,
            "filename": unquote(Path(urlsplit(item.resolved_url).path).name),
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "discovery": item.metadata.get("discovery", "deterministic_browser"),
        }
        for item in manifest.documents
    ]


def _ocr_result(manifest_path: Path) -> tuple[dict[str, dict[str, Any]], Path]:
    extraction_path = manifest_path.with_name(f"{manifest_path.stem}-extracted.json")
    extraction, _ = extract_manifest(manifest_path, extraction_path)
    documents: dict[str, dict[str, Any]] = {}
    for item in extraction.documents:
        warnings = item.warnings
        attempted = any("OCR" in warning for warning in warnings)
        recovered = any("recuperado por OCR" in warning for warning in warnings)
        documents[_filename(item.document.resolved_url)] = {
            "attempted": attempted,
            "succeeded": attempted and not item.needs_ocr,
            "recovered": recovered,
            "needs_ocr": item.needs_ocr,
            "pages": len(item.pages),
            "characters": len(item.text),
            "warnings": warnings,
        }
    return documents, extraction_path


def _pairing(expected: list[dict[str, str]], matched: set[str]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, str]]]] = {}
    for item in expected:
        grouped.setdefault(item["pair_key"], {"exam": [], "answer_key": []})[
            item["kind"]
        ].append(item)
    pairs: list[dict[str, Any]] = []
    for key, items in sorted(grouped.items()):
        exams = items["exam"]
        keys = items["answer_key"]
        exam_found = all(item["filename"].casefold() in matched for item in exams)
        key_found = all(item["filename"].casefold() in matched for item in keys)
        pairs.append(
            {
                "pair_key": key,
                "exam_files": [item["filename"] for item in exams],
                "answer_key_files": [item["filename"] for item in keys],
                "exam_found": exam_found,
                "answer_key_found": key_found,
                "associated": bool(exams and keys and exam_found and key_found),
            }
        )
    return pairs


def _source_result(
    sample: dict[str, Any],
    manifest: DownloadManifest,
    manifest_path: Path,
    duration: float,
    *,
    skip_extraction: bool,
) -> dict[str, Any]:
    expected = list(sample["expected"])
    actual = _actual_items(manifest)
    expected_by_filename = {item["filename"].casefold(): item for item in expected}
    actual_by_filename = {item["filename"].casefold(): item for item in actual}
    matched_filenames = set(expected_by_filename) & set(actual_by_filename)
    false_positives = [
        item for name, item in actual_by_filename.items() if name not in expected_by_filename
    ]
    false_negatives = [
        item for name, item in expected_by_filename.items() if name not in actual_by_filename
    ]
    prohibited = [
        item for item in actual if FORBIDDEN.search(f"{item['title']}\n{item['url']}")
    ]
    ocr: dict[str, dict[str, Any]] = {}
    extraction_path: Path | None = None
    if manifest.documents and not skip_extraction:
        ocr, extraction_path = _ocr_result(manifest_path)
    for item in actual:
        item["ocr"] = ocr.get(item["filename"].casefold())
    pairings = _pairing(expected, matched_filenames)
    ai_events = [event for event in manifest.telemetry if event.strategy == "ai_fallback"]
    page_events = [event for event in manifest.telemetry if event.strategy == "html"]
    paths = {str(item["discovery"]) for item in actual}
    if ai_events:
        paths.add("qwen_fallback")
    return {
        "id": sample["id"],
        "contest": sample["contest"],
        "year": sample["year"],
        "board_identified": (
            "CESPE/CESPE-UnB (acervo atual no Cebraspe)"
            if sample["id"] == "pf_18"
            else "CEBRASPE"
        ),
        "start_url": sample["public_page_url"],
        "expected": expected,
        "expected_count": len(expected),
        "found_count": len(actual),
        "exam_count": sum(item["kind"] == "exam" for item in actual),
        "answer_key_count": sum(item["kind"] == "answer_key" for item in actual),
        "accepted": actual,
        "rejected_examples": sample["rejected_examples"],
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "prohibited_accepted": prohibited,
        "precision": len(matched_filenames) / len(actual) if actual else 0.0,
        "coverage": len(matched_filenames) / len(expected) if expected else 1.0,
        "pairings": pairings,
        "pairing_rate": (
            sum(item["associated"] for item in pairings) / len(pairings) if pairings else 1.0
        ),
        "paths": sorted(paths or {"deterministic_browser"}),
        "pages_visited": list(dict.fromkeys(event.url for event in page_events)),
        "qwen_calls": [event.model_dump(mode="json") for event in ai_events],
        "duration_seconds": round(duration, 3),
        "ocr_attempted": sum(bool(item.get("attempted")) for item in ocr.values()),
        "ocr_succeeded": sum(bool(item.get("succeeded")) for item in ocr.values()),
        "documents_still_needing_ocr": sum(bool(item.get("needs_ocr")) for item in ocr.values()),
        "failures": [item.model_dump(mode="json") for item in manifest.failures],
        "warnings": manifest.warnings,
        "duplicate_documents": manifest.duplicate_documents,
        "manifest": manifest_path.as_posix(),
        "extraction_manifest": extraction_path.as_posix() if extraction_path else None,
    }


def _run_one(
    base: AppConfig,
    sample: dict[str, Any],
    data_dir: Path,
    *,
    suffix: str = "",
    skip_extraction: bool = False,
) -> dict[str, Any]:
    configured = next(source for source in base.sources if source.id == SOURCE_ID)
    source: SourceDefinition = configured.model_copy(
        update={
            "start_urls": [sample["public_page_url"]],
            "max_pages_per_run": 1,
            "metadata": {
                **configured.metadata,
                "ano": str(sample["year"]),
                "concurso": sample["contest"],
            },
        }
    )
    settings = base.collector.model_copy(
        update={
            "data_dir": str(data_dir / f"{sample['id']}{suffix}"),
            "request_interval_seconds": 0.25,
            "timeout_seconds": 45.0,
            "connect_timeout_seconds": 15.0,
            "max_files_per_source": None,
            "max_concurrency": 4,
            "max_retries": 1,
            "retry_max_delay_seconds": 2.0,
            "ai_discovery_enabled": True,
            "ai_discovery_model": "qwen3:8b",
        }
    )
    started = time.perf_counter()
    manifest, manifest_path = collect_documents(AppConfig(collector=settings, sources=[source]))
    return _source_result(
        sample,
        manifest,
        manifest_path,
        time.perf_counter() - started,
        skip_extraction=skip_extraction,
    )


def _failed_result(sample: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "id": sample["id"],
        "contest": sample["contest"],
        "year": sample["year"],
        "board_identified": "não identificado devido à falha",
        "start_url": sample["public_page_url"],
        "expected": sample["expected"],
        "expected_count": len(sample["expected"]),
        "found_count": 0,
        "exam_count": 0,
        "answer_key_count": 0,
        "accepted": [],
        "rejected_examples": sample["rejected_examples"],
        "false_positives": [],
        "false_negatives": sample["expected"],
        "prohibited_accepted": [],
        "precision": 0.0,
        "coverage": 0.0,
        "pairings": [],
        "pairing_rate": 0.0,
        "paths": ["deterministic_browser"],
        "pages_visited": [],
        "qwen_calls": [],
        "duration_seconds": 0.0,
        "ocr_attempted": 0,
        "ocr_succeeded": 0,
        "documents_still_needing_ocr": 0,
        "failures": [{"stage": "discovery", "message": f"falha isolada: {exc}"}],
        "warnings": [],
        "duplicate_documents": 0,
        "manifest": None,
        "extraction_manifest": None,
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    expected = sum(item["expected_count"] for item in results)
    found = sum(item["found_count"] for item in results)
    false_positives = sum(len(item["false_positives"]) for item in results)
    false_negatives = sum(len(item["false_negatives"]) for item in results)
    pairings = [pair for item in results for pair in item["pairings"]]
    ocr_attempted = sum(item["ocr_attempted"] for item in results)
    qwen_calls = [call for item in results for call in item["qwen_calls"]]
    qwen_accepted = sum(call.get("outcome") == "selected" for call in qwen_calls)
    return {
        "precision": (found - false_positives) / found if found else 0.0,
        "coverage": (expected - false_negatives) / expected if expected else 1.0,
        "pairing_rate": (
            sum(pair["associated"] for pair in pairings) / len(pairings) if pairings else 0.0
        ),
        "ocr_success_rate": (
            sum(item["ocr_succeeded"] for item in results) / ocr_attempted
            if ocr_attempted
            else None
        ),
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "prohibited_accepted": sum(len(item["prohibited_accepted"]) for item in results),
        "expected_documents": expected,
        "found_documents": found,
        "exams_found": sum(item["exam_count"] for item in results),
        "answer_keys_found": sum(item["answer_key_count"] for item in results),
        "deterministic_decisions": sum(
            item["found_count"]
            for item in results
            if "qwen_fallback" not in item["paths"]
        ),
        "qwen_decisions": len(qwen_calls),
        "qwen_proposals_accepted": qwen_accepted,
        "qwen_proposals_refused": len(qwen_calls) - qwen_accepted,
        "average_seconds_per_source": (
            sum(item["duration_seconds"] for item in results) / len(results) if results else 0.0
        ),
        "sources_without_intervention_rate": (
            sum(not item["failures"] for item in results) / len(results) if results else 0.0
        ),
        "sources": len(results),
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    ocr_rate = summary["ocr_success_rate"]
    lines = [
        "# Homologação Cebraspe — Polícia Federal",
        "",
        f"- Data UTC: {report['created_at']}",
        f"- Commit: `{report['commit']}`",
        f"- Ollama: `{json.dumps(report['ollama'], ensure_ascii=False)}`",
        f"- Precisão: {summary['precision']:.1%}",
        f"- Cobertura: {summary['coverage']:.1%}",
        f"- Associação prova/gabarito: {summary['pairing_rate']:.1%}",
        f"- OCR: {ocr_rate:.1%}" if ocr_rate is not None else "- OCR: não acionado",
        f"- Esperados/encontrados: {summary['expected_documents']}/{summary['found_documents']}",
        f"- Provas encontradas: {summary['exams_found']}",
        f"- Gabaritos encontrados: {summary['answer_keys_found']}",
        f"- Falsos positivos: {summary['false_positives']}",
        f"- Falsos negativos: {summary['false_negatives']}",
        f"- Documentos proibidos aceitos: {summary['prohibited_accepted']}",
        (
            "- Fontes concluídas sem intervenção: "
            f"{summary['sources_without_intervention_rate']:.1%}"
        ),
        f"- Tempo médio por fonte: {summary['average_seconds_per_source']:.2f}s",
        f"- Decisões determinísticas: {summary['deterministic_decisions']}",
        f"- Decisões com `qwen_fallback`: {summary['qwen_decisions']}",
        (
            "- Propostas do Qwen aceitas/recusadas: "
            f"{summary['qwen_proposals_accepted']}/{summary['qwen_proposals_refused']}"
        ),
        "",
    ]
    if report["idempotency"] is not None:
        repeat = report["idempotency"]
        lines.extend(
            [
                "## Idempotência",
                "",
                f"- Amostra repetida: `{repeat['sample']}`",
                f"- Mesmo conjunto de URLs e hashes: `{repeat['stable']}`",
                (
                    "- Contagens original/repetição: "
                    f"{repeat['original_count']}/{repeat['repeated_count']}"
                ),
                "",
            ]
        )
    for item in report["samples"]:
        lines.extend(
            [
                f"## {item['id']} — {item['contest']}",
                "",
                f"- URL inicial: {item['start_url']}",
                f"- Banca identificada: {item['board_identified']}",
                f"- Esperados/encontrados: {item['expected_count']}/{item['found_count']}",
                f"- Provas/gabaritos aceitos: {item['exam_count']}/{item['answer_key_count']}",
                f"- Precisão/cobertura: {item['precision']:.1%}/{item['coverage']:.1%}",
                f"- Associação prova/gabarito: {item['pairing_rate']:.1%}",
                f"- Caminho: {', '.join(item['paths'])}",
                f"- Páginas visitadas: {len(item['pages_visited'])}",
                f"- Chamadas ao Qwen: {len(item['qwen_calls'])}",
                f"- Tempo: {item['duration_seconds']:.2f}s",
                f"- OCR tentado/suficiente: {item['ocr_attempted']}/{item['ocr_succeeded']}",
                f"- Falhas tratadas: {len(item['failures'])}",
                f"- Exemplos rejeitados: {len(item['rejected_examples'])}",
                "",
                "### Documentos aceitos",
                "",
            ]
        )
        for actual in item["accepted"]:
            lines.append(
                f"- `{actual['kind']}` {actual['title']} — `{actual['sha256']}` — {actual['url']}"
            )
        lines.extend(["", "### Associações prova/gabarito", ""])
        for pairing in item["pairings"]:
            status = "associado" if pairing["associated"] else "sem associação"
            lines.append(
                f"- `{pairing['pair_key']}` — {status}; "
                f"provas={', '.join(pairing['exam_files'])}; "
                f"gabaritos={', '.join(pairing['answer_key_files'])}"
            )
        lines.extend(["", "### Rejeições e falhas", ""])
        for rejected in item["rejected_examples"]:
            lines.append(
                f"- Rejeitado: {rejected['title']} — `{rejected['reason']}` — {rejected['url']}"
            )
        for failure in item["failures"]:
            lines.append(
                f"- Falha isolada ({failure.get('stage', 'execução')}): {failure['message']}"
            )
        if not item["rejected_examples"] and not item["failures"]:
            lines.append("- Nenhuma.")
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    matrix = _load_matrix(args.matrix)
    samples = list(matrix["samples"])
    if args.sample:
        selected = set(args.sample)
        samples = [sample for sample in samples if sample["id"] in selected]
    if not samples:
        raise SystemExit("nenhuma amostra selecionada")
    base = load_config(args.config)
    if not any(source.id == SOURCE_ID for source in base.sources):
        raise SystemExit(f"fonte {SOURCE_ID} ausente da configuração")
    ollama = _ollama_status(offline=args.ollama_offline)
    if not args.ollama_offline and not ollama["qwen3_8b"]:
        raise SystemExit("qwen3:8b não está disponível no Ollama local")
    previous_ollama = os.environ.get("OLLAMA_BASE_URL")
    if args.ollama_offline:
        os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
    try:
        results: list[dict[str, Any]] = []
        for sample in samples:
            print(f"homologando {sample['id']}...", flush=True)
            try:
                result = _run_one(
                    base,
                    sample,
                    args.data_dir,
                    skip_extraction=args.skip_extraction,
                )
            except Exception as exc:  # noqa: BLE001 - uma fonte não derruba as demais
                result = _failed_result(sample, exc)
            results.append(result)
            print(
                f"{sample['id']}: {result['found_count']}/{result['expected_count']} documentos",
                flush=True,
            )
        repeat = None
        if args.repeat_sample:
            sample = next(item for item in samples if item["id"] == args.repeat_sample)
            repeated = _run_one(
                base,
                sample,
                args.data_dir,
                suffix="-repeat",
                skip_extraction=True,
            )
            original = next(item for item in results if item["id"] == args.repeat_sample)
            original_documents = sorted(
                (item["url"], item["sha256"]) for item in original["accepted"]
            )
            repeated_documents = sorted(
                (item["url"], item["sha256"]) for item in repeated["accepted"]
            )
            repeat = {
                "sample": args.repeat_sample,
                "stable": original_documents == repeated_documents,
                "original_count": len(original_documents),
                "repeated_count": len(repeated_documents),
            }
        report = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "commit": _commit(),
            "matrix": args.matrix.as_posix(),
            "ollama": ollama,
            "samples": results,
            "summary": _summary(results),
            "idempotency": repeat,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        args.markdown.write_text(_markdown(report), encoding="utf-8")
        print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    finally:
        if args.ollama_offline:
            if previous_ollama is None:
                os.environ.pop("OLLAMA_BASE_URL", None)
            else:
                os.environ["OLLAMA_BASE_URL"] = previous_ollama
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
