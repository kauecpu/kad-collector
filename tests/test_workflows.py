from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from kad_collector.automation import run_automatic, update_retry_queue
from kad_collector.models import (
    Alternative,
    AutomationState,
    CollectionFailure,
    DocumentRecord,
    DownloadManifest,
    ExtractionManifest,
    QuestionBatch,
    QuestionRecord,
    ValidationState,
)
from kad_collector.reporting import build_question_report
from kad_collector.security import HttpResult
from kad_collector.workflow import read_requested_urls, run_semiautomatic


def document_record(
    *,
    suffix: str,
    organization: str = "Orgao Teste",
    local_path: str = "data/raw/prova.pdf",
    sha256: str | None = None,
    size_bytes: int = 100,
    document_type: str = "exam",
) -> DocumentRecord:
    return DocumentRecord(
        source_id=f"fonte_{suffix}",
        source_name=f"Fonte {suffix}",
        document_type=document_type,
        title=f"Documento {suffix}",
        original_url=f"https://provas.example.gov.br/{suffix}.pdf",
        resolved_url=f"https://provas.example.gov.br/{suffix}.pdf",
        local_path=local_path,
        sha256=sha256 or (suffix[0] * 64),
        content_type="application/pdf",
        size_bytes=size_bytes,
        downloaded_at=datetime.now(UTC),
        authorization_basis="Publicacao oficial conferida.",
        metadata={"orgao": organization},
    )


def question(
    number: int,
    statement: str,
    *,
    organization: str | None,
    role: str | None = "Analista",
) -> QuestionRecord:
    return QuestionRecord(
        number=number,
        statement=statement,
        alternatives=[
            Alternative(letter="A", text="Primeira alternativa"),
            Alternative(letter="B", text="Segunda alternativa"),
        ],
        matter="Direito" if organization else None,
        subject=None,
        board="FGV" if organization else None,
        organization=organization,
        role=role,
        year=2022 if organization else None,
        source_pages=[1],
    )


def batch(record: DocumentRecord, questions: list[QuestionRecord]) -> QuestionBatch:
    return QuestionBatch(
        batch_id=f"batch-{record.source_id}",
        created_at=datetime.now(UTC),
        model="fake-model",
        source_document=record,
        questions=questions,
        validation=ValidationState(valid=True),
    )


class ReportingTests(unittest.TestCase):
    def test_report_deduplicates_across_documents_sorts_and_separates_exceptions(self) -> None:
        first = document_record(suffix="a", organization="Zeta")
        duplicate = document_record(suffix="b", organization="Zeta")
        earlier = document_record(suffix="c", organization="Alfa")
        batches = [
            batch(first, [question(1, "Questao repetida", organization="Zeta")]),
            batch(duplicate, [question(1, "Questao repetida", organization="Zeta")]),
            batch(
                earlier,
                [
                    question(2, "Questao anterior", organization="Alfa"),
                    question(3, "Questao incompleta", organization=None, role=None),
                ],
            ),
        ]
        download = DownloadManifest(
            created_at=datetime.now(UTC), documents=[first, duplicate, earlier]
        )
        extraction = ExtractionManifest(created_at=datetime.now(UTC), documents=[])
        report = build_question_report(
            requested_urls=[record.original_url for record in [first, duplicate, earlier]],
            download_manifest=download,
            download_path=Path("download.json"),
            extraction_manifest=extraction,
            extraction_path=Path("extracted.json"),
            batches=batches,
            batch_paths=[Path("a.json"), Path("b.json"), Path("c.json")],
        )

        self.assertEqual(report.metrics.duplicate_questions, 1)
        self.assertEqual(
            [item.question.organization for item in report.questions], ["Alfa", "Zeta"]
        )
        self.assertEqual(len(report.questions[1].origins), 2)
        self.assertEqual(len(report.exceptions), 1)
        self.assertIn("banca nao identificada", report.exceptions[0].issues)

    def test_duplicate_can_fill_missing_metadata_without_leaving_a_stale_issue(self) -> None:
        incomplete_record = document_record(suffix="a")
        complete_record = document_record(suffix="b")
        incomplete = question(1, "Questao compartilhada", organization=None)
        complete = question(1, "Questao compartilhada", organization="Orgao Completo")
        download = DownloadManifest(
            created_at=datetime.now(UTC),
            documents=[incomplete_record, complete_record],
        )
        report = build_question_report(
            requested_urls=[incomplete_record.original_url, complete_record.original_url],
            download_manifest=download,
            download_path=Path("download.json"),
            extraction_manifest=ExtractionManifest(created_at=datetime.now(UTC), documents=[]),
            extraction_path=Path("extracted.json"),
            batches=[
                batch(incomplete_record, [incomplete]),
                batch(complete_record, [complete]),
            ],
            batch_paths=[Path("a.json"), Path("b.json")],
        )

        self.assertEqual(len(report.questions), 1)
        self.assertEqual(report.exceptions, [])
        self.assertEqual(report.questions[0].question.organization, "Orgao Completo")

    def test_url_files_ignore_comments_and_remove_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "links.txt"
            path.write_text(
                "# lote de teste\nhttps://example.test/a.pdf\n\nhttps://example.test/b.pdf\n",
                encoding="utf-8",
            )
            urls = read_requested_urls(["https://example.test/a.pdf"], [path])
        self.assertEqual(
            urls,
            ["https://example.test/a.pdf", "https://example.test/b.pdf"],
        )

    def test_semiautomatic_run_writes_one_report_for_direct_pdf(self) -> None:
        direct_url = "https://provas.example.gov.br/prova-direta.pdf"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_path = root / "fixture.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with pdf_path.open("wb") as handle:
                writer.write(handle)
            pdf_body = pdf_path.read_bytes()

            class FixtureClient:
                def __init__(
                    self, user_agent: str, timeout: float, interval_seconds: float
                ) -> None:
                    pass

                def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                    headers = Message()
                    if url.endswith("/robots.txt"):
                        headers["Content-Type"] = "text/plain; charset=utf-8"
                        return HttpResult(
                            url=url,
                            status_code=200,
                            headers=headers,
                            body=b"User-agent: *\nAllow: /\n",
                        )
                    headers["Content-Type"] = "application/pdf"
                    return HttpResult(
                        url=url,
                        status_code=200,
                        headers=headers,
                        body=pdf_body,
                    )

            config_path = root / "sources.toml"
            config_path.write_text(
                f"""
[collector]
data_dir = "{root.as_posix()}"

[[sources]]
id = "fonte_direta"
name = "Fonte Direta"
enabled = true
start_urls = ["https://provas.example.gov.br/lista"]
allowed_hosts = ["provas.example.gov.br"]
authorization_basis = "Publicacao oficial conferida."
""".strip(),
                encoding="utf-8",
            )
            output_path = root / "resultado.json"
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                report, written_path = run_semiautomatic(
                    config_path=config_path,
                    urls=[direct_url],
                    output_path=output_path,
                )

            self.assertEqual(written_path, output_path)
            self.assertTrue(output_path.exists())
            self.assertEqual(report.metrics.requested_links, 1)
            self.assertEqual(report.metrics.collected_documents, 1)
            self.assertEqual(report.metrics.documents_needing_ocr, 1)


class AutomationTests(unittest.TestCase):
    def test_retry_queue_uses_backoff_and_marks_persistent_failure(self) -> None:
        now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
        failure = CollectionFailure(
            source_id="fonte",
            url="https://example.test/prova.pdf",
            stage="download",
            message="HTTP 503",
            retryable=True,
        )
        state = AutomationState()
        state.retries = update_retry_queue(
            state,
            [failure],
            now=now,
            max_attempts=2,
            base_delay_seconds=30,
        )
        self.assertEqual(state.retries[0].next_attempt_at, now + timedelta(seconds=30))
        state.retries = update_retry_queue(
            state,
            [failure],
            now=now + timedelta(seconds=30),
            max_attempts=2,
            base_delay_seconds=30,
        )
        self.assertTrue(state.retries[0].exhausted)
        self.assertEqual(state.retries[0].attempts, 2)
        state.retries = update_retry_queue(
            state,
            [],
            now=now + timedelta(seconds=60),
            max_attempts=2,
            base_delay_seconds=30,
        )
        self.assertEqual(state.retries, [])

    def test_automatic_run_processes_a_document_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf_path = root / "gabarito.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with pdf_path.open("wb") as handle:
                writer.write(handle)
            body = pdf_path.read_bytes()
            record = document_record(
                suffix="g",
                local_path=str(pdf_path),
                sha256=hashlib.sha256(body).hexdigest(),
                size_bytes=len(body),
                document_type="answer_key",
            )
            manifest = DownloadManifest(created_at=datetime.now(UTC), documents=[record])
            full_manifest_path = root / "manifests" / "download-fixture.json"
            config_path = root / "sources.toml"
            config_path.write_text(
                f"""
[collector]
data_dir = "{root.as_posix()}"

[[sources]]
id = "fonte_g"
name = "Fonte G"
enabled = true
start_urls = ["https://provas.example.gov.br/lista"]
allowed_hosts = ["provas.example.gov.br"]
authorization_basis = "Publicacao oficial conferida."
""".strip(),
                encoding="utf-8",
            )
            state_path = root / "state.json"
            with patch(
                "kad_collector.automation.collect_documents",
                return_value=(manifest, full_manifest_path),
            ):
                first, _ = run_automatic(
                    config_path=config_path,
                    state_path=state_path,
                    output_path=root / "first.json",
                    now=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
                )
                second, _ = run_automatic(
                    config_path=config_path,
                    state_path=state_path,
                    output_path=root / "second.json",
                    now=datetime(2026, 8, 9, 13, 0, tzinfo=UTC),
                )

        self.assertEqual(first.automatic_metrics.new_documents, 1)
        self.assertEqual(second.automatic_metrics.new_documents, 0)
        self.assertEqual(second.automatic_metrics.known_documents, 1)


if __name__ == "__main__":
    unittest.main()
