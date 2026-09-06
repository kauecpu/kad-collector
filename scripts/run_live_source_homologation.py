from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import time
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kad_collector.collector import classify_document, collect_documents, extract_links
from kad_collector.config import load_config
from kad_collector.models import AppConfig, DownloadManifest, SourceDefinition
from kad_collector.pdf_extractor import extract_manifest

FORBIDDEN = re.compile(r"(?i)\b(edital|resultado|comunicado|convoca[cç][aã]o)\b")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Homologa fontes oficiais reais do coletor.")
    parser.add_argument(
        "--matrix", type=Path, default=Path("tests/homologation/live-sources.v1.toml")
    )
    parser.add_argument("--config", type=Path, default=Path("config/sources.official.toml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--ollama-offline", action="store_true")
    parser.add_argument("--repeat-source")
    return parser


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--always", "--dirty"], text=True, encoding="utf-8"
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load_matrix(path: Path) -> list[dict[str, Any]]:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("sources"), list):
        raise ValueError("matriz de homologação inválida")
    return list(payload["sources"])


def _actual_items(manifest: DownloadManifest, source: SourceDefinition) -> list[dict[str, str]]:
    items = [
        {
            "kind": item.document_type,
            "title": item.title,
            "url": item.resolved_url,
            "sha256": item.sha256,
            "mode": item.metadata.get("discovery", "deterministic"),
        }
        for item in manifest.documents
    ]
    items.extend(
        {
            "kind": classify_document(item.url, item.title, source),
            "title": item.title,
            "url": item.url,
            "sha256": "reference-only",
            "mode": "deterministic",
        }
        for item in manifest.references
    )
    return items


def _matches(expected: dict[str, Any], actual: dict[str, str]) -> bool:
    return expected["kind"] == actual["kind"] and bool(
        re.search(str(expected["pattern"]), f"{actual['title']}\n{actual['url']}")
    )


def _rejected_candidates(source: SourceDefinition, data_dir: Path) -> list[dict[str, str]]:
    database = data_dir / source.id / "collection-engine.sqlite3"
    if not database.is_file():
        return []
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT final_url, local_path FROM http_cache WHERE strategy = 'html'"
        ).fetchall()
    rejected: dict[str, dict[str, str]] = {}
    for page_url, local_path in rows:
        try:
            html = Path(local_path).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for url, title in extract_links(html, str(page_url), allow_data_url=True):
            candidate = f"{title}\n{url}"
            included = any(
                re.search(pattern, value)
                for pattern in source.include_patterns
                for value in (url, candidate)
            )
            exclusion = next(
                (pattern for pattern in source.exclude_patterns if re.search(pattern, candidate)),
                None,
            )
            if included and exclusion:
                rejected.setdefault(
                    url,
                    {
                        "title": title,
                        "url": url,
                        "reason": f"exclude_pattern: {exclusion}",
                    },
                )
    return list(rejected.values())


def _source_result(
    entry: dict[str, Any],
    source: SourceDefinition,
    manifest: DownloadManifest,
    manifest_path: Path,
    data_dir: Path,
    duration: float,
) -> dict[str, Any]:
    actual = _actual_items(manifest, source)
    expected = list(entry["expected"])
    matched_expected = [item for item in expected if any(_matches(item, value) for value in actual)]
    matched_actual = [item for item in actual if any(_matches(value, item) for value in expected)]
    false_positives = [item for item in actual if item not in matched_actual]
    prohibited = [item for item in actual if FORBIDDEN.search(f"{item['title']}\n{item['url']}")]
    rejected = _rejected_candidates(source, data_dir)
    extraction = None
    extraction_path = manifest_path.with_name(f"{manifest_path.stem}-extracted.json")
    if manifest.documents:
        extraction, _ = extract_manifest(manifest_path, extraction_path)
    ocr_attempted = 0
    ocr_succeeded = 0
    needs_ocr = 0
    if extraction is not None:
        for document in extraction.documents:
            recovered = any("recuperado por OCR" in warning for warning in document.warnings)
            attempted = recovered or any("OCR" in warning for warning in document.warnings)
            ocr_attempted += int(attempted)
            ocr_succeeded += int(attempted and not document.needs_ocr)
            needs_ocr += int(document.needs_ocr)
    page_events = [event for event in manifest.telemetry if event.strategy == "html"]
    ai_events = [event for event in manifest.telemetry if event.strategy == "ai_fallback"]
    paths = {item["mode"] for item in actual}
    if ai_events and any(event.outcome == "selected" for event in ai_events):
        paths.add("qwen_fallback")
    expected_kinds = {item["kind"] for item in expected}
    actual_kinds = {item["kind"] for item in actual}
    pairing_required = {"exam", "answer_key"}.issubset(expected_kinds)
    return {
        "id": source.id,
        "scenario": entry["scenario"],
        "start_url": entry["start_url"],
        "expected": expected,
        "actual": actual,
        "accepted": matched_actual,
        "rejected": rejected,
        "prohibited_accepted": prohibited,
        "false_positives": false_positives,
        "false_negatives": [item for item in expected if item not in matched_expected],
        "precision": len(matched_actual) / len(actual)
        if actual
        else (1.0 if not expected else 0.0),
        "coverage": len(matched_expected) / len(expected),
        "pairing_required": pairing_required,
        "pairing_success": not pairing_required or {"exam", "answer_key"}.issubset(actual_kinds),
        "paths": sorted(paths or {"deterministic"}),
        "pages_visited": list(dict.fromkeys(event.url for event in page_events)),
        "qwen_calls": [event.model_dump(mode="json") for event in ai_events],
        "duration_seconds": round(duration, 3),
        "ocr_attempted": ocr_attempted,
        "ocr_succeeded": ocr_succeeded,
        "documents_still_needing_ocr": needs_ocr,
        "failures": [item.model_dump(mode="json") for item in manifest.failures],
        "warnings": manifest.warnings,
        "manifest": str(manifest_path),
        "extraction_manifest": str(extraction_path) if extraction is not None else None,
    }


def _run_one(
    base: AppConfig,
    entry: dict[str, Any],
    data_dir: Path,
    *,
    suffix: str = "",
) -> dict[str, Any]:
    source = next(item for item in base.sources if item.id == entry["id"])
    source = source.model_copy(update={"start_urls": [entry["start_url"]], "max_pages_per_run": 3})
    settings = base.collector.model_copy(
        update={
            "data_dir": str(data_dir / f"{source.id}{suffix}"),
            "request_interval_seconds": 0.25,
            "timeout_seconds": 30.0,
            "connect_timeout_seconds": 15.0,
            "max_files_per_source": 2,
            "max_retries": 1,
            "retry_max_delay_seconds": 1.0,
            "ai_discovery_enabled": True,
            "ai_discovery_model": "qwen3:8b",
        }
    )
    started = time.perf_counter()
    manifest, manifest_path = collect_documents(AppConfig(collector=settings, sources=[source]))
    return _source_result(
        entry,
        source,
        manifest,
        manifest_path,
        data_dir,
        time.perf_counter() - started,
    )


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = sum(len(item["accepted"]) for item in results)
    actual = sum(len(item["actual"]) for item in results)
    expected = sum(len(item["expected"]) for item in results)
    covered = expected - sum(len(item["false_negatives"]) for item in results)
    pairs = [item for item in results if item["pairing_required"]]
    ocr_attempted = sum(item["ocr_attempted"] for item in results)
    return {
        "precision": accepted / actual if actual else 0.0,
        "coverage": covered / expected if expected else 0.0,
        "pairing_rate": sum(item["pairing_success"] for item in pairs) / len(pairs)
        if pairs
        else 1.0,
        "ocr_success_rate": sum(item["ocr_succeeded"] for item in results) / ocr_attempted
        if ocr_attempted
        else None,
        "false_positives": sum(len(item["false_positives"]) for item in results),
        "false_negatives": sum(len(item["false_negatives"]) for item in results),
        "average_seconds_per_source": sum(item["duration_seconds"] for item in results)
        / len(results),
        "sources_without_intervention_rate": sum(not item["failures"] for item in results)
        / len(results),
        "sources": len(results),
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    ollama_status = (
        "indisponível de propósito" if report["ollama_offline"] else "qwen3:8b disponível"
    )
    lines = [
        "# Homologação real das fontes do KAD Collector",
        "",
        f"- Data UTC: {report['created_at']}",
        f"- Commit: `{report['commit']}`",
        f"- Ollama: {ollama_status}",
        f"- Precisão: {summary['precision']:.1%}",
        f"- Cobertura: {summary['coverage']:.1%}",
        f"- Associação prova/gabarito: {summary['pairing_rate']:.1%}",
        f"- Falsos positivos: {summary['false_positives']}",
        f"- Falsos negativos: {summary['false_negatives']}",
        f"- Fontes sem intervenção: {summary['sources_without_intervention_rate']:.1%}",
        f"- Tempo médio por fonte: {summary['average_seconds_per_source']:.2f}s",
        "",
    ]
    for item in report["sources"]:
        document_counts = f"{len(item['expected'])}/{len(item['actual'])}/{len(item['accepted'])}"
        lines.extend(
            [
                f"## {item['id']}",
                "",
                f"- Cenário: {item['scenario']}",
                f"- URL inicial: {item['start_url']}",
                f"- Esperados/encontrados/aceitos: {document_counts}",
                f"- Caminho: {', '.join(item['paths'])}",
                f"- Páginas visitadas: {len(item['pages_visited'])}",
                f"- Chamadas ao Qwen: {len(item['qwen_calls'])}",
                f"- Tempo: {item['duration_seconds']:.2f}s",
                f"- OCR tentado/suficiente: {item['ocr_attempted']}/{item['ocr_succeeded']}",
                f"- Falhas isoladas: {len(item['failures'])}",
                f"- Arquivos rejeitados por regra: {len(item['rejected'])}",
                "",
            ]
        )
        for actual in item["actual"]:
            lines.append(
                f"- `{actual['kind']}` {actual['title']} — `{actual['sha256']}` — {actual['url']}"
            )
        for failure in item["failures"]:
            lines.append(f"- Falha tratada ({failure['stage']}): {failure['message']}")
        for rejected in item["rejected"]:
            lines.append(
                f"- Rejeitado: {rejected['title']} — {rejected['reason']} — {rejected['url']}"
            )
        if not item["actual"] and not item["failures"]:
            lines.append(
                "- Nenhum documento localizado; registrado como ausência, sem falso sucesso."
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    entries = _load_matrix(args.matrix)
    if args.source:
        selected = set(args.source)
        entries = [item for item in entries if item["id"] in selected]
    if not entries:
        raise SystemExit("nenhuma fonte selecionada")
    base = load_config(args.config)
    previous_ollama = os.environ.get("OLLAMA_BASE_URL")
    if args.ollama_offline:
        os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"
    try:
        results = []
        for entry in entries:
            print(f"homologando {entry['id']}...", flush=True)
            try:
                results.append(_run_one(base, entry, args.data_dir))
            except Exception as exc:  # noqa: BLE001 - uma fonte não interrompe as demais
                results.append(
                    {
                        "id": entry["id"],
                        "scenario": entry["scenario"],
                        "start_url": entry["start_url"],
                        "expected": entry["expected"],
                        "actual": [],
                        "accepted": [],
                        "rejected": [],
                        "prohibited_accepted": [],
                        "false_positives": [],
                        "false_negatives": entry["expected"],
                        "precision": 0.0,
                        "coverage": 0.0,
                        "pairing_required": False,
                        "pairing_success": False,
                        "paths": ["deterministic"],
                        "pages_visited": [],
                        "qwen_calls": [],
                        "duration_seconds": 0.0,
                        "ocr_attempted": 0,
                        "ocr_succeeded": 0,
                        "documents_still_needing_ocr": 0,
                        "failures": [{"stage": "discovery", "message": f"falha isolada: {exc}"}],
                        "warnings": [],
                        "manifest": None,
                        "extraction_manifest": None,
                    }
                )
        repeat = None
        if args.repeat_source:
            entry = next(item for item in entries if item["id"] == args.repeat_source)
            repeated = _run_one(base, entry, args.data_dir, suffix="-repeat")
            original = next(item for item in results if item["id"] == args.repeat_source)
            repeat = {
                "source": args.repeat_source,
                "stable_hashes": sorted(item["sha256"] for item in original["actual"])
                == sorted(item["sha256"] for item in repeated["actual"]),
                "original_count": len(original["actual"]),
                "repeated_count": len(repeated["actual"]),
            }
        report = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "commit": _commit(),
            "matrix": str(args.matrix),
            "ollama_offline": args.ollama_offline,
            "sources": results,
            "summary": _summary(results),
            "idempotency": repeat,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
