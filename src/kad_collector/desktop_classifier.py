from __future__ import annotations

import json
import os
from typing import Any, Protocol, cast

from .desktop_models import (
    ClassificationRequest,
    ClassificationResponseItem,
    ClassificationValue,
    DesktopImportMetadata,
    QuestionClassification,
    TaxonomyChoiceResponse,
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
    provenance = [
        item for item in (metadata.provider, metadata.source_url) if item is not None
    ]
    values = {
        key: ClassificationValue(
            value=value,
            confidence=1 if value is not None else 0,
            evidence="Informado pelo operador na origem" if value is not None else None,
            source="operator_metadata" if value is not None else None,
            reason="Metadado explícito do documento" if value is not None else None,
            provenance=provenance if value is not None else [],
        )
        for key, value in mapping.items()
    }
    return QuestionClassification.model_validate(values)


class LocalRuleClassifier:
    name = "local"
    _AUTHORITATIVE_BLOCK_SOURCES = frozenset({"section_title", "page_section"})

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
        provenance: tuple[str, ...] | list[str] = (),
    ) -> ClassificationValue:
        return ClassificationValue(
            value=value,
            confidence=confidence,
            evidence=evidence,
            source=source,
            reason=reason,
            provenance=list(provenance),
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
                    provenance=path.provenance,
                ),
            )

    def _canonicalize_metadata_values(
        self, classification: QuestionClassification
    ) -> None:
        taxonomy_fields: dict[str, TaxonomyField] = {
            "discipline": "discipline",
            "subject": "matter",
            "topic": "subject",
        }
        for classification_field, taxonomy_field in taxonomy_fields.items():
            current = getattr(classification, classification_field)
            if current.value is None:
                continue
            original = str(current.value)
            try:
                canonical = self.taxonomy.canonical_name(taxonomy_field, original)
            except ValueError:
                setattr(
                    classification,
                    classification_field,
                    ClassificationValue(
                        value=None,
                        confidence=0,
                        evidence=original,
                        source="taxonomy_rejected",
                        reason=(
                            f"Nome informado não pertence à taxonomia "
                            f"{self.taxonomy.version}"
                        ),
                        provenance=current.provenance,
                    ),
                )
                continue
            current.value = canonical

    @staticmethod
    def _mark_unresolved(classification: QuestionClassification) -> None:
        for field in ("discipline", "subject", "topic"):
            current = getattr(classification, field)
            if current.value is None and current.source != "taxonomy_rejected":
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
        self,
        classifications: list[QuestionClassification],
        questions: list[ClassificationRequest],
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
                    and questions[index].block_id is not None
                    and (
                        questions[index - 1].block_id
                        == questions[index].block_id
                        == questions[index + 1].block_id
                    )
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
                            provenance=list(
                                dict.fromkeys([*left.provenance, *right.provenance])
                            ),
                        ),
                    )

    def _classify_verified_blocks(
        self,
        classifications: list[QuestionClassification],
        questions: list[ClassificationRequest],
        *,
        catalog_ids: tuple[str, ...],
    ) -> None:
        grouped: dict[str, list[int]] = {}
        for index, question in enumerate(questions):
            if question.block_id is not None:
                grouped.setdefault(question.block_id, []).append(index)

        for block_id, indexes in grouped.items():
            if len(indexes) < 2:
                continue
            contexts = {
                " ".join((questions[index].context or "").split())
                for index in indexes
                if (questions[index].context or "").strip()
            }
            authoritative_paths: list[TaxonomyPath] = []
            for index in indexes:
                classification = classifications[index]
                path = self.taxonomy.path_for_values(
                    cast(str | None, classification.discipline.value),
                    cast(str | None, classification.subject.value),
                    cast(str | None, classification.topic.value),
                )
                sources = {
                    classification.discipline.source,
                    classification.subject.source,
                    classification.topic.source,
                }
                if (
                    path is not None
                    and sources
                    and sources <= self._AUTHORITATIVE_BLOCK_SOURCES
                    and min(
                        classification.discipline.confidence,
                        classification.subject.confidence,
                        classification.topic.confidence,
                    )
                    >= 0.9
                ):
                    authoritative_paths.append(path)

            selected: TaxonomyPath | None = None
            source = "verified_block_context"
            confidence = 0.9
            reason = "Seção controlada aplicada somente ao bloco explícito correspondente"
            evidence = f"Bloco {block_id} possui seção oficial reconhecida"
            unique_authoritative = {
                (path.discipline, path.matter, path.subject): path
                for path in authoritative_paths
            }
            if len(unique_authoritative) == 1:
                selected = next(iter(unique_authoritative.values()))
            elif len(unique_authoritative) > 1 or len(contexts) != 1:
                continue
            else:
                shared_context = next(iter(contexts))
                known_disciplines = {
                    str(classifications[index].discipline.value)
                    for index in indexes
                    if classifications[index].discipline.value is not None
                    and classifications[index].discipline.source
                    in {"official_document_range", "official_exam_range"}
                }
                if len(known_disciplines) > 1:
                    continue
                discipline = next(iter(known_disciplines), None)
                semantic = self.taxonomy.semantic_match(
                    shared_context,
                    discipline=discipline,
                    catalog_ids=catalog_ids,
                )
                minimum_score = 2 if discipline is not None else 3
                if semantic is None or semantic.score < minimum_score:
                    continue
                selected = semantic.path
                confidence = min(0.9, 0.78 + semantic.score * 0.03)
                evidence = semantic.evidence
                reason = (
                    "Contexto repetido do bloco contém evidência taxonômica "
                    "controlada suficiente"
                )

            expected = (selected.discipline, selected.matter, selected.subject)
            conflict = any(
                current.value is not None and current.value != expected[position]
                for index in indexes
                for position, current in enumerate(
                    (
                        classifications[index].discipline,
                        classifications[index].subject,
                        classifications[index].topic,
                    )
                )
            )
            if conflict:
                continue
            for index in indexes:
                self._apply_path(
                    classifications[index],
                    selected,
                    confidence=confidence,
                    source=source,
                    evidence=evidence,
                    reason=reason,
                )

    def classify_many(
        self,
        questions: list[ClassificationRequest],
        metadata: DesktopImportMetadata,
    ) -> list[ClassificationResponseItem]:
        classifications: list[QuestionClassification] = []
        catalog_ids = self.taxonomy.relevant_catalog_ids(metadata)
        for question in questions:
            classification = _metadata_classification(metadata)
            self._canonicalize_metadata_values(classification)
            official_level = self.taxonomy.official_level(metadata)
            if classification.level.value is None and official_level is not None:
                classification.level = self._classified_value(
                    official_level,
                    confidence=0.98,
                    source="official_contest_requirement",
                    evidence="Requisito de escolaridade do edital oficial",
                    reason="Cargo e concurso correspondem ao perfil oficial versionado",
                )
            section = self.taxonomy.match_section(
                question.section_title, catalog_ids=catalog_ids
            )
            section_source = "section_title"
            if section is None:
                section = self.taxonomy.match_context_heading(
                    question.context, catalog_ids=catalog_ids
                )
                section_source = "page_section"
            section_is_specific = (
                section is not None
                and section.matter is not None
                and section.subject is not None
            )
            if section is not None and section_is_specific:
                self._apply_path(
                    classification,
                    section,
                    confidence=0.96 if section_source == "section_title" else 0.91,
                    source=section_source,
                    evidence=question.section_title or "Título identificado na página da questão",
                    reason="Título de seção reconhecido na taxonomia versionada",
                )

            document_range = None
            if question.official_discipline is not None:
                document_range = self.taxonomy.discipline_path(
                    question.official_discipline,
                    catalog_ids=catalog_ids,
                )
            if document_range is not None:
                self._apply_path(
                    classification,
                    document_range,
                    confidence=0.98,
                    source="official_document_range",
                    evidence=question.official_range_evidence
                    or f"Questão {question.question_number} em intervalo oficial",
                    reason="Disciplina e intervalo lidos do quadro oficial da prova",
                )

            official_range = self.taxonomy.match_official_range(
                metadata, question.question_number
            )
            if official_range is not None and classification.discipline.value is None:
                self._apply_path(
                    classification,
                    official_range,
                    confidence=0.94,
                    source="official_exam_range",
                    evidence=f"Questão {question.question_number} no bloco do edital oficial",
                    reason="Intervalo de questões definido no edital oficial do concurso",
                )
            if section is not None and not section_is_specific:
                self._apply_path(
                    classification,
                    section,
                    confidence=0.96 if section_source == "section_title" else 0.91,
                    source=section_source,
                    evidence=(
                        question.section_title
                        or "Título identificado na página da questão"
                    ),
                    reason="Título de seção reconhecido na taxonomia versionada",
                )

            # Alternatives are possible answers, not evidence that their topics
            # are what the question tests. Distractors can otherwise outvote the
            # statement (for example, several payment products beside Registrato).
            text = question.statement
            semantic = self.taxonomy.semantic_match(
                text,
                discipline=(
                    str(classification.discipline.value)
                    if classification.discipline.value is not None
                    else None
                ),
                catalog_ids=catalog_ids,
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

        self._classify_verified_blocks(
            classifications,
            questions,
            catalog_ids=catalog_ids,
        )
        self._propagate_neighbors(classifications, questions)
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
    minimum_confidence = 0.78

    def __init__(
        self,
        model: str | None = None,
        *,
        client: Any | None = None,
        taxonomy: EditorialTaxonomy | None = None,
    ) -> None:
        self.taxonomy = taxonomy or EditorialTaxonomy.load_default()
        self._local = LocalRuleClassifier(self.taxonomy)
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5.6-terra")
        if client is not None:
            self._client = client
            return
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self._client = None
            return
        try:
            from openai import OpenAI
        except ImportError:
            self._client = None
            return
        self._client = OpenAI(api_key=api_key, timeout=180.0, max_retries=2)

    def _option_id(self, path: TaxonomyPath) -> str:
        return path.path_id

    @staticmethod
    def _response_schema(option_ids: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "question_number",
                            "option_id",
                            "confidence",
                            "evidence",
                        ],
                        "properties": {
                            "question_number": {"type": "integer", "minimum": 1},
                            "option_id": {"type": "string", "enum": option_ids},
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "evidence": {"type": "string", "minLength": 1},
                        },
                    },
                }
            },
        }

    def classify_many(
        self,
        questions: list[ClassificationRequest],
        metadata: DesktopImportMetadata,
    ) -> list[ClassificationResponseItem]:
        local = self._local.classify_many(questions, metadata)
        if self._client is None:
            return local

        catalog_ids = self.taxonomy.relevant_catalog_ids(metadata)
        requests_by_number = {item.question_number: item for item in questions}
        allowed_by_number: dict[int, dict[str, TaxonomyPath]] = {}
        all_options: dict[str, TaxonomyPath] = {}
        for item in local:
            classification = item.classification
            if all(
                getattr(classification, field).value is not None
                for field in ("discipline", "subject", "topic")
            ):
                continue
            discipline = (
                str(classification.discipline.value)
                if classification.discipline.value is not None
                else None
            )
            paths = self.taxonomy.candidate_paths(
                catalog_ids=catalog_ids,
                discipline=discipline,
            )
            filtered = tuple(
                path
                for path in paths
                if (
                    classification.subject.value is None
                    or path.matter == classification.subject.value
                )
                and (
                    classification.topic.value is None
                    or path.subject == classification.topic.value
                )
            )
            options = {self._option_id(path): path for path in filtered}
            if options:
                allowed_by_number[item.question_number] = options
                all_options.update(options)
        if not allowed_by_number:
            return local

        option_ids = sorted(all_options)
        payload = {
            "metadata": metadata.model_dump(mode="json"),
            "taxonomy_version": self.taxonomy.version,
            "taxonomy_options": [
                {
                    "id": option_id,
                    "discipline": all_options[option_id].discipline,
                    "matter": all_options[option_id].matter,
                    "subject": all_options[option_id].subject,
                }
                for option_id in option_ids
            ],
            "questions": [
                {
                    **requests_by_number[number].model_dump(mode="json"),
                    "allowed_option_ids": sorted(options),
                }
                for number, options in allowed_by_number.items()
            ],
        }
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=(
                    "Escolha somente um option_id permitido para cada questão. "
                    "Não crie nomes. Omita questões sem evidência suficiente."
                ),
                input=json.dumps(payload, ensure_ascii=False),
                reasoning={"effort": "low"},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "taxonomy_choice",
                        "strict": True,
                        "schema": self._response_schema(option_ids),
                    },
                    "verbosity": "low",
                },
                max_output_tokens=4_000,
                store=False,
            )
            if not response.output_text:
                return local
            parsed = TaxonomyChoiceResponse.model_validate_json(response.output_text)
        except Exception:
            return local

        seen_numbers: set[int] = set()
        for choice in parsed.items:
            if choice.question_number in seen_numbers:
                continue
            seen_numbers.add(choice.question_number)
            allowed = allowed_by_number.get(choice.question_number, {})
            path = allowed.get(choice.option_id)
            if (
                path is None
                or choice.confidence < self.minimum_confidence
                or not choice.evidence.strip()
            ):
                continue
            target = next(
                (
                    item.classification
                    for item in local
                    if item.question_number == choice.question_number
                ),
                None,
            )
            if target is None:
                continue
            audited_path = TaxonomyPath(
                path_id=path.path_id,
                discipline=path.discipline,
                matter=path.matter,
                subject=path.subject,
                catalog_id=path.catalog_id,
                provenance=tuple(
                    dict.fromkeys(
                        [
                            *path.provenance,
                            f"taxonomy:{self.taxonomy.version}",
                            f"model:{self.model}",
                        ]
                    )
                ),
            )
            self._local._apply_path(
                target,
                audited_path,
                confidence=min(0.86, choice.confidence),
                source="openai_taxonomy_choice",
                evidence=choice.evidence.strip(),
                reason=(
                    f"Último recurso por opção fechada; modelo {self.model}; "
                    f"taxonomia {self.taxonomy.version}"
                ),
            )
        return local


def build_classifier(name: str) -> ClassificationProvider:
    if name == "openai":
        return OpenAIClassificationProvider()
    return LocalRuleClassifier()
