from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from kad_collector.regression import RegressionError, load_regression_manifest

TOPICS = [
    "exam_answer_separate",
    "same_pdf",
    "types_1_4",
    "preliminary_definitive",
    "annulled",
    "republication_version",
    "scanned_ocr",
    "multirole_turn_version",
    "ambiguous_association_blocked",
    "unrelated_document",
]


def manifest_text(
    *,
    fixture_rows: str = "",
    case_rows: str = "",
    topics: list[str] | None = None,
) -> str:
    topic_lines = ",\n  ".join(f'"{item}"' for item in (topics or TOPICS))
    return f"""
schema_version = 1
coverage_topics = [
  {topic_lines},
]
{fixture_rows}
{case_rows}
"""


def fixture_row(
    *,
    fixture_id: str = "synthetic-one",
    path: str = "synthetic/one.txt",
    sha256: str | None = None,
) -> str:
    digest = sha256 or hashlib.sha256(b"fixture\n").hexdigest()
    return f"""
[[fixtures]]
id = "{fixture_id}"
kind = "synthetic"
path = "{path}"
format = "text"
size_bytes = 8
sha256 = "{digest}"
description = "Fixture sintética fictícia."
"""


def supported_case_row(*, case_id: str = "case-one", fixture_id: str = "synthetic-one") -> str:
    topics = ", ".join(f'"{item}"' for item in TOPICS)
    return f"""
[[cases]]
id = "{case_id}"
title = "Caso suportado"
status = "supported"
executor = "inline_answer"
fixtures = ["{fixture_id}"]
covers = [{topics}]
expected = {{ question_count = 1 }}
"""


class RegressionManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "synthetic").mkdir()
        (self.root / "synthetic" / "one.txt").write_text("fixture\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(self, text: str) -> Path:
        path = self.root / "manifest.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_a_valid_manifest(self) -> None:
        path = self.write_manifest(
            manifest_text(fixture_rows=fixture_row(), case_rows=supported_case_row())
        )

        manifest = load_regression_manifest(path)

        self.assertEqual(manifest.schema_version, 1)
        self.assertEqual(manifest.cases[0].status, "supported")
        self.assertEqual(manifest.fixtures[0].path, Path("synthetic/one.txt"))

    def test_rejects_duplicate_case_ids(self) -> None:
        cases = supported_case_row() + supported_case_row()
        path = self.write_manifest(manifest_text(fixture_rows=fixture_row(), case_rows=cases))

        with self.assertRaisesRegex(RegressionError, "caso duplicado: case-one"):
            load_regression_manifest(path)

    def test_rejects_duplicate_fixture_ids_and_paths(self) -> None:
        fixtures = fixture_row() + fixture_row(fixture_id="synthetic-two")
        path = self.write_manifest(
            manifest_text(fixture_rows=fixtures, case_rows=supported_case_row())
        )

        with self.assertRaisesRegex(RegressionError, "caminho de fixture duplicado"):
            load_regression_manifest(path)

    def test_rejects_invalid_fixture_metadata(self) -> None:
        fixture = fixture_row(sha256="not-a-digest").replace(
            'kind = "synthetic"',
            'kind = "official"\nsource_url = "http://example.test/file.pdf"',
        )
        path = self.write_manifest(
            manifest_text(fixture_rows=fixture, case_rows=supported_case_row())
        )

        with self.assertRaises(RegressionError) as context:
            load_regression_manifest(path)

        message = str(context.exception)
        self.assertIn("SHA-256 inválido", message)
        self.assertIn("origem oficial deve usar HTTPS", message)

    def test_rejects_planned_case_without_gap(self) -> None:
        planned_topics = ", ".join(f'"{item}"' for item in TOPICS)
        planned = f"""
[[cases]]
id = "planned-one"
title = "Caso planejado"
status = "planned"
fixtures = []
covers = [{planned_topics}]
"""
        path = self.write_manifest(manifest_text(case_rows=planned))

        with self.assertRaisesRegex(RegressionError, "caso planned exige gap"):
            load_regression_manifest(path)

    def test_rejects_supported_case_without_executor_or_known_fixture(self) -> None:
        supported = supported_case_row(fixture_id="missing").replace(
            'executor = "inline_answer"\n', ""
        )
        path = self.write_manifest(manifest_text(case_rows=supported))

        with self.assertRaises(RegressionError) as context:
            load_regression_manifest(path)

        message = str(context.exception)
        self.assertIn("caso supported exige executor", message)
        self.assertIn("fixture desconhecida: missing", message)

    def test_rejects_a_coverage_topic_absent_from_all_cases(self) -> None:
        case = supported_case_row().replace(', "unrelated_document"', "")
        path = self.write_manifest(manifest_text(fixture_rows=fixture_row(), case_rows=case))

        with self.assertRaisesRegex(RegressionError, "cobertura sem caso: unrelated_document"):
            load_regression_manifest(path)


if __name__ == "__main__":
    unittest.main()
