from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import Field

from .models import StrictModel

TRIAGE_ALGORITHM_VERSION = "desktop-document-triage-v1"


class DocumentTriage(StrictModel):
    decision: Literal["exam", "answer_key", "other", "review"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]
    algorithm_version: str = TRIAGE_ALGORITHM_VERSION
    reason: str
    source: Literal["manual", "local_rules"]


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(character for character in normalized if not unicodedata.combining(character))
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def _plain_multiline(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character for character in normalized if not unicodedata.combining(character)
    ).replace("_", " ")


def classify_document(
    *,
    filename: str,
    title: str | None,
    text: str,
    declared_type: Literal["auto", "exam", "answer_key", "other"],
) -> DocumentTriage:
    """Conservatively classify a local PDF before semantic processing."""

    if declared_type != "auto":
        labels = {
            "exam": "prova",
            "answer_key": "gabarito",
            "other": "outro documento",
        }
        return DocumentTriage(
            decision=declared_type,
            confidence=1,
            evidence=[f"tipo escolhido pelo operador: {labels[declared_type]}"],
            reason=f"O operador definiu este arquivo como {labels[declared_type]}.",
            source="manual",
        )

    name = _plain(" ".join(item for item in (filename, title) if item))
    content = _plain_multiline(text[:120_000])
    combined = f"{name}\n{content}"

    answer_name = any(
        marker in name
        for marker in ("gabarito", "answer key", "padrao de resposta", "respostas oficiais")
    )
    answer_rows = len(
        re.findall(r"(?m)^\s*(?:questao\s*)?\d{1,3}\s*[-.:)]\s*[a-e]\b", content)
    )
    question_headers = len(
        re.findall(r"(?m)^\s*(?:questao|questao n|q)\s*[ºo.]?\s*\d{1,3}\b", content)
    )
    alternative_rows = len(re.findall(r"(?m)^\s*[a-e]\s*[).:-]\s+\S+", content))
    exam_structure = question_headers >= 1 and alternative_rows >= 2

    if answer_name or answer_rows >= 3:
        evidence = []
        if answer_name:
            evidence.append("nome ou título indica gabarito")
        if answer_rows >= 3:
            evidence.append(f"{answer_rows} respostas numeradas reconhecidas")
        return DocumentTriage(
            decision="answer_key",
            confidence=0.98 if answer_name and answer_rows >= 3 else 0.9,
            evidence=evidence,
            reason="Há evidência local suficiente de que o PDF é um gabarito.",
            source="local_rules",
        )

    if exam_structure:
        return DocumentTriage(
            decision="exam",
            confidence=0.96,
            evidence=[
                f"{question_headers} enunciado(s) numerado(s)",
                f"{alternative_rows} alternativa(s) estruturada(s)",
            ],
            reason=(
                "O texto contém estrutura de questão e alternativas; "
                "o documento não foi descartado."
            ),
            source="local_rules",
        )

    irrelevant_markers = (
        "edital",
        "comunicado",
        "convocacao",
        "resultado final",
        "resultado preliminar",
        "lista de inscritos",
        "lista de candidatos",
        "cronograma",
        "formulario",
        "manual do candidato",
    )
    name_hits = [marker for marker in irrelevant_markers if marker in name]
    text_hits = [marker for marker in irrelevant_markers if marker in content]
    administrative_hits = [
        marker
        for marker in (
            "torna publico",
            "inscricoes",
            "prazo para recurso",
            "resultado da analise",
            "candidatos convocados",
        )
        if marker in combined
    ]
    strong_irrelevant = bool(name_hits and (text_hits or administrative_hits)) or (
        len(set(text_hits)) >= 2 and bool(administrative_hits)
    )
    if strong_irrelevant:
        evidence = [
            *(f"nome/título contém “{item}”" for item in name_hits[:3]),
            *(f"texto contém “{item}”" for item in text_hits[:3]),
            *(f"sinal administrativo: “{item}”" for item in administrative_hits[:2]),
        ]
        return DocumentTriage(
            decision="other",
            confidence=min(0.99, 0.86 + (0.03 * min(4, len(evidence)))),
            evidence=evidence,
            reason=(
                "O PDF apresenta sinais administrativos fortes e não contém estrutura "
                "reconhecível de prova ou gabarito. O arquivo continuará salvo."
            ),
            source="local_rules",
        )

    evidence = []
    if name_hits or text_hits:
        evidence.append(
            "sinal administrativo isolado: " + ", ".join(dict.fromkeys([*name_hits, *text_hits]))
        )
    if question_headers:
        evidence.append(
            f"{question_headers} possível(is) enunciado(s), sem alternativas suficientes"
        )
    if not evidence:
        evidence.append(
            "texto sem estrutura conclusiva de prova, gabarito ou documento administrativo"
        )
    return DocumentTriage(
        decision="review",
        confidence=0.5,
        evidence=evidence,
        reason="A evidência é insuficiente; o tipo precisa ser decidido por uma pessoa.",
        source="local_rules",
    )
