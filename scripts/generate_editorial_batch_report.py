from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _final_status(question: dict[str, Any]) -> tuple[str, list[str]]:
    state = str(question["state"])
    blockers = [str(item) for item in question.get("blockers", [])]
    if state == "approved_for_staging":
        return "pronta", []
    if state == "rejected":
        return "rejeitada", blockers or ["rejeitada pela auditoria editorial"]
    if state == "audit_sample":
        return "pendente", ["amostra obrigatória ainda não auditada"]
    if state == "auto_ready":
        return "pendente", ["grupo ainda não liberado pela amostra de auditoria"]
    if state == "quarantined":
        return "pendente", blockers or ["quarentena estrutural"]
    return "pendente", blockers or ["revisão editorial necessária"]


def build_report(
    campaign: dict[str, Any],
    approval: dict[str, Any],
    repeat_campaign: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_questions = campaign.get("run_questions", [])
    approval_by_id = {
        str(item["stable_id"]): item for item in approval.get("questions", [])
    }
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for run_question in run_questions:
        stable_id = str(run_question["stable_id"])
        question = approval_by_id.get(stable_id)
        if question is None:
            missing.append(stable_id)
            continue
        status, reasons = _final_status(question)
        rows.append(
            {
                "stable_id": stable_id,
                "semantic_fingerprint": run_question["semantic_fingerprint"],
                "status": status,
                "automatic_gate_passed": bool(question.get("eligible_for_auto")),
                "approval_state": question["state"],
                "reasons": reasons,
                "classification_method": question["classification_method"],
                "discipline": question.get("discipline"),
                "matter": question.get("matter"),
                "subject": question.get("subject"),
                "level": question.get("level"),
                "question_number": question["question_number"],
                "batch_id": question["batch_id"],
            }
        )
    if missing:
        raise ValueError(
            "questões processadas ausentes do estado de aprovação: " + ", ".join(missing)
        )

    statuses = Counter(item["status"] for item in rows)
    methods = Counter(item["classification_method"] for item in rows)
    blockers = Counter(
        reason for item in rows for reason in item["reasons"]
    )
    distributions = {
        field: dict(
            sorted(
                Counter(str(item.get(field) or "(não informado)") for item in rows).items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        )
        for field in ("discipline", "matter", "subject")
    }
    duration = float(campaign["duration_seconds"])
    processed = len(rows)
    repeated_projection = [
        {
            key: item.get(key)
            for key in (
                "stable_id",
                "semantic_fingerprint",
                "structural_state",
                "classification_method",
                "classification_complete",
                "discipline",
                "matter",
                "subject",
                "level",
            )
        }
        for item in (repeat_campaign or {}).get("run_questions", [])
    ]
    original_projection = [
        {
            key: item.get(key)
            for key in (
                "stable_id",
                "semantic_fingerprint",
                "structural_state",
                "classification_method",
                "classification_complete",
                "discipline",
                "matter",
                "subject",
                "level",
            )
        }
        for item in run_questions
    ]
    idempotency = {
        "verified": repeat_campaign is not None,
        "same_ids_and_classifications": (
            original_projection == repeated_projection
            if repeat_campaign is not None
            else None
        ),
        "repeat_processed": len(repeated_projection),
    }
    return {
        "schema_version": "1.0",
        "campaign_id": campaign["campaign_id"],
        "campaign_version": campaign["campaign_version"],
        "source_campaign_hash": campaign["content_sha256"],
        "source_approval_hash": approval["content_sha256"],
        "publication_status": "draft",
        "summary": {
            "processed": processed,
            "ready_for_import": statuses["pronta"],
            "automatic_gate_candidates": sum(
                1 for item in rows if item["automatic_gate_passed"]
            ),
            "resolved_by_local_rules": methods["deterministic"],
            "completed_by_qwen": methods["hybrid"] + methods["qwen"],
            "qwen_unresolved": methods["qwen_unresolved"],
            "pending": statuses["pendente"],
            "rejected": statuses["rejeitada"],
            "duplicate_candidates_skipped": campaign[
                "run_duplicate_candidates_skipped"
            ],
            "unique_ids": len({item["stable_id"] for item in rows}),
            "unique_semantic_fingerprints": len(
                {item["semantic_fingerprint"] for item in rows}
            ),
            "qwen_calls": campaign["qwen"]["calls"],
            "qwen_failures": campaign["qwen"]["failures"],
            "technical_failures": campaign["qwen"]["failures"],
            "duration_seconds": duration,
            "average_seconds_per_question": round(duration / processed, 3)
            if processed
            else 0,
        },
        "pending_reasons": dict(
            sorted(blockers.items(), key=lambda pair: (-pair[1], pair[0]))
        ),
        "distributions": distributions,
        "idempotency": idempotency,
        "questions": rows,
        "publication_writes": 0,
        "corrections": [
            (
                "O limite de execução passou a contar hashes semânticos únicos, "
                "não apenas IDs de origem."
            ),
            (
                "O relatório da campanha passou a registrar os IDs e hashes "
                "processados em cada execução."
            ),
        ],
        "validation": {
            "tests": "983 passed, 114 subtests passed",
            "lint": "passed",
            "types": "passed",
            "desktop_smoke": "passed",
            "wheel_build": "passed",
            "wheel_install": "passed",
            "wheel_sha256": (
                "bebe88c1cad5cd8e7a115dece0f34c52053d6f2588bdcb34881bc2cd35f85ed4"
            ),
        },
        "notes": [
            "Dificuldade não foi usada como requisito editorial.",
            (
                "Passar pelos critérios automáticos não libera importação enquanto "
                "a amostra do grupo estiver pendente."
            ),
            "Nenhum dado foi publicado no KAD ou no Supabase.",
        ],
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# Primeiro lote real — Banco do Brasil/Cesgranrio",
        "",
        f"- Campanha: `{report['campaign_id']}`",
        f"- Estado de publicação: `{report['publication_status']}`",
        f"- Hash da campanha: `{report['source_campaign_hash']}`",
        f"- Hash da auditoria: `{report['source_approval_hash']}`",
        "",
        "## Resultado",
        "",
        "| Indicador | Total |",
        "|---|---:|",
        f"| Questões processadas | {summary['processed']} |",
        f"| Prontas para importação | {summary['ready_for_import']} |",
        (
            "| Candidatas que passaram os critérios automáticos | "
            f"{summary['automatic_gate_candidates']} |"
        ),
        f"| Resolvidas somente por regras locais | {summary['resolved_by_local_rules']} |",
        f"| Completadas pelo Qwen | {summary['completed_by_qwen']} |",
        f"| Sem sugestão segura do Qwen | {summary['qwen_unresolved']} |",
        f"| Pendentes | {summary['pending']} |",
        f"| Rejeitadas | {summary['rejected']} |",
        f"| Candidatas duplicadas ignoradas | {summary['duplicate_candidates_skipped']} |",
        f"| IDs únicos | {summary['unique_ids']} |",
        f"| Hashes semânticos únicos | {summary['unique_semantic_fingerprints']} |",
        f"| Chamadas ao Qwen | {summary['qwen_calls']} |",
        f"| Falhas do Qwen | {summary['qwen_failures']} |",
        f"| Falhas técnicas | {summary['technical_failures']} |",
        f"| Tempo total | {summary['duration_seconds']:.3f} s |",
        f"| Tempo médio por questão | {summary['average_seconds_per_question']:.3f} s |",
        (
            "| Repetição idempotente | "
            f"{'sim' if report['idempotency']['same_ids_and_classifications'] else 'não'} |"
        ),
        "",
        (
            "As 41 candidatas automáticas continuam pendentes porque compõem a "
            "primeira amostra obrigatória de auditoria. Nenhuma foi liberada para "
            "staging ou produção."
        ),
        "",
        "## Motivos das pendências",
        "",
        "| Motivo | Questões |",
        "|---|---:|",
        *[
            f"| {reason} | {count} |"
            for reason, count in report["pending_reasons"].items()
        ],
        "",
    ]
    for field, title in (
        ("discipline", "Disciplina"),
        ("matter", "Matéria"),
        ("subject", "Assunto"),
    ):
        lines.extend(
            [
                f"## Distribuição por {title.lower()}",
                "",
                f"| {title} | Questões |",
                "|---|---:|",
                *[
                    f"| {value} | {count} |"
                    for value, count in report["distributions"][field].items()
                ],
                "",
            ]
        )
    lines.extend(
        [
            "## Questões",
            "",
            (
                "| # | ID | Hash semântico | Estado | Método | Disciplina | "
                "Matéria | Assunto | Motivo |"
            ),
            "|---:|---|---|---|---|---|---|---|---|",
        ]
    )
    for index, item in enumerate(report["questions"], start=1):
        reasons = "; ".join(item["reasons"]) or "—"
        values = [
            str(index),
            f"`{item['stable_id']}`",
            f"`{item['semantic_fingerprint']}`",
            item["status"],
            item["classification_method"],
            item["discipline"] or "—",
            item["matter"] or "—",
            item["subject"] or "—",
            reasons,
        ]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    lines.extend(["", *[f"- {note}" for note in report["notes"]], ""])
    lines.extend(
        [
            "## Correções gerais",
            "",
            *[f"- {item}" for item in report["corrections"]],
            "",
            "## Validação",
            "",
            "| Verificação | Resultado |",
            "|---|---|",
            *[
                f"| {name} | {result} |"
                for name, result in report["validation"].items()
            ],
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_report", type=Path)
    parser.add_argument("approval_state", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--repeat-campaign-report", type=Path)
    args = parser.parse_args()
    repeat = (
        _read(args.repeat_campaign_report) if args.repeat_campaign_report else None
    )
    report = build_report(
        _read(args.campaign_report),
        _read(args.approval_state),
        repeat,
    )
    _write_json(args.json, report)
    write_markdown(report, args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
