from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from kad_collector.canonical_identity import (
    CANONICAL_IDENTITY_ALGORITHM_VERSION,
    CanonicalIdentityConflict,
    canonical_entity_counts,
    canonical_identity_for_version,
    canonicalize_profile_for_version,
    contest_inventory,
    resolve_application,
    resolve_contest_alias,
    run_canonical_identity_migration,
)
from kad_collector.desktop_models import DesktopImportMetadata
from kad_collector.desktop_store import DesktopStore
from kad_collector.semantic_identity import DocumentSemanticProfile

RFB22_MANIFEST = Path("tests/regression/rfb22/manifest.v1.toml")


def write_synthetic_manifest(
    path: Path,
    *,
    contest_id: str = "synthetic-contest-2026",
    alias: str = "SYN26",
    hostname: str = "example.test",
    exam_sha256: str = "a" * 64,
) -> Path:
    answer_sha256 = "b" * 64
    path.write_text(
        f'''schema_version = 1
id = "{contest_id}"
contest_name = "Concurso sintético de 2026"
contest_aliases = ["{alias}"]
organization = "Órgão sintético"
board = "Banca sintética"
notice_year = 2026
source_page_url = "https://{hostname}/concursos/2026"
evidence_urls = ["https://{hostname}/edital.pdf"]
robots_policy = "enforce"
crawl_delay_policy = "enforce"

[[applications]]
id = "synthetic-main-2026-05-10"
title = "Prova principal"
stage = "Primeira etapa - prova objetiva"
application_date = 2026-05-10
support_status = "supported"
notes = "Fixture local sem acesso à rede."

[[documents]]
id = "synthetic-answer-key"
kind = "answer_key"
path = "official/answer-key.pdf"
source_url = "https://{hostname}/answer-key.pdf"
size_bytes = 100
page_count = 1
sha256 = "{answer_sha256}"
title = "Gabarito definitivo"
application_id = "synthetic-main-2026-05-10"
published_on = 2026-05-11
roles = ["Analista"]
content_kinds = ["answer_key"]
answer_key_status = "definitive"
answer_scopes = [
  {{role="Analista",shift="Manhã",booklet_types=[1],first=1,last=2,count=2}},
]

[[documents]]
id = "synthetic-exam"
kind = "exam"
path = "official/exam.pdf"
source_url = "https://{hostname}/exam.pdf"
size_bytes = 100
page_count = 1
sha256 = "{exam_sha256}"
title = "Prova de Analista - tipo 1"
application_id = "synthetic-main-2026-05-10"
published_on = 2026-05-11
roles = ["Analista"]
shift = "Manhã"
booklet_type = 1
content_kinds = ["objective"]
sections = [{{kind="objective",first=1,last=2,count=2}}]
answer_key_id = "synthetic-answer-key"
''',
        encoding="utf-8",
    )
    return path


class CanonicalIdentityCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = DesktopStore(self.root / "collector.sqlite3")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def migrate_rfb22(self, *, run_id: str = "rfb22-canonical") -> dict[str, object]:
        with closing(self.store._connect()) as connection:
            report = run_canonical_identity_migration(
                connection,
                manifest_paths=[RFB22_MANIFEST],
                contest_alias="RFB22",
                apply=True,
                run_id=run_id,
            )
        return report.as_dict()

    def test_rfb22_alias_resolves_to_one_contest_with_five_applications(self) -> None:
        report = self.migrate_rfb22()
        with closing(self.store._connect()) as connection:
            resolution = resolve_contest_alias(connection, " rfb22 ")
            inventory = contest_inventory(connection, "RFB22")
            counts = canonical_entity_counts(connection)
            application = resolve_application(
                connection,
                str(resolution.contest_id),
                application_date="2023-03-19",
            )
            ambiguous = resolve_application(connection, str(resolution.contest_id))

        self.assertEqual(report["algorithmVersion"], CANONICAL_IDENTITY_ALGORITHM_VERSION)
        self.assertEqual(resolution.outcome, "selected")
        self.assertEqual(counts["canonical_contests"], 1)
        self.assertEqual(counts["exam_applications"], 5)
        self.assertEqual(counts["application_stages"], 5)
        self.assertEqual(counts["contest_roles"], 2)
        self.assertEqual(counts["contest_role_aliases"], 2)
        self.assertEqual(counts["application_shifts"], 2)
        self.assertEqual(counts["application_booklets"], 4)
        self.assertEqual(counts["application_scopes"], 16)
        self.assertEqual(counts["canonical_documents"], 19)
        self.assertEqual(counts["canonical_document_scopes"], 56)
        self.assertEqual(len(inventory["applications"]), 5)
        self.assertEqual(inventory["documentCounts"], {"answer_key": 3, "exam": 16})
        self.assertEqual(application.outcome, "selected")
        self.assertEqual(ambiguous.outcome, "ambiguous")

    def test_rfb22_booklets_and_answer_keys_share_only_declared_scopes(self) -> None:
        self.migrate_rfb22()
        with closing(self.store._connect()) as connection:
            exams = connection.execute(
                "SELECT id, application_id FROM canonical_documents "
                "WHERE document_kind = 'exam' ORDER BY id"
            ).fetchall()
            main_application = connection.execute(
                "SELECT id FROM exam_applications WHERE application_date = '2023-03-19'"
            ).fetchone()[0]
            exam_scope_counts = [
                connection.execute(
                    "SELECT COUNT(DISTINCT scope_id) FROM canonical_document_scopes "
                    "WHERE document_id = ?",
                    (row["id"],),
                ).fetchone()[0]
                for row in exams
            ]
            cross_application = connection.execute(
                """
                SELECT cds.document_id
                FROM canonical_document_scopes cds
                JOIN application_scopes s ON s.id = cds.scope_id
                JOIN canonical_documents d ON d.id = cds.document_id
                WHERE s.application_id != d.application_id
                """
            ).fetchall()

        self.assertEqual(len(exams), 16)
        self.assertTrue(all(row["application_id"] == main_application for row in exams))
        self.assertEqual(exam_scope_counts, [1] * 16)
        self.assertEqual(cross_application, [])

    def test_repeated_migration_keeps_identifiers_and_counts(self) -> None:
        first = self.migrate_rfb22(run_id="repeatable-run")
        with closing(self.store._connect()) as connection:
            contest_before = connection.execute(
                "SELECT id FROM canonical_contests"
            ).fetchone()[0]
            connection.execute(
                "UPDATE canonical_contests SET display_name = 'Nome editorial revisado'"
            )
            connection.commit()
            second = run_canonical_identity_migration(
                connection,
                manifest_paths=[RFB22_MANIFEST],
                contest_alias="RFB22",
                apply=True,
                run_id="repeatable-run",
            ).as_dict()
            contest_after = connection.execute(
                "SELECT id FROM canonical_contests"
            ).fetchone()[0]
            run_count = connection.execute(
                "SELECT COUNT(*) FROM canonical_identity_migration_runs"
            ).fetchone()[0]
        self.assertEqual(first["entityCounts"], second["entityCounts"])
        self.assertEqual(contest_before, contest_after)
        self.assertEqual(run_count, 1)

    def test_dry_run_rolls_back_catalog_and_review_records(self) -> None:
        manifest = write_synthetic_manifest(self.root / "synthetic.toml")
        with closing(self.store._connect()) as connection:
            report = run_canonical_identity_migration(
                connection,
                manifest_paths=[manifest],
                contest_alias="SYN26",
            )
            counts = canonical_entity_counts(connection)
            runs = connection.execute(
                "SELECT COUNT(*) FROM canonical_identity_migration_runs"
            ).fetchone()[0]
        self.assertEqual(report.mode, "dry-run")
        self.assertEqual(counts["canonical_contests"], 0)
        self.assertEqual(runs, 0)

    def test_unknown_and_ambiguous_aliases_do_not_select_a_contest(self) -> None:
        self.migrate_rfb22()
        with closing(self.store._connect()) as connection:
            unknown = resolve_contest_alias(connection, "SEM-CADASTRO")
            second_id = str(uuid.uuid4())
            now = "2026-08-23T00:00:00+00:00"
            connection.execute(
                "INSERT INTO canonical_contests "
                "(id, canonical_key, official_name, display_name, notice_year, board, "
                "organization, source_url, evidence_json, created_at, updated_at) "
                "VALUES (?, 'other-contest', 'Outro', 'Outro', 2026, 'Outra banca', "
                "'Outro órgão', 'https://other.test', '{}', ?, ?)",
                (second_id, now, now),
            )
            connection.execute(
                "INSERT INTO contest_aliases "
                "(id, contest_id, raw_value, normalized_value, alias_type, source_context, "
                "source_url, evidence_json, status, created_at, updated_at) "
                "VALUES (?, ?, 'RFB22', 'rfb22', 'input_alias', 'other.test', "
                "'https://other.test', '{}', 'active', ?, ?)",
                (str(uuid.uuid4()), second_id, now, now),
            )
            connection.commit()
            ambiguous = resolve_contest_alias(connection, "RFB22")
            scoped = resolve_contest_alias(
                connection, "RFB22", source_context="conhecimento.fgv.br"
            )
        self.assertEqual(unknown.outcome, "unknown")
        self.assertEqual(ambiguous.outcome, "ambiguous")
        self.assertEqual(scoped.outcome, "selected")

    def test_alias_collision_rolls_back_the_second_manifest(self) -> None:
        first = write_synthetic_manifest(
            self.root / "first.toml", contest_id="first-contest", alias="COLLIDE"
        )
        second = write_synthetic_manifest(
            self.root / "second.toml", contest_id="second-contest", alias="COLLIDE"
        )
        with closing(self.store._connect()) as connection:
            run_canonical_identity_migration(
                connection, manifest_paths=[first], apply=True, run_id="first"
            )
            with self.assertRaises(CanonicalIdentityConflict):
                run_canonical_identity_migration(
                    connection, manifest_paths=[second], apply=True, run_id="second"
                )
            contests = connection.execute(
                "SELECT canonical_key FROM canonical_contests ORDER BY canonical_key"
            ).fetchall()
            failed_run = connection.execute(
                "SELECT 1 FROM canonical_identity_migration_runs WHERE id = 'second'"
            ).fetchone()
        self.assertEqual([row["canonical_key"] for row in contests], ["first-contest"])
        self.assertIsNone(failed_run)

    def test_generic_manifest_uses_the_same_model_without_bank_rules(self) -> None:
        manifest = write_synthetic_manifest(self.root / "synthetic.toml")
        with closing(self.store._connect()) as connection:
            run_canonical_identity_migration(
                connection,
                manifest_paths=[manifest],
                contest_alias="SYN26",
                apply=True,
                run_id="synthetic",
            )
            inventory = contest_inventory(connection, "SYN26")
            scopes = connection.execute(
                "SELECT COUNT(*) FROM application_scopes"
            ).fetchone()[0]
        self.assertEqual(inventory["outcome"], "selected")
        self.assertEqual(inventory["documentCounts"], {"answer_key": 1, "exam": 1})
        self.assertEqual(scopes, 1)


class CanonicalLegacyMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = self.root / "collector.sqlite3"
        self.store = DesktopStore(self.database)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_backfill_preserves_semantic_history_and_uses_canonical_ids(self) -> None:
        source = self.root / "exam.pdf"
        source.write_bytes(
            b"%PDF-1.4\nBanca: Banca sintetica\nConcurso: SYN26\nAno: 2026\n%%EOF"
        )
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest = write_synthetic_manifest(
            self.root / "synthetic.toml", exam_sha256=digest
        )
        metadata = DesktopImportMetadata(
            external_id="synthetic-exam",
            document_title="Prova sintética",
            document_type="exam",
            concurso="SYN26",
            board="Banca sintética",
            organization="Órgão sintético",
            year=2026,
            role="Analista",
            stage="Primeira etapa - prova objetiva",
            turn="Manhã",
            variant="Tipo 1",
        )
        job_id = self.store.create_job([source], metadata, "local")
        document = self.store.documents_for_job(job_id)[0]
        document_id = str(document["id"])
        self.store.save_page(
            document_id,
            1,
            "Banca: Banca sintética\nConcurso: SYN26\nAno: 2026\nCargo: Analista\n"
            "Etapa: Primeira etapa - prova objetiva\nTurno: Manhã\nTipo: 1",
            status="text",
        )
        self.store.update_document(document_id, status="extracted", page_count=1)
        resolution = self.store.resolve_extracted_document(document_id)
        version_id = str(resolution.document_version_id)
        with closing(self.store._connect()) as connection:
            before = {
                "versions": connection.execute(
                    "SELECT COUNT(*) FROM document_versions"
                ).fetchone()[0],
                "observations": connection.execute(
                    "SELECT COUNT(*) FROM document_observations"
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM document_identity_events"
                ).fetchone()[0],
            }
            raw_profile = DocumentSemanticProfile.model_validate_json(
                connection.execute(
                    "SELECT profile_json FROM document_versions WHERE id = ?", (version_id,)
                ).fetchone()[0]
            )
            report = run_canonical_identity_migration(
                connection,
                manifest_paths=[manifest],
                contest_alias="SYN26",
                apply=True,
                run_id="legacy-backfill",
            )
            after = {
                "versions": connection.execute(
                    "SELECT COUNT(*) FROM document_versions"
                ).fetchone()[0],
                "observations": connection.execute(
                    "SELECT COUNT(*) FROM document_observations"
                ).fetchone()[0],
                "events": connection.execute(
                    "SELECT COUNT(*) FROM document_identity_events"
                ).fetchone()[0],
            }
            canonical = canonical_identity_for_version(connection, version_id)
            canonical_profile = canonicalize_profile_for_version(
                connection, version_id, raw_profile
            )
            mapping = connection.execute(
                "SELECT evidence_json FROM canonical_identity_mappings "
                "WHERE legacy_kind = 'document_version' AND legacy_id = ?",
                (version_id,),
            ).fetchone()

        self.assertEqual(report.mapped_documents, 1)
        self.assertEqual(report.mapped_versions, 1)
        self.assertEqual(before, after)
        self.assertIsNotNone(canonical)
        assert canonical is not None
        self.assertEqual(len(canonical["scopeIds"]), 1)
        self.assertEqual(
            canonical_profile.identity.concurso.normalized_values,
            (canonical["contestId"],),
        )
        self.assertNotEqual(canonical_profile.identity_key, raw_profile.identity_key)
        self.assertEqual(json.loads(mapping["evidence_json"])["sha256"], digest)

    def test_unmatched_legacy_document_enters_review_without_invented_application(self) -> None:
        source = self.root / "unknown.pdf"
        source.write_bytes(b"%PDF-1.4\nunknown\n%%EOF")
        job_id = self.store.create_job(
            [source],
            DesktopImportMetadata(
                external_id="not-in-manifest",
                document_type="exam",
                concurso="SYN26",
            ),
            "local",
        )
        document_id = str(self.store.documents_for_job(job_id)[0]["id"])
        manifest = write_synthetic_manifest(self.root / "synthetic.toml")
        with closing(self.store._connect()) as connection:
            report = run_canonical_identity_migration(
                connection,
                manifest_paths=[manifest],
                apply=True,
                run_id="unresolved",
            )
            review = connection.execute(
                "SELECT reason, status FROM canonical_identity_review_queue "
                "WHERE legacy_id = ?",
                (document_id,),
            ).fetchone()
            canonical_document_id = connection.execute(
                "SELECT canonical_document_id FROM documents WHERE id = ?", (document_id,)
            ).fetchone()[0]
        self.assertEqual(report.unresolved_documents, 1)
        self.assertEqual(review["status"], "pending")
        self.assertIsNone(canonical_document_id)

    def test_multi_scope_answer_key_remains_known_after_canonicalization(self) -> None:
        source = self.root / "rfb22-answer-key.pdf"
        source.write_bytes(b"%PDF-1.4\nanswer key\n%%EOF")
        metadata = DesktopImportMetadata(
            external_id="rfb22-main-2023-answer-key-preliminary",
            document_title="Gabarito preliminar RFB22",
            document_type="answer_key",
            concurso="RFB22",
            board="Fundação Getulio Vargas",
            organization="Receita Federal do Brasil",
            year=2022,
            role="Auditor-Fiscal da Receita Federal do Brasil",
            stage="Primeira etapa - provas objetiva e discursiva",
            turn="Manhã",
            variant="Tipo 1",
        )
        job_id = self.store.create_job([source], metadata, "local")
        document_id = str(self.store.documents_for_job(job_id)[0]["id"])
        self.store.save_page(
            document_id,
            1,
            "Gabarito preliminar\nBanca: Fundação Getulio Vargas\nConcurso: RFB22\n"
            "Ano: 2022\nCargo: Auditor-Fiscal da Receita Federal do Brasil\n"
            "Turno: Manhã\nTipo: 1",
            status="text",
        )
        self.store.update_document(document_id, status="extracted", page_count=1)
        resolution = self.store.resolve_extracted_document(document_id)
        version_id = str(resolution.document_version_id)
        with closing(self.store._connect()) as connection:
            run_canonical_identity_migration(
                connection,
                manifest_paths=[RFB22_MANIFEST],
                contest_alias="RFB22",
                apply=True,
                run_id="rfb22-answer-key",
            )
            raw_profile = DocumentSemanticProfile.model_validate_json(
                connection.execute(
                    "SELECT profile_json FROM document_versions WHERE id = ?", (version_id,)
                ).fetchone()[0]
            )
            profile = canonicalize_profile_for_version(connection, version_id, raw_profile)

        self.assertEqual(profile.coverage.roles.status, "known")
        self.assertEqual(profile.coverage.turns.status, "known")
        self.assertEqual(profile.coverage.variants.status, "known")
        self.assertEqual(len(profile.coverage.roles.normalized_values), 2)
        self.assertEqual(len(profile.coverage.turns.normalized_values), 2)
        self.assertEqual(len(profile.coverage.variants.normalized_values), 4)


if __name__ == "__main__":
    unittest.main()
