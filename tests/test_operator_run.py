from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from kad_collector.cli import _operator_request_from_args, _run, build_parser
from kad_collector.json_utils import read_json, write_json
from kad_collector.models import (
    AppConfig,
    CollectionFailure,
    CollectorSettings,
    DocumentRecord,
    DownloadManifest,
    ExtractedDocument,
    ExtractionManifest,
    SourceDefinition,
)
from kad_collector.operator_run import (
    OperatorParameters,
    _filter_manifest_period,
    default_operator_output,
    run_operator,
    select_operator_config,
)
from kad_collector.structured_questions import (
    QwenDecisionTrace,
    QwenRuntimeTrace,
    StructuredPackageMetrics,
    StructuredQuestionPackage,
)


def _config(root: Path) -> AppConfig:
    return AppConfig(
        collector=CollectorSettings(data_dir=str(root / "unscoped")),
        sources=[
            SourceDefinition(
                id="cebraspe_pf",
                name="Cebraspe - Polícia Federal",
                enabled=True,
                start_urls=["https://example.test/pf"],
                allowed_hosts=["example.test"],
                authorization_basis="Fixture pública.",
                metadata={"banca": "CEBRASPE", "orgao": "Policia Federal"},
            ),
            SourceDefinition(
                id="cesgranrio_bb",
                name="Cesgranrio - Banco do Brasil",
                enabled=True,
                start_urls=["https://bb.example.test/concursos"],
                allowed_hosts=["bb.example.test"],
                authorization_basis="Fixture pública.",
                metadata={"banca": "CESGRANRIO", "orgao": "Banco do Brasil"},
            ),
        ],
    )


def _write_config(root: Path) -> Path:
    path = root / "sources.toml"
    path.write_text(
        """
[collector]
request_interval_seconds = 0

[[sources]]
id = "cebraspe_pf"
name = "Cebraspe - Polícia Federal"
enabled = true
start_urls = ["https://example.test/pf"]
allowed_hosts = ["example.test"]
authorization_basis = "Fixture pública."

[sources.metadata]
banca = "CEBRASPE"
orgao = "Policia Federal"
""".strip(),
        encoding="utf-8",
    )
    return path


def _record(root: Path, suffix: str, document_type: str) -> DocumentRecord:
    path = root / f"{suffix}.pdf"
    body = f"%PDF-1.4\n{suffix}\n%%EOF".encode()
    path.write_bytes(body)
    return DocumentRecord(
        source_id="cebraspe_pf",
        source_name="Cebraspe - Polícia Federal",
        document_type=document_type,
        title=f"Documento {suffix}",
        original_url=f"https://example.test/{suffix}.pdf",
        resolved_url=f"https://example.test/{suffix}.pdf",
        local_path=str(path),
        sha256=hashlib.sha256(body).hexdigest(),
        content_type="application/pdf",
        size_bytes=len(body),
        downloaded_at=datetime.now(UTC),
        authorization_basis="Fixture pública.",
        metadata={"banca": "CEBRASPE", "orgao": "Policia Federal", "ano": "2021"},
    )


def _manifest(root: Path, *, with_failure: bool = False) -> DownloadManifest:
    failures = (
        [
            CollectionFailure(
                source_id="cebraspe_pf",
                url="https://example.test/quebrado.pdf",
                stage="download",
                message="PDF inválido; a coleta continuou",
            )
        ]
        if with_failure
        else []
    )
    return DownloadManifest(
        created_at=datetime.now(UTC),
        documents=[_record(root, "prova", "exam"), _record(root, "gabarito", "answer_key")],
        failures=failures,
    )


def _package(*, qwen_calls: int = 0) -> StructuredQuestionPackage:
    calls = (
        [
            QwenDecisionTrace(
                reason="questão ausente",
                input_sha256="b" * 64,
                response={"recovered_numbers": [1]},
                accepted=True,
                duration_ms=1,
                model="qwen3:8b",
            )
        ]
        if qwen_calls
        else []
    )
    return StructuredQuestionPackage(
        parser_version="fixture-1",
        input_manifest_sha256s=["c" * 64],
        accepted=[],
        quarantined=[],
        rejected=[],
        errors=[],
        page_extraction={},
        qwen=QwenRuntimeTrace(
            endpoint="http://127.0.0.1:11434",
            model="qwen3:8b",
            available=bool(qwen_calls),
            calls=calls,
        ),
        exams=[],
        metrics=StructuredPackageMetrics(
            documents_processed=2,
            exams_processed=1,
            answer_keys_processed=1,
            expected_questions=1,
            detected_questions=1,
            accepted_questions=1,
            quarantined_questions=0,
            rejected_questions=0,
            segmentation_precision=1,
            segmentation_coverage=1,
            associated_answers=1,
            missing_answers=0,
            duplicate_questions=0,
            ocr_pages=0,
            qwen_calls=qwen_calls,
            intervention_free_percent=1,
            duration_ms=1,
        ),
        content_sha256="a" * 64,
    )


def _extractor(
    manifest_path: Path, output_path: Path | None = None
) -> tuple[ExtractionManifest, Path]:
    assert output_path is not None
    manifest = DownloadManifest.model_validate(read_json(manifest_path))
    extraction = ExtractionManifest(
        created_at=datetime.now(UTC),
        documents=[
            ExtractedDocument(document=item, pages=[], text="", needs_ocr=True)
            for item in manifest.documents
        ],
    )
    write_json(output_path, extraction.model_dump(mode="json"))
    return extraction, output_path


def _structurer(
    manifest_paths: list[Path],
    output_path: Path,
    **_options: object,
) -> StructuredQuestionPackage:
    assert manifest_paths
    package = _package()
    write_json(output_path, package.model_dump(mode="json"))
    return package


class OperatorParameterTests(unittest.TestCase):
    def test_validates_period_and_builds_all_years(self) -> None:
        parameters = OperatorParameters(
            organization="Polícia Federal",
            board="CESPE",
            start_year=2019,
            end_year=2021,
        )
        self.assertEqual(parameters.filters().years, [2019, 2020, 2021])
        self.assertEqual(
            default_operator_output(parameters),
            Path("data/runs/policia-federal-cespe-2019-2021"),
        )

    def test_rejects_inverted_period(self) -> None:
        with self.assertRaisesRegex(ValueError, "ano inicial"):
            OperatorParameters(
                organization="Polícia Federal",
                board="Cebraspe",
                start_year=2025,
                end_year=2021,
            )

    def test_selects_sources_by_registered_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = select_operator_config(
                _config(root),
                OperatorParameters(
                    organization="PF", board="CESPE", start_year=2021, end_year=2021
                ),
                root / "run",
                enable_ollama=False,
            )
        self.assertEqual([item.id for item in selected.sources], ["cebraspe_pf"])
        self.assertFalse(selected.collector.ai_discovery_enabled)
        self.assertEqual(selected.collector.max_files_per_source, 50)

    def test_file_budget_scales_with_the_requested_period_and_remains_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = select_operator_config(
                _config(root),
                OperatorParameters(
                    organization="PF", board="CESPE", start_year=2000, end_year=2025
                ),
                root / "run",
                enable_ollama=False,
            )
        self.assertEqual(selected.collector.max_files_per_source, 1_000)

    def test_limits_cebraspe_start_urls_to_requested_period(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _config(root)
            source = config.sources[0].model_copy(
                update={
                    "start_urls": [
                        "https://example.test/concursos/pf_18",
                        "https://example.test/concursos/pf_21",
                        "https://example.test/concursos/pf_25",
                    ]
                }
            )
            config = config.model_copy(update={"sources": [source]})
            selected = select_operator_config(
                config,
                OperatorParameters(
                    organization="PF", board="CESPE", start_year=2021, end_year=2021
                ),
                root / "run",
                enable_ollama=False,
            )
        self.assertEqual(
            selected.sources[0].start_urls,
            ["https://example.test/concursos/pf_21"],
        )

    def test_manifest_period_filter_removes_short_contest_years(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _manifest(root)
            outside = _record(root, "fora", "exam").model_copy(
                update={
                    "original_url": "https://example.test/concursos/pf_25/arquivos/prova.pdf",
                    "resolved_url": "https://example.test/concursos/pf_25/arquivos/prova.pdf",
                    "metadata": {"banca": "CEBRASPE", "orgao": "Policia Federal"},
                }
            )
            manifest.documents.append(outside)
            filtered = _filter_manifest_period(
                manifest,
                OperatorParameters(
                    organization="PF", board="CESPE", start_year=2021, end_year=2021
                ),
            )
        self.assertEqual(len(filtered.documents), 2)
        self.assertEqual(filtered.filtered_out_documents, 1)

    def test_rejects_unknown_source_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "nenhuma fonte"):
                select_operator_config(
                    _config(root),
                    OperatorParameters(
                        organization="Órgão ausente",
                        board="Banca ausente",
                        start_year=2021,
                        end_year=2021,
                    ),
                    root / "run",
                    enable_ollama=True,
                )


class OperatorRunTests(unittest.TestCase):
    def _run(
        self,
        root: Path,
        *,
        manifest: DownloadManifest | None = None,
        structurer: object = _structurer,
        enable_ollama: bool = False,
    ):
        selected_manifest = manifest or _manifest(root)

        def collector(
            _config_value: AppConfig,
            _filters: object = None,
            *,
            run_id: str | None = None,
        ) -> tuple[DownloadManifest, Path]:
            self.assertIsNotNone(run_id)
            return selected_manifest, root / "generated.json"

        return run_operator(
            config_path=_write_config(root),
            output_dir=root / "run",
            parameters=OperatorParameters(
                organization="Polícia Federal",
                board="Cebraspe",
                start_year=2021,
                end_year=2021,
            ),
            enable_ollama=enable_ollama,
            collector=collector,
            extractor=_extractor,
            structurer=structurer,  # type: ignore[arg-type]
            emit=lambda _message: None,
        )

    def test_creates_the_five_operator_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._run(root)
            run_dir = root / "run"
            self.assertEqual(result.state.status, "completed")
            self.assertEqual(result.state.metrics.documents_found, 2)
            self.assertEqual(result.state.metrics.questions_ready, 1)
            for name in (
                "run.json",
                "manifest.json",
                "review-package.json",
                "report.md",
                "failures.json",
            ):
                self.assertTrue((run_dir / name).is_file(), name)
            self.assertIn(
                "Publicação externa: não executada",
                (run_dir / "report.md").read_text(encoding="utf-8"),
            )

    def test_deduplicates_documents_and_repeated_run_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _manifest(root)
            manifest.documents.append(manifest.documents[0])
            first = self._run(root, manifest=manifest)
            second = self._run(root, manifest=manifest)
            stored = DownloadManifest.model_validate(read_json(root / "run" / "manifest.json"))
            self.assertEqual(len(stored.documents), 2)
            self.assertEqual(stored.duplicate_documents, 1)
            self.assertEqual(first.package.content_sha256, second.package.content_sha256)
            self.assertEqual(second.state.repeated_count, 1)
            self.assertEqual(second.state.changes.new_documents, 0)

    def test_isolates_collection_failure_and_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._run(root, manifest=_manifest(root, with_failure=True))
            self.assertEqual(result.state.status, "completed")
            self.assertEqual(result.state.metrics.handled_failures, 1)
            self.assertEqual(read_json(root / "run" / "failures.json")[0]["stage"], "download")

    def test_total_source_failure_preserves_last_verified_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._run(root)
            failed_manifest = DownloadManifest(
                created_at=datetime.now(UTC),
                documents=[],
                failures=[
                    CollectionFailure(
                        source_id="cebraspe_pf",
                        url="https://example.test/pf",
                        stage="robots",
                        message="fonte indisponível nesta tentativa",
                    )
                ],
            )

            second = self._run(root, manifest=failed_manifest)

            stored = DownloadManifest.model_validate(
                read_json(root / "run" / "manifest.json")
            )
            self.assertEqual(len(stored.documents), 2)
            self.assertEqual(second.state.metrics.documents_found, 2)
            self.assertEqual(first.package.content_sha256, second.package.content_sha256)
            self.assertIn("último manifesto verificado", stored.warnings[-1])

    def test_first_run_with_total_source_failure_is_not_reported_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            failed_manifest = DownloadManifest(
                created_at=datetime.now(UTC),
                documents=[],
                failures=[
                    CollectionFailure(
                        source_id="cebraspe_pf",
                        url="https://example.test/pf",
                        stage="robots",
                        message="fonte indisponível nesta tentativa",
                    )
                ],
            )

            result = self._run(root, manifest=failed_manifest)

            self.assertEqual(result.state.status, "failed")
            self.assertEqual(
                result.state.last_error,
                "nenhuma fonte entregou documentos nesta tentativa",
            )

    def test_resume_after_total_source_failure_retries_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = _write_config(root)
            manifests = iter(
                [
                    DownloadManifest(
                        created_at=datetime.now(UTC),
                        documents=[],
                        failures=[
                            CollectionFailure(
                                source_id="cebraspe_pf",
                                url="https://example.test/pf",
                                stage="robots",
                                message="fonte indisponível nesta tentativa",
                            )
                        ],
                    ),
                    _manifest(root),
                ]
            )
            collection_calls = 0

            def collector(
                _config_value: AppConfig,
                _filters: object = None,
                *,
                run_id: str | None = None,
            ) -> tuple[DownloadManifest, Path]:
                nonlocal collection_calls
                collection_calls += 1
                return next(manifests), root / "generated.json"

            parameters = OperatorParameters(
                organization="PF", board="CESPE", start_year=2021, end_year=2021
            )
            first = run_operator(
                config_path=config_path,
                output_dir=root / "run",
                parameters=parameters,
                collector=collector,
                extractor=_extractor,
                structurer=_structurer,
                emit=lambda _message: None,
            )
            resumed = run_operator(
                config_path=config_path,
                output_dir=root / "run",
                resume=True,
                collector=collector,
                extractor=_extractor,
                structurer=_structurer,
                emit=lambda _message: None,
            )

            self.assertEqual(first.state.status, "failed")
            self.assertEqual(resumed.state.status, "completed")
            self.assertEqual(collection_calls, 2)

    def test_interrupt_and_resume_skip_completed_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = _write_config(root)
            manifest = _manifest(root)
            collection_calls = 0

            def collector(
                _config_value: AppConfig,
                _filters: object = None,
                *,
                run_id: str | None = None,
            ) -> tuple[DownloadManifest, Path]:
                nonlocal collection_calls
                collection_calls += 1
                return manifest, root / "generated.json"

            def interrupted_extractor(
                _manifest_path: Path, _output_path: Path | None = None
            ) -> tuple[ExtractionManifest, Path]:
                raise KeyboardInterrupt

            parameters = OperatorParameters(
                organization="PF", board="CESPE", start_year=2021, end_year=2021
            )
            with self.assertRaises(KeyboardInterrupt):
                run_operator(
                    config_path=config_path,
                    output_dir=root / "run",
                    parameters=parameters,
                    collector=collector,
                    extractor=interrupted_extractor,
                    structurer=_structurer,
                    emit=lambda _message: None,
                )
            interrupted = read_json(root / "run" / "run.json")
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual(interrupted["completed_stages"], ["discovery", "download"])

            resumed = run_operator(
                config_path=config_path,
                output_dir=root / "run",
                resume=True,
                collector=collector,
                extractor=_extractor,
                structurer=_structurer,
                emit=lambda _message: None,
            )
            self.assertEqual(collection_calls, 1)
            self.assertEqual(resumed.state.status, "completed")
            self.assertEqual(resumed.state.resumed_count, 1)

    def test_existing_unmanaged_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "arquivo.txt").write_text("ocupado", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sem run.json"):
                self._run(root)

    def test_ollama_setting_and_qwen_telemetry_reach_the_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observed: dict[str, object] = {}

            def structurer(
                manifest_paths: list[Path], output_path: Path, **options: object
            ) -> StructuredQuestionPackage:
                observed.update(options)
                package = _package(qwen_calls=1)
                write_json(output_path, package.model_dump(mode="json"))
                return package

            result = self._run(root, structurer=structurer, enable_ollama=True)
            self.assertTrue(observed["enable_ollama"])
            self.assertEqual(observed["qwen_model"], "qwen3:8b")
            self.assertEqual(result.state.metrics.qwen_calls, 1)


class OperatorCliTests(unittest.TestCase):
    def test_non_interactive_cli_builds_operator_request(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "--orgao",
                "Polícia Federal",
                "--banca",
                "Cebraspe",
                "--ano-inicial",
                "2021",
                "--ano-final",
                "2025",
            ]
        )
        parameters, output, resume = _operator_request_from_args(args)
        assert parameters is not None
        self.assertEqual(parameters.start_year, 2021)
        self.assertEqual(parameters.end_year, 2025)
        self.assertFalse(resume)
        self.assertEqual(output, Path("data/runs/policia-federal-cebraspe-2021-2025"))

    def test_interactive_cli_asks_for_scope_and_confirmation(self) -> None:
        args = build_parser().parse_args(["run", "--interactive", "--output", "saida"])
        answers = iter(["Polícia Federal", "Cebraspe", "2021", "2021", "sim"])
        with patch("builtins.input", side_effect=lambda _prompt: next(answers)):
            parameters, output, resume = _operator_request_from_args(args)
        assert parameters is not None
        self.assertEqual(parameters.organization, "Polícia Federal")
        self.assertEqual(output, Path("saida"))
        self.assertFalse(resume)

    def test_cli_routes_operator_without_using_legacy_semiautomatic_flow(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "--orgao",
                "PF",
                "--banca",
                "CESPE",
                "--ano-inicial",
                "2021",
                "--ano-final",
                "2021",
                "--disable-ollama",
            ]
        )
        with patch("kad_collector.cli.run_operator") as mocked:
            self.assertEqual(_run(args), 0)
        self.assertFalse(mocked.call_args.kwargs["enable_ollama"])


if __name__ == "__main__":
    unittest.main()
