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


def _decision(booklet: str) -> dict[str, object]:
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
            ("year", [2023]),
            ("organization", ["receita federal"]),
            ("role", ["analista"]),
            ("stage", ["prova objetiva"]),
            ("turn", ["manhã"]),
            ("variant", [f"tipo {booklet}"]),
            ("interval", [1, 1]),
        )
    ]
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
        "algorithm_version": "semantic-association-v2",
    }


def _seed(root: Path, *, include_review: bool = False) -> DesktopStore:
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
            "('key-version','identity','answer_key','definitive','{}','{}',"
            "'key-sha','fixture',1,?,?)",
            (NOW, NOW),
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
                    f"exam-sha-{booklet}",
                    int(booklet),
                    NOW,
                    NOW,
                ),
            )
            metadata = {
                "document_type": "exam",
                "source_url": f"https://example.test/exam-{booklet}",
                "variant": f"Tipo {booklet}",
                "concurso": "RFB22",
                "board": "FGV",
                "year": 2023,
                "role": "Analista",
                "stage": "Prova objetiva",
                "turn": "Manhã",
            }
            connection.execute(
                "INSERT INTO documents (id,job_id,local_path,filename,sha256,status,metadata_json,"
                "warnings_json,created_at,updated_at,document_version_id,semantic_resolution) "
                "VALUES (?,'job',?,?,?,'processed',?,'[]',?,?,?,'new_identity')",
                (
                    document_id,
                    f"exam-{booklet}.pdf",
                    f"exam-{booklet}.pdf",
                    f"exam-sha-{booklet}",
                    _json(metadata),
                    NOW,
                    NOW,
                    version_id,
                ),
            )
            connection.execute(
                "INSERT INTO document_links (id,exam_version_id,answer_key_version_id,status,"
                "decision_json,algorithm_version,created_at,updated_at) "
                "VALUES (?,?,'key-version','active',?,'semantic-association-v2',?,?)",
                (link_id, version_id, _json(_decision(booklet)), NOW, NOW),
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
        self.assertEqual(result["duplicateQuestions"], 1)
        self.assertEqual(result["pendingQuestions"], 0)
        self.assertEqual(repeated["canonicalQuestions"], 1)
        self.assertEqual(store.query(DesktopFilterSet())["total"], 1)
        self.assertEqual(
            store.query(
                DesktopFilterSet(), include_equivalent_copies=True
            )["total"],
            2,
        )

    def test_pending_association_points_to_document_identity_review(self) -> None:
        manager = DesktopPreparationManager(_seed(self.root, include_review=True))

        summary = manager.summary()

        self.assertEqual(summary["pendingCases"], 1)
        self.assertEqual(summary["reviews"][0]["missingLabels"], ["turno"])
        self.assertIsNotNone(summary["reviews"][0]["questionId"])

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
