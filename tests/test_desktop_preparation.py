from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kad_collector.desktop_models import (
    ClassificationValue,
    DesktopFilterSet,
    QuestionClassification,
)
from kad_collector.desktop_preparation import DesktopPreparationManager
from kad_collector.desktop_server import DesktopApplication
from kad_collector.desktop_store import DesktopStore
from kad_collector.models import Alternative, QuestionRecord

NOW = "2026-08-26T12:00:00+00:00"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _classification() -> QuestionClassification:
    def value(item: str | int) -> ClassificationValue:
        return ClassificationValue(value=item, confidence=1, evidence="fixture")

    return QuestionClassification(
        concurso=value("RFB22"),
        board=value("FGV"),
        year=value(2023),
        role=value("Analista"),
        organization=value("Receita Federal"),
        level=ClassificationValue(),
        discipline=ClassificationValue(),
        subject=ClassificationValue(),
        topic=ClassificationValue(),
        difficulty=ClassificationValue(),
    )


def _question(*, noisy: bool = False) -> QuestionRecord:
    return QuestionRecord(
        number=1,
        statement="Assinale a alternativa correta de acordo com a norma apresentada.",
        alternatives=[
            Alternative(letter="A", text="Errada\nCabeçalho seguinte" if noisy else "Errada"),
            Alternative(letter="B", text="Certa"),
        ],
        matter=None,
        subject=None,
        board="FGV",
        organization="Receita Federal",
        concurso="RFB22",
        role="Analista",
        year=2023,
        source_pages=[1],
        answer_status="matched",
        correct_answer="B",
    )


def _decision(
    booklet: str,
    *,
    year: object = 2023,
    exam_turns: list[str] | None = None,
    candidate_turns: list[str] | None = None,
) -> dict[str, object]:
    exam_turns = ["manhã"] if exam_turns is None else exam_turns
    candidate_turns = exam_turns if candidate_turns is None else candidate_turns
    comparisons = [
        {
            "field": name,
            "status": "matched",
            "exam_values": values,
            "candidate_values": values,
            "reason": "fixture",
        }
        for name, values in (
            ("board", ["fgv"]),
            ("concurso", ["rfb22"]),
            ("year", [year]),
            ("organization", ["receita federal"]),
            ("role", ["analista"]),
            ("stage", ["prova objetiva"]),
            ("variant", [f"tipo {booklet}"]),
            ("interval", [1, 1]),
        )
    ]
    comparisons.append(
        {
            "field": "turn",
            "status": "matched",
            "exam_values": exam_turns,
            "candidate_values": candidate_turns,
            "reason": (
                "turno derivado do único turno declarado pelo gabarito"
                if not exam_turns and len(candidate_turns) == 1
                else "prova e gabarito não separam a aplicação por turno"
                if not exam_turns and not candidate_turns
                else "fixture"
            ),
        }
    )
    return {
        "outcome": "selected",
        "selected_version_id": "key-version",
        "assessments": [
            {
                "version_id": "key-version",
                "compatible": True,
                "score": 100,
                "matched_fields": [item["field"] for item in comparisons],
                "conflicts": [],
                "incomplete_fields": [],
                "comparisons": comparisons,
                "reasons": ["fixture"],
            }
        ],
        "minimum_score": 1,
        "minimum_margin": 1,
        "achieved_margin": None,
        "reason": "fixture selecionada",
        "algorithm_version": "semantic-association-v3",
    }


def _seed(
    root: Path,
    *,
    include_review: bool = False,
    decision_year: object = 2023,
    exam_turns: list[str] | None = None,
    candidate_turns: list[str] | None = None,
    answer_key_state: str = "definitive",
) -> DesktopStore:
    store = DesktopStore(root / "collector.sqlite3")
    profile = {
        "identity": {
            "roles": {"normalized_values": ["analista"]},
            "stage": {"normalized_values": ["prova objetiva"]},
            "turns": {"normalized_values": []},
            "variants": {"normalized_values": ["tipo 1"]},
        }
    }
    with closing(store._connect()) as connection:
        connection.execute(
            "INSERT INTO jobs (id,created_at,updated_at,status,classifier_provider) "
            "VALUES ('job',?,?,'completed','local')",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO semantic_identities "
            "(identity_key,schema_version,algorithm_version,identity_json,evidence_json,"
            "created_at,updated_at) VALUES ('identity',1,'fixture','{}','{}',?,?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO document_versions (id,identity_key,document_role,answer_key_state,"
            "coverage_json,profile_json,content_sha256,content_normalizer_version,version_number,"
            "created_at,updated_at) VALUES "
            "('key-version','identity','answer_key',?,'{}','{}',"
            "'key-sha','fixture',1,?,?)",
            (answer_key_state, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO documents (id,job_id,local_path,filename,sha256,status,metadata_json,"
            "warnings_json,created_at,updated_at,document_version_id,semantic_resolution) "
            "VALUES ('key-document','job','key.pdf','key.pdf','key-sha','processed',?,'[]',?,?,"
            "'key-version','new_identity')",
            (
                _json(
                    {
                        "document_type": "answer_key",
                        "source_url": "https://example.test/key",
                    }
                ),
                NOW,
                NOW,
            ),
        )
        for booklet in ("1", "2"):
            version_id = f"exam-version-{booklet}"
            document_id = f"exam-document-{booklet}"
            link_id = f"link-{booklet}"
            connection.execute(
                "INSERT INTO document_versions (id,identity_key,document_role,answer_key_state,"
                "coverage_json,profile_json,content_sha256,content_normalizer_version,"
                "version_number,created_at,updated_at) "
                "VALUES (?,?,'exam','unknown','{}',?,?,'fixture',?,?,?)",
                (
                    version_id,
                    "identity",
                    _json(profile),
                    booklet * 64,
                    int(booklet),
                    NOW,
                    NOW,
                ),
            )
            metadata = {
                "provider": "fgv",
                "document_type": "exam",
                "source_url": f"https://example.test/exam-{booklet}",
                "variant": f"Tipo {booklet}",
                "concurso": "RFB22",
                "board": "FGV",
                "year": 2023,
                "role": "Analista",
                "stage": "Prova objetiva",
            }
            connection.execute(
                "INSERT INTO documents (id,job_id,local_path,filename,sha256,status,metadata_json,"
                "warnings_json,created_at,updated_at,document_version_id,semantic_resolution) "
                "VALUES (?,'job',?,?,?,'processed',?,'[]',?,?,?,'new_identity')",
                (
                    document_id,
                    f"exam-{booklet}.pdf",
                    f"exam-{booklet}.pdf",
                    booklet * 64,
                    _json(metadata),
                    NOW,
                    NOW,
                    version_id,
                ),
            )
            connection.execute(
                "INSERT INTO document_links (id,exam_version_id,answer_key_version_id,status,"
                "decision_json,algorithm_version,created_at,updated_at) "
                "VALUES (?,?,'key-version','active',?,'semantic-association-v3',?,?)",
                (
                    link_id,
                    version_id,
                    _json(
                        _decision(
                            booklet,
                            year=decision_year,
                            exam_turns=exam_turns,
                            candidate_turns=candidate_turns,
                        )
                    ),
                    NOW,
                    NOW,
                ),
            )
        connection.commit()
    for booklet in ("1", "2"):
        question_id = store.save_question(
            f"exam-document-{booklet}", _question(noisy=booklet == "2"), _classification()
        )
        with closing(store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET answer_key_link_id=? WHERE id=?",
                (f"link-{booklet}", question_id),
            )
            connection.commit()
    if include_review:
        with closing(store._connect()) as connection:
            connection.execute(
                "INSERT INTO association_review_queue "
                "(exam_version_id,status,reason,candidates_json,created_at,updated_at) "
                "VALUES ('exam-version-1','pending','campos incompletos: turn',?, ?, ?)",
                (_json([_decision("1")["assessments"][0]]), NOW, NOW),
            )
            connection.commit()
    return store


class DesktopPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_preview_is_passive_and_run_creates_one_main_copy(self) -> None:
        store = _seed(self.root)
        manager = DesktopPreparationManager(store)

        preview = manager.preview()
        with closing(store._connect()) as connection:
            before = connection.execute("SELECT COUNT(*) FROM canonical_questions").fetchone()[0]
        result = manager.run()
        repeated = manager.run()

        self.assertEqual(before, 0)
        self.assertEqual(preview["qwenEligible"], 1)
        self.assertEqual(result["qwenEligible"], 1)
        self.assertEqual(result["mainQuestions"], 1)
        self.assertEqual(result["duplicateQuestions"], 1)
        self.assertEqual(result["conflictQuestions"], 0)
        self.assertEqual(result["pendingQuestions"], 0)
        self.assertEqual(repeated["canonicalQuestions"], 1)
        self.assertEqual(repeated["equivalence"]["canonicalQuestions"], 1)
        self.assertEqual(store.query(DesktopFilterSet())["total"], 1)
        self.assertEqual(
            store.query(
                DesktopFilterSet(), include_equivalent_copies=True
            )["total"],
            2,
        )
        self.assertEqual(len(store.export_candidates(DesktopFilterSet())), 1)
        self.assertTrue(Path(result["backupPath"]).is_file())

    def test_preparation_uses_turn_derived_from_unique_definitive_key(self) -> None:
        store = _seed(self.root, exam_turns=[], candidate_turns=["manhã"])

        result = DesktopPreparationManager(store).run()

        self.assertEqual(result["identifiedExams"], 2)
        self.assertEqual(result["canonicalQuestions"], 1)
        self.assertEqual(result["skipped"], [])
        with closing(store._connect()) as connection:
            shift = connection.execute(
                "SELECT official_name,evidence_json FROM application_shifts"
            ).fetchone()
        self.assertEqual(shift["official_name"], "manhã")
        evidence = json.loads(shift["evidence_json"])
        self.assertEqual(
            evidence["turnResolution"]["source"],
            "derived_from_unique_definitive_answer_key",
        )

    def test_preparation_uses_not_applicable_without_turn_partition(self) -> None:
        store = _seed(self.root, exam_turns=[], candidate_turns=[])

        result = DesktopPreparationManager(store).run()

        self.assertEqual(result["skipped"], [])
        with closing(store._connect()) as connection:
            shift = connection.execute(
                "SELECT official_name,evidence_json FROM application_shifts"
            ).fetchone()
        self.assertEqual(shift["official_name"], "não se aplica")
        evidence = json.loads(shift["evidence_json"])
        self.assertEqual(evidence["turnResolution"]["source"], "not_applicable")

    def test_preparation_blocks_multiple_candidate_turns(self) -> None:
        manager = DesktopPreparationManager(
            _seed(self.root, exam_turns=[], candidate_turns=["manhã", "tarde"])
        )

        result = manager.run()

        self.assertEqual(result["identifiedExams"], 0)
        self.assertEqual(result["skipped"][0]["missingFields"], ["turn"])
        self.assertEqual(result["canonicalQuestions"], 0)

    def test_preparation_does_not_derive_turn_from_preliminary_key(self) -> None:
        manager = DesktopPreparationManager(
            _seed(
                self.root,
                exam_turns=[],
                candidate_turns=["manhã"],
                answer_key_state="preliminary",
            )
        )

        result = manager.run()

        self.assertEqual(result["identifiedExams"], 0)
        self.assertEqual(result["skipped"][0]["missingFields"], ["turn"])

    def test_newly_resolved_scope_refreshes_existing_occurrences(self) -> None:
        store = _seed(self.root, exam_turns=[], candidate_turns=["manhã", "tarde"])
        manager = DesktopPreparationManager(store)
        first = manager.run()
        with closing(store._connect()) as connection:
            unresolved_before = connection.execute(
                "SELECT COUNT(*) FROM question_occurrences WHERE scope_id IS NULL"
            ).fetchone()[0]
            for booklet in ("1", "2"):
                connection.execute(
                    "UPDATE document_links SET decision_json=? WHERE id=?",
                    (
                        _json(
                            _decision(
                                booklet,
                                exam_turns=[],
                                candidate_turns=["manhã"],
                            )
                        ),
                        f"link-{booklet}",
                    ),
                )
            connection.commit()

        second = manager.run()

        self.assertEqual(first["canonicalQuestions"], 0)
        self.assertEqual(unresolved_before, 2)
        self.assertEqual(second["canonicalQuestions"], 1)
        self.assertEqual(second["occurrences"], 2)
        with closing(store._connect()) as connection:
            unresolved_after = connection.execute(
                "SELECT COUNT(*) FROM question_occurrences WHERE scope_id IS NULL"
            ).fetchone()[0]
        self.assertEqual(unresolved_after, 0)

    def test_unprepared_question_does_not_claim_an_unconfirmed_group(self) -> None:
        store = _seed(
            self.root, exam_turns=[], candidate_turns=["manhã", "tarde"]
        )
        DesktopPreparationManager(store).run()
        with closing(store._connect()) as connection:
            connection.execute("UPDATE questions SET flags_json='[\"duplicate\"]'")
            connection.commit()

        result = store.query(DesktopFilterSet(), include_equivalent_copies=True)

        issue_codes = {
            issue["code"]
            for item in result["questions"]
            for issue in item["import_diagnosis"]["issues"]
        }
        self.assertIn("canonical_preparation_pending", issue_codes)
        self.assertNotIn("unresolved_duplicate", issue_codes)

    def test_pending_association_points_to_document_identity_review(self) -> None:
        manager = DesktopPreparationManager(_seed(self.root, include_review=True))

        summary = manager.summary()

        self.assertEqual(summary["pendingCases"], 1)
        self.assertEqual(summary["reviews"][0]["missingLabels"], ["turno"])
        self.assertIsNotNone(summary["reviews"][0]["questionId"])

    def test_preparation_accepts_numeric_year_stored_as_text(self) -> None:
        manager = DesktopPreparationManager(_seed(self.root, decision_year="2023"))

        result = manager.run()

        self.assertEqual(result["canonicalQuestions"], 1)
        self.assertEqual(result["qwenEligible"], 1)

    def test_desktop_application_exposes_preparation_without_calling_qwen(self) -> None:
        _seed(self.root)
        application = DesktopApplication(self.root)

        preview = application.preview_preparation()
        result = application.prepare_questions()

        self.assertEqual(preview["mode"], "preview")
        self.assertEqual(result["qwenEligible"], 1)
        self.assertEqual(application.bootstrap()["preparationSummary"]["canonicalQuestions"], 1)


if __name__ == "__main__":
    unittest.main()
