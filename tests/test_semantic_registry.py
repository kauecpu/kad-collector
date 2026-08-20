import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kad_collector.desktop_store import DesktopStore

SEMANTIC_TABLES = {
    "semantic_identities",
    "document_versions",
    "document_observations",
    "document_observation_origins",
    "document_links",
    "question_lineage",
    "document_identity_events",
}


def _legacy_store_with_one_document(database_path: Path) -> tuple[DesktopStore, str]:
    store = DesktopStore(database_path)
    document_id = "legacy-document"
    timestamp = "2026-08-20T00:00:00+00:00"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "INSERT INTO jobs (id, created_at, updated_at, status, classifier_provider) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy-job", timestamp, timestamp, "queued", "local"),
        )
        connection.execute(
            "INSERT INTO documents ("
            "id, job_id, local_path, filename, metadata_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                "legacy-job",
                "/tmp/legacy.pdf",
                "legacy.pdf",
                "{}",
                timestamp,
                timestamp,
            ),
        )
        connection.commit()
    return store, document_id


class SemanticRegistryMigrationTests(unittest.TestCase):
    def test_legacy_database_adds_semantic_schema_without_touching_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "collector.sqlite3"
            store, legacy_document_id = _legacy_store_with_one_document(database_path)

            with closing(store._connect()) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
                self.assertTrue(tables >= SEMANTIC_TABLES)
                self.assertTrue(
                    {"document_version_id", "observation_id", "semantic_resolution"} <= columns
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1
                )

            self.assertEqual(
                store.semantic_document_view(legacy_document_id)["identityStatus"], "unknown"
            )

    def test_initialization_is_idempotent_and_enforces_semantic_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "collector.sqlite3"
            DesktopStore(database_path)
            DesktopStore(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertEqual(len(SEMANTIC_TABLES & tables), 7)
                document_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(documents)")
                }
                question_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(questions)")
                }
                for name in ("document_version_id", "observation_id", "semantic_resolution"):
                    self.assertEqual(document_columns[name][3], 0)
                self.assertEqual(question_columns["decision_fingerprint"][3], 0)

                indexes = {
                    row[0]: row[1]
                    for row in connection.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
                    )
                }
                self.assertIn("document_links_one_active_exam_idx", indexes)
                self.assertIn("question_lineage_successor_question_idx", indexes)
                self.assertIn("question_lineage_version_question_idx", indexes)

    def test_semantic_read_adapters_keep_legacy_identity_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "collector.sqlite3"
            store, legacy_document_id = _legacy_store_with_one_document(database_path)

            self.assertEqual(
                store.semantic_summary(),
                {
                    "documents": 1,
                    "known": 0,
                    "unknown": 1,
                    "conflict": 0,
                    "observations": 0,
                    "versions": 0,
                    "events": 0,
                },
            )
            self.assertEqual(store.identity_events(legacy_document_id), [])
            view = store.semantic_document_view(legacy_document_id)
            self.assertEqual(view["documentId"], legacy_document_id)
            self.assertEqual(view["identityStatus"], "unknown")
            self.assertIsNone(view["documentVersionId"])
            self.assertIsNone(view["identityKey"])


if __name__ == "__main__":
    unittest.main()
