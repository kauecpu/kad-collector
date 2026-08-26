from __future__ import annotations

import json
import subprocess
import unittest
from contextlib import closing
from importlib import resources
from pathlib import Path
from tempfile import TemporaryDirectory

from kad_collector.desktop_server import _desktop_environment, _next_desktop_action
from kad_collector.desktop_store import DesktopStore


def _summary_views(*, matched: int, annulled: int, missing: int) -> list[dict[str, object]]:
    statuses = (["matched"] * matched) + (["annulled"] * annulled) + (["missing"] * missing)
    return [
        {
            "status": "pending" if answer_status == "matched" else "exception",
            "question": {"answer_status": answer_status},
            "exportable": False,
            "importable": answer_status == "matched",
            "publication_ready": False,
            "readiness_states": ["unclassified", "blocked"],
            "block_reasons": ["missing_classification"],
        }
        for answer_status in statuses
    ]


class DesktopExperienceTests(unittest.TestCase):
    def test_empty_bank_and_completed_collection_without_questions_have_clear_next_action(
        self,
    ) -> None:
        empty = _next_desktop_action(
            {"unclassified": 0, "pending": 0, "exception": 0, "importable": 0},
            {"rawQuestions": 0, "canonicalQuestions": 0},
        )
        self.assertEqual(empty["step"], "collect")
        self.assertIn("não existem questões", empty["detail"])

    def test_raw_collection_with_zero_canonical_questions_points_to_preparation(self) -> None:
        with TemporaryDirectory() as directory:
            store = DesktopStore(Path(directory) / "collector.sqlite3")
            with closing(store._connect()) as connection:
                connection.execute(
                    "INSERT INTO jobs (id,created_at,updated_at,status,classifier_provider) "
                    "VALUES ('job','now','now','completed','local')"
                )
                connection.execute(
                    "INSERT INTO documents "
                    "(id,job_id,local_path,filename,size_bytes,page_count,processed_pages,status,"
                    "needs_ocr,metadata_json,warnings_json,created_at,updated_at) "
                    "VALUES ('doc','job','fixture.pdf','fixture.pdf',1,1,1,'completed',0,"
                    "'{\"document_type\":\"exam\"}','[]','now','now')"
                )
                rows = [
                    (
                        f"q-{index}",
                        index,
                        f"fingerprint-{index}",
                    )
                    for index in range(1, 1541)
                ]
                connection.executemany(
                    "INSERT INTO questions "
                    "(id,document_id,question_number,fingerprint,payload_json,classification_json,"
                    "confidence,flags_json,status,created_at,updated_at) "
                    "VALUES (?,'doc',?,?,'{}','{}',0,'[]','pending','now','now')",
                    rows,
                )
                connection.commit()

            operational = store.operational_presentation_summary()
            action = _next_desktop_action(
                {"unclassified": 1540, "pending": 1540, "exception": 0, "importable": 0},
                operational,
            )

        self.assertEqual(operational["rawQuestions"], 1540)
        self.assertEqual(operational["canonicalQuestions"], 0)
        self.assertEqual(action["step"], "prepare")
        self.assertIn("preparação canônica", action["title"].casefold())

    def test_answer_fixture_preserves_1092_matched_28_annulled_and_420_pending(self) -> None:
        summary = DesktopStore._summary(
            _summary_views(matched=1092, annulled=28, missing=420)
        )

        self.assertEqual(summary["total"], 1540)
        self.assertEqual(summary["answer_matched"], 1092)
        self.assertEqual(summary["answer_annulled"], 28)
        self.assertEqual(summary["answer_missing"], 420)

    def test_environment_labels_distinguish_operational_test_and_reference_banks(self) -> None:
        self.assertEqual(
            _desktop_environment(Path("C:/Users/operator/AppData/Local/KAD Collector")),
            "operational",
        )
        self.assertEqual(_desktop_environment(Path("C:/work/tests/fixture")), "test")
        self.assertEqual(
            _desktop_environment(Path("C:/work/data/benchmarks/baseline")),
            "reference",
        )

    def test_qwen_zero_eligibility_is_not_reported_as_zero_missing_fields(self) -> None:
        package = resources.files("kad_collector")
        javascript = package.joinpath("desktop_app.js")
        runner = """
const renderers = require(process.argv[1]);
const output = renderers.qwenPreviewPresentation({counts: {
  rawQuestions: 1540, canonicalQuestions: 0, eligible: 0,
  missingFields: {}, exclusionReasons: [{
    code: 'canonical_preparation_pending', label: 'Preparação canônica pendente',
    count: 1540, action: 'Executar preparação.'
  }]
}});
console.log(JSON.stringify(output));
"""
        completed = subprocess.run(
            ["node", "-e", runner, str(javascript)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual(output["missing"], [])
        self.assertEqual(output["zeroReason"]["code"], "canonical_preparation_pending")

    def test_question_with_empty_matter_and_subject_shows_cause_and_safe_next_action(self) -> None:
        package = resources.files("kad_collector")
        javascript = package.joinpath("desktop_app.js")
        runner = """
const renderers = require(process.argv[1]);
const output = renderers.questionStatePresentation({
  question: {answer_status: 'missing', discipline: 'Direito', matter: '', subject: '', level: ''},
  block_reasons: ['missing_official_answer', 'missing_classification'],
  importable: false,
  import_diagnosis: {issues: [{what: 'Resposta oficial ausente.', how: 'Relacione o gabarito.'}]}
});
console.log(JSON.stringify(output));
"""
        completed = subprocess.run(
            ["node", "-e", runner, str(javascript)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        states = {item["label"]: item for item in json.loads(completed.stdout)}
        self.assertEqual(states["Gabarito"]["state"], "Diagnóstico pendente")
        self.assertEqual(states["Preparação"]["state"], "Bruta")
        self.assertEqual(states["Classificação"]["state"], "Incompleta")
        self.assertEqual(states["Classificação"]["action"], "Concluir preparação primeiro")
        self.assertEqual(states["Importação"]["state"], "Bloqueada")

    def test_packaged_ui_exposes_pipeline_accessibility_and_qwen_safety_copy(self) -> None:
        package = resources.files("kad_collector")
        html = package.joinpath("desktop_ui.html").read_text(encoding="utf-8")
        css = package.joinpath("desktop_styles.css").read_text(encoding="utf-8")
        for value in (
            "Banco ativo neste aplicativo",
            "Página consultada",
            "Gabaritos associados",
            "Preparação canônica",
            "Preparar questões para classificação",
            'id="prep-duplicates"',
            'id="edit-stage"',
            'id="edit-turn"',
            'id="question-state-groups"',
            "Ele não altera gabaritos, respostas oficiais",
        ):
            self.assertIn(value, html)
        self.assertIn(":focus-visible", css)
        self.assertIn("@media (max-width: 1180px)", css)


if __name__ == "__main__":
    unittest.main()
