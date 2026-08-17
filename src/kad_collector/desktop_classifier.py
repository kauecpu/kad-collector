from __future__ import annotations

import json
import os
from collections import Counter
from typing import Protocol

from .desktop_models import (
    ClassificationRequest,
    ClassificationResponse,
    ClassificationResponseItem,
    ClassificationValue,
    DesktopImportMetadata,
    QuestionClassification,
)


class ClassificationProvider(Protocol):
    name: str

    def classify_many(
        self,
        questions: list[ClassificationRequest],
        metadata: DesktopImportMetadata,
    ) -> list[ClassificationResponseItem]: ...


_DISCIPLINE_RULES: dict[str, tuple[str, ...]] = {
    "Língua Portuguesa": (
        "texto",
        "oração",
        "verbo",
        "pronome",
        "concordância",
        "regência",
        "crase",
        "pontuação",
        "sinônimo",
        "gramática",
    ),
    "Matemática": (
        "equação",
        "porcentagem",
        "probabilidade",
        "função",
        "ângulo",
        "triângulo",
        "razão",
        "proporção",
        "média aritmética",
        "conjunto",
    ),
    "Direito Constitucional": (
        "constituição federal",
        "direitos fundamentais",
        "controle de constitucionalidade",
        "poder constituinte",
        "mandado de segurança",
    ),
    "Direito Administrativo": (
        "administração pública",
        "ato administrativo",
        "licitação",
        "servidor público",
        "improbidade",
        "poder de polícia",
    ),
    "Informática": (
        "sistema operacional",
        "windows",
        "linux",
        "planilha",
        "internet",
        "segurança da informação",
        "banco de dados",
        "rede de computadores",
    ),
    "Geografia": (
        "mapa",
        "escala cartográfica",
        "clima",
        "relevo",
        "urbanização",
        "território",
        "população",
    ),
    "História": (
        "revolução",
        "império",
        "república",
        "colonial",
        "ditadura",
        "guerra mundial",
    ),
    "Contabilidade": (
        "patrimônio líquido",
        "balanço patrimonial",
        "débito",
        "crédito",
        "ativo circulante",
        "demonstração contábil",
    ),
}

_SUBJECT_RULES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "interpretação": (
        "Interpretação de textos",
        "Compreensão e interpretação",
        ("de acordo com o texto", "infere-se", "ideia central", "sentido do texto"),
    ),
    "concordância": (
        "Gramática",
        "Concordância verbal e nominal",
        ("concordância", "concorda", "flexão verbal"),
    ),
    "porcentagem": (
        "Aritmética",
        "Porcentagem",
        ("porcentagem", "%", "acréscimo percentual", "desconto"),
    ),
    "cartografia": (
        "Cartografia",
        "Escala cartográfica",
        ("escala", "mapa", "coordenadas geográficas"),
    ),
    "licitações": (
        "Direito Administrativo",
        "Licitações e contratos",
        ("licitação", "contrato administrativo", "pregão"),
    ),
    "constitucionalidade": (
        "Direito Constitucional",
        "Controle de constitucionalidade",
        ("constitucionalidade", "ação direta", "controle difuso"),
    ),
}


def _metadata_classification(metadata: DesktopImportMetadata) -> QuestionClassification:
    mapping = {
        "concurso": metadata.concurso,
        "board": metadata.board,
        "year": metadata.year,
        "role": metadata.role,
        "organization": metadata.organization,
        "level": metadata.level,
        "discipline": metadata.discipline,
        "subject": metadata.subject,
        "topic": metadata.topic,
        "difficulty": metadata.difficulty,
    }
    values = {
        key: ClassificationValue(
            value=value,
            confidence=1 if value is not None else 0,
            evidence="Informado pelo operador na origem" if value is not None else None,
        )
        for key, value in mapping.items()
    }
    return QuestionClassification.model_validate(values)


class LocalRuleClassifier:
    name = "local"

    def classify_many(
        self,
        questions: list[ClassificationRequest],
        metadata: DesktopImportMetadata,
    ) -> list[ClassificationResponseItem]:
        results: list[ClassificationResponseItem] = []
        for question in questions:
            classification = _metadata_classification(metadata)
            text = " ".join([question.statement, *question.alternatives]).casefold()
            if classification.discipline.value is None:
                scores = Counter(
                    {
                        discipline: sum(keyword.casefold() in text for keyword in keywords)
                        for discipline, keywords in _DISCIPLINE_RULES.items()
                    }
                )
                best = scores.most_common(2)
                if best and best[0][1] >= 2 and (len(best) == 1 or best[0][1] > best[1][1]):
                    classification.discipline = ClassificationValue(
                        value=best[0][0],
                        confidence=min(0.9, 0.68 + best[0][1] * 0.05),
                        evidence=f"{best[0][1]} indicadores semânticos locais",
                    )
            if classification.subject.value is None or classification.topic.value is None:
                candidates: list[tuple[int, str, str, str]] = []
                for label, (subject, topic, keywords) in _SUBJECT_RULES.items():
                    score = sum(keyword.casefold() in text for keyword in keywords)
                    if score:
                        candidates.append((score, label, subject, topic))
                candidates.sort(reverse=True)
                if candidates:
                    score, label, subject, topic = candidates[0]
                    if classification.subject.value is None:
                        classification.subject = ClassificationValue(
                            value=subject,
                            confidence=min(0.84, 0.65 + score * 0.07),
                            evidence=f"Padrão local: {label}",
                        )
                    if classification.topic.value is None:
                        classification.topic = ClassificationValue(
                            value=topic,
                            confidence=min(0.82, 0.64 + score * 0.07),
                            evidence=f"Padrão local: {label}",
                        )
            results.append(
                ClassificationResponseItem(
                    question_number=question.question_number,
                    classification=classification,
                )
            )
        return results


class OpenAIClassificationProvider:
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY nao configurada; use classificacao local "
                "ou defina a chave na sessao"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("dependencia openai nao instalada") from exc
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
        self._client = OpenAI(api_key=api_key, timeout=180.0, max_retries=2)

    def classify_many(
        self,
        questions: list[ClassificationRequest],
        metadata: DesktopImportMetadata,
    ) -> list[ClassificationResponseItem]:
        response = self._client.responses.create(
            model=self.model,
            instructions=(
                "Classifique questões brasileiras de concurso usando apenas evidências do texto "
                "e os metadados fornecidos. Não invente banca, concurso, ano, cargo, instituição, "
                "nível, disciplina, assunto, tópico ou dificuldade. Use null e confiança 0 quando "
                "não houver evidência suficiente. Confiança deve refletir a certeza real."
            ),
            input=json.dumps(
                {
                    "metadata": metadata.model_dump(mode="json"),
                    "questions": [item.model_dump(mode="json") for item in questions],
                },
                ensure_ascii=False,
            ),
            reasoning={"effort": "low"},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "question_classification",
                    "strict": True,
                    "schema": ClassificationResponse.model_json_schema(),
                },
                "verbosity": "low",
            },
            max_output_tokens=12_000,
            store=False,
        )
        if not response.output_text:
            raise RuntimeError("a classificacao por IA nao retornou dados")
        parsed = ClassificationResponse.model_validate_json(response.output_text)
        by_number = {item.question_number: item for item in parsed.items}
        local = LocalRuleClassifier().classify_many(questions, metadata)
        merged: list[ClassificationResponseItem] = []
        for fallback in local:
            candidate = by_number.get(fallback.question_number)
            if candidate is None:
                merged.append(fallback)
                continue
            for field in candidate.classification.model_fields:
                ai_value = getattr(candidate.classification, field)
                local_value = getattr(fallback.classification, field)
                if (
                    local_value.confidence == 1
                    or ai_value.value is None
                    and local_value.value is not None
                ):
                    setattr(candidate.classification, field, local_value)
            merged.append(candidate)
        return merged


def build_classifier(name: str) -> ClassificationProvider:
    if name == "openai":
        return OpenAIClassificationProvider()
    return LocalRuleClassifier()
