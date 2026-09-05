from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from pypdf import PdfWriter

from kad_collector.desktop_store import DesktopStore
from kad_collector.real_homologation import (
    RealHomologationError,
    _check_download_policy,
    _entry_metrics,
    corpus_entries,
    load_real_homologation,
    validate_corpus_file,
)

REAL_MANIFEST = Path("tests/homologation/real-corpus.v1.toml")


class RealHomologationManifestTests(unittest.TestCase):
    def test_manifest_expands_to_required_banks_and_document_count(self) -> None:
        entries = corpus_entries(load_real_homologation(REAL_MANIFEST))

        self.assertEqual(len(entries), 23)
        self.assertEqual({entry.bank for entry in entries}, {"FGV", "Cebraspe", "FCC"})
        self.assertEqual(sum(entry.bank == "FGV" for entry in entries), 19)
        self.assertTrue(any(entry.kind == "other" for entry in entries))
        cebraspe_exam = next(
            entry for entry in entries if entry.id == "cebraspe-pcdf13-agent-exam"
        )
        self.assertEqual(cebraspe_exam.metadata.document_type, "exam")

    def test_manifest_rejects_a_path_outside_the_repository(self) -> None:
        original = REAL_MANIFEST.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(dir=".") as directory:
            path = Path(directory) / "manifest.toml"
            path.write_text(
                original.replace(
                    'official_manifests = ["../regression/rfb22/manifest.v1.toml"]',
                    'official_manifests = ["../../../../outside.toml"]',
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RealHomologationError, "inside the repository"):
                load_real_homologation(path)

    def test_fixture_validation_checks_signature_size_hash_and_pages(self) -> None:
        entries = corpus_entries(load_real_homologation(REAL_MANIFEST))
        entry = entries[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.pdf"
            payload = b"not a PDF"
            path.write_bytes(payload)
            invalid = type(entry)(
                **{
                    **entry.__dict__,
                    "path": path,
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )

            with self.assertRaisesRegex(RealHomologationError, "signature"):
                validate_corpus_file(invalid)

    def test_valid_pdf_checks_hash_and_page_count(self) -> None:
        entry = corpus_entries(load_real_homologation(REAL_MANIFEST))[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(path)
            valid = replace(
                entry, path=path, page_count=1, size_bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            validate_corpus_file(valid)
            with self.assertRaisesRegex(RealHomologationError, "SHA-256"):
                validate_corpus_file(replace(valid, sha256="0" * 64))
            with self.assertRaisesRegex(RealHomologationError, "page count"):
                validate_corpus_file(replace(valid, page_count=2))

    def test_direct_script_entrypoints_work_without_network(self) -> None:
        for script in ("prepare_real_homologation.py", "run_real_homologation.py"):
            result = subprocess.run(
                [sys.executable, str(Path("scripts") / script), "--help"],
                capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_manual_triage_is_not_counted_as_automatic_accuracy(self) -> None:
        entry = corpus_entries(load_real_homologation(REAL_MANIFEST))[0]
        entry = replace(
            entry,
            kind="exam", expected_triage="exam", path=Path("ambiguous.pdf").resolve(),
            metadata=entry.metadata.model_copy(update={"document_title": "Sem título"}),
        )
        document = {
            "id": "synthetic", "local_path": str(entry.path), "status": "exception",
            "warnings": [], "triage": {
                "decision": "exam", "confidence": 1, "source": "manual",
            },
        }
        store = cast(DesktopStore, SimpleNamespace(
            documents_for_job=lambda _: [document],
            pages=lambda _: [{"status": "text", "text": "Texto sem estrutura reconhecível."}],
            question_records=lambda _: [], job=lambda _: {"status": "completed"},
        ))
        metrics = _entry_metrics(entry, store, "job", 0.1, 100)
        self.assertFalse(metrics["classification_correct"])
        self.assertEqual(metrics["automatic_triage"]["decision"], "review")
        self.assertEqual(metrics["triage_source"], "manual")
        self.assertEqual(metrics["result"], "partial")

    def test_unknown_download_host_is_rejected_before_network(self) -> None:
        entry = corpus_entries(load_real_homologation(REAL_MANIFEST))[-1]
        with patch("kad_collector.real_homologation.build_opener") as opener:
            for url in ("https://127.0.0.1/file.pdf", "https://evil.example/file.pdf"):
                with self.assertRaisesRegex(RealHomologationError, "download host"):
                    _check_download_policy(replace(entry, source_url=url))
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
