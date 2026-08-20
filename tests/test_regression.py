from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import unittest
from pathlib import Path

from kad_collector.regression import (
    FixtureSpec,
    RegressionError,
    load_regression_manifest,
    run_regression,
    validate_fixture,
)

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
        (self.root / "synthetic" / "one.txt").write_bytes(b"fixture\n")

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


class RegressionRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "synthetic").mkdir()
        self.fixture_path = self.root / "synthetic" / "one.txt"
        self.fixture_path.write_bytes(b"fixture\n")
        self.manifest_path = self.root / "manifest.toml"
        self.manifest_path.write_text(
            manifest_text(fixture_rows=fixture_row(), case_rows=supported_case_row()),
            encoding="utf-8",
        )
        self.report_path = self.root / "report.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def executor(self, *_args: object) -> dict[str, object]:
        return {"question_count": 1}

    def fixture_spec(self, *, format: str = "text", sha256: str | None = None) -> FixtureSpec:
        return FixtureSpec(
            id="fixture",
            kind="synthetic",
            path=Path("synthetic/one.txt"),
            format=format,  # type: ignore[arg-type]
            size_bytes=self.fixture_path.stat().st_size,
            sha256=sha256 or hashlib.sha256(self.fixture_path.read_bytes()).hexdigest(),
            description="Fixture sintética fictícia.",
        )

    def test_missing_fixture_fails_before_case_execution(self) -> None:
        self.fixture_path.unlink()

        with self.assertRaisesRegex(RegressionError, "fixture ausente: synthetic-one"):
            run_regression(
                self.manifest_path,
                self.report_path,
                executors={"inline_answer": self.executor},
            )

    def test_fixture_size_and_hash_must_match(self) -> None:
        original_spec = self.fixture_spec()
        self.fixture_path.write_text("changed\n", encoding="utf-8")

        with self.assertRaisesRegex(RegressionError, "tamanho divergente"):
            validate_fixture(original_spec, self.root)

        digest = hashlib.sha256(self.fixture_path.read_bytes()).hexdigest()
        wrong_digest = ("0" if digest[0] != "0" else "1") + digest[1:]
        with self.assertRaisesRegex(RegressionError, "SHA-256 divergente"):
            validate_fixture(self.fixture_spec(sha256=wrong_digest), self.root)

    def test_pdf_fixture_requires_pdf_signature(self) -> None:
        with self.assertRaisesRegex(RegressionError, "assinatura PDF inválida"):
            validate_fixture(self.fixture_spec(format="pdf"), self.root)

    def test_runner_blocks_network_access(self) -> None:
        def network_executor(*_args: object) -> dict[str, object]:
            socket.create_connection(("example.com", 443))
            return {"question_count": 1}

        with self.assertRaisesRegex(RegressionError, "acesso à rede bloqueado"):
            run_regression(
                self.manifest_path,
                self.report_path,
                executors={"inline_answer": network_executor},
            )

    def test_runner_rejects_nondeterministic_executor(self) -> None:
        calls = 0

        def changing_executor(*_args: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"question_count": calls}

        with self.assertRaisesRegex(RegressionError, "caso não determinístico: case-one"):
            run_regression(
                self.manifest_path,
                self.report_path,
                executors={"inline_answer": changing_executor},
            )

    def test_valid_run_writes_deterministic_report_payload(self) -> None:
        first = run_regression(
            self.manifest_path,
            self.report_path,
            executors={"inline_answer": self.executor},
        )
        first_disk = json.loads(self.report_path.read_text(encoding="utf-8"))
        second = run_regression(
            self.manifest_path,
            self.report_path,
            executors={"inline_answer": self.executor},
        )

        self.assertEqual(first, first_disk)
        self.assertEqual(first["summary"], {"supported": 1, "passed": 1, "planned": 0})
        self.assertTrue(first["offline"])
        first.pop("generated_at")
        second.pop("generated_at")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
