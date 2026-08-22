from __future__ import annotations

import json
import os
from typing import Protocol

from .desktop_models import (
    ClassificationRequest,
    ClassificationResponse,
    ClassificationResponseItem,
    ClassificationValue,
    DesktopImportMetadata,
    QuestionClassification,
)
from .editorial_taxonomy import EditorialTaxonomy, TaxonomyField, TaxonomyPath


class ClassificationProvider(Protocol):
    name: str

    def classify_many(
        self,
        questions: list[ClassificationRequest],
        metadata: DesktopImportMetadata,
    ) -> list[ClassificationResponseItem]: ...


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
            source="operator_metadata" if value is not None else None,
            reason="Metadado explícito do documento" if value is not None else None,
        )
        for key, value in mapping.items()
    }
    return QuestionClassification.model_validate(values)


class LocalRuleClassifier:
    name = "local"

    def __init__(self, taxonomy: EditorialTaxonomy | None = None) -> None:
        self.taxonomy = taxonomy or EditorialTaxonomy.load_default()

    @staticmethod
    def _classified_value(
        value: str,
        *,
        confidence: float,
        source: str,
        evidence: str,
        reason: str,
    ) -> ClassificationValue:
        return ClassificationValue(
            value=value,
            confidence=confidence,
            evidence=evidence,
            source=source,
            reason=reason,
        )

    def _apply_path(
        self,
        classification: QuestionClassification,
        path: TaxonomyPath,
        *,
        confidence: float,
        source: str,
        evidence: str,
        reason: str,
    ) -> None:
        values = {
            "discipline": path.discipline,
            "subject": path.matter,
            "topic": path.subject,
        }
        taxonomy_fields: dict[str, TaxonomyField] = {
            "discipline": "discipline",
            "subject": "matter",
            "topic": "subject",
        }
        for field, value in values.items():
            current = getattr(classification, field)
            if value is None or current.value is not None:
                continue
            self.taxonomy.ensure_known(taxonomy_fields[field], value)
            setattr(
                classification,
                field,
                self._classified_value(
                    value,
                    confidence=confidence,
                    source=source,
                    evidence=evidence,
                    reason=reason,
                ),
            )

    @staticmethod
    def _mark_unresolved(classification: QuestionClassification) -> None:
        for field in ("discipline", "subject", "topic"):
            current = getattr(classification, field)
            if current.value is None:
                setattr(
                    classification,
                    field,
                    ClassificationValue(
                        value=None,
                        confidence=0,
                        source="unresolved",
                        reason="Não há evidência suficiente na taxonomia para classificar",
                    ),
                )

    def _propagate_neighbors(
        self, classifications: list[QuestionClassification]
    ) -> None:
        snapshot = [item.model_copy(deep=True) for item in classifications]
        for index in range(1, len(snapshot) - 1):
            previous = snapshot[index - 1]
            current = classifications[index]
            following = snapshot[index + 1]
            for field in ("discipline", "subject", "topic"):
                current_value = getattr(current, field)
                left = getattr(previous, field)
                right = getattr(following, field)
                if (
                    current_value.value is None
                    and left.value is not None
                    and left.value == right.value
                    and left.confidence >= 0.8
                    and right.confidence >= 0.8
                ):
                    setattr(
                        current,
                        field,
                        self._classified_value(
                            str(left.value),
                            confidence=0.78,
                            source="neighbor_context",
                            evidence=f"Questões vizinhas concordam em {left.value}",
                            reason="Campo propagado somente entre vizinhas confiáveis concordantes",
                        ),
                    )

    def classify_many(
        self,
        questions: list[ClassificationRequest],
        metadata: DesktopImportMetadata,
    ) -> list[ClassificationResponseItem]:
        classifications: list[QuestionClassification] = []
        for question in questions:
            classification = _metadata_classification(metadata)
            official_level = self.taxonomy.official_level(metadata)
            if classification.level.value is None and official_level is not None:
                classification.level = self._classified_value(
                    official_level,
                    confidence=0.98,
                    source="official_contest_requirement",
                    evidence="Requisito de escolaridade do edital oficial",
                    reason="Cargo e concurso correspondem ao perfil oficial versionado",
                )
            section = self.taxonomy.match_section(question.section_title)
            section_source = "section_title"
            if section is None:
                section = self.taxonomy.match_section(question.context)
                section_source = "page_section"
            if section is not None:
                self._apply_path(
                    classification,
                    section,
                    confidence=0.96 if section_source == "section_title" else 0.91,
                    source=section_source,
                    evidence=question.section_title or "Título identificado na página da questão",
                    reason="Título de seção reconhecido na taxonomia versionada",
                )

            official_range = self.taxonomy.match_official_range(
                metadata, question.question_number
            )
            if official_range is not None:
                self._apply_path(
                    classification,
                    official_range,
                    confidence=0.94,
                    source="official_exam_range",
                    evidence=f"Questão {question.question_number} no bloco do edital oficial",
                    reason="Intervalo de questões definido no edital oficial do concurso",
                )

            text = " ".join([question.statement, *question.alternatives])
            semantic = self.taxonomy.semantic_match(
                text,
                discipline=(
                    str(classification.discipline.value)
                    if classification.discipline.value is not None
                    else None
                ),
            )
            if semantic is not None:
                self._apply_path(
                    classification,
                    semantic.path,
                    confidence=min(0.88, 0.72 + semantic.score * 0.04),
                    source="local_semantic_rule",
                    evidence=semantic.evidence,
                    reason="Indicadores locais compatíveis com a taxonomia controlada",
                )
            classifications.append(classification)

        self._propagate_neighbors(classifications)
        for classification in classifications:
            self._mark_unresolved(classification)
        return [
            ClassificationResponseItem(
                question_number=question.question_number,
                classification=classification,
            )
            for question, classification in zip(questions, classifications, strict=True)
        ]


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
        taxonomy = EditorialTaxonomy.load_default()
        taxonomy_fields: dict[str, TaxonomyField] = {
            "discipline": "discipline",
            "subject": "matter",
            "topic": "subject",
        }
        for item in parsed.items:
            for classification_field, taxonomy_field in taxonomy_fields.items():
                value = getattr(item.classification, classification_field)
                if value.value is None:
                    continue
                try:
                    taxonomy.ensure_known(taxonomy_field, str(value.value))
                except ValueError:
                    setattr(
                        item.classification,
                        classification_field,
                        ClassificationValue(
                            value=None,
                            confidence=0,
                            evidence=str(value.value),
                            source="taxonomy_rejected",
                            reason=(
                                f"Nome rejeitado por não pertencer à taxonomia "
                                f"{taxonomy.version}"
                            ),
                        ),
                    )
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
