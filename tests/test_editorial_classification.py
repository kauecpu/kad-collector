from __future__ import annotations

import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from reportlab.pdfgen import canvas

from kad_collector.desktop_classifier import LocalRuleClassifier
from kad_collector.desktop_models import (
    ClassificationRequest,
    ClassificationValue,
    DesktopImportMetadata,
    QuestionClassification,
)
from kad_collector.desktop_server import DesktopApplication
from kad_collector.editorial_taxonomy import EditorialTaxonomy
from kad_collector.models import Alternative, QuestionRecord
from kad_collector.validation import (
    validate_app_import_question,
    validate_editorial_question,
)


def _question(number: int, statement: str) -> QuestionRecord:
    return QuestionRecord(
        number=number,
        statement=statement,
        alternatives=[
            Alternative(letter="A", text="Primeira alternativa válida."),
            Alternative(letter="B", text="Segunda alternativa válida."),
            Alternative(letter="C", text="Terceira alternativa válida."),
        ],
        matter=None,
        subject=None,
        discipline=None,
        board="FGV",
        organization="Receita Federal do Brasil",
        concurso="RFB22",
        role="Auditor-Fiscal da Receita Federal do Brasil",
        year=2023,
        level="Superior",
        difficulty=None,
        source_pages=[1],
        explanation=None,
        answer_status="matched",
        correct_answer="B",
    )


def _request(
    number: int,
    statement: str,
    *,
    section_title: str | None = None,
    block_id: str | None = None,
    context: str | None = None,
) -> ClassificationRequest:
    return ClassificationRequest(
        question_number=number,
        statement=statement,
        alternatives=["Primeira alternativa", "Segunda alternativa", "Terceira alternativa"],
        section_title=section_title,
        block_id=block_id,
        context=context,
    )


def _fgv_metadata(**changes: object) -> DesktopImportMetadata:
    values: dict[str, object] = {
        "provider": "fgv_conhecimento",
        "source_url": (
            "https://conhecimento.fgv.br/sites/default/files/concursos/"
            "cns101-auditor-fiscal-tipo-1.pdf"
        ),
        "concurso": "RFB22",
        "board": "FGV",
        "year": 2023,
        "role": "Auditor-Fiscal da Receita Federal do Brasil",
        "stage": "prova objetiva",
        "organization": "Receita Federal do Brasil",
        "level": "Superior",
    }
    values.update(changes)
    return DesktopImportMetadata.model_validate(values)


class EditorialTaxonomyTests(unittest.TestCase):
    def test_taxonomy_is_versioned_sourced_and_rejects_unknown_names(self) -> None:
        taxonomy = EditorialTaxonomy.load_default()

        self.assertRegex(taxonomy.version, r"^\d+\.\d+\.\d+$")
        self.assertTrue(any(source.startswith("https://") for source in taxonomy.sources))
        taxonomy.ensure_known("discipline", "Direito Tributário")
        with self.assertRaisesRegex(ValueError, "fora da taxonomia"):
            taxonomy.ensure_known("discipline", "Direito Inventado pela IA")

    def test_section_title_has_priority_and_avoids_customs_math_false_positive(self) -> None:
        item = LocalRuleClassifier().classify_many(
            [
                _request(
                    1,
                    "Uma carga tem média aritmética de 20 toneladas.",
                    section_title="ADMINISTRAÇÃO ADUANEIRA E MODELO DE CONTROLE - MCA",
                )
            ],
            _fgv_metadata(stage="curso de formação", source_url="https://example.gov.br/cf.pdf"),
        )[0].classification

        self.assertEqual(item.discipline.value, "Legislação Aduaneira")
        self.assertEqual(item.subject.value, "Administração Aduaneira")
        self.assertEqual(item.discipline.source, "section_title")
        self.assertGreaterEqual(item.discipline.confidence, 0.9)

    def test_official_exam_block_classifies_by_question_range(self) -> None:
        results = LocalRuleClassifier().classify_many(
            [
                _request(1, "Assinale a opção correta."),
                _request(11, "Choose the correct answer."),
            ],
            _fgv_metadata(level=None),
        )

        self.assertEqual(results[0].classification.discipline.value, "Língua Portuguesa")
        self.assertEqual(results[1].classification.discipline.value, "Língua Inglesa")
        self.assertEqual(results[1].classification.discipline.source, "official_exam_range")
        self.assertEqual(results[1].classification.level.value, "Superior")
        self.assertEqual(
            results[1].classification.level.source, "official_contest_requirement"
        )

    def test_neighbor_context_fills_only_between_matching_confident_neighbors(self) -> None:
        results = LocalRuleClassifier().classify_many(
            [
                _request(
                    1,
                    "Primeira questão.",
                    section_title="SIGILO FISCAL",
                    block_id="bloco-sigilo",
                ),
                _request(
                    2,
                    "Enunciado neutro sem evidência temática suficiente.",
                    block_id="bloco-sigilo",
                ),
                _request(
                    3,
                    "Terceira questão.",
                    section_title="SIGILO FISCAL",
                    block_id="bloco-sigilo",
                ),
            ],
            _fgv_metadata(stage="curso de formação", source_url="https://example.gov.br/cf.pdf"),
        )

        middle = results[1].classification
        self.assertEqual(middle.discipline.value, "Direito Tributário")
        self.assertEqual(middle.subject.value, "Sigilo Fiscal")
        self.assertEqual(middle.discipline.source, "neighbor_context")

    def test_neighbor_context_does_not_propagate_without_explicit_block(self) -> None:
        results = LocalRuleClassifier().classify_many(
            [
                _request(1, "Primeira questão.", section_title="SIGILO FISCAL"),
                _request(2, "Enunciado neutro sem evidência temática suficiente."),
                _request(3, "Terceira questão.", section_title="SIGILO FISCAL"),
            ],
            DesktopImportMetadata(),
        )

        middle = results[1].classification
        self.assertIsNone(middle.discipline.value)
        self.assertEqual(middle.discipline.source, "unresolved")

    def test_neighbor_context_does_not_cross_block_boundary(self) -> None:
        results = LocalRuleClassifier().classify_many(
            [
                _request(
                    1,
                    "Primeira questão.",
                    section_title="SIGILO FISCAL",
                    block_id="bloco-a",
                ),
                _request(
                    2,
                    "Enunciado neutro sem evidência temática suficiente.",
                    block_id="bloco-b",
                ),
                _request(
                    3,
                    "Terceira questão.",
                    section_title="SIGILO FISCAL",
                    block_id="bloco-a",
                ),
            ],
            DesktopImportMetadata(),
        )

        self.assertIsNone(results[1].classification.discipline.value)

    def test_local_semantics_prefers_customs_evidence_over_math_word(self) -> None:
        classification = LocalRuleClassifier().classify_many(
            [
                _request(
                    1,
                    (
                        "No controle aduaneiro, a administração aduaneira calcula a "
                        "média aritmética das cargas para fiscalizar a aduana."
                    ),
                )
            ],
            DesktopImportMetadata(),
        )[0].classification

        self.assertEqual(classification.discipline.value, "Legislação Aduaneira")
        self.assertEqual(classification.subject.value, "Administração Aduaneira")
        self.assertEqual(classification.discipline.source, "local_semantic_rule")

    def test_single_generic_keyword_does_not_fill_topic_at_low_confidence(self) -> None:
        classification = LocalRuleClassifier().classify_many(
            [_request(1, "Considere somente a mediana apresentada no quadro.")],
            DesktopImportMetadata(),
        )[0].classification

        self.assertIsNone(classification.discipline.value)
        self.assertIsNone(classification.subject.value)
        self.assertIsNone(classification.topic.value)
        self.assertEqual(classification.subject.source, "unresolved")

    def test_exact_keyword_can_specialize_an_already_proven_discipline(self) -> None:
        classification = LocalRuleClassifier().classify_many(
            [_request(1, "Calcule a probabilidade do evento apresentado.")],
            DesktopImportMetadata(discipline="Estatística"),
        )[0].classification

        self.assertEqual(classification.discipline.value, "Estatística")
        self.assertEqual(classification.subject.value, "Probabilidade")
        self.assertEqual(classification.topic.value, "Probabilidade de Eventos")
        self.assertEqual(classification.subject.source, "local_semantic_rule")

    def test_controlled_compound_keyword_recognizes_nosql_as_database(self) -> None:
        classification = LocalRuleClassifier().classify_many(
            [_request(1, "Bancos de dados NoSQL usam estruturas flexíveis.")],
            DesktopImportMetadata(discipline="Fluência em Dados"),
        )[0].classification

        self.assertEqual(classification.subject.value, "Banco de Dados")
        self.assertEqual(classification.topic.value, "Modelagem e Consulta de Dados")

    def test_insufficient_evidence_remains_unclassified(self) -> None:
        classification = LocalRuleClassifier().classify_many(
            [_request(1, "Considere as afirmações e assinale a opção correta.")],
            DesktopImportMetadata(),
        )[0].classification

        self.assertIsNone(classification.discipline.value)
        self.assertIsNone(classification.subject.value)
        self.assertIsNone(classification.topic.value)
        self.assertEqual(classification.discipline.source, "unresolved")
        self.assertIn("evidência", classification.discipline.reason or "")


class ImportReadinessTests(unittest.TestCase):
    def test_importable_is_less_strict_than_ready_for_publication(self) -> None:
        question = _question(
            1,
            "Sobre competência tributária, assinale a alternativa correta.",
        ).model_copy(
            update={
                "discipline": "Direito Tributário",
                "matter": "Sistema Tributário Nacional",
                "subject": "Competência Tributária",
            }
        )

        self.assertEqual(validate_app_import_question(question), [])
        publication_errors = validate_editorial_question(question)
        self.assertTrue(any("dificuldade" in error for error in publication_errors))
        self.assertTrue(any("explicacao" in error for error in publication_errors))

    def test_import_validation_keeps_answer_alternative_and_origin_fields_required(self) -> None:
        question = _question(1, "Questão sem resposta oficial suficiente.").model_copy(
            update={
                "discipline": "Direito Tributário",
                "matter": "Sistema Tributário Nacional",
                "subject": "Competência Tributária",
                "answer_status": "missing",
                "correct_answer": None,
            }
        )

        errors = validate_app_import_question(question)

        self.assertTrue(any("sem gabarito" in error for error in errors))


class StoredReclassificationTests(unittest.TestCase):
    def test_reclassification_preserves_accepted_qwen_fields(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova.pdf"
            document = canvas.Canvas(str(pdf_path))
            document.drawString(50, 800, "CONHECIMENTOS GERAIS")
            document.showPage()
            document.save()
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job([pdf_path], _fgv_metadata(), "local")
            stored_document = application.store.documents_for_job(job_id)[0]
            application.store.save_page(
                stored_document["id"], 1, "CONHECIMENTOS GERAIS", status="text"
            )
            question = _question(1, "Assinale a alternativa correta.").model_copy(
                update={
                    "discipline": "Direito Tributário",
                    "matter": "Sistema Tributário Nacional",
                    "subject": "Competência Tributária",
                }
            )

            def qwen(value: str) -> ClassificationValue:
                return ClassificationValue(
                    value=value,
                    confidence=0.91,
                    evidence="sugestão local aceita",
                    source="ai_suggestion",
                )

            question_id = application.store.save_question(
                stored_document["id"],
                question,
                QuestionClassification(
                    discipline=qwen("Direito Tributário"),
                    subject=qwen("Sistema Tributário Nacional"),
                    topic=qwen("Competência Tributária"),
                    level=qwen("Superior"),
                ),
            )

            first = application.reclassify_questions()
            after = application.store.question(question_id)
            second = application.reclassify_questions()

            self.assertEqual(first["changed"], 0)
            self.assertEqual(second["changed"], 0)
            self.assertEqual(after["question"]["discipline"], "Direito Tributário")
            self.assertEqual(
                after["classification"]["discipline"]["source"], "ai_suggestion"
            )

    def test_reclassification_is_idempotent_audited_and_preserves_human_decision(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "curso-formacao.pdf"
            document = canvas.Canvas(str(pdf_path))
            document.drawString(50, 800, "ADMINISTRAÇÃO ADUANEIRA E MODELO DE CONTROLE - MCA")
            document.showPage()
            document.save()
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job(
                [pdf_path],
                _fgv_metadata(
                    stage="curso de formação",
                    source_url="https://example.gov.br/curso-formacao.pdf",
                ),
                "local",
            )
            stored_document = application.store.documents_for_job(job_id)[0]
            application.store.save_page(
                stored_document["id"],
                1,
                "ADMINISTRAÇÃO ADUANEIRA E MODELO DE CONTROLE - MCA",
                status="text",
            )
            wrong = _question(
                1,
                "No controle aduaneiro, considere a média aritmética das cargas.",
            ).model_copy(
                update={
                    "discipline": "Matemática",
                    "matter": "Aritmética",
                    "subject": "Média Aritmética",
                    "difficulty": "Média",
                    "explanation": "A resposta B foi conferida na fonte oficial apresentada.",
                }
            )
            value = lambda item: ClassificationValue(  # noqa: E731
                value=item,
                confidence=0.7,
                evidence="classificador local legado",
            )
            classification = QuestionClassification(
                discipline=value("Matemática"),
                subject=value("Aritmética"),
                topic=value("Média Aritmética"),
            )
            question_id = application.store.save_question(
                stored_document["id"], wrong, classification
            )
            with closing(application.store._connect()) as connection:
                connection.execute(
                    "UPDATE questions SET status='approved', reviewer='revisora', "
                    "review_notes='Conferida.' WHERE id=?",
                    (question_id,),
                )
                connection.commit()

            first = application.reclassify_questions()
            after = application.store.question(question_id)
            second = application.reclassify_questions()

            self.assertEqual(first["total"], 1)
            self.assertEqual(first["changed"], 1)
            self.assertEqual(second["changed"], 0)
            self.assertEqual(after["question"]["discipline"], "Legislação Aduaneira")
            self.assertEqual(after["question"]["correct_answer"], "B")
            self.assertEqual(after["status"], "approved")
            self.assertEqual(after["reviewer"], "revisora")
            self.assertEqual(
                [event["action"] for event in application.store.audit_log(question_id)],
                ["classification_reprocessed"],
            )

    def test_question_view_exposes_importable_separately_from_publication_ready(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "prova.pdf"
            document = canvas.Canvas(str(pdf_path))
            document.drawString(50, 800, "Fonte oficial")
            document.showPage()
            document.save()
            application = DesktopApplication(root / "data")
            job_id = application.store.create_job([pdf_path], _fgv_metadata(), "local")
            stored_document = application.store.documents_for_job(job_id)[0]
            question = _question(
                1, "Sobre competência tributária, assinale a alternativa correta."
            ).model_copy(
                update={
                    "discipline": "Direito Tributário",
                    "matter": "Sistema Tributário Nacional",
                    "subject": "Competência Tributária",
                }
            )
            question_id = application.store.save_question(
                stored_document["id"], question, QuestionClassification()
            )

            view = application.store.question(question_id)

            self.assertTrue(view["importable"])
            self.assertFalse(view["publication_ready"])
            self.assertEqual(application.bootstrap()["summary"]["importable"], 1)

    def test_ui_presents_missing_classification_as_text_not_zero_percent(self) -> None:
        javascript = (
            Path(__file__).parents[1] / "src" / "kad_collector" / "desktop_app.js"
        ).read_text(encoding="utf-8")
        html = (
            Path(__file__).parents[1] / "src" / "kad_collector" / "desktop_ui.html"
        ).read_text(encoding="utf-8")

        self.assertIn("Não classificada", javascript)
        self.assertIn("IMPORTÁVEIS", html)
        self.assertIn("Matéria", html)
        self.assertNotIn("<label>Tópico<input id=\"edit-subject\">", html)


if __name__ == "__main__":
    unittest.main()
