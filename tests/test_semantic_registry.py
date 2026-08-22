# ruff: noqa: E501

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kad_collector import semantic_registry
from kad_collector.desktop_store import DesktopStore
from kad_collector.document_contract import normalize_local_document

SEMANTIC_TABLES = {
    "semantic_identities", "document_versions", "document_observations",
    "document_observation_origins", "document_links", "question_lineage",
    "document_identity_events",
}
TIMESTAMP = "2026-08-20T00:00:00+00:00"

# This reproduces DesktopStore immediately before the semantic registry. No reusable legacy
# fixture exists, so the DDL includes every table created by the prior initializer.
LEGACY_SCHEMA = """
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL,
 classifier_provider TEXT NOT NULL, total_pages INTEGER NOT NULL DEFAULT 0,
 processed_pages INTEGER NOT NULL DEFAULT 0, current_file TEXT, message TEXT, error TEXT,
 started_at TEXT, eta_seconds INTEGER
);
CREATE TABLE documents (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 local_path TEXT NOT NULL, filename TEXT NOT NULL, sha256 TEXT, size_bytes INTEGER NOT NULL DEFAULT 0,
 page_count INTEGER NOT NULL DEFAULT 0, processed_pages INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'queued', needs_ocr INTEGER NOT NULL DEFAULT 0,
 metadata_json TEXT NOT NULL, normalized_json TEXT, warnings_json TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id, local_path)
);
CREATE TABLE pages (
 document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE, page_number INTEGER NOT NULL,
 text TEXT NOT NULL, character_count INTEGER NOT NULL, status TEXT NOT NULL, error TEXT,
 PRIMARY KEY(document_id, page_number)
);
CREATE TABLE questions (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 question_number INTEGER NOT NULL, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
 classification_json TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0, flags_json TEXT NOT NULL,
 status TEXT NOT NULL, reviewer TEXT, review_notes TEXT, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, exported_at TEXT, UNIQUE(document_id, question_number)
);
CREATE INDEX questions_fingerprint_idx ON questions(fingerprint);
CREATE INDEX questions_status_idx ON questions(status);
CREATE TABLE audit_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
 action TEXT NOT NULL, actor TEXT, created_at TEXT NOT NULL, before_json TEXT, after_json TEXT, notes TEXT
);
CREATE TABLE saved_filters (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, filters_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def _create_legacy_database_with_one_document(database_path: Path) -> str:
    document_id = "legacy-document"
    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            "INSERT INTO jobs (id, created_at, updated_at, status, classifier_provider) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy-job", TIMESTAMP, TIMESTAMP, "queued", "local"),
        )
        connection.execute(
            "INSERT INTO documents (id, job_id, local_path, filename, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (document_id, "legacy-job", "/tmp/legacy.pdf", "legacy.pdf", "{}", TIMESTAMP, TIMESTAMP),
        )
        connection.commit()
    return document_id


def _insert_document(connection: sqlite3.Connection, document_id: str, filename: str) -> None:
    connection.execute(
        "INSERT INTO documents (id, job_id, local_path, filename, metadata_json, created_at, updated_at) "
        "VALUES (?, 'semantic-job', ?, ?, '{}', ?, ?)",
        (document_id, f"/tmp/{filename}", filename, TIMESTAMP, TIMESTAMP),
    )


def _insert_question(connection: sqlite3.Connection, question_id: str, document_id: str) -> None:
    connection.execute(
        "INSERT INTO questions (id, document_id, question_number, fingerprint, payload_json, "
        "classification_json, flags_json, status, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, '{}', '{}', '[]', 'pending', ?, ?)",
        (question_id, document_id, f"fingerprint-{question_id}", TIMESTAMP, TIMESTAMP),
    )


def _insert_version(connection: sqlite3.Connection, version_id: str, role: str, number: int) -> None:
    connection.execute(
        "INSERT INTO document_versions (id, identity_key, document_role, answer_key_state, coverage_json, "
        "profile_json, content_sha256, content_normalizer_version, version_number, created_at, updated_at) "
        "VALUES (?, 'identity-1', ?, 'unknown', '{}', '{}', ?, 'normalizer-v1', ?, ?, ?)",
        (version_id, role, f"content-{version_id}", number, TIMESTAMP, TIMESTAMP),
    )


def _insert_link(connection: sqlite3.Connection, link_id: str, answer_version: str, status: str) -> None:
    connection.execute(
        "INSERT INTO document_links (id, exam_version_id, answer_key_version_id, status, decision_json, "
        "algorithm_version, created_at, updated_at) "
        "VALUES (?, 'exam-version', ?, ?, '{}', 'semantic-v1', ?, ?)",
        (link_id, answer_version, status, TIMESTAMP, TIMESTAMP),
    )


def _insert_lineage(
    connection: sqlite3.Connection, lineage_id: str, number: int, successor_question_id: str | None
) -> None:
    connection.execute(
        "INSERT INTO question_lineage (id, predecessor_version_id, successor_version_id, question_number, "
        "predecessor_question_id, successor_question_id, comparison, content_equal, answer_equal, reason, "
        "created_at) VALUES (?, 'exam-version', 'exam-version-2', ?, 'predecessor-question', ?, "
        "'removed', 0, 0, 'fixture', ?)",
        (lineage_id, number, successor_question_id, TIMESTAMP),
    )


def _semantic_store(database_path: Path) -> DesktopStore:
    store = DesktopStore(database_path)
    with closing(store._connect()) as connection:
        connection.execute(
            "INSERT INTO jobs (id, created_at, updated_at, status, classifier_provider) "
            "VALUES ('semantic-job', ?, ?, 'queued', 'local')",
            (TIMESTAMP, TIMESTAMP),
        )
        for document_id, filename in (
            ("exam-document", "exam.pdf"),
            ("answer-document", "answer.pdf"),
            ("predecessor-document", "predecessor.pdf"),
        ):
            _insert_document(connection, document_id, filename)
        _insert_question(connection, "successor-question", "exam-document")
        _insert_question(connection, "predecessor-question", "predecessor-document")
        connection.execute(
            "INSERT INTO semantic_identities (identity_key, schema_version, algorithm_version, identity_json, "
            "evidence_json, created_at, updated_at) VALUES ('identity-1', 1, 'semantic-v1', ?, ?, ?, ?)",
            (
                json.dumps({"year": 2026}, sort_keys=True, separators=(",", ":")),
                json.dumps({"source": "fixture"}, sort_keys=True, separators=(",", ":")),
                TIMESTAMP, TIMESTAMP,
            ),
        )
        for version_id, role, number in (
            ("exam-version", "exam", 1), ("exam-version-2", "exam", 2),
            ("answer-version-1", "answer_key", 1), ("answer-version-2", "answer_key", 2),
        ):
            _insert_version(connection, version_id, role, number)
        connection.execute(
            "UPDATE documents SET document_version_id = 'exam-version', observation_id = 'observation-1', "
            "semantic_resolution = 'new_identity' WHERE id = 'exam-document'"
        )
        connection.execute(
            "INSERT INTO document_observations (id, binary_sha256, size_bytes, document_id, "
            "document_version_id, resolution_status, first_seen_at, last_seen_at) "
            "VALUES ('observation-1', ?, 1, 'exam-document', 'exam-version', 'new_identity', ?, ?)",
            ("a" * 64, TIMESTAMP, TIMESTAMP),
        )
        connection.commit()
    return store


class SemanticRegistryMigrationTests(unittest.TestCase):
    def test_answer_key_scope_excludes_each_known_mismatch_and_accepts_multicoverage(
        self,
    ) -> None:
        def known(*values: str | int) -> dict[str, object]:
            return {"status": "known", "normalized_values": list(values)}

        core = {"board": known("board"), "concurso": known("contest"), "year": known(2026)}
        exam_profile = {
            "identity": {
                **core,
                "roles": known("analista"), "stage": known("fase 1"),
                "turns": known("manha"), "variants": known("tipo 1"),
            }
        }
        matching_coverage = {
            "roles": known("analista", "tecnico"), "stage": known("fase 1", "fase 2"),
            "turns": known("manha", "tarde"), "variants": known("tipo 1", "tipo 2"),
        }
        with tempfile.TemporaryDirectory() as directory:
            store = _semantic_store(Path(directory) / "collector.sqlite3")
            with closing(store._connect()) as connection:
                connection.execute(
                    "UPDATE document_versions SET profile_json = ? WHERE id = 'exam-version'",
                    (json.dumps(exam_profile),),
                )
                for field, mismatch in (
                    ("roles", "auditor"), ("stage", "fase 3"),
                    ("turns", "noite"), ("variants", "tipo 4"),
                ):
                    with self.subTest(field=field):
                        coverage = {**matching_coverage, field: known(mismatch)}
                        key_profile = {"identity": core, "coverage": coverage}
                        connection.execute(
                            "UPDATE document_versions SET profile_json = ?, coverage_json = ? "
                            "WHERE id = 'answer-version-1'",
                            (json.dumps(key_profile), json.dumps(coverage)),
                        )
                        self.assertEqual(
                            semantic_registry.exam_documents_affected_by_answer_key(
                                connection, "answer-version-1"
                            ),
                            [],
                        )
                        self.assertNotIn(
                            "answer-version-1",
                            {
                                item["answer_key_version_id"]
                                for item in semantic_registry.active_answer_key_candidates(
                                    connection, "exam-version"
                                )
                            },
                        )
                key_profile = {"identity": core, "coverage": matching_coverage}
                connection.execute(
                    "UPDATE document_versions SET profile_json = ?, coverage_json = ? "
                    "WHERE id = 'answer-version-1'",
                    (json.dumps(key_profile), json.dumps(matching_coverage)),
                )
                affected = semantic_registry.exam_documents_affected_by_answer_key(
                    connection, "answer-version-1"
                )
                candidates = semantic_registry.active_answer_key_candidates(
                    connection, "exam-version"
                )

            self.assertEqual([row["id"] for row in affected], ["exam-document"])
            self.assertIn(
                "answer-version-1",
                {item["answer_key_version_id"] for item in candidates},
            )

    def test_registry_prefilter_accepts_equivalent_role_spelling_and_suffix(self) -> None:
        def known(*values: str | int) -> dict[str, object]:
            return {"status": "known", "normalized_values": list(values)}

        core = {"board": known("fgv"), "concurso": known("rfb22"), "year": known(2025)}
        exam_profile = {
            "identity": {
                **core,
                "roles": known("auditor-fiscal da receita federal do brasil"),
                "stage": known("curso de formação"),
                "turns": {"status": "unknown", "normalized_values": []},
                "variants": known("tipo 1"),
            }
        }
        coverage = {
            "roles": known("auditor fiscal"),
            "stage": known("curso de formação"),
            "turns": {"status": "unknown", "normalized_values": []},
            "variants": known("tipo 1"),
        }
        key_profile = {"identity": core, "coverage": coverage}

        with tempfile.TemporaryDirectory() as directory:
            store = _semantic_store(Path(directory) / "collector.sqlite3")
            with closing(store._connect()) as connection:
                connection.execute(
                    "UPDATE document_versions SET profile_json = ? WHERE id = 'exam-version'",
                    (json.dumps(exam_profile),),
                )
                connection.execute(
                    "UPDATE document_versions SET profile_json = ?, coverage_json = ? "
                    "WHERE id = 'answer-version-1'",
                    (json.dumps(key_profile), json.dumps(coverage)),
                )

                affected = semantic_registry.exam_documents_affected_by_answer_key(
                    connection, "answer-version-1"
                )
                candidates = semantic_registry.active_answer_key_candidates(
                    connection, "exam-version"
                )

            self.assertEqual([row["id"] for row in affected], ["exam-document"])
            self.assertIn(
                "answer-version-1",
                {item["answer_key_version_id"] for item in candidates},
            )

    def test_active_candidates_include_new_key_not_yet_linked_to_exam(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = _semantic_store(Path(directory) / "collector.sqlite3")
            with closing(store._connect()) as connection:
                candidates = semantic_registry.active_answer_key_candidates(
                    connection, "exam-version"
                )

            self.assertEqual(
                [candidate["answer_key_version_id"] for candidate in candidates],
                ["answer-version-1", "answer-version-2"],
            )
            self.assertTrue(all(candidate["link_id"] is None for candidate in candidates))

    def test_record_document_link_persists_complete_decision_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = _semantic_store(Path(directory) / "collector.sqlite3")
            from kad_collector.semantic_identity import DocumentAssociationDecision
            decision = DocumentAssociationDecision(
                outcome="selected", selected_version_id="answer-version-1",
                assessments=(), minimum_score=36, minimum_margin=8,
                achieved_margin=None, reason="test", algorithm_version="semantic-association-v1",
            )
            with closing(store._connect()) as connection:
                first = semantic_registry.record_document_link(
                    connection, "exam-version", "answer-version-1", decision, TIMESTAMP
                )
                second = semantic_registry.record_document_link(
                    connection, "exam-version", "answer-version-1", decision, TIMESTAMP
                )
                changed_reason = decision.model_copy(update={"reason": "different explanation"})
                third = semantic_registry.record_document_link(
                    connection, "exam-version", "answer-version-1", changed_reason, TIMESTAMP
                )
                connection.commit()
                links = connection.execute("SELECT status, decision_json FROM document_links").fetchall()
                events = connection.execute(
                    "SELECT action FROM document_identity_events WHERE action LIKE 'association_%'"
                ).fetchall()
            self.assertEqual(first, second)
            self.assertEqual(second, third)
            self.assertEqual(len(links), 1)
            self.assertEqual(json.loads(links[0][1])["selected_version_id"], "answer-version-1")
            self.assertEqual([row[0] for row in events], ["association_selected"])

    def test_record_document_link_preserves_each_active_period_across_a_b_a(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = _semantic_store(Path(directory) / "collector.sqlite3")
            from kad_collector.semantic_identity import DocumentAssociationDecision

            def decision(version_id: str) -> DocumentAssociationDecision:
                return DocumentAssociationDecision(
                    outcome="selected", selected_version_id=version_id,
                    assessments=(), minimum_score=36, minimum_margin=8,
                    achieved_margin=None, reason="test",
                    algorithm_version="semantic-association-v1",
                )

            with closing(store._connect()) as connection:
                first = semantic_registry.record_document_link(
                    connection, "exam-version", "answer-version-1",
                    decision("answer-version-1"), TIMESTAMP,
                )
                second = semantic_registry.record_document_link(
                    connection, "exam-version", "answer-version-2",
                    decision("answer-version-2"), "2026-08-20T00:00:01+00:00",
                )
                third = semantic_registry.record_document_link(
                    connection, "exam-version", "answer-version-1",
                    decision("answer-version-1"), "2026-08-20T00:00:02+00:00",
                )
                connection.commit()
                links = connection.execute(
                    "SELECT id, status, predecessor_link_id FROM document_links "
                    "ORDER BY created_at, id"
                ).fetchall()

            self.assertEqual(len({first, second, third}), 3)
            self.assertEqual([row["status"] for row in links], [
                "superseded", "superseded", "active",
            ])
            by_id = {row["id"]: row for row in links}
            self.assertIsNone(by_id[first]["predecessor_link_id"])
            self.assertEqual(by_id[second]["predecessor_link_id"], first)
            self.assertEqual(by_id[third]["predecessor_link_id"], second)

    def test_legacy_database_adds_semantic_schema_without_touching_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "collector.sqlite3"
            legacy_document_id = _create_legacy_database_with_one_document(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                tables_before = {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                columns_before = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
                self.assertEqual(tables_before & SEMANTIC_TABLES, set())
                self.assertFalse({"document_version_id", "observation_id", "semantic_resolution"} & columns_before)

            store = DesktopStore(database_path)
            with closing(store._connect()) as connection:
                tables = {
                    row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)")}
                row = connection.execute(
                    "SELECT id, job_id, local_path, filename, metadata_json, created_at, updated_at "
                    "FROM documents WHERE id = ?",
                    (legacy_document_id,),
                ).fetchone()
            self.assertTrue(tables >= SEMANTIC_TABLES)
            self.assertTrue({"document_version_id", "observation_id", "semantic_resolution"} <= columns)
            self.assertEqual(
                tuple(row),
                (legacy_document_id, "legacy-job", "/tmp/legacy.pdf", "legacy.pdf", "{}", TIMESTAMP, TIMESTAMP),
            )
            self.assertEqual(store.semantic_document_view(legacy_document_id)["identityStatus"], "unknown")

    def test_initialization_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "collector.sqlite3"
            DesktopStore(database_path)
            DesktopStore(database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                tables = {
                    row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                document_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(documents)")
                }
                question_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(questions)")
                }
            self.assertEqual(len(SEMANTIC_TABLES & tables), 7)
            for name in ("document_version_id", "observation_id", "semantic_resolution"):
                self.assertEqual(document_columns[name][3], 0)
            self.assertEqual(question_columns["decision_fingerprint"][3], 0)

    def test_partial_unique_indexes_enforce_semantic_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = _semantic_store(Path(directory) / "collector.sqlite3")
            with closing(store._connect()) as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                _insert_link(connection, "active-link", "answer-version-1", "active")
                with self.assertRaises(sqlite3.IntegrityError):
                    _insert_link(connection, "second-active-link", "answer-version-2", "active")
                _insert_link(connection, "superseded-link", "answer-version-1", "superseded")
                _insert_link(connection, "rejected-link", "answer-version-2", "rejected")
                _insert_lineage(connection, "successor-lineage", 1, "successor-question")
                with self.assertRaises(sqlite3.IntegrityError):
                    _insert_lineage(connection, "duplicate-successor-lineage", 2, "successor-question")
                _insert_lineage(connection, "removed-lineage", 3, None)
                with self.assertRaises(sqlite3.IntegrityError):
                    _insert_lineage(connection, "duplicate-removed-lineage", 3, None)
                connection.commit()

    def test_semantic_read_adapters_keep_legacy_identity_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "collector.sqlite3"
            legacy_document_id = _create_legacy_database_with_one_document(database_path)
            store = DesktopStore(database_path)
            self.assertEqual(
                store.semantic_summary(),
                {"documents": 1, "known": 0, "unknown": 1, "conflict": 0, "observations": 0, "versions": 0, "events": 0},
            )
            self.assertEqual(store.identity_events(legacy_document_id), [])
            view = store.semantic_document_view(legacy_document_id)
            self.assertEqual(view["documentId"], legacy_document_id)
            self.assertEqual(view["identityStatus"], "unknown")
            self.assertIsNone(view["documentVersionId"])
            self.assertIsNone(view["identityKey"])

    def test_semantic_read_adapters_decode_and_order_populated_registry_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = _semantic_store(Path(directory) / "collector.sqlite3")
            with closing(store._connect()) as connection:
                connection.execute(
                    "INSERT INTO document_identity_events (event_key, document_id, document_version_id, action, "
                    "actor, algorithm_version, payload_json, created_at) VALUES "
                    "('event-document', 'exam-document', 'exam-version', 'observed', 'system', 'semantic-v1', ?, ?)",
                    (json.dumps({"score": 1}, sort_keys=True, separators=(",", ":")), TIMESTAMP),
                )
                connection.execute(
                    "INSERT INTO document_identity_events (event_key, document_id, document_version_id, action, "
                    "actor, algorithm_version, payload_json, created_at) VALUES "
                    "('event-version', NULL, 'exam-version', 'version_created', 'system', 'semantic-v1', ?, "
                    "'2026-08-20T00:00:01+00:00')",
                    (json.dumps({"score": 2}, sort_keys=True, separators=(",", ":")),),
                )
                connection.commit()
            view = store.semantic_document_view("exam-document")
            events = store.identity_events("exam-document")
            self.assertEqual(view["identity"], {"year": 2026})
            self.assertEqual(view["evidence"], {"source": "fixture"})
            self.assertEqual(view["coverage"], {})
            self.assertEqual(view["profile"], {})
            self.assertEqual(view["identityStatus"], "known")
            self.assertEqual([event["eventKey"] for event in events], ["event-version", "event-document"])
            self.assertEqual(events[0]["documentId"], None)
            self.assertEqual(events[0]["documentVersionId"], "exam-version")
            self.assertEqual(events[0]["payload"], {"score": 2})
            self.assertEqual(json.loads(json.dumps(events)), events)


class DocumentObservationClaimTests(unittest.TestCase):
    def test_repeated_origin_updates_last_seen_and_emits_one_idempotent_duplicate_event(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(semantic_registry, "claim_document_observation"),
            "o registro ainda não expõe a barreira de observação",
        )
        claim_document_observation = semantic_registry.claim_document_observation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "prova.pdf"
            path.write_bytes(b"%PDF-1.7\nsemantic claim fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            document = normalize_local_document(path).model_copy(
                update={
                    "title": "Prova oficial",
                    "metadata": {"board": "Banca", "year": 2026},
                }
            )

            with closing(store._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                first = claim_document_observation(connection, document, TIMESTAMP)
                connection.commit()
                before = connection.execute(
                    "SELECT COUNT(*) FROM document_identity_events WHERE action = 'exact_duplicate'"
                ).fetchone()[0]

                connection.execute("BEGIN IMMEDIATE")
                second = claim_document_observation(
                    connection, document, "2026-08-20T00:00:01+00:00"
                )
                connection.commit()
                after_second = connection.execute(
                    "SELECT COUNT(*) FROM document_identity_events WHERE action = 'exact_duplicate'"
                ).fetchone()[0]

                connection.execute("BEGIN IMMEDIATE")
                third = claim_document_observation(
                    connection, document, "2026-08-20T00:00:02+00:00"
                )
                connection.commit()
                after_third = connection.execute(
                    "SELECT COUNT(*) FROM document_identity_events WHERE action = 'exact_duplicate'"
                ).fetchone()[0]
                observation = connection.execute(
                    "SELECT last_seen_at FROM document_observations WHERE binary_sha256 = ?",
                    (document.sha256,),
                ).fetchone()
                origin_count = connection.execute(
                    "SELECT COUNT(*) FROM document_observation_origins"
                ).fetchone()[0]

            self.assertFalse(first.exact_duplicate)
            self.assertTrue(second.exact_duplicate)
            self.assertTrue(third.exact_duplicate)
            self.assertEqual(first.observation_id, second.observation_id)
            self.assertEqual(second.observation_id, third.observation_id)
            self.assertEqual(origin_count, 1)
            self.assertEqual(observation["last_seen_at"], "2026-08-20T00:00:02+00:00")
            self.assertEqual(after_second, before + 1)
            self.assertEqual(after_third, after_second)

    def test_same_sha_from_a_new_origin_preserves_both_origins(self) -> None:
        self.assertTrue(
            hasattr(semantic_registry, "claim_document_observation"),
            "o registro ainda não expõe a barreira de observação",
        )
        claim_document_observation = semantic_registry.claim_document_observation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "prova.pdf"
            path.write_bytes(b"%PDF-1.7\nmultiple origin fixture\n")
            store = DesktopStore(root / "collector.sqlite3")
            direct = normalize_local_document(path)
            collected = direct.model_copy(
                update={
                    "entry_method": "automated_collection",
                    "original_url": "https://example.test/prova.pdf",
                    "resolved_url": "https://cdn.example.test/prova.pdf",
                    "source_id": "official-source",
                }
            )

            with closing(store._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                first = claim_document_observation(connection, direct, TIMESTAMP)
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                second = claim_document_observation(
                    connection, collected, "2026-08-20T00:00:01+00:00"
                )
                connection.commit()
                origins = connection.execute(
                    "SELECT origin_key, normalized_json FROM document_observation_origins "
                    "ORDER BY origin_key"
                ).fetchall()

            self.assertFalse(first.exact_duplicate)
            self.assertTrue(second.exact_duplicate)
            self.assertEqual(len(origins), 2)
            self.assertNotEqual(first.origin_key, second.origin_key)
            self.assertEqual(
                {json.loads(row["normalized_json"])["entry_method"] for row in origins},
                {"direct_import", "automated_collection"},
            )


if __name__ == "__main__":
    unittest.main()
