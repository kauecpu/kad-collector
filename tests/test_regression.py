from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stderr
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from kad_collector.cli import _run, build_parser, main
from kad_collector.regression import (
    CaseSpec,
    FixtureSpec,
    RegressionError,
    load_regression_manifest,
    production_executors,
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

    def test_rejects_observe_or_ignore_policy_without_recorded_decision(self) -> None:
        fixture = fixture_row().replace(
            'kind = "synthetic"',
            'kind = "official"\n'
            'source_url = "https://example.test/file.txt"\n'
            'robots_policy = "ignore"\n'
            'crawl_delay_policy = "observe"',
        )
        path = self.write_manifest(
            manifest_text(fixture_rows=fixture, case_rows=supported_case_row())
        )

        with self.assertRaisesRegex(RegressionError, "política exige decisão registrada"):
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

    def test_planned_case_never_calls_an_executor(self) -> None:
        topic_rows = ", ".join(f'"{item}"' for item in TOPICS)
        self.manifest_path.write_text(
            manifest_text(
                case_rows=f"""
[[cases]]
id = "planned-one"
title = "Lacuna conhecida"
status = "planned"
fixtures = []
covers = [{topic_rows}]
gap = "Contrato ausente no produto."
"""
            ),
            encoding="utf-8",
        )
        called = False

        def forbidden(*_args: object) -> dict[str, object]:
            nonlocal called
            called = True
            return {}

        report = run_regression(
            self.manifest_path,
            self.report_path,
            executors={"planned-one": forbidden},
        )

        self.assertFalse(called)
        self.assertEqual(report["summary"], {"supported": 0, "passed": 0, "planned": 1})
        self.assertEqual(report["cases"][0]["status"], "planned")  # type: ignore[index]


class RegressionExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def case(self, executor: str) -> CaseSpec:
        return CaseSpec(
            id=f"{executor}-case",
            title="Caso sintético",
            status="supported",
            executor=executor,
            fixtures=(),
            covers=(),
            expected={},
        )

    def test_inline_answer_executor_reads_answer_from_same_document(self) -> None:
        path = self.root / "inline.txt"
        path.write_text(
            """QUESTÃO 1
Assinale a opção correta neste caso fictício.
A) Primeira opção sintética.
B) Segunda opção sintética.
C) Terceira opção sintética.
Alternativa Correta: C
""",
            encoding="utf-8",
        )

        result = production_executors()["inline_answer"](
            self.case("inline_answer"), {"inline": path}
        )

        self.assertEqual(
            result,
            {"question_count": 1, "question_number": 1, "answer": "C", "warnings": []},
        )

    def test_answer_grid_executor_selects_types_role_turn_and_annulment(self) -> None:
        path = self.root / "grid.txt"
        path.write_text(
            """Técnico – Tipo 1 (Manhã)
1 2 3
A B *
Técnico – Tipo 2 (Manhã)
1 2 3
B C D
Técnico – Tipo 3 (Tarde)
1 2 3
C D A
Técnico – Tipo 4 (Tarde)
1 2 3
D A B
Analista – Tipo 2 (Manhã)
1 2 3
A A C
""",
            encoding="utf-8",
        )

        result = production_executors()["answer_grid"](
            self.case("answer_grid"), {"grid": path}
        )

        self.assertEqual(
            result,
            {
                "types": {"1": ["A", "B", "*"], "2": ["B", "C", "D"],
                          "3": ["C", "D", "A"], "4": ["D", "A", "B"]},
                "annulled": 1,
                "other_role_type_2": ["A", "A", "C"],
            },
        )

    def test_answer_key_selection_prefers_definitive_and_blocks_ambiguity(self) -> None:
        definitive = self.root / "definitive.json"
        definitive.write_text(
            json.dumps(
                {
                    "exam": {"filename": "prova.pdf", "metadata": {"year": 2026}},
                    "answer_keys": [
                        {
                            "id": "preliminary",
                            "filename": "preliminar.pdf",
                            "metadata": {"document_title": "Gabarito preliminar", "year": 2026},
                        },
                        {
                            "id": "definitive",
                            "filename": "definitivo.pdf",
                            "metadata": {"document_title": "Gabarito definitivo", "year": 2026},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        ambiguous = self.root / "ambiguous.json"
        ambiguous.write_text(
            json.dumps(
                {
                    "exam": {"filename": "prova.pdf", "metadata": {}},
                    "answer_keys": [
                        {"id": "a", "filename": "a.pdf", "metadata": {}},
                        {"id": "b", "filename": "b.pdf", "metadata": {}},
                    ],
                }
            ),
            encoding="utf-8",
        )

        selected = production_executors()["definitive_selection"](
            self.case("definitive_selection"), {"selection": definitive}
        )
        blocked = production_executors()["ambiguous_selection"](
            self.case("ambiguous_selection"), {"selection": ambiguous}
        )

        self.assertEqual(selected, {"selected_id": "definitive"})
        self.assertEqual(blocked, {"selected_id": "blocked"})


class RegressionCommandTests(unittest.TestCase):
    def test_cli_forwards_manifest_and_report_to_runner(self) -> None:
        args = build_parser().parse_args(
            ["regression", "--manifest", "custom.toml", "--report", "custom.json"]
        )
        result = {
            "summary": {"supported": 5, "passed": 5, "planned": 3},
            "coverage": [],
        }

        with patch("kad_collector.cli.run_regression", return_value=result) as runner:
            exit_code = _run(args)

        self.assertEqual(exit_code, 0)
        runner.assert_called_once_with(Path("custom.toml"), Path("custom.json"))

    def test_cli_returns_two_for_regression_error(self) -> None:
        error_output = StringIO()
        with (
            patch(
                "kad_collector.cli.run_regression",
                side_effect=RegressionError("fixture ausente"),
            ),
            redirect_stderr(error_output),
        ):
            exit_code = main(["regression"])

        self.assertEqual(exit_code, 2)
        self.assertIn("fixture ausente", error_output.getvalue())

    def test_preparation_downloads_and_verifies_before_atomic_replace(self) -> None:
        from scripts.prepare_regression_fixtures import prepare_official_fixtures

        payload = b"%PDF-fixture\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.toml"
            manifest_path.write_text(
                manifest_text(
                    fixture_rows=f"""
[[fixtures]]
id = "official-one"
kind = "official"
path = "official/one.pdf"
format = "pdf"
size_bytes = {len(payload)}
sha256 = "{digest}"
source_url = "https://example.test/one.pdf"
description = "PDF oficial de teste."
robots_policy = "ignore"
crawl_delay_policy = "ignore"
policy_basis = "Decisão explícita do teste."
""",
                    case_rows=supported_case_row(fixture_id="official-one"),
                ),
                encoding="utf-8",
            )

            with patch(
                "scripts.prepare_regression_fixtures.urlopen",
                return_value=BytesIO(payload),
            ) as urlopen:
                prepared = prepare_official_fixtures(manifest_path)

            destination = root / "official" / "one.pdf"
            self.assertEqual(prepared, [destination])
            self.assertEqual(destination.read_bytes(), payload)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://example.test/one.pdf")

            destination.write_bytes(b"preserve-me")
            with (
                patch(
                    "scripts.prepare_regression_fixtures.urlopen",
                    return_value=BytesIO(b"%PDF-" + b"x" * 100),
                ),
                self.assertRaisesRegex(RegressionError, "limite de tamanho"),
            ):
                prepare_official_fixtures(manifest_path)
            self.assertEqual(destination.read_bytes(), b"preserve-me")

    def test_preparation_enforces_robots_policy_by_default(self) -> None:
        from scripts.prepare_regression_fixtures import prepare_official_fixtures

        payload = b"%PDF-fixture\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.toml"
            manifest_path.write_text(
                manifest_text(
                    fixture_rows=f"""
[[fixtures]]
id = "official-one"
kind = "official"
path = "official/one.pdf"
format = "pdf"
size_bytes = {len(payload)}
sha256 = "{hashlib.sha256(payload).hexdigest()}"
source_url = "https://example.test/one.pdf"
description = "PDF oficial de teste."
""",
                    case_rows=supported_case_row(fixture_id="official-one"),
                ),
                encoding="utf-8",
            )
            robots = BytesIO(b"User-agent: *\nDisallow: /one.pdf\n")

            with (
                patch("scripts.prepare_regression_fixtures.urlopen", return_value=robots),
                self.assertRaisesRegex(RegressionError, "robots.txt bloqueia"),
            ):
                prepare_official_fixtures(manifest_path)


if __name__ == "__main__":
    unittest.main()
