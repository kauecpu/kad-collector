from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from kad_collector.canonical_identity import run_canonical_identity_migration
from kad_collector.desktop_export import export_filtered_questions
from kad_collector.desktop_models import (
    ClassificationValue,
    DesktopFilterSet,
    QuestionClassification,
)
from kad_collector.desktop_store import (
    DesktopStore,
    question_decision_fingerprint,
    question_fingerprint,
)
from kad_collector.models import Alternative, QuestionRecord
from kad_collector.question_equivalence import (
    QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
    _content_groupings,
    question_fingerprints,
    run_question_equivalence_migration,
    sync_canonical_editorial_from_question,
)

RFB22_MANIFEST = Path("tests/regression/rfb22/manifest.v1.toml")
NOW = "2026-08-23T12:00:00+00:00"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _classification(role: str = "Analista") -> QuestionClassification:
    def value(item: str | int) -> ClassificationValue:
        return ClassificationValue(value=item, confidence=1, evidence="fixture local")

    return QuestionClassification(
        concurso=value("Concurso sintético"),
        board=value("FGV"),
        year=value(2026),
        role=value(role),
        organization=value("Órgão sintético"),
        level=value("Superior"),
        discipline=value("Direito"),
        subject=value("Normas"),
        topic=value("Aplicação da lei"),
        difficulty=value("Média"),
    )


def _question(
    *,
    number: int = 1,
    role: str = "Analista",
    order: tuple[str, str] = ("Errada", "Certa"),
    correct_text: str = "Certa",
    statement: str = "Assinale a alternativa correta segundo a norma apresentada.",
) -> QuestionRecord:
    letters = ("A", "B")
    correct = letters[order.index(correct_text)]
    return QuestionRecord(
        number=number,
        statement=statement,
        alternatives=[
            Alternative(letter=letter, text=text)
            for letter, text in zip(letters, order, strict=True)
        ],
        matter="Normas",
        subject="Aplicação da lei",
        discipline="Direito",
        board="FGV",
        organization="Órgão sintético",
        concurso="Concurso sintético",
        role=role,
        year=2026,
        level="Superior",
        difficulty="Média",
        source_pages=[1],
        explanation="A alternativa indicada reproduz corretamente a norma aplicável.",
        answer_status="matched",
        correct_answer=correct,
    )


class SyntheticCatalog:
    def __init__(
        self,
        root: Path,
        *,
        roles: tuple[str, ...] = ("Analista",),
        booklets: tuple[str, ...] = ("1", "2"),
    ) -> None:
        self.root = root
        self.store = DesktopStore(root / "collector.sqlite3")
        self.roles = roles
        self.booklets = booklets
        self.documents: dict[tuple[str, str], tuple[str, str]] = {}
        self._seed()

    def _seed(self) -> None:
        with closing(self.store._connect()) as connection:
            connection.execute(
                "INSERT INTO canonical_contests VALUES "
                "('contest','contest-2026','Concurso sintético','Concurso sintético',2026,"
                "'FGV','Órgão sintético',NULL,'https://example.test/concurso','{}',?,?)",
                (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO contest_aliases VALUES "
                "('alias','contest','SYN26','syn26','input','','https://example.test/concurso',"
                "'{}','active',?,?)",
                (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO exam_applications VALUES "
                "('application','application-2026','contest','Prova objetiva',"
                "'Prova objetiva','2026-08-23','supported','https://example.test/prova',"
                "'{}',?,?)",
                (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO application_stages VALUES "
                "('stage','stage-2026','application','Prova objetiva','objective','prova objetiva',"
                "'{}',?,?)",
                (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO application_shifts VALUES "
                "('shift','shift-2026','application','Manhã','manha',1,'{}',?,?)",
                (NOW, NOW),
            )
            for role_index, role in enumerate(self.roles):
                role_id = f"role-{role_index}"
                connection.execute(
                    "INSERT INTO contest_roles VALUES (?, ?, 'contest', ?, ?, NULL, ?, '{}', ?, ?)",
                    (role_id, f"role-key-{role_index}", role, role, role.casefold(), NOW, NOW),
                )
            for booklet_index, booklet in enumerate(self.booklets):
                booklet_id = f"booklet-{booklet}"
                connection.execute(
                    "INSERT INTO application_booklets VALUES (?, ?, 'application', ?, ?, ?, "
                    "'{}', ?, ?)",
                    (
                        booklet_id,
                        f"booklet-key-{booklet_index}",
                        booklet,
                        f"Tipo {booklet}",
                        booklet,
                        NOW,
                        NOW,
                    ),
                )
            connection.execute(
                "INSERT INTO semantic_identities VALUES "
                "('identity',1,'fixture','{}','{}',?,?)",
                (NOW, NOW),
            )
            connection.execute(
                "INSERT INTO document_versions "
                "(id,identity_key,document_role,answer_key_state,coverage_json,profile_json,"
                "content_sha256,content_normalizer_version,version_number,created_at,updated_at) "
                "VALUES ('key-version','identity','answer_key','definitive','{}','{}',?,"
                "'fixture',1,?,?)",
                ("f" * 64, NOW, NOW),
            )
            connection.execute(
                "INSERT INTO jobs (id,created_at,updated_at,status,classifier_provider) "
                "VALUES ('job',?,?,'completed','local')",
                (NOW, NOW),
            )
            version = 1
            for role_index, role in enumerate(self.roles):
                for booklet in self.booklets:
                    document_id = f"document-{role_index}-{booklet}"
                    canonical_document_id = f"canonical-document-{role_index}-{booklet}"
                    scope_id = f"scope-{role_index}-{booklet}"
                    version_id = f"exam-version-{role_index}-{booklet}"
                    link_id = f"link-{role_index}-{booklet}"
                    path = self.root / f"{document_id}.pdf"
                    path.write_bytes(f"%PDF-1.4\n{document_id}\n%%EOF".encode())
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    connection.execute(
                        "INSERT INTO application_scopes VALUES (?, ?, 'application', ?, 'stage', "
                        "'shift', ?, ?, ?)",
                        (
                            scope_id,
                            f"scope-key-{role_index}-{booklet}",
                            f"role-{role_index}",
                            f"booklet-{booklet}",
                            NOW,
                            NOW,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO canonical_documents "
                        "(id,canonical_key,source_document_key,contest_id,application_id,"
                        "document_kind,official_title,display_name,answer_key_state,source_url,"
                        "sha256,evidence_json,created_at,updated_at) VALUES "
                        "(?,?,?,'contest','application','exam',?,?,NULL,?,?,?,?,?)",
                        (
                            canonical_document_id,
                            f"document-key-{role_index}-{booklet}",
                            document_id,
                            f"Prova {role} tipo {booklet}",
                            f"Prova {role} tipo {booklet}",
                            f"https://example.test/{document_id}.pdf",
                            digest,
                            "{}",
                            NOW,
                            NOW,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO canonical_document_scopes VALUES "
                        "(?,?,'objective',1,1,?)",
                        (canonical_document_id, scope_id, NOW),
                    )
                    connection.execute(
                        "INSERT INTO document_versions "
                        "(id,identity_key,document_role,answer_key_state,coverage_json,profile_json,"
                        "content_sha256,content_normalizer_version,version_number,created_at,"
                        "updated_at,canonical_contest_id,canonical_application_id,"
                        "canonical_document_id) VALUES (?, 'identity','exam','none','{}','{}',?,"
                        "'fixture',?,?,?,'contest','application',?)",
                        (version_id, digest, version, NOW, NOW, canonical_document_id),
                    )
                    connection.execute(
                        "INSERT INTO documents "
                        "(id,job_id,local_path,filename,sha256,size_bytes,page_count,processed_pages,"
                        "status,needs_ocr,metadata_json,warnings_json,created_at,updated_at,"
                        "document_version_id,semantic_resolution,canonical_contest_id,"
                        "canonical_application_id,canonical_document_id) VALUES "
                        "(?,'job',?,?,?,?,1,1,'processed',0,?,'[]',?,?,?,'selected','contest',"
                        "'application',?)",
                        (
                            document_id,
                            str(path),
                            path.name,
                            digest,
                            path.stat().st_size,
                            _json(
                                {
                                    "provider": "fgv",
                                    "source_url": f"https://example.test/{document_id}.pdf",
                                    "document_title": f"Prova {role} tipo {booklet}",
                                    "role": role,
                                    "variant": f"Tipo {booklet}",
                                }
                            ),
                            NOW,
                            NOW,
                            version_id,
                            canonical_document_id,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO document_links VALUES "
                        "(?,?,?,'active','{}','semantic-association-v3',NULL,?,?)",
                        (link_id, version_id, "key-version", NOW, NOW),
                    )
                    self.documents[(role, booklet)] = (document_id, link_id)
                    version += 1
            connection.commit()

    def add(self, role: str, booklet: str, question: QuestionRecord) -> str:
        document_id, link_id = self.documents[(role, booklet)]
        question_id = self.store.save_question(
            document_id, question, _classification(role)
        )
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET answer_key_link_id = ? WHERE id = ?",
                (link_id, question_id),
            )
            connection.commit()
        return question_id


class QuestionEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_large_catalog_keeps_occurrences_but_operates_on_canonical_questions(self) -> None:
        """Regression scenario: 600 extracted occurrences, 100 confirmed copies."""
        fixture = SyntheticCatalog(
            self.root,
            booklets=tuple(str(index) for index in range(1, 11)),
        )
        # Five hundred distinct questions spread across ten source PDFs.
        for index in range(500):
            booklet = str(index % 10 + 1)
            fixture.add(
                "Analista",
                booklet,
                _question(
                    number=index // 10 + 1,
                    statement=f"Enunciado sintético único {index}.",
                ),
            )
        # One hundred repeated occurrences are deliberately stored under a
        # different document/question key so the audit trail remains intact.
        for index in range(100):
            target_booklet = str((index + 1) % 10 + 1)
            fixture.add(
                "Analista",
                target_booklet,
                _question(
                    number=1000 + index,
                    statement=f"Enunciado sintético único {index}.",
                ),
            )

        with closing(fixture.store._connect()) as connection:
            report = run_question_equivalence_migration(
                connection,
                contest_alias="SYN26",
                apply=True,
                run_id="large-catalog-equivalence",
            )
            repeated = run_question_equivalence_migration(
                connection,
                contest_alias="SYN26",
                apply=True,
                run_id="large-catalog-equivalence-repeated",
            )
            occurrence_count = connection.execute(
                "SELECT COUNT(*) FROM question_occurrences WHERE scope_id IS NOT NULL"
            ).fetchone()[0]
            copy_count = connection.execute(
                "SELECT COALESCE(SUM(occurrence_count - 1), 0) "
                "FROM question_equivalence_groups WHERE status='confirmed'"
            ).fetchone()[0]

        self.assertEqual(occurrence_count, 600)
        self.assertEqual(report.canonical_questions, 500)
        self.assertEqual(repeated.occurrences_total, 600)
        self.assertEqual(repeated.canonical_questions, 500)
        self.assertEqual(copy_count, 100)
        self.assertEqual(fixture.store.query(DesktopFilterSet())["total"], 500)
        self.assertEqual(len(fixture.store.classification_question_rows()), 500)
        self.assertEqual(
            fixture.store.query(
                DesktopFilterSet(), include_equivalent_copies=True
            )["total"],
            600,
        )
        self.assertEqual(
            len(fixture.store.export_candidates(DesktopFilterSet())),
            500,
        )

    def test_permuted_alternatives_create_one_canonical_export_with_two_origins(self) -> None:
        fixture = SyntheticCatalog(self.root)
        fixture.add("Analista", "1", _question(order=("Errada", "Certa")))
        fixture.add("Analista", "2", _question(order=("Certa", "Errada")))

        with closing(fixture.store._connect()) as connection:
            dry_run = run_question_equivalence_migration(
                connection, contest_alias="SYN26"
            )
            self.assertEqual(dry_run.confirmed_groups, 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM question_occurrences").fetchone()[0],
                0,
            )
            report = run_question_equivalence_migration(
                connection, contest_alias="SYN26", apply=True, run_id="equivalence-run"
            )
            repeated = run_question_equivalence_migration(
                connection, contest_alias="SYN26", apply=True, run_id="equivalence-run"
            )

        self.assertEqual(report.confirmed_groups, 1)
        self.assertEqual(report.canonical_questions, 1)
        self.assertEqual(repeated.canonical_questions, 1)
        self.assertEqual(fixture.store.query(DesktopFilterSet())["total"], 1)
        classification_rows = fixture.store.classification_question_rows()
        self.assertEqual(len(classification_rows), 1)
        representative_id = classification_rows[0]["id"]
        fixture.store.decide_question(
            representative_id,
            "approved",
            actor="revisora",
            notes="Conteúdo e proveniências conferidos.",
        )
        export = export_filtered_questions(
            fixture.store,
            DesktopFilterSet(statuses=["exportable"]),
            output_root=self.root / "exports",
        )
        records = [
            json.loads(line)
            for line in export.questions_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(export.exported_count, 1)
        self.assertEqual(records[0]["data"]["canonicalQuestion"]["occurrenceCount"], 2)
        self.assertEqual(
            len(records[0]["data"]["canonicalQuestion"]["provenances"]), 2
        )
        self.assertTrue(records[0]["data"]["id"].startswith("cq-"))

    def test_missing_booklet_does_not_block_and_answer_conflict_does(self) -> None:
        missing = SyntheticCatalog(self.root / "missing")
        missing.add("Analista", "1", _question())
        with closing(missing.store._connect()) as connection:
            missing_report = run_question_equivalence_migration(connection, apply=True)
        self.assertEqual(missing_report.confirmed_groups, 1)
        self.assertEqual(missing_report.canonical_questions, 1)

        conflict = SyntheticCatalog(self.root / "conflict")
        conflict.add("Analista", "1", _question(correct_text="Certa"))
        conflict.add("Analista", "2", _question(correct_text="Errada"))
        with closing(conflict.store._connect()) as connection:
            conflict_report = run_question_equivalence_migration(connection, apply=True)
        self.assertEqual(conflict_report.conflicting_groups, 1)
        self.assertEqual(conflict_report.answer_conflicts, 1)

    def test_consistent_annulment_keeps_a_canonical_record_but_not_an_export(self) -> None:
        fixture = SyntheticCatalog(self.root)
        annulled = _question().model_copy(
            update={"answer_status": "annulled", "correct_answer": None}
        )
        fixture.add("Analista", "1", annulled)
        fixture.add("Analista", "2", annulled)
        with closing(fixture.store._connect()) as connection:
            report = run_question_equivalence_migration(connection, apply=True)
        self.assertEqual(report.confirmed_groups, 1)
        self.assertEqual(report.canonical_questions, 1)
        self.assertEqual(
            fixture.store.export_candidates(DesktopFilterSet(statuses=["exportable"])),
            [],
        )

    def test_same_content_in_different_roles_does_not_cross_the_boundary(self) -> None:
        fixture = SyntheticCatalog(
            self.root, roles=("Analista", "Auditor"), booklets=("1",)
        )
        fixture.add("Analista", "1", _question(role="Analista"))
        fixture.add("Auditor", "1", _question(role="Auditor"))
        with closing(fixture.store._connect()) as connection:
            report = run_question_equivalence_migration(connection, apply=True)
        self.assertEqual(report.confirmed_groups, 2)
        self.assertEqual(report.canonical_questions, 2)

    def test_same_statement_with_real_differences_stays_separate_without_blocking(self) -> None:
        fixture = SyntheticCatalog(self.root)
        fixture.add("Analista", "1", _question(order=("Errada", "Certa")))
        fixture.add(
            "Analista",
            "2",
            _question(order=("Outra errada", "Certa"), correct_text="Certa"),
        )
        with closing(fixture.store._connect()) as connection:
            report = run_question_equivalence_migration(connection, apply=True)
            reasons = [
                row[0]
                for row in connection.execute(
                    "SELECT reason FROM question_equivalence_groups "
                    "WHERE status='confirmed' ORDER BY id"
                )
            ]
            variant_markers = [
                row[0]
                for row in connection.execute(
                    "SELECT has_statement_variants FROM question_equivalence_groups "
                    "WHERE status='confirmed' ORDER BY id"
                )
            ]
        self.assertEqual(report.confirmed_groups, 2)
        self.assertEqual(report.conflicting_groups, 0)
        self.assertEqual(report.canonical_questions, 2)
        self.assertTrue(
            all("mantidas separadas" in reason for reason in reasons), reasons
        )
        self.assertEqual(variant_markers, [1, 1])

    def test_main_copy_is_stable_and_only_it_is_importable(self) -> None:
        fixture = SyntheticCatalog(self.root)
        first_id = fixture.add("Analista", "1", _question(number=17))
        second_id = fixture.add(
            "Analista", "2", _question(number=42, order=("Certa", "Errada"))
        )
        with closing(fixture.store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET status='approved' WHERE id=?", (second_id,)
            )
            connection.commit()
            run_question_equivalence_migration(
                connection, apply=True, run_id="stable-primary-first"
            )
            primary_before = connection.execute(
                "SELECT o.question_id FROM canonical_questions cq "
                "JOIN question_occurrences o ON o.id=cq.representative_occurrence_id"
            ).fetchone()[0]
            connection.execute(
                "UPDATE question_occurrences SET algorithm_version='legacy'"
            )
            connection.commit()
            run_question_equivalence_migration(
                connection, apply=True, run_id="stable-primary-second"
            )
            primary_after = connection.execute(
                "SELECT o.question_id FROM canonical_questions cq "
                "JOIN question_occurrences o ON o.id=cq.representative_occurrence_id"
            ).fetchone()[0]
            occurrence_versions = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT algorithm_version FROM question_occurrences"
                )
            }

        self.assertEqual(primary_before, second_id)
        self.assertEqual(primary_after, second_id)
        self.assertEqual(
            occurrence_versions, {QUESTION_EQUIVALENCE_ALGORITHM_VERSION}
        )
        visible = fixture.store.query(DesktopFilterSet())
        all_copies = fixture.store.query(
            DesktopFilterSet(), include_equivalent_copies=True
        )
        self.assertEqual(visible["total"], 1)
        self.assertEqual(sum(item["importable"] for item in all_copies["questions"]), 1)
        hidden = next(item for item in all_copies["questions"] if item["id"] == first_id)
        self.assertIn("unresolved_duplicate", hidden["block_reasons"])
        details = fixture.store.question_equivalence(first_id)
        assert details is not None
        self.assertFalse(details["isRepresentative"])
        self.assertEqual(len(details["provenances"]), 2)

    def test_question_number_prefix_and_extraction_noise_are_ignored(self) -> None:
        fixture = SyntheticCatalog(self.root)
        fixture.add(
            "Analista",
            "1",
            _question(
                number=11,
                statement=(
                    "Questão 11 - Assinale a alternativa correta segundo a norma "
                    "apresentada."
                ),
                order=("Distrator extenso", "Certa"),
            ),
        )
        fixture.add(
            "Analista",
            "2",
            _question(
                number=38,
                statement="Q. 38: Assinale a alternativa correta segundo a norma apresentada.",
                order=("Certa", "Distrator ex tenso\nTipo 2 - Página 7"),
            ),
        )
        with closing(fixture.store._connect()) as connection:
            report = run_question_equivalence_migration(connection, apply=True)
        self.assertEqual(report.confirmed_groups, 1)
        self.assertEqual(report.canonical_questions, 1)

    def test_editorial_fields_from_main_copy_are_replicated(self) -> None:
        fixture = SyntheticCatalog(self.root)
        first_id = fixture.add("Analista", "1", _question())
        second_id = fixture.add("Analista", "2", _question(order=("Certa", "Errada")))
        with closing(fixture.store._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM questions WHERE id=?", (second_id,)
            ).fetchone()
            payload = json.loads(row[0])
            payload["discipline"] = "Língua Portuguesa"
            connection.execute(
                "UPDATE questions SET payload_json=?,updated_at=? WHERE id=?",
                (_json(payload), "2026-08-26T13:00:00+00:00", second_id),
            )
            connection.commit()
            report = run_question_equivalence_migration(connection, apply=True)

        self.assertEqual(report.confirmed_groups, 1)
        self.assertEqual(report.classification_conflicts, 1)
        self.assertEqual(
            [
                fixture.store.question(question_id)["question"]["discipline"]
                for question_id in (first_id, second_id)
            ],
            ["Direito", "Direito"],
        )

    def test_qwen_fields_left_on_a_copy_are_recovered_to_the_main_copy(self) -> None:
        fixture = SyntheticCatalog(self.root)
        first_id = fixture.add("Analista", "1", _question())
        second_id = fixture.add("Analista", "2", _question(order=("Certa", "Errada")))
        with closing(fixture.store._connect()) as connection:
            run_question_equivalence_migration(connection, apply=True)
            primary_id = connection.execute(
                "SELECT o.question_id FROM canonical_questions cq "
                "JOIN question_occurrences o ON o.id=cq.representative_occurrence_id"
            ).fetchone()[0]
            copy_id = second_id if primary_id == first_id else first_id

            for question_id, values in (
                (primary_id, (None, None, None, None, None)),
                (
                    copy_id,
                    (
                        "Direito Tributário",
                        "Sistema Tributário Nacional",
                        "Competência Tributária",
                        "Superior",
                        "ai_suggestion",
                    ),
                ),
            ):
                row = connection.execute(
                    "SELECT payload_json,classification_json FROM questions WHERE id=?",
                    (question_id,),
                ).fetchone()
                payload = json.loads(row[0])
                classification = json.loads(row[1])
                for payload_field, classification_field, value in (
                    ("discipline", "discipline", values[0]),
                    ("matter", "subject", values[1]),
                    ("subject", "topic", values[2]),
                    ("level", "level", values[3]),
                ):
                    payload[payload_field] = value
                    classification[classification_field] = {
                        "value": value,
                        "confidence": 0.91 if value else 0,
                        "evidence": "sugestão local aceita" if value else None,
                        "source": values[4],
                        "reason": None,
                        "provenance": [],
                    }
                connection.execute(
                    "UPDATE questions SET payload_json=?,classification_json=?,updated_at=? "
                    "WHERE id=?",
                    (_json(payload), _json(classification), NOW, question_id),
                )
            connection.commit()

        first = fixture.store.recover_canonical_classifications()
        second = fixture.store.recover_canonical_classifications()

        self.assertEqual(first["groupsRecovered"], 1)
        self.assertEqual(first["fieldsRecovered"], 4)
        self.assertEqual(second["groupsRecovered"], 0)
        self.assertEqual(second["fieldsRecovered"], 0)
        for question_id in (first_id, second_id):
            stored = fixture.store.question(question_id)
            self.assertEqual(stored["question"]["discipline"], "Direito Tributário")
            self.assertEqual(
                stored["classification"]["discipline"]["source"], "ai_suggestion"
            )
        with closing(fixture.store._connect()) as connection:
            events = connection.execute(
                "SELECT action FROM question_equivalence_events "
                "WHERE action='canonical_classification_recovered'"
            ).fetchall()
        self.assertEqual(len(events), 1)

    def test_new_copy_found_later_inherits_the_main_qwen_classification(self) -> None:
        fixture = SyntheticCatalog(self.root)
        main_id = fixture.add("Analista", "1", _question())
        with closing(fixture.store._connect()) as connection:
            run_question_equivalence_migration(
                connection, apply=True, run_id="before-new-copy"
            )
            row = connection.execute(
                "SELECT payload_json,classification_json FROM questions WHERE id=?",
                (main_id,),
            ).fetchone()
            payload = json.loads(row[0])
            classification = json.loads(row[1])
            payload["discipline"] = "Direito Tributário"
            classification["discipline"] = {
                "value": "Direito Tributário",
                "confidence": 0.91,
                "evidence": "sugestão local aceita",
                "source": "ai_suggestion",
                "reason": None,
                "provenance": [],
            }
            connection.execute(
                "UPDATE questions SET payload_json=?,classification_json=?,updated_at=? "
                "WHERE id=?",
                (_json(payload), _json(classification), NOW, main_id),
            )
            connection.commit()

        copy_id = fixture.add(
            "Analista", "2", _question(number=42, order=("Certa", "Errada"))
        )
        with closing(fixture.store._connect()) as connection:
            run_question_equivalence_migration(
                connection, apply=True, run_id="after-new-copy"
            )

        visible = fixture.store.query(DesktopFilterSet())
        inherited = fixture.store.question(copy_id)
        self.assertEqual(visible["total"], 1)
        self.assertEqual(inherited["question"]["discipline"], "Direito Tributário")
        self.assertEqual(
            inherited["classification"]["discipline"]["source"], "ai_suggestion"
        )

    def test_recovery_keeps_a_conflicting_group_blocked(self) -> None:
        fixture = SyntheticCatalog(self.root)
        first_id = fixture.add(
            "Analista", "1", _question(correct_text="Certa")
        )
        second_id = fixture.add(
            "Analista", "2", _question(correct_text="Errada")
        )
        with closing(fixture.store._connect()) as connection:
            run_question_equivalence_migration(connection, apply=True)
            group = connection.execute(
                "SELECT id,status,representative_occurrence_id "
                "FROM question_equivalence_groups"
            ).fetchone()
            self.assertEqual(group["status"], "conflict")
            primary_id = connection.execute(
                "SELECT question_id FROM question_occurrences WHERE id=?",
                (group["representative_occurrence_id"],),
            ).fetchone()[0]
            copy_id = second_id if primary_id == first_id else first_id
            for question_id, value, source in (
                (primary_id, None, None),
                (copy_id, "Direito Tributário", "ai_suggestion"),
            ):
                row = connection.execute(
                    "SELECT payload_json,classification_json FROM questions WHERE id=?",
                    (question_id,),
                ).fetchone()
                payload = json.loads(row[0])
                classification = json.loads(row[1])
                payload["discipline"] = value
                classification["discipline"] = {
                    "value": value,
                    "confidence": 0.91 if value else 0,
                    "evidence": "sugestão local aceita" if value else None,
                    "source": source,
                    "reason": None,
                    "provenance": [],
                }
                connection.execute(
                    "UPDATE questions SET payload_json=?,classification_json=?,updated_at=? "
                    "WHERE id=?",
                    (_json(payload), _json(classification), NOW, question_id),
                )
            connection.commit()

        report = fixture.store.recover_canonical_classifications()

        self.assertEqual(report["groupsRecovered"], 1)
        self.assertEqual(
            fixture.store.question(primary_id)["question"]["discipline"],
            "Direito Tributário",
        )
        with closing(fixture.store._connect()) as connection:
            status = connection.execute(
                "SELECT status FROM question_equivalence_groups WHERE id=?", (group["id"],)
            ).fetchone()[0]
        self.assertEqual(status, "conflict")

    def test_stale_inactive_primary_does_not_abort_other_synchronization(self) -> None:
        fixture = SyntheticCatalog(self.root)
        fixture.add("Analista", "1", _question())
        fixture.add("Analista", "2", _question(order=("Certa", "Errada")))
        with closing(fixture.store._connect()) as connection:
            run_question_equivalence_migration(connection, apply=True)
            representative = connection.execute(
                "SELECT cq.representative_occurrence_id,o.question_id "
                "FROM canonical_questions cq JOIN question_occurrences o "
                "ON o.id=cq.representative_occurrence_id"
            ).fetchone()
            connection.execute(
                "UPDATE question_group_occurrences SET status='inactive' "
                "WHERE occurrence_id=?",
                (representative["representative_occurrence_id"],),
            )

            sync_canonical_editorial_from_question(
                connection, representative["question_id"], changed_at=NOW
            )
            connection.commit()

        self.assertIsNotNone(fixture.store.question(representative["question_id"]))

    def test_content_grouping_scales_to_twenty_thousand_occurrences(self) -> None:
        occurrences: list[dict[str, object]] = []
        for question_index in range(5_000):
            payload = _question(
                number=question_index + 1,
                statement=f"Enunciado exclusivo {question_index}",
            ).model_dump(mode="json")
            fingerprints = question_fingerprints(payload)
            for booklet in range(4):
                occurrences.append(
                    {
                        "id": f"occurrence-{question_index}-{booklet}",
                        "contest_id": "contest",
                        "application_id": "application",
                        "role_id": "role",
                        "stage_id": "stage",
                        "shift_id": "shift",
                        "content_kind": "objective",
                        "booklet_id": f"booklet-{booklet}",
                        "statement_fingerprint": fingerprints.statement,
                        "equivalence_fingerprint": fingerprints.invariant,
                        "payload_json": _json(payload),
                    }
                )

        grouped = _content_groupings(occurrences)  # type: ignore[arg-type]

        self.assertEqual(len(grouped), 20_000)
        self.assertEqual(len({fingerprint for fingerprint, _ in grouped.values()}), 5_000)

    def test_content_edit_blocks_a_confirmed_group_until_revalidation(self) -> None:
        fixture = SyntheticCatalog(self.root)
        ids = [
            fixture.add("Analista", "1", _question()),
            fixture.add("Analista", "2", _question(order=("Certa", "Errada"))),
        ]
        with closing(fixture.store._connect()) as connection:
            run_question_equivalence_migration(connection, apply=True)
        fixture.store.update_question(
            ids[1],
            _question(
                order=("Certa", "Errada"),
                statement="Enunciado alterado depois da confirmação editorial.",
            ),
            actor="revisora",
            notes="Correção manual.",
        )
        equivalence = fixture.store.question_equivalence(ids[0])
        assert equivalence is not None
        self.assertEqual(equivalence["status"], "needs_review")

    def test_inactive_answer_link_makes_the_group_stale_before_reprocessing(self) -> None:
        fixture = SyntheticCatalog(self.root)
        ids = [
            fixture.add("Analista", "1", _question()),
            fixture.add("Analista", "2", _question(order=("Certa", "Errada"))),
        ]
        with closing(fixture.store._connect()) as connection:
            run_question_equivalence_migration(connection, apply=True)
            connection.execute(
                "UPDATE document_links SET status = 'rejected' WHERE id = 'link-0-2'"
            )
            connection.commit()
        equivalence = fixture.store.question_equivalence(ids[0])
        assert equivalence is not None
        self.assertFalse(equivalence["groupFresh"])
        self.assertEqual(
            fixture.store.export_candidates(DesktopFilterSet(statuses=["exportable"])),
            [],
        )
        representative_id = fixture.store.query(DesktopFilterSet())["questions"][0]["id"]
        with self.assertRaisesRegex(ValueError, "equivalência"):
            fixture.store.decide_question(
                representative_id,
                "approved",
                actor="revisora",
                notes="Não deve aprovar grupo desatualizado.",
            )


class Rfb22ManifestBackedEquivalenceTests(unittest.TestCase):
    """Validates manifest arithmetic; it does not claim to inspect absent official PDFs."""

    def test_manifest_scopes_preserve_1120_occurrences_as_280_canonical_questions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DesktopStore(root / "collector.sqlite3")
            with closing(store._connect()) as connection:
                run_canonical_identity_migration(
                    connection,
                    manifest_paths=[RFB22_MANIFEST],
                    contest_alias="RFB22",
                    apply=True,
                    run_id="rfb22-catalog",
                )
                self._seed_manifest_occurrences(connection, root)
                report = run_question_equivalence_migration(
                    connection,
                    contest_alias="RFB22",
                    apply=True,
                    run_id="rfb22-equivalence",
                )

        self.assertEqual(report.occurrences_total, 1_120)
        self.assertEqual(report.confirmed_groups, 280)
        self.assertEqual(report.canonical_questions, 280)
        role_totals: dict[str, int] = {}
        for context, counts in report.by_context.items():
            role = context.split(" | ", 1)[0]
            role_totals[role] = role_totals.get(role, 0) + counts["canonical"]
        self.assertEqual(sorted(role_totals.values()), [140, 140])
        self.assertEqual(
            report.as_dict()["algorithmVersion"],
            QUESTION_EQUIVALENCE_ALGORITHM_VERSION,
        )

    @staticmethod
    def _seed_manifest_occurrences(
        connection: sqlite3.Connection, root: Path
    ) -> None:
        db = connection
        contest_id = db.execute("SELECT id FROM canonical_contests").fetchone()[0]
        application_id = db.execute(
            "SELECT id FROM exam_applications WHERE application_date = '2023-03-19'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO jobs (id,created_at,updated_at,status,classifier_provider) "
            "VALUES ('rfb-job',?,?,'completed','local')",
            (NOW, NOW),
        )
        db.execute(
            "INSERT INTO semantic_identities VALUES "
            "('rfb-identity',1,'fixture','{}','{}',?,?)",
            (NOW, NOW),
        )
        db.execute(
            "INSERT INTO document_versions "
            "(id,identity_key,document_role,answer_key_state,coverage_json,profile_json,"
            "content_sha256,content_normalizer_version,version_number,created_at,updated_at,"
            "canonical_contest_id,canonical_application_id) VALUES "
            "('rfb-key','rfb-identity','answer_key','definitive','{}','{}',?,'fixture',1,"
            "?,?,?,?)",
            ("e" * 64, NOW, NOW, contest_id, application_id),
        )
        documents = db.execute(
            """
            SELECT d.id AS canonical_document_id, d.source_document_key, d.official_title,
                   d.source_url, cds.first_question, cds.last_question, cds.scope_id,
                   r.display_name AS role, sh.official_name AS shift, b.official_code AS booklet
            FROM canonical_documents d
            JOIN canonical_document_scopes cds ON cds.document_id = d.id
            JOIN application_scopes s ON s.id = cds.scope_id
            JOIN contest_roles r ON r.id = s.role_id
            JOIN application_shifts sh ON sh.id = s.shift_id
            JOIN application_booklets b ON b.id = s.booklet_id
            WHERE d.application_id = ? AND d.document_kind = 'exam'
              AND cds.content_kind = 'objective'
            ORDER BY d.id
            """,
            (application_id,),
        ).fetchall()
        question_rows: list[tuple[object, ...]] = []
        for index, document in enumerate(documents, start=1):
            document_id = f"rfb-document-{index}"
            version_id = f"rfb-version-{index}"
            link_id = f"rfb-link-{index}"
            path = root / f"{document_id}.pdf"
            path.write_bytes(f"%PDF-1.4\n{document_id}\n%%EOF".encode())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            db.execute(
                "INSERT INTO document_versions "
                "(id,identity_key,document_role,answer_key_state,coverage_json,profile_json,"
                "content_sha256,content_normalizer_version,version_number,created_at,updated_at,"
                "canonical_contest_id,canonical_application_id,canonical_document_id) VALUES "
                "(?,'rfb-identity','exam','none','{}','{}',?,'fixture',?,?,?, ?, ?, ?)",
                (
                    version_id,
                    digest,
                    index,
                    NOW,
                    NOW,
                    contest_id,
                    application_id,
                    document["canonical_document_id"],
                ),
            )
            metadata = {
                "provider": "fgv",
                "source_url": document["source_url"],
                "document_title": document["official_title"],
                "role": document["role"],
                "turn": document["shift"],
                "variant": f"Tipo {document['booklet']}",
            }
            db.execute(
                "INSERT INTO documents "
                "(id,job_id,local_path,filename,sha256,size_bytes,page_count,processed_pages,"
                "status,needs_ocr,metadata_json,warnings_json,created_at,updated_at,"
                "document_version_id,semantic_resolution,canonical_contest_id,"
                "canonical_application_id,canonical_document_id) VALUES "
                "(?,'rfb-job',?,?,?,?,1,1,'processed',0,?,'[]',?,?,?,'selected',?,?,?)",
                (
                    document_id,
                    str(path),
                    path.name,
                    digest,
                    path.stat().st_size,
                    _json(metadata),
                    NOW,
                    NOW,
                    version_id,
                    contest_id,
                    application_id,
                    document["canonical_document_id"],
                ),
            )
            db.execute(
                "INSERT INTO document_links VALUES "
                "(?,?,?,'active','{}','semantic-association-v3',NULL,?,?)",
                (link_id, version_id, "rfb-key", NOW, NOW),
            )
            booklet_offset = int(document["booklet"]) - 1
            for number in range(document["first_question"], document["last_question"] + 1):
                texts = [f"Correta {number}", f"Distrator {number} A", f"Distrator {number} B"]
                offset = booklet_offset % len(texts)
                ordered = texts[offset:] + texts[:offset]
                correct_letter = ("A", "B", "C")[ordered.index(texts[0])]
                question = QuestionRecord(
                    number=number,
                    statement=(
                        f"Questão sintética {document['role']} {document['shift']} número "
                        f"{number}, conforme intervalo oficial do manifesto."
                    ),
                    alternatives=[
                        Alternative(letter=letter, text=text)
                        for letter, text in zip(("A", "B", "C"), ordered, strict=True)
                    ],
                    matter="Normas",
                    subject="Aplicação da lei",
                    discipline="Direito",
                    board="FGV",
                    organization="Receita Federal do Brasil",
                    concurso="RFB22",
                    role=document["role"],
                    year=2023,
                    level="Superior",
                    difficulty="Média",
                    source_pages=[1],
                    explanation="Conteúdo sintético usado apenas para validar a cardinalidade.",
                    answer_status="matched",
                    correct_answer=correct_letter,
                )
                question_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{number}")
                )
                question_rows.append(
                    (
                        question_id,
                        document_id,
                        number,
                        question_fingerprint(question),
                        question_decision_fingerprint(question),
                        _json(question.model_dump(mode="json")),
                        _json(_classification(str(document["role"])).model_dump(mode="json")),
                        1.0,
                        "[]",
                        "pending",
                        NOW,
                        NOW,
                        link_id,
                    )
                )
        db.executemany(
            "INSERT INTO questions "
            "(id,document_id,question_number,fingerprint,decision_fingerprint,payload_json,"
            "classification_json,confidence,flags_json,status,created_at,updated_at,"
            "answer_key_link_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            question_rows,
        )
        db.commit()


if __name__ == "__main__":
    unittest.main()
