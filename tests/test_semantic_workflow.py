from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kad_collector.desktop_processor import DesktopProcessor
from kad_collector.desktop_store import DesktopStore
from kad_collector.document_contract import NormalizedDocument
from kad_collector.semantic_identity import (
    AnswerKeyCoverage,
    ContentFingerprint,
    DocumentSemanticProfile,
    ExamSemanticIdentity,
    KnownDocumentVersion,
    SemanticField,
)
from kad_collector.semantic_registry import claim_document_observation
from kad_collector.semantic_resolution import decide_document_version


def profile(
    *, key: str | None = "identity", sha: str = "sha", conflict: bool = False
) -> DocumentSemanticProfile:
    known = SemanticField(
        status="known", normalized_values=("x",), method="test", reason="test", confidence=1.0
    )
    identity = ExamSemanticIdentity(
        board=known,
        concurso=known,
        organization=SemanticField.unknown("test"),
        year=known,
        roles=SemanticField.unknown("test"),
        stage=SemanticField.unknown("test"),
        turns=SemanticField.unknown("test"),
        variants=SemanticField.unknown("test"),
    )
    return DocumentSemanticProfile(
        identity=identity,
        identity_key=key,
        document_role="exam",
        coverage=AnswerKeyCoverage(
            roles=identity.roles,
            stage=identity.stage,
            turns=identity.turns,
            variants=identity.variants,
        ),
        content_fingerprint=ContentFingerprint(
            sha256=sha, page_sha256s=(), page_count=1, character_count=1
        ),
        has_conflict=conflict,
    )


class SemanticResolutionDecisionTests(unittest.TestCase):
    def test_decision_has_the_five_expected_outcomes(self) -> None:
        current = KnownDocumentVersion(
            version_id="v1",
            identity_key="identity",
            document_role="exam",
            content_sha256="sha-1",
            version_number=1,
        )
        self.assertEqual(decide_document_version(profile(key=None), ()).outcome, "uncertain")
        self.assertEqual(
            decide_document_version(profile(sha="sha-1"), (current,)).outcome, "republication"
        )
        self.assertEqual(
            decide_document_version(profile(sha="sha-2"), (current,)).outcome, "new_version"
        )
        self.assertEqual(
            decide_document_version(profile(key="other"), (current,)).outcome, "new_identity"
        )
        self.assertEqual(
            decide_document_version(profile(conflict=True), (current,)).outcome, "uncertain"
        )

    def test_changed_content_can_add_or_remove_questions_without_changing_outcome(self) -> None:
        current = KnownDocumentVersion(
            version_id="v1",
            identity_key="identity",
            document_role="exam",
            content_sha256="sha-1",
            version_number=1,
        )
        for sha in ("added-question", "removed-question"):
            self.assertEqual(
                decide_document_version(profile(sha=sha), (current,)).outcome, "new_version"
            )


class SemanticWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = DesktopStore(Path(self.directory.name) / "collector.sqlite3")
        self.now = "2026-08-21T00:00:00+00:00"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def add_document(
        self, document_id: str, *, binary: bytes, text: str, metadata: dict[str, str | int]
    ) -> None:
        path = Path(self.directory.name) / f"{document_id}.pdf"
        path.write_bytes(binary)
        normalized = NormalizedDocument(
            local_path=str(path),
            sha256=hashlib.sha256(binary).hexdigest(),
            size_bytes=len(binary),
            title="Prova 2026",
            entry_method="direct_import",
            metadata=metadata,
        )
        with closing(self.store._connect()) as connection:
            connection.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, classifier_provider) "
                "VALUES (?, ?, ?, 'queued', 'local')",
                (f"job-{document_id}", self.now, self.now),
            )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            claim = claim_document_observation(connection, normalized, self.now)
            connection.execute(
                "INSERT INTO documents (id, job_id, local_path, filename, sha256, size_bytes, "
                "metadata_json, normalized_json, warnings_json, created_at, updated_at, "
                "observation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)",
                (
                    document_id,
                    f"job-{document_id}",
                    str(path),
                    path.name,
                    normalized.sha256,
                    normalized.size_bytes,
                    json.dumps(metadata),
                    normalized.model_dump_json(),
                    self.now,
                    self.now,
                    claim.observation_id,
                ),
            )
            connection.execute(
                "UPDATE document_observations SET document_id = ? WHERE id = ?",
                (document_id, claim.observation_id),
            )
            connection.commit()
        self.store.save_page(document_id, 1, text, status="text")

    def resolve(self, document_id: str):
        return self.store.resolve_extracted_document(document_id)

    def test_equivalent_text_with_different_bytes_is_republication(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\r\nQuestão 1\r\nA) Azul   B) Verde",
            metadata=metadata,
        )
        first, second = self.resolve("first"), self.resolve("second")
        self.assertEqual(second.outcome, "republication")
        self.assertEqual(second.document_version_id, first.document_version_id)
        self.assertEqual(self.store.semantic_summary()["versions"], 1)
        self.assertEqual(self.store.semantic_summary()["observations"], 2)
        self.assertEqual(self.store.semantic_summary()["events"], 4)

    def test_same_identity_with_changed_content_creates_successor(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Vermelho",
            metadata=metadata,
        )
        first, second = self.resolve("first"), self.resolve("second")
        self.assertEqual(second.outcome, "new_version")
        self.assertEqual(second.predecessor_version_id, first.document_version_id)
        self.assertEqual(second.version_number, 2)

    def test_repeating_resolution_for_same_document_is_idempotent(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first", binary=b"pdf-one", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        first = self.resolve("first")
        event_count = self.store.semantic_summary()["events"]
        second = self.resolve("first")
        self.assertEqual(second.outcome, first.outcome)
        self.assertEqual(second.document_version_id, first.document_version_id)
        self.assertEqual(second.version_number, first.version_number)
        self.assertEqual(self.store.semantic_summary()["events"], event_count)

    def test_changed_content_with_question_added_creates_successor(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first", binary=b"pdf-one", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul\nQuestão 2\nA) Verde",
            metadata=metadata,
        )
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.resolve("second").outcome, "new_version")

    def test_changed_content_with_question_removed_creates_successor(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul\nQuestão 2\nA) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second", binary=b"pdf-two", text="Prova 2026\nQuestão 1\nA) Azul", metadata=metadata
        )
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.resolve("second").outcome, "new_version")

    def test_insufficient_identity_is_uncertain_and_not_structured(self) -> None:
        self.add_document(
            "unknown", binary=b"pdf-one", text="Questão 1\nA) Azul B) Verde", metadata={}
        )
        result = self.resolve("unknown")
        self.assertEqual(result.outcome, "uncertain")
        self.assertIsNone(result.document_version_id)
        self.assertEqual(self.store.semantic_summary()["versions"], 0)
        self.assertIn("identidade semântica insuficiente", result.reason)

    def test_processor_does_not_structure_resolution_failure(self) -> None:
        self.add_document(
            "unknown", binary=b"pdf-one", text="Questão 1\nA) Azul B) Verde", metadata={}
        )
        self.resolve("unknown")
        processor = DesktopProcessor(self.store)
        try:
            with patch(
                "kad_collector.desktop_processor.parse_question_pages",
                side_effect=AssertionError("parser não deveria ser chamado"),
            ):
                processor._structure_job("job-unknown", threading.Event())
        finally:
            processor._executor.shutdown(wait=True)
        with closing(self.store._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 0)

    def test_new_identity_and_changed_content_are_structurable_once(self) -> None:
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata={"board": "Banca", "concurso": "A", "year": 2026},
        )
        self.add_document(
            "other",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata={"board": "Banca", "concurso": "B", "year": 2026},
        )
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.resolve("other").outcome, "new_identity")
        self.assertEqual(self.store.semantic_summary()["versions"], 2)

    def test_republication_adds_origin_without_new_questions(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        first, second = self.resolve("first"), self.resolve("second")
        with closing(self.store._connect()) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM document_observation_origins").fetchone()[
                    0
                ],
                2,
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 0)
        self.assertEqual(first.document_version_id, second.document_version_id)

    def test_concurrent_republications_share_one_version(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        self.add_document(
            "second",
            binary=b"pdf-two",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        results, errors = [], []

        def run(document_id: str) -> None:
            try:
                results.append(self.resolve(document_id))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(item,)) for item in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len({result.document_version_id for result in results}), 1)
        self.assertEqual(self.store.semantic_summary()["versions"], 1)

    def test_reprocessing_resumes_failed_resolution_without_duplicate_event(self) -> None:
        metadata = {"board": "Banca", "concurso": "Concurso", "year": 2026}
        self.add_document(
            "first",
            binary=b"pdf-one",
            text="Prova 2026\nQuestão 1\nA) Azul B) Verde",
            metadata=metadata,
        )
        with (
            patch("kad_collector.semantic_resolution._event", side_effect=RuntimeError("injected")),
            self.assertRaises(RuntimeError),
        ):
            self.resolve("first")
        self.assertEqual(self.resolve("first").outcome, "new_identity")
        self.assertEqual(self.store.semantic_summary()["events"], 2)


if __name__ == "__main__":
    unittest.main()
