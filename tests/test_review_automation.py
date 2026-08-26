from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from kad_collector.answer_key import parse_answer_key
from kad_collector.json_utils import read_json, write_json
from kad_collector.models import (
    Alternative,
    DocumentRecord,
    ExtractedDocument,
    ExtractedPage,
    ExtractionManifest,
    PromotionPackage,
    QuestionBatch,
    QuestionRecord,
    ValidationState,
)
from kad_collector.promotion import (
    build_promotion_package,
    dry_run_promotion,
    verify_promotion_package,
)
from kad_collector.review import approve_batch_model
from kad_collector.review_queue import prepare_review_queue


def document(kind: str, title: str, url: str, digest: str) -> DocumentRecord:
    return DocumentRecord(
        source_id="fuvest_vestibular",
        source_name="FUVEST",
        document_type=kind,  # type: ignore[arg-type]
        title=title,
        original_url=url,
        resolved_url=url,
        local_path=f"data/raw/{digest}.pdf",
        sha256=digest * 64,
        content_type="application/pdf",
        size_bytes=100,
        downloaded_at=datetime.now(UTC),
        authorization_basis="Acervo oficial publico.",
        metadata={"banca": "FUVEST", "orgao": "USP", "ano": "2026"},
    )


def question(number: int) -> QuestionRecord:
    return QuestionRecord(
        number=number,
        statement=f"Enunciado {number}",
        alternatives=[
            Alternative(letter="A", text="Alternativa A"),
            Alternative(letter="B", text="Alternativa B"),
            Alternative(letter="C", text="Alternativa C"),
            Alternative(letter="D", text="Alternativa D"),
        ],
        matter="Conhecimentos Gerais",
        subject="Teste",
        discipline="Conhecimentos Gerais",
        board="FUVEST",
        organization="USP",
        concurso="FUVEST 2026",
        role="Vestibular",
        year=2026,
        level="Superior",
        difficulty="Média",
        explanation="A resposta indicada decorre diretamente do enunciado apresentado.",
        source_pages=[1],
    )


class ReviewAutomationTests(unittest.TestCase):
    def _queue_with_single_key(
        self,
        root: Path,
        *,
        exam: DocumentRecord,
        answer_key: DocumentRecord,
        batch_id: str,
    ) -> tuple[QuestionBatch, list[str]]:
        extraction_path = root / f"{batch_id}-extraction.json"
        batch_path = root / f"{batch_id}.json"
        write_json(
            extraction_path,
            ExtractionManifest(created_at=datetime.now(UTC), documents=[]).model_dump(
                mode="json"
            ),
        )
        write_json(
            batch_path,
            QuestionBatch(
                batch_id=batch_id,
                created_at=datetime.now(UTC),
                model="fake-model",
                source_document=exam,
                questions=[question(1)],
                validation=ValidationState(valid=True),
            ).model_dump(mode="json"),
        )
        queue, _ = prepare_review_queue(
            extraction_path=extraction_path,
            batch_paths=[batch_path],
            data_dir=root,
            answer_key_documents=[
                ExtractedDocument(document=answer_key, pages=[], text="1 B")
            ],
        )
        reviewed = QuestionBatch.model_validate(read_json(Path(queue.items[0].batch_path)))
        return reviewed, queue.items[0].issues

    def test_variant_table_is_parsed_without_mixing_answer_keys(self) -> None:
        text = "PROVA V1 PROVA V2\n1 A 2 B 1 C 2 D\n3 * 4 A 3 B 4 C"
        first = parse_answer_key(text, variant="V1")
        second = parse_answer_key(text, variant="V2")

        self.assertEqual(first[1].answer, "A")
        self.assertEqual(first[2].answer, "B")
        self.assertTrue(first[3].annulled)
        self.assertEqual(second[1].answer, "C")
        self.assertEqual(second[4].answer, "C")

    def test_fgv_grid_is_selected_by_role_and_type(self) -> None:
        text = """
Cuidador – Tipo 1
1 2 3 4
A B C D
Cuidador – Tipo 2
1 2 3 4
B C * A
Professor de Sociologia – Tipo 2
1 2 3 4
D A B C
"""

        entries = parse_answer_key(text, variant="Tipo 2", role="Cuidador")

        self.assertEqual(entries[1].answer, "B")
        self.assertEqual(entries[2].answer, "C")
        self.assertTrue(entries[3].annulled)
        self.assertEqual(entries[4].answer, "A")

    def test_fgv_grid_accepts_turn_suffix_from_real_answer_key(self) -> None:
        text = """
Auditor-Fiscal da Receita Federal do Brasil – TIPO 1 (Manhã)
1 2 3 4
C B B A
Auditor-Fiscal da Receita Federal do Brasil – TIPO 2 (Tarde)
1 2 3 4
A D C B
"""

        entries = parse_answer_key(
            text,
            variant="Tipo 1",
            role="Auditor-Fiscal da Receita Federal do Brasil",
            turn="Manhã",
        )

        self.assertEqual([entries[number].answer for number in range(1, 5)], ["C", "B", "B", "A"])

        afternoon = parse_answer_key(
            text,
            variant="Tipo 2",
            role="Auditor-Fiscal da Receita Federal do Brasil",
            turn="Tarde",
        )
        self.assertEqual(
            [afternoon[number].answer for number in range(1, 5)], ["A", "D", "C", "B"]
        )

    def test_fgv_grid_inherits_standalone_shift_heading(self) -> None:
        text = """
MANHÃ
Auditor Fiscal - TIPO 1
1 2
A B
TARDE
Auditor Fiscal - TIPO 1
1 2
C D
"""

        morning = parse_answer_key(
            text, variant="Tipo 1", role="Auditor Fiscal", turn="Manhã"
        )
        afternoon = parse_answer_key(
            text, variant="Tipo 1", role="Auditor Fiscal", turn="Tarde"
        )

        self.assertEqual([morning[1].answer, morning[2].answer], ["A", "B"])
        self.assertEqual([afternoon[1].answer, afternoon[2].answer], ["C", "D"])

    def test_fgv_grid_returns_no_entries_when_requested_shift_is_absent(self) -> None:
        text = """
MANHÃ
Auditor Fiscal - TIPO 1
1 2
A B
"""

        entries = parse_answer_key(
            text, variant="Tipo 1", role="Auditor Fiscal", turn="Tarde"
        )

        self.assertEqual(entries, {})

    def test_fgv_vertical_grid_is_selected_by_role(self) -> None:
        text = """
Auditor Fiscal - 1 - Turno Manhã
1
C
2
D
3
*
Analista Tributário - 1 - Turno Manhã
1
A
2
B
3
C
"""

        entries = parse_answer_key(
            text,
            variant="Tipo 1",
            role="Auditor Fiscal",
            turn="Manhã",
        )

        self.assertEqual(entries[1].answer, "C")
        self.assertEqual(entries[2].answer, "D")
        self.assertTrue(entries[3].annulled)

    def test_queue_matches_cached_answers_and_creates_local_review_session(self) -> None:
        exam = document(
            "exam",
            "Prova 2026 V1",
            "https://www.fuvest.br/wp-content/fuvest2026-fase1-prova-V1.pdf",
            "a",
        ).model_copy(update={"metadata": {
            "banca": "FUVEST", "concurso": "Vestibular", "ano": "2026",
            "cargo": "Vestibular", "orgao": "Universidade de Sao Paulo", "variant": "V1",
            "stage": "Primeira fase", "turn": "Manha",
        }})
        answer = document(
            "answer_key",
            "Gabaritos de Provas da 1a fase",
            "https://www.fuvest.br/wp-content/fuvest2026-fase1-gabarito.pdf",
            "b",
        ).model_copy(update={"metadata": {
            "banca": "FUVEST", "concurso": "Vestibular", "ano": "2026",
            "cargo": "Vestibular", "orgao": "Universidade de Sao Paulo", "variant": "V1",
            "stage": "Primeira fase", "turn": "Manha",
        }})
        other_answer = document(
            "answer_key",
            "Gabarito da primeira fase 2025",
            "https://www.fuvest.br/wp-content/uploads/fuvest2025_gabarito_primeira_fase.pdf",
            "d",
        )
        batch = QuestionBatch(
            batch_id="batch-fuvest-v1",
            created_at=datetime.now(UTC),
            model="fake-model",
            source_document=exam,
            questions=[question(1), question(2)],
            validation=ValidationState(valid=True),
        )
        answer_text = "PROVA V1 PROVA V2\n1 A 2 B 1 C 2 D"
        extraction = ExtractionManifest(
            created_at=datetime.now(UTC),
            documents=[],
        )
        cached_answers = [
            ExtractedDocument(
                document=other_answer,
                pages=[ExtractedPage(number=1, text="1 D\n2 D", character_count=7)],
                text="1 D\n2 D",
            ),
            ExtractedDocument(
                document=answer,
                pages=[ExtractedPage(number=1, text=answer_text, character_count=40)],
                text=answer_text,
            ),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extraction_path = root / "extraction.json"
            batch_path = root / "batch.json"
            write_json(extraction_path, extraction.model_dump(mode="json"))
            write_json(batch_path, batch.model_dump(mode="json"))
            queue, queue_path = prepare_review_queue(
                extraction_path=extraction_path,
                batch_paths=[batch_path],
                data_dir=root,
                answer_key_documents=cached_answers,
            )
            reviewed = QuestionBatch.model_validate(read_json(Path(queue.items[0].batch_path)))

            self.assertTrue(queue_path.exists())
            self.assertTrue(Path(queue.items[0].session_path).exists())
            self.assertEqual(queue.items[0].status, "ready")
            self.assertEqual(queue.items[0].matched_answers, 2)
            self.assertEqual(reviewed.questions[0].correct_answer, "A")
            self.assertEqual(reviewed.questions[1].correct_answer, "B")

    def test_promotion_package_is_local_deterministic_and_tamper_evident(self) -> None:
        exam = document(
            "exam",
            "Prova 2026 V1",
            "https://www.fuvest.br/wp-content/fuvest2026-fase1-prova-V1.pdf",
            "c",
        )
        first = question(1)
        first.correct_answer = "A"
        first.answer_status = "matched"
        batch = QuestionBatch(
            batch_id="batch-approved",
            created_at=datetime.now(UTC),
            model="fake-model",
            source_document=exam,
            questions=[first],
            validation=ValidationState(valid=True),
        )
        approved = approve_batch_model(batch, "revisor.teste")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_path = root / "approved.json"
            package_path = root / "promotion.json"
            write_json(batch_path, approved.model_dump(mode="json"))
            package, _ = build_promotion_package([batch_path], package_path)
            dry_run = dry_run_promotion(package_path)

            self.assertFalse(dry_run.executed)
            self.assertEqual(dry_run.question_count, 1)
            self.assertEqual(dry_run.content_sha256, package.content_sha256)

            raw = json.loads(package_path.read_text(encoding="utf-8"))
            raw["batches"][0]["questions"][0]["statement"] = "Conteudo adulterado"
            tampered = PromotionPackage.model_validate(raw)
            with self.assertRaisesRegex(ValueError, "conteudo mudou"):
                verify_promotion_package(tampered)

    def test_queue_matches_semantically_compatible_key_from_another_source(self) -> None:
        exam = document(
            "exam",
            "Concurso Fiscal 2026 - Analista Tributario - V1",
            "https://exam-source.test/prova-v1.pdf",
            "e",
        ).model_copy(
            update={
                "source_id": "exam_source",
                "metadata": {
                    "banca": "Banca Ficticia",
                    "orgao": "Secretaria da Fazenda",
                    "ano": "2026",
                    "concurso": "Concurso Fiscal",
                    "cargo": "Analista Tributario",
                    "variant": "V1",
                    "stage": "Prova objetiva",
                    "turn": "Manha",
                },
            }
        )
        compatible = document(
            "answer_key",
            "Gabarito definitivo Concurso Fiscal 2026 Analista Tributario V1",
            "https://answers-source.test/gabarito-analista-2026-v1.pdf",
            "f",
        ).model_copy(
            update={
                "source_id": "official_answers_source",
                "metadata": {
                    "banca": "Banca Ficticia",
                    "orgao": "Secretaria da Fazenda",
                    "ano": "2026",
                    "concurso": "Concurso Fiscal",
                    "cargo": "Analista Tributario",
                    "variant": "V1",
                    "stage": "Prova objetiva",
                    "turn": "Manha",
                },
            }
        )
        incompatible = document(
            "answer_key",
            "Gabarito Professor 2025 V2",
            "https://another-source.test/gabarito-professor-2025-v2.pdf",
            "9",
        ).model_copy(
            update={
                "source_id": "another_answers_source",
                "metadata": {
                    "banca": "Outra Banca",
                    "orgao": "Secretaria da Educacao",
                    "ano": "2025",
                    "concurso": "Concurso Educacao",
                    "cargo": "Professor",
                    "variant": "V2",
                },
            }
        )
        batch = QuestionBatch(
            batch_id="batch-cross-source",
            created_at=datetime.now(UTC),
            model="fake-model",
            source_document=exam,
            questions=[question(1)],
            validation=ValidationState(valid=True),
        )
        candidates = [
            ExtractedDocument(document=incompatible, pages=[], text="1 D"),
            ExtractedDocument(document=compatible, pages=[], text="1 B"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extraction_path = root / "extraction.json"
            batch_path = root / "batch.json"
            write_json(
                extraction_path,
                ExtractionManifest(created_at=datetime.now(UTC), documents=[]).model_dump(
                    mode="json"
                ),
            )
            write_json(batch_path, batch.model_dump(mode="json"))

            queue, _ = prepare_review_queue(
                extraction_path=extraction_path,
                batch_paths=[batch_path],
                data_dir=root,
                answer_key_documents=candidates,
            )
            reviewed = QuestionBatch.model_validate(read_json(Path(queue.items[0].batch_path)))

        self.assertEqual(reviewed.questions[0].correct_answer, "B")
        self.assertEqual(reviewed.answer_key_document, compatible)
        self.assertEqual(queue.items[0].matched_answers, 1)

    def test_queue_blocks_equal_answer_key_candidates_as_ambiguous(self) -> None:
        exam = document(
            "exam",
            "Concurso Fiscal 2026 Analista V1",
            "https://exam-source.test/prova-v1.pdf",
            "1",
        ).model_copy(update={"source_id": "exam_source", "metadata": {
            "banca": "Banca Fiscal", "concurso": "Concurso Fiscal", "ano": "2026",
            "cargo": "Analista", "variant": "V1", "stage": "Prova objetiva",
            "turn": "Manha",
        }})
        first = document(
            "answer_key",
            "Gabarito Concurso Fiscal 2026 Analista V1",
            "https://first-source.test/gabarito-v1.pdf",
            "2",
        ).model_copy(update={"source_id": "first_answers_source", "metadata": {
            "banca": "Banca Fiscal", "concurso": "Concurso Fiscal", "ano": "2026",
            "cargo": "Analista", "variant": "V1", "stage": "Prova objetiva",
            "turn": "Manha",
        }})
        second = first.model_copy(
            update={
                "source_id": "second_answers_source",
                "local_path": "data/raw/second.pdf",
                "sha256": "3" * 64,
            }
        )
        batch = QuestionBatch(
            batch_id="batch-ambiguous",
            created_at=datetime.now(UTC),
            model="fake-model",
            source_document=exam,
            questions=[question(1)],
            validation=ValidationState(valid=True),
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extraction_path = root / "extraction.json"
            batch_path = root / "batch.json"
            write_json(
                extraction_path,
                ExtractionManifest(created_at=datetime.now(UTC), documents=[]).model_dump(
                    mode="json"
                ),
            )
            write_json(batch_path, batch.model_dump(mode="json"))
            queue, _ = prepare_review_queue(
                extraction_path=extraction_path,
                batch_paths=[batch_path],
                data_dir=root,
                answer_key_documents=[
                    ExtractedDocument(document=first, pages=[], text="1 A"),
                    ExtractedDocument(document=second, pages=[], text="1 B"),
                ],
            )
            reviewed = QuestionBatch.model_validate(read_json(Path(queue.items[0].batch_path)))

        self.assertIsNone(reviewed.answer_key_document)
        self.assertIsNone(reviewed.questions[0].correct_answer)
        self.assertEqual(reviewed.questions[0].answer_status, "missing")
        self.assertTrue(any("ambigua" in issue for issue in queue.items[0].issues))

    def test_queue_rejects_sole_key_supported_only_by_candidate_weak_signals(self) -> None:
        exam = document(
            "exam",
            "Prova de Direito",
            "https://exam-source.test/direito.pdf",
            "4",
        ).model_copy(update={"metadata": {}})
        unrelated = document(
            "answer_key",
            "Gabarito definitivo de Quimica",
            "https://answers-source.test/quimica.pdf",
            "5",
        ).model_copy(update={"metadata": {}})

        with tempfile.TemporaryDirectory() as temporary:
            reviewed, issues = self._queue_with_single_key(
                Path(temporary),
                exam=exam,
                answer_key=unrelated,
                batch_id="batch-unrelated-weak-signals",
            )

        self.assertIsNone(reviewed.answer_key_document)
        self.assertIsNone(reviewed.questions[0].correct_answer)
        self.assertEqual(reviewed.questions[0].answer_status, "missing")
        self.assertTrue(any("nenhum corresponde" in issue for issue in issues))

    def test_queue_rejects_one_shared_boilerplate_title_token(self) -> None:
        exam = document(
            "exam",
            "Concurso Direito",
            "https://exam-source.test/concurso-direito.pdf",
            "a",
        ).model_copy(update={"metadata": {}})
        unrelated = document(
            "answer_key",
            "Gabarito Concurso Quimica",
            "https://answers-source.test/concurso-quimica.pdf",
            "b",
        ).model_copy(update={"metadata": {}})

        with tempfile.TemporaryDirectory() as temporary:
            reviewed, issues = self._queue_with_single_key(
                Path(temporary),
                exam=exam,
                answer_key=unrelated,
                batch_id="batch-one-boilerplate-token",
            )

        self.assertIsNone(reviewed.answer_key_document)
        self.assertIsNone(reviewed.questions[0].correct_answer)
        self.assertEqual(reviewed.questions[0].answer_status, "missing")
        self.assertTrue(any("nenhum corresponde" in issue for issue in issues))

    def test_queue_rejects_known_year_and_variant_contradictions(self) -> None:
        exam = document(
            "exam",
            "Concurso Fiscal 2026 Analista V1 Manhã",
            "https://exam-source.test/fiscal-2026-v1.pdf",
            "6",
        ).model_copy(
            update={
                "metadata": {
                    "ano": "2026",
                    "concurso": "Concurso Fiscal",
                    "cargo": "Analista",
                    "orgao": "Secretaria da Fazenda",
                    "variant": "V1",
                    "stage": "Prova objetiva",
                    "turn": "Manha",
                }
            }
        )
        wrong_year = document(
            "answer_key",
            "Gabarito Concurso Fiscal 2025 Analista V1",
            "https://answers-source.test/fiscal-2025-v1.pdf",
            "7",
        ).model_copy(
            update={
                "metadata": {
                    "ano": "2025",
                    "concurso": "Concurso Fiscal",
                    "cargo": "Analista",
                    "orgao": "Secretaria da Fazenda",
                    "variant": "V1",
                    "stage": "Prova objetiva",
                    "turn": "Manha",
                }
            }
        )
        wrong_variant = document(
            "answer_key",
            "Gabarito Concurso Fiscal 2026 Analista V2",
            "https://answers-source.test/fiscal-2026-v2.pdf",
            "8",
        ).model_copy(
            update={
                "metadata": {
                    "ano": "2026",
                    "concurso": "Concurso Fiscal",
                    "cargo": "Analista",
                    "orgao": "Secretaria da Fazenda",
                    "variant": "V2",
                    "stage": "Prova objetiva",
                    "turn": "Manha",
                }
            }
        )
        wrong_role = document(
            "answer_key",
            "Gabarito Concurso Fiscal 2026 Auditor V1",
            "https://answers-source.test/fiscal-2026-auditor-v1.pdf",
            "9",
        ).model_copy(
            update={
                "metadata": {
                    "ano": "2026",
                    "concurso": "Concurso Fiscal",
                    "cargo": "Auditor",
                    "orgao": "Secretaria da Fazenda",
                    "variant": "V1",
                    "stage": "Prova objetiva",
                    "turn": "Manha",
                }
            }
        )
        wrong_turn = document(
            "answer_key",
            "Gabarito Concurso Fiscal 2026 Analista V1 Tarde",
            "https://answers-source.test/fiscal-2026-analista-v1-tarde.pdf",
            "a",
        ).model_copy(
            update={
                "metadata": {
                    "ano": "2026",
                    "concurso": "Concurso Fiscal",
                    "cargo": "Analista",
                    "orgao": "Secretaria da Fazenda",
                    "variant": "V1",
                    "stage": "Prova objetiva",
                    "turn": "Tarde",
                }
            }
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, candidate in (
                ("wrong-year", wrong_year),
                ("wrong-variant", wrong_variant),
                ("wrong-role", wrong_role),
                ("wrong-turn", wrong_turn),
            ):
                with self.subTest(label=label):
                    reviewed, issues = self._queue_with_single_key(
                        root / label,
                        exam=exam,
                        answer_key=candidate,
                        batch_id=f"batch-{label}",
                    )
                    self.assertIsNone(reviewed.answer_key_document)
                    self.assertIsNone(reviewed.questions[0].correct_answer)
                    self.assertEqual(reviewed.questions[0].answer_status, "missing")
                    self.assertTrue(any("nenhum corresponde" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
