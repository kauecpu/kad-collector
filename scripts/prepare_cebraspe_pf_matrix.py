from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

CATALOG_URL = "https://apis.cebraspe.org.br/cebraspe/eventos/tipo/concursos/"
DETAIL_URL = "https://apis.cebraspe.org.br/cebraspe/eventos/{slug}"
PUBLIC_PAGE_URL = "https://www.cebraspe.org.br/concursos/{slug}"
CDN_URL = "https://cdn.cebraspe.org.br/concursos/{slug}/arquivos/{filename}"
SAMPLES = ("pf_25", "pf_25_adm", "pf_21", "pf_18")
USER_AGENT = "KADCollector/0.3 (+https://github.com/kauecpu/kad-collector)"


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents.casefold()).split())


def _read_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        if response.getcode() != 200:
            raise RuntimeError(f"HTTP {response.getcode()} em {url}")
        return json.load(response)


def _kind(title: str) -> str | None:
    normalized = _normalize(title)
    forbidden = (
        "resultado",
        "comunicado",
        "convocacao",
        "justificativa",
        "prova oral",
        "prova discursiva",
        "padrao de resposta",
        "prova de digitacao",
    )
    if any(value in normalized for value in forbidden):
        return None
    if re.search(r"\bgabarito (?:oficial )?(?:preliminar|definitivo)\b", normalized):
        return "answer_key"
    if "prova objetiva" in normalized or "caderno de prova" in normalized:
        return "exam"
    return None


def _pair_key(title: str) -> str:
    normalized = _normalize(title)
    if "conhecimentos basicos" in normalized:
        block = re.search(r"\bbloco (i{1,3}|iv|v)\b", normalized)
        if block is not None:
            return f"conhecimentos_basicos_bloco_{block.group(1)}"
        if "todos os cargos de perito criminal federal" in normalized:
            return "conhecimentos_basicos_peritos"
        cargo_range = re.search(r"\bcargos? de (\d{1,2}) a (\d{1,2})\b", normalized)
        if cargo_range is not None and cargo_range.groups() != ("2", "11"):
            return f"conhecimentos_basicos_cargos_{cargo_range.group(1)}_{cargo_range.group(2)}"
        cargo = re.search(r"\bcargo (\d{1,2})\b", normalized)
        if cargo is not None and "todas as areas" not in normalized:
            return f"conhecimentos_basicos_cargo_{cargo.group(1)}"
        return "conhecimentos_basicos"
    cargo = re.search(r"\bcargo (\d{1,3})\b", normalized)
    if cargo is not None:
        return f"cargo_{int(cargo.group(1))}"
    normalized = re.sub(
        r"\bgabarito (?:oficial )?(?:preliminar|definitivo)\b", " ", normalized
    )
    normalized = re.sub(r"\bprova objetiva\b", " ", normalized)
    normalized = re.sub(r"\batualizado em \d{1,2} \d{1,2} \d{4}\b", " ", normalized)
    return " ".join(normalized.split())


def _rejection_reason(title: str) -> str:
    normalized = _normalize(title)
    for token in (
        "edital",
        "resultado",
        "convocacao",
        "comunicado",
        "recurso",
        "justificativa",
        "prova oral",
        "prova discursiva",
        "padrao de resposta",
        "prova de digitacao",
    ):
        if token in normalized:
            return token.replace(" ", "_")
    return "fora_do_escopo_objetivo"


def _document_url(slug: str, filename: str) -> str:
    return CDN_URL.format(slug=quote(slug, safe="_-"), filename=quote(filename, safe="._-"))


def build_matrix() -> dict[str, Any]:
    catalog = _read_json(CATALOG_URL)
    catalog_slugs = {
        str(event.get("eventoURL", "")).casefold()
        for group in catalog
        for event in group.get("eventos", [])
        if isinstance(event, dict)
    }
    samples: list[dict[str, Any]] = []
    for slug in SAMPLES:
        if slug.casefold() not in catalog_slugs:
            raise RuntimeError(f"{slug} nao foi encontrado no catalogo oficial")
        detail_url = DETAIL_URL.format(slug=slug)
        payload = _read_json(detail_url)
        expected: list[dict[str, str]] = []
        rejected: list[dict[str, str]] = []
        for item in payload.get("arquivosGabarito") or []:
            title = str(item.get("descricaoArquivo") or "").strip()
            filename = str(item.get("nomeArquivo") or "").strip()
            if not title or not filename:
                continue
            url = _document_url(slug, filename)
            kind = _kind(title)
            if kind is None:
                if len(rejected) < 12:
                    rejected.append(
                        {
                            "title": title,
                            "url": url,
                            "reason": _rejection_reason(title),
                        }
                    )
                continue
            expected.append(
                {
                    "kind": kind,
                    "title": title,
                    "filename": filename,
                    "url": url,
                    "pair_key": _pair_key(title),
                }
            )
        for item in payload.get("arquivosEdital") or []:
            if len(rejected) >= 20:
                break
            filename = str(item.get("nomeArquivo") or "").strip()
            title = str(item.get("descricaoArquivo") or "").strip()
            if filename.casefold().endswith(".pdf") and title:
                rejected.append(
                    {
                        "title": title,
                        "url": _document_url(slug, filename),
                        "reason": _rejection_reason(title),
                    }
                )
        kinds = {item["kind"] for item in expected}
        if kinds != {"exam", "answer_key"}:
            raise RuntimeError(f"{slug} nao possui prova e gabarito na matriz oficial")
        samples.append(
            {
                "id": slug,
                "contest": str(
                    payload.get("eventoNomeCompleto") or payload.get("eventoNomeAbreviado")
                ),
                "year": int(payload["eventoAno"]),
                "public_page_url": PUBLIC_PAGE_URL.format(slug=slug),
                "detail_api_url": detail_url,
                "catalog_membership": True,
                "roles": [
                    str(item.get("area") or "")
                    for item in payload.get("eventoCargos") or []
                ],
                "expected": expected,
                "rejected_examples": rejected,
            }
        )
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_id": "cebraspe_policia_federal",
        "catalog_url": CATALOG_URL,
        "selection_rule": (
            "Provas objetivas e gabaritos oficiais preliminares ou definitivos listados "
            "em arquivosGabarito; documentos administrativos e outras etapas ficam fora."
        ),
        "samples": samples,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixa a matriz oficial Cebraspe/PF.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    matrix = build_matrix()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                item["id"]: {
                    "expected": len(item["expected"]),
                    "exams": sum(value["kind"] == "exam" for value in item["expected"]),
                    "answer_keys": sum(
                        value["kind"] == "answer_key" for value in item["expected"]
                    ),
                }
                for item in matrix["samples"]
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
