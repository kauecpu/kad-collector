from __future__ import annotations

import json
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from reportlab.pdfgen import canvas

from kad_collector.desktop_export import (
    export_filtered_questions,
    preview_filtered_questions,
)
from kad_collector.desktop_models import (
    ClassificationValue,
    DesktopFilterSet,
    DesktopImportMetadata,
    QuestionClassification,
)
from kad_collector.desktop_server import DesktopApplication
from kad_collector.import_readiness import diagnose_import_readiness
from kad_collector.models import Alternative, QuestionRecord


def complete_question(**changes: object) -> QuestionRecord:
    values: dict[str, object] = {
        "number": 1,
        "statement": "Sobre competência tributária, assinale a alternativa correta.",
        "alternatives": [
            Alternative(letter="A", text="Primeira alternativa válida."),
            Alternative(letter="B", text="Segunda alternativa válida."),
            Alternative(letter="C", text="Terceira alternativa válida."),
        ],
        "discipline": "Direito Tributário",
        "matter": "Sistema Tributário Nacional",
        "subject": "Competência Tributária",
        "board": "FGV",
        "organization": "Receita Federal do Brasil",
        "concurso": "Concurso Fiscal",
        "role": "Auditor",
        "year": 2023,
        "level": "Superior",
        "source_pages": [2],
        "answer_status": "matched",
        "correct_answer": "B",
    }
    values.update(changes)
    return QuestionRecord.model_validate(values)


def diagnose(question: QuestionRecord, **changes: object):
    values: dict[str, object] = {
        "source_document": "prova-tipo-1.pdf",
        "provider": "fgv_conhecimento",
        "source_url": "https://example.gov.br/prova.pdf",
        "document_sha256": "a" * 64,
        "flags": [],
        "document_warnings": [],
        "semantic_resolution": "new_identity",
    }
    values.update(changes)
    return diagnose_import_readiness(question, **values)


class ImportReadinessDiagnosisTests(unittest.TestCase):
    def test_complete_question_is_importable_without_explanation_or_difficulty(self) -> None:
        result = diagnose(complete_question())

        self.assertTrue(result.importable)
        self.assertEqual(result.issues, [])

    def test_missing_classification_explains_what_why_how_and_source(self) -> None:
        result = diagnose(complete_question(matter=None, subject=None))

        self.assertFalse(result.importable)
        issue = next(item for item in result.issues if item.code == "missing_classification")
        self.assertEqual(issue.missing, ["matéria", "assunto"])
        self.assertIn("Matéria", issue.what)
        self.assertIn("taxonomia", issue.why)
        self.assertIn("classificação", issue.how_to_resolve)
        self.assertEqual(issue.source_document, "prova-tipo-1.pdf")

    def test_quality_blocks_have_stable_distinct_reasons(self) -> None:
        cases = [
            (
                complete_question(answer_status="missing", correct_answer=None),
                {},
                "missing_official_answer",
            ),
            (
                complete_question(alternatives=[
                    Alternative(letter="A", text="Primeira alternativa."),
                    Alternative(letter="C", text="Alternativa fora de sequência."),
                ], correct_answer="A"),
                {},
                "invalid_alternatives",
            ),
            (complete_question(), {"flags": ["duplicate"]}, "unresolved_duplicate"),
            (complete_question(), {"source_url": None}, "unproved_origin"),
            (
                complete_question(),
                {"document_warnings": ["associação de gabarito ambígua: a.pdf, b.pdf"]},
                "ambiguous_association",
            ),
            (
                complete_question(),
                {"semantic_resolution": "uncertain"},
                "version_conflict",
            ),
        ]

        for question, context, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result = diagnose(question, **context)
                self.assertFalse(result.importable)
                self.assertIn(expected_code, [item.code for item in result.issues])

    def test_diagnosis_deduplicates_answer_problem_and_keeps_source(self) -> None:
        result = diagnose(
            complete_question(answer_status="missing", correct_answer=None),
            flags=["without_answer"],
            document_warnings=["3 questões ficaram sem resposta oficial"],
        )

        self.assertEqual(
            [item.code for item in result.issues].count("missing_official_answer"), 1
        )
        self.assertTrue(all(item.source_document == "prova-tipo-1.pdf" for item in result.issues))


class ImportReadinessStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        pdf_path = root / "prova-oficial.pdf"
        document = canvas.Canvas(str(pdf_path))
        document.drawString(50, 800, "Prova oficial")
        document.showPage()
        document.save()
        self.application = DesktopApplication(root / "data")
        job_id = self.application.store.create_job(
            [pdf_path],
            DesktopImportMetadata(
                provider="banca_oficial",
                source_url="https://example.gov.br/prova.pdf",
                board="FGV",
                concurso="Concurso Fiscal",
                year=2023,
                role="Auditor",
                organization="Receita Federal do Brasil",
                level="Superior",
            ),
            "local",
        )
        self.document = self.application.store.documents_for_job(job_id)[0]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def save(
        self,
        number: int,
        *,
        classification: QuestionClassification | None = None,
        **changes: object,
    ) -> str:
        question = complete_question(
            number=number,
            statement=f"Questão número {number} com enunciado completo e distinto.",
            **changes,
        )
        return self.application.store.save_question(
            self.document["id"], question, classification or QuestionClassification()
        )

    def test_filters_separate_importable_unclassified_and_blocked(self) -> None:
        importable_id = self.save(1)
        unclassified_id = self.save(2, discipline=None, matter=None, subject=None)
        blocked_id = self.save(3, answer_status="missing", correct_answer=None)

        importable = self.application.store.query(
            DesktopFilterSet(readiness_states=["importable"])
        )
        unclassified = self.application.store.query(
            DesktopFilterSet(readiness_states=["unclassified"])
        )
        blocked = self.application.store.query(
            DesktopFilterSet(readiness_states=["blocked"])
        )

        self.assertEqual([item["id"] for item in importable["questions"]], [importable_id])
        self.assertEqual([item["id"] for item in unclassified["questions"]], [unclassified_id])
        self.assertEqual(
            {item["id"] for item in blocked["questions"]},
            {unclassified_id, blocked_id},
        )

    def test_block_reason_facet_summary_and_individual_diagnosis_share_codes(self) -> None:
        first_id = self.save(1, matter=None, subject=None)
        self.save(2, answer_status="missing", correct_answer=None)

        result = self.application.store.query(
            DesktopFilterSet(block_reasons=["missing_classification"])
        )

        self.assertEqual([item["id"] for item in result["questions"]], [first_id])
        self.assertEqual(
            result["summary"]["import_block_reasons"],
            {"missing_classification": 1, "missing_official_answer": 1},
        )
        self.assertEqual(
            result["questions"][0]["import_diagnosis"]["issues"][0]["code"],
            "missing_classification",
        )
        self.assertTrue(result["facets"]["block_reasons"])


class AssistedBatchReviewTests(ImportReadinessStoreTests):
    @staticmethod
    def classification(evidence: str = "regra semântica local") -> QuestionClassification:
        def value(item: str) -> ClassificationValue:
            return ClassificationValue(
                value=item,
                confidence=0.82,
                evidence=evidence,
                source="local_semantic_rule",
                reason="termos específicos encontrados",
                provenance=["taxonomia:2.0.0"],
            )

        return QuestionClassification(
            discipline=value("Direito Tributário"),
            subject=value("Sistema Tributário Nacional"),
            topic=value("Competência Tributária"),
        )

    def test_confirmation_is_explicit_audited_and_preserves_protected_fields(self) -> None:
        classification = self.classification()
        first_id = self.save(1, classification=classification)
        second_id = self.save(2, classification=classification)
        with closing(self.application.store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET status='approved', reviewer='humana', "
                "review_notes='Decisão preservada.' WHERE id=?",
                (first_id,),
            )
            connection.commit()
        before = {
            question_id: self.application.store.question(question_id)
            for question_id in (first_id, second_id)
        }

        preview = self.application.store.preview_classification_batch(
            [first_id, second_id]
        )
        self.assertEqual(preview["count"], 2)
        self.assertEqual(
            preview["suggestion"],
            {
                "discipline": "Direito Tributário",
                "matter": "Sistema Tributário Nacional",
                "subject": "Competência Tributária",
            },
        )
        with self.assertRaisesRegex(ValueError, "confirmação explícita"):
            self.application.store.confirm_classification_batch(
                [first_id, second_id], confirmation_token="", actor="operador_local"
            )

        result = self.application.store.confirm_classification_batch(
            [first_id, second_id],
            confirmation_token=preview["confirmationToken"],
            actor="operador_local",
        )

        self.assertEqual(result["updated"], 2)
        for question_id in (first_id, second_id):
            after = self.application.store.question(question_id)
            self.assertEqual(
                after["question"]["correct_answer"],
                before[question_id]["question"]["correct_answer"],
            )
            self.assertEqual(
                after["question"]["answer_status"],
                before[question_id]["question"]["answer_status"],
            )
            self.assertEqual(after["status"], before[question_id]["status"])
            self.assertEqual(after["reviewer"], before[question_id]["reviewer"])
            self.assertEqual(after["review_notes"], before[question_id]["review_notes"])
            self.assertEqual(after["classification"]["discipline"]["source"], "human_review")
            self.assertEqual(
                self.application.store.audit_log(question_id)[0]["action"],
                "classification_batch_confirmed",
            )

    def test_mixed_evidence_and_stale_confirmation_are_rejected_atomically(self) -> None:
        first_id = self.save(1, classification=self.classification("evidência A"))
        second_id = self.save(2, classification=self.classification("evidência B"))
        with self.assertRaisesRegex(ValueError, "mesma sugestão e evidência"):
            self.application.store.preview_classification_batch([first_id, second_id])

        with closing(self.application.store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET classification_json=? WHERE id=?",
                (
                    self.classification("evidência A").model_dump_json(),
                    second_id,
                ),
            )
            connection.commit()
        preview = self.application.store.preview_classification_batch([first_id, second_id])
        with closing(self.application.store._connect()) as connection:
            changed = self.classification("evidência alterada").model_dump_json()
            connection.execute(
                "UPDATE questions SET classification_json=? WHERE id=?", (changed, second_id)
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "fila mudou"):
            self.application.store.confirm_classification_batch(
                [first_id, second_id],
                confirmation_token=preview["confirmationToken"],
                actor="operador_local",
            )
        self.assertEqual(self.application.store.audit_log(first_id), [])

    def test_batch_decision_can_be_reverted_without_overwriting_later_changes(self) -> None:
        first_id = self.save(1, classification=self.classification())
        second_id = self.save(2, classification=self.classification())
        before = self.application.store.question(first_id)["classification"]
        preview = self.application.store.preview_classification_batch([first_id, second_id])
        result = self.application.store.confirm_classification_batch(
            [first_id, second_id],
            confirmation_token=preview["confirmationToken"],
            actor="operador_local",
        )

        reverted = self.application.store.revert_classification_batch(
            result["batchId"], actor="operador_local"
        )

        self.assertEqual(reverted["reverted"], 2)
        self.assertEqual(self.application.store.question(first_id)["classification"], before)
        self.assertEqual(
            self.application.store.audit_log(first_id)[0]["action"],
            "classification_batch_reverted",
        )
        with self.assertRaisesRegex(ValueError, "já foi corrigido"):
            self.application.store.revert_classification_batch(
                result["batchId"], actor="operador_local"
            )

    def test_reversion_stops_before_overwriting_a_later_human_classification(self) -> None:
        first_id = self.save(1, classification=self.classification())
        second_id = self.save(2, classification=self.classification())
        preview = self.application.store.preview_classification_batch([first_id, second_id])
        result = self.application.store.confirm_classification_batch(
            [first_id, second_id],
            confirmation_token=preview["confirmationToken"],
            actor="operador_local",
        )
        later = self.classification().model_copy(
            update={
                "discipline": ClassificationValue(
                    value="Direito Tributário",
                    confidence=1,
                    evidence="Correção posterior conferida no PDF.",
                    source="human_review",
                    reason="Decisão humana posterior.",
                )
            }
        )
        with closing(self.application.store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET classification_json=? WHERE id=?",
                (later.model_dump_json(), first_id),
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "mudou após o lote"):
            self.application.store.revert_classification_batch(
                result["batchId"], actor="operador_local"
            )

        self.assertEqual(
            self.application.store.question(first_id)["classification"]["discipline"][
                "reason"
            ],
            "Decisão humana posterior.",
        )
        self.assertEqual(
            self.application.store.question(second_id)["classification"]["discipline"][
                "source"
            ],
            "human_review",
        )


class ExportPreviewTests(ImportReadinessStoreTests):
    def test_preview_matches_export_selection_without_writing_or_changing_status(self) -> None:
        ready = {
            "difficulty": "Média",
            "explanation": "A alternativa B corresponde ao gabarito oficial conferido.",
        }
        approved_id = self.save(1, **ready)
        pending_id = self.save(2, **ready)
        self.application.store.decide_question(
            approved_id, "approved", actor="operador_local", notes=None
        )
        before = {
            approved_id: self.application.store.question(approved_id)["status"],
            pending_id: self.application.store.question(pending_id)["status"],
        }
        output = Path(self.temporary.name) / "preview-must-not-write"

        preview = preview_filtered_questions(self.application.store, DesktopFilterSet())

        self.assertEqual(preview.selected, 2)
        self.assertEqual(preview.included_count, 1)
        self.assertEqual(preview.answer_key_summary, {"official": 2})
        self.assertEqual(preview.answer_key_diagnostics, {})
        self.assertEqual(preview.questions[0]["questionId"], approved_id)
        self.assertTrue(
            any(
                "questão ainda não aprovada" in reason
                for reason in preview.exclusion_reasons
            )
        )
        self.assertFalse(output.exists())
        self.assertEqual(
            {
                approved_id: self.application.store.question(approved_id)["status"],
                pending_id: self.application.store.question(pending_id)["status"],
            },
            before,
        )

        result = export_filtered_questions(
            self.application.store, DesktopFilterSet(), output_root=output
        )
        records = [
            json.loads(line)
            for line in result.questions_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(result.exported_count, preview.included_count)
        self.assertEqual(records[0]["data"]["statement"], preview.questions[0]["statement"])

    def test_preview_reports_and_blocks_a_missing_official_answer(self) -> None:
        self.save(1, answer_status="missing", correct_answer=None)

        preview = preview_filtered_questions(self.application.store, DesktopFilterSet())

        self.assertEqual(preview.selected, 1)
        self.assertEqual(preview.included_count, 0)
        self.assertEqual(preview.answer_key_summary, {"missing": 1})
        self.assertEqual(
            preview.answer_key_diagnostics,
            {"answer_key_diagnosis_pending": 1},
        )


if __name__ == "__main__":
    unittest.main()
