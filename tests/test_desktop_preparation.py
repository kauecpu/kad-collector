from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kad_collector.desktop_models import (
    ClassificationValue,
    DesktopFilterSet,
    DesktopImportMetadata,
    DesktopOperationScope,
    QuestionClassification,
)
from kad_collector.desktop_preparation import DesktopPreparationManager
from kad_collector.desktop_server import DesktopApplication
from kad_collector.desktop_store import DesktopStore
from kad_collector.models import Alternative, QuestionRecord

NOW = "2026-08-26T12:00:00+00:00"


def _all_scope() -> DesktopOperationScope:
    return DesktopOperationScope(type="all")


def _preview_all(manager: DesktopPreparationManager) -> dict[str, object]:
    return manager.preview(_all_scope())


def _run_all(manager: DesktopPreparationManager) -> dict[str, object]:
    scope = _all_scope()
    preview = manager.preview(scope)
    return manager.run(str(preview["confirmationToken"]), scope)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _classification() -> QuestionClassification:
    def value(item: str | int) -> ClassificationValue:
        return ClassificationValue(value=item, confidence=1, evidence="fixture")

    return QuestionClassification(
        concurso=value("RFB22"),
        board=value("FGV"),
        year=value(2023),
        role=value("Analista"),
        organization=value("Receita Federal"),
        level=ClassificationValue(),
        discipline=ClassificationValue(),
        subject=ClassificationValue(),
        topic=ClassificationValue(),
        difficulty=ClassificationValue(),
    )


def _question(*, noisy: bool = False) -> QuestionRecord:
    return QuestionRecord(
        number=1,
        statement="Assinale a alternativa correta de acordo com a norma apresentada.",
        alternatives=[
            Alternative(letter="A", text="Errada\nCabeçalho seguinte" if noisy else "Errada"),
            Alternative(letter="B", text="Certa"),
        ],
        matter=None,
        subject=None,
        board="FGV",
        organization="Receita Federal",
        concurso="RFB22",
        role="Analista",
        year=2023,
        source_pages=[1],
        answer_status="matched",
        correct_answer="B",
    )


def _pci_question(number: int) -> QuestionRecord:
    return QuestionRecord(
        number=number,
        statement=f"Questão sanitizada do Banco do Brasil número {number}.",
        alternatives=[
            Alternative(letter="A", text=f"Alternativa A da questão {number}."),
            Alternative(letter="B", text=f"Alternativa B da questão {number}."),
        ],
        matter=None,
        subject=None,
        board="CESGRANRIO",
        organization="Banco do Brasil",
        concurso="PCI Concursos - Banco do Brasil",
        role="Escriturário – Agente Comercial",
        year=2023,
        source_pages=[1],
        answer_status="matched",
        correct_answer="A",
    )


def _pci_classification() -> QuestionClassification:
    def value(item: str | int) -> ClassificationValue:
        return ClassificationValue(value=item, confidence=1, evidence="fixture sanitizada")

    return QuestionClassification(
        concurso=value("PCI Concursos - Banco do Brasil"),
        board=value("CESGRANRIO"),
        year=value(2023),
        role=value("Escriturário – Agente Comercial"),
        organization=value("Banco do Brasil"),
        level=ClassificationValue(),
        discipline=ClassificationValue(),
        subject=ClassificationValue(),
        topic=ClassificationValue(),
        difficulty=ClassificationValue(),
    )


def _decision(
    booklet: str,
    *,
    year: object = 2023,
    exam_turns: list[str] | None = None,
    candidate_turns: list[str] | None = None,
) -> dict[str, object]:
    exam_turns = ["manhã"] if exam_turns is None else exam_turns
    candidate_turns = exam_turns if candidate_turns is None else candidate_turns
    comparisons = [
        {
            "field": name,
            "status": "matched",
            "exam_values": values,
            "candidate_values": values,
            "reason": "fixture",
        }
        for name, values in (
            ("board", ["fgv"]),
            ("concurso", ["rfb22"]),
            ("year", [year]),
            ("organization", ["receita federal"]),
            ("role", ["analista"]),
            ("stage", ["prova objetiva"]),
            ("variant", [f"tipo {booklet}"]),
            ("interval", [1, 1]),
        )
    ]
    comparisons.append(
        {
            "field": "turn",
            "status": "matched",
            "exam_values": exam_turns,
            "candidate_values": candidate_turns,
            "reason": (
                "turno derivado do único turno declarado pelo gabarito"
                if not exam_turns and len(candidate_turns) == 1
                else "prova e gabarito não separam a aplicação por turno"
                if not exam_turns and not candidate_turns
                else "fixture"
            ),
        }
    )
    return {
        "outcome": "selected",
        "selected_version_id": "key-version",
        "assessments": [
            {
                "version_id": "key-version",
                "compatible": True,
                "score": 100,
                "matched_fields": [item["field"] for item in comparisons],
                "conflicts": [],
                "incomplete_fields": [],
                "comparisons": comparisons,
                "reasons": ["fixture"],
            }
        ],
        "minimum_score": 1,
        "minimum_margin": 1,
        "achieved_margin": None,
        "reason": "fixture selecionada",
        "algorithm_version": "semantic-association-v3",
    }


def _seed(
    root: Path,
    *,
    include_review: bool = False,
    decision_year: object = 2023,
    exam_turns: list[str] | None = None,
    candidate_turns: list[str] | None = None,
    answer_key_state: str = "definitive",
) -> DesktopStore:
    store = DesktopStore(root / "collector.sqlite3")
    profile = {
        "identity": {
            "roles": {"normalized_values": ["analista"]},
            "stage": {"normalized_values": ["prova objetiva"]},
            "turns": {"normalized_values": []},
            "variants": {"normalized_values": ["tipo 1"]},
        }
    }
    with closing(store._connect()) as connection:
        connection.execute(
            "INSERT INTO jobs (id,created_at,updated_at,status,classifier_provider) "
            "VALUES ('job',?,?,'completed','local')",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO semantic_identities "
            "(identity_key,schema_version,algorithm_version,identity_json,evidence_json,"
            "created_at,updated_at) VALUES ('identity',1,'fixture','{}','{}',?,?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO document_versions (id,identity_key,document_role,answer_key_state,"
            "coverage_json,profile_json,content_sha256,content_normalizer_version,version_number,"
            "created_at,updated_at) VALUES "
            "('key-version','identity','answer_key',?,'{}','{}',"
            "'key-sha','fixture',1,?,?)",
            (answer_key_state, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO documents (id,job_id,local_path,filename,sha256,status,metadata_json,"
            "warnings_json,created_at,updated_at,document_version_id,semantic_resolution) "
            "VALUES ('key-document','job','key.pdf','key.pdf','key-sha','processed',?,'[]',?,?,"
            "'key-version','new_identity')",
            (
                _json(
                    {
                        "document_type": "answer_key",
                        "source_url": "https://example.test/key",
                    }
                ),
                NOW,
                NOW,
            ),
        )
        for booklet in ("1", "2"):
            version_id = f"exam-version-{booklet}"
            document_id = f"exam-document-{booklet}"
            link_id = f"link-{booklet}"
            connection.execute(
                "INSERT INTO document_versions (id,identity_key,document_role,answer_key_state,"
                "coverage_json,profile_json,content_sha256,content_normalizer_version,"
                "version_number,created_at,updated_at) "
                "VALUES (?,?,'exam','unknown','{}',?,?,'fixture',?,?,?)",
                (
                    version_id,
                    "identity",
                    _json(profile),
                    booklet * 64,
                    int(booklet),
                    NOW,
                    NOW,
                ),
            )
            metadata = {
                "provider": "fgv",
                "document_type": "exam",
                "source_url": f"https://example.test/exam-{booklet}",
                "variant": f"Tipo {booklet}",
                "concurso": "RFB22",
                "board": "FGV",
                "year": 2023,
                "role": "Analista",
                "stage": "Prova objetiva",
            }
            connection.execute(
                "INSERT INTO documents (id,job_id,local_path,filename,sha256,status,metadata_json,"
                "warnings_json,created_at,updated_at,document_version_id,semantic_resolution) "
                "VALUES (?,'job',?,?,?,'processed',?,'[]',?,?,?,'new_identity')",
                (
                    document_id,
                    f"exam-{booklet}.pdf",
                    f"exam-{booklet}.pdf",
                    booklet * 64,
                    _json(metadata),
                    NOW,
                    NOW,
                    version_id,
                ),
            )
            connection.execute(
                "INSERT INTO document_links (id,exam_version_id,answer_key_version_id,status,"
                "decision_json,algorithm_version,created_at,updated_at) "
                "VALUES (?,?,'key-version','active',?,'semantic-association-v3',?,?)",
                (
                    link_id,
                    version_id,
                    _json(
                        _decision(
                            booklet,
                            year=decision_year,
                            exam_turns=exam_turns,
                            candidate_turns=candidate_turns,
                        )
                    ),
                    NOW,
                    NOW,
                ),
            )
        connection.commit()
    for booklet in ("1", "2"):
        question_id = store.save_question(
            f"exam-document-{booklet}", _question(noisy=booklet == "2"), _classification()
        )
        with closing(store._connect()) as connection:
            connection.execute(
                "UPDATE questions SET answer_key_link_id=? WHERE id=?",
                (f"link-{booklet}", question_id),
            )
            connection.commit()
    if include_review:
        with closing(store._connect()) as connection:
            connection.execute(
                "INSERT INTO association_review_queue "
                "(exam_version_id,status,reason,candidates_json,created_at,updated_at) "
                "VALUES ('exam-version-1','pending','campos incompletos: turn',?, ?, ?)",
                (_json([_decision("1")["assessments"][0]]), NOW, NOW),
            )
            connection.commit()
    return store


def _seed_pci_70(root: Path) -> tuple[DesktopStore, list[str]]:
    store = _seed(
        root,
        exam_turns=[],
        candidate_turns=[],
        answer_key_state="unknown",
    )
    decision = _decision(
        "1",
        year=2023,
        exam_turns=[],
        candidate_turns=[],
    )
    replacements = {
        "board": ["cesgranrio"],
        "concurso": ["pci concursos banco do brasil"],
        "year": [2023],
        "organization": ["banco do brasil"],
        "role": ["escriturário agente comercial"],
        "stage": ["prova objetiva"],
        "variant": ["tipo 1"],
        "interval": [1, 70],
        "turn": [],
    }
    assessment = decision["assessments"][0]
    for comparison in assessment["comparisons"]:
        values = replacements[comparison["field"]]
        comparison["exam_values"] = values
        comparison["candidate_values"] = values
    metadata = {
        "provider": "pci_concursos",
        "document_type": "exam",
        "source_url": "https://www.pciconcursos.com.br/provas/download/fixture-sanitizada",
        "document_title": "Banco do Brasil 2023 — Escriturário – Agente Comercial",
        "variant": "Tipo 1",
        "concurso": "PCI Concursos - Banco do Brasil",
        "board": "CESGRANRIO",
        "year": 2023,
        "role": "Escriturário – Agente Comercial",
        "stage": "Prova objetiva",
        "organization": "Banco do Brasil",
    }
    with closing(store._connect()) as connection:
        connection.execute("DELETE FROM questions")
        connection.execute("DELETE FROM document_links WHERE id='link-2'")
        connection.execute("DELETE FROM documents WHERE id='exam-document-2'")
        connection.execute("DELETE FROM document_versions WHERE id='exam-version-2'")
        connection.execute(
            "UPDATE documents SET metadata_json=? WHERE id='exam-document-1'",
            (_json(metadata),),
        )
        connection.execute(
            "UPDATE document_links SET decision_json=? WHERE id='link-1'",
            (_json(decision),),
        )
        connection.commit()
    question_ids: list[str] = []
    for number in range(1, 71):
        question_id = store.save_question(
            "exam-document-1", _pci_question(number), _pci_classification()
        )
        question_ids.append(question_id)
    with closing(store._connect()) as connection:
        connection.execute("UPDATE questions SET answer_key_link_id='link-1'")
        connection.commit()
    return store, question_ids


class DesktopPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_preview_is_passive_and_run_creates_one_main_copy(self) -> None:
        store = _seed(self.root)
        manager = DesktopPreparationManager(store)

        preview = _preview_all(manager)
        with closing(store._connect()) as connection:
            before = connection.execute("SELECT COUNT(*) FROM canonical_questions").fetchone()[0]
        result = manager.run(str(preview["confirmationToken"]), _all_scope())
        repeated = _run_all(manager)

        self.assertEqual(before, 0)
        self.assertEqual(preview["qwenEligible"], 1)
        self.assertEqual(result["qwenEligible"], 1)
        self.assertEqual(result["mainQuestions"], 1)
        self.assertEqual(result["duplicateQuestions"], 1)
        self.assertEqual(result["conflictQuestions"], 0)
        self.assertEqual(result["pendingQuestions"], 0)
        self.assertEqual(repeated["canonicalQuestions"], 1)
        self.assertEqual(repeated["equivalence"]["canonicalQuestions"], 1)
        self.assertEqual(store.query(DesktopFilterSet())["total"], 1)
        self.assertEqual(
            store.query(
                DesktopFilterSet(), include_equivalent_copies=True
            )["total"],
            2,
        )
        self.assertEqual(len(store.export_candidates(DesktopFilterSet())), 1)
        self.assertTrue(Path(result["backupPath"]).is_file())

    def test_pci_document_confirmation_prepares_all_70_questions_once(self) -> None:
        store, question_ids = _seed_pci_70(self.root)
        manager = DesktopPreparationManager(store)
        scope = DesktopOperationScope(
            type="filter",
            filter=DesktopFilterSet(
                boards=["CESGRANRIO"],
                concursos=["PCI Concursos - Banco do Brasil"],
                years=[2023],
            ),
        )
        with closing(store._connect()) as connection:
            protected_before = connection.execute(
                "SELECT id,payload_json,status,reviewer,review_notes,answer_key_link_id "
                "FROM questions ORDER BY id"
            ).fetchall()

        preview = manager.preview(scope)
        result = manager.run(str(preview["confirmationToken"]), scope)
        repeated_preview = manager.preview(scope)
        repeated = manager.run(str(repeated_preview["confirmationToken"]), scope)

        self.assertEqual(preview["selectedCount"], 70)
        self.assertEqual(preview["includedCount"], 70)
        self.assertEqual(preview["excludedCount"], 0)
        self.assertEqual(len(preview["confirmationUnits"]), 1)
        self.assertEqual(preview["confirmationUnits"][0]["questionCount"], 70)
        self.assertIn("uma vez", preview["confirmationUnits"][0]["action"])
        self.assertEqual(result["includedCount"], 70)
        self.assertEqual(result["canonicalQuestions"], 70)
        self.assertEqual(repeated["canonicalQuestions"], 70)
        self.assertEqual(repeated["includedCount"], 70)
        with closing(store._connect()) as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "question_occurrences",
                    "question_equivalence_groups",
                    "canonical_questions",
                )
            }
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM question_occurrences "
                "WHERE occurrence_status='unresolved_scope'"
            ).fetchone()[0]
            group_statuses = connection.execute(
                "SELECT status,COUNT(*) AS count FROM question_equivalence_groups GROUP BY status"
            ).fetchall()
            contest = connection.execute(
                "SELECT board,display_name,notice_year FROM canonical_contests"
            ).fetchone()
            protected_after = connection.execute(
                "SELECT id,payload_json,status,reviewer,review_notes,answer_key_link_id "
                "FROM questions ORDER BY id"
            ).fetchall()
        self.assertEqual(counts, {
            "question_occurrences": 70,
            "question_equivalence_groups": 70,
            "canonical_questions": 70,
        })
        self.assertEqual(unresolved, 0)
        self.assertEqual([tuple(row) for row in group_statuses], [("confirmed", 70)])
        self.assertEqual(tuple(contest), ("cesgranrio", "pci concursos banco do brasil", 2023))
        self.assertEqual(
            [tuple(row) for row in protected_after],
            [tuple(row) for row in protected_before],
        )
        self.assertEqual(set(question_ids), set(preview["scope"]["questionIds"]))

    def test_packaged_preparation_ui_confirms_document_not_70_questions(self) -> None:
        javascript = (
            Path(__file__).parents[1] / "src" / "kad_collector" / "desktop_app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function renderPreparationUnits", javascript)
        self.assertIn("Uma confirmação vale para todas as questões relacionadas.", javascript)
        preparation_renderer = javascript.split("function renderPreparationPreview", 1)[1].split(
            "async function refreshPreparationPreview", 1
        )[0]
        self.assertIn("renderPreparationUnits(", preparation_renderer)
        self.assertNotIn("renderScopeItems(", preparation_renderer)

    def test_pci_and_fgv_filters_do_not_mix_sources(self) -> None:
        store, _question_ids = _seed_pci_70(self.root)
        fgv_path = self.root / "fgv-fixture.pdf"
        fgv_path.write_bytes(b"%PDF-1.4\nfixture sanitizada\n%%EOF")
        fgv_job = store.create_job(
            [fgv_path],
            DesktopImportMetadata(
                provider="fgv_conhecimento",
                document_type="exam",
                document_title="Prova FGV sanitizada",
                board="FGV",
                concurso="RFB22",
                organization="Receita Federal",
                year=2023,
                role="Analista",
                stage="Prova objetiva",
                turn="Manhã",
                variant="Tipo 1",
            ),
            "local",
        )
        fgv_document = store.documents_for_job(fgv_job)[0]
        store.save_question(str(fgv_document["id"]), _question(), _classification())

        pci = store.query(
            DesktopFilterSet(
                boards=["CESGRANRIO"],
                concursos=["PCI Concursos - Banco do Brasil"],
            )
        )["questions"]
        fgv = store.query(
            DesktopFilterSet(boards=["FGV"], concursos=["RFB22"])
        )["questions"]

        self.assertEqual(len(pci), 70)
        self.assertTrue(all(item["question"]["board"] == "CESGRANRIO" for item in pci))
        self.assertEqual(len(fgv), 1)
        self.assertTrue(all(item["question"]["board"] == "FGV" for item in fgv))

    def test_preparation_uses_turn_derived_from_unique_definitive_key(self) -> None:
        store = _seed(self.root, exam_turns=[], candidate_turns=["manhã"])

        result = _run_all(DesktopPreparationManager(store))

        self.assertEqual(result["identifiedExams"], 2)
        self.assertEqual(result["canonicalQuestions"], 1)
        self.assertEqual(result["skipped"], [])
        with closing(store._connect()) as connection:
            shift = connection.execute(
                "SELECT official_name,evidence_json FROM application_shifts"
            ).fetchone()
        self.assertEqual(shift["official_name"], "manhã")
        evidence = json.loads(shift["evidence_json"])
        self.assertEqual(
            evidence["turnResolution"]["source"],
            "derived_from_unique_definitive_answer_key",
        )

    def test_preparation_uses_not_applicable_without_turn_partition(self) -> None:
        store = _seed(self.root, exam_turns=[], candidate_turns=[])

        result = _run_all(DesktopPreparationManager(store))

        self.assertEqual(result["skipped"], [])
        with closing(store._connect()) as connection:
            shift = connection.execute(
                "SELECT official_name,evidence_json FROM application_shifts"
            ).fetchone()
        self.assertEqual(shift["official_name"], "não se aplica")
        evidence = json.loads(shift["evidence_json"])
        self.assertEqual(evidence["turnResolution"]["source"], "not_applicable")

    def test_preparation_accepts_unknown_key_without_turn_partition(self) -> None:
        store = _seed(
            self.root,
            exam_turns=[],
            candidate_turns=[],
            answer_key_state="unknown",
        )

        result = _run_all(DesktopPreparationManager(store))

        self.assertEqual(result["identifiedExams"], 2)
        self.assertEqual(result["canonicalQuestions"], 1)
        with closing(store._connect()) as connection:
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM question_occurrences "
                "WHERE occurrence_status='unresolved_scope'"
            ).fetchone()[0]
        self.assertEqual(unresolved, 0)

    def test_preparation_blocks_multiple_candidate_turns(self) -> None:
        manager = DesktopPreparationManager(
            _seed(self.root, exam_turns=[], candidate_turns=["manhã", "tarde"])
        )

        result = _run_all(manager)

        self.assertEqual(result["identifiedExams"], 0)
        self.assertEqual(result["skipped"][0]["missingFields"], ["turn"])
        self.assertEqual(result["canonicalQuestions"], 0)

    def test_preparation_does_not_derive_turn_from_preliminary_key(self) -> None:
        manager = DesktopPreparationManager(
            _seed(
                self.root,
                exam_turns=[],
                candidate_turns=["manhã"],
                answer_key_state="preliminary",
            )
        )

        result = _run_all(manager)

        self.assertEqual(result["identifiedExams"], 0)
        self.assertEqual(result["skipped"][0]["missingFields"], ["turn"])

    def test_newly_resolved_scope_refreshes_existing_occurrences(self) -> None:
        store = _seed(self.root, exam_turns=[], candidate_turns=["manhã", "tarde"])
        manager = DesktopPreparationManager(store)
        first = _run_all(manager)
        with closing(store._connect()) as connection:
            unresolved_before = connection.execute(
                "SELECT COUNT(*) FROM question_occurrences WHERE scope_id IS NULL"
            ).fetchone()[0]
            for booklet in ("1", "2"):
                connection.execute(
                    "UPDATE document_links SET decision_json=? WHERE id=?",
                    (
                        _json(
                            _decision(
                                booklet,
                                exam_turns=[],
                                candidate_turns=["manhã"],
                            )
                        ),
                        f"link-{booklet}",
                    ),
                )
            connection.commit()

        second = _run_all(manager)

        self.assertEqual(first["canonicalQuestions"], 0)
        self.assertEqual(unresolved_before, 2)
        self.assertEqual(second["canonicalQuestions"], 1)
        self.assertEqual(second["occurrences"], 2)
        with closing(store._connect()) as connection:
            unresolved_after = connection.execute(
                "SELECT COUNT(*) FROM question_occurrences WHERE scope_id IS NULL"
            ).fetchone()[0]
        self.assertEqual(unresolved_after, 0)

    def test_unprepared_question_does_not_claim_an_unconfirmed_group(self) -> None:
        store = _seed(
            self.root, exam_turns=[], candidate_turns=["manhã", "tarde"]
        )
        _run_all(DesktopPreparationManager(store))
        with closing(store._connect()) as connection:
            connection.execute("UPDATE questions SET flags_json='[\"duplicate\"]'")
            connection.commit()

        result = store.query(DesktopFilterSet(), include_equivalent_copies=True)

        issue_codes = {
            issue["code"]
            for item in result["questions"]
            for issue in item["import_diagnosis"]["issues"]
        }
        self.assertIn("canonical_preparation_pending", issue_codes)
        self.assertNotIn("unresolved_duplicate", issue_codes)

    def test_pending_association_points_to_document_identity_review(self) -> None:
        manager = DesktopPreparationManager(_seed(self.root, include_review=True))

        summary = manager.summary()

        self.assertEqual(summary["pendingCases"], 1)
        self.assertEqual(summary["reviews"][0]["missingLabels"], ["turno"])
        self.assertIsNotNone(summary["reviews"][0]["questionId"])

    def test_preparation_accepts_numeric_year_stored_as_text(self) -> None:
        manager = DesktopPreparationManager(_seed(self.root, decision_year="2023"))

        result = _run_all(manager)

        self.assertEqual(result["canonicalQuestions"], 1)
        self.assertEqual(result["qwenEligible"], 1)

    def test_desktop_application_exposes_preparation_without_calling_qwen(self) -> None:
        _seed(self.root)
        application = DesktopApplication(self.root)

        scope = {"type": "all"}
        preview = application.preview_preparation({"scope": scope})
        result = application.prepare_questions(
            {"scope": scope, "confirmationToken": preview["confirmationToken"]}
        )

        self.assertEqual(preview["mode"], "preview")
        self.assertEqual(result["qwenEligible"], 1)
        self.assertEqual(application.bootstrap()["preparationSummary"]["canonicalQuestions"], 1)

    def test_selected_scope_lists_exact_records_and_rejects_outside_copy_impact(self) -> None:
        store = _seed(self.root)
        manager = DesktopPreparationManager(store)
        _run_all(manager)
        all_views = store.query(DesktopFilterSet(), include_equivalent_copies=True)["questions"]
        selected_id = str(all_views[0]["id"])
        scope = DesktopOperationScope(type="selected", questionIds=[selected_id])

        preview = manager.preview(scope)

        self.assertEqual(preview["scope"]["questionIds"], [selected_id])
        self.assertEqual(preview["selectedCount"], 1)
        self.assertEqual(preview["outsideScopeCount"], 1)
        self.assertTrue(preview["requiresOutsideScopeAuthorization"])
        with self.assertRaisesRegex(RuntimeError, "fora do escopo"):
            manager.run(str(preview["confirmationToken"]), scope)

        authorized_scope = DesktopOperationScope(
            type="selected", questionIds=[selected_id], allowOutOfScope=True
        )
        authorized_preview = manager.preview(authorized_scope)
        authorized = manager.run(
            str(authorized_preview["confirmationToken"]), authorized_scope
        )
        self.assertEqual(authorized["outsideScopeCount"], 1)
        self.assertTrue(authorized["outsideScopeAuthorized"])

    def test_filter_scope_is_explicit_and_token_cannot_be_reused(self) -> None:
        store = _seed(self.root)
        manager = DesktopPreparationManager(store)
        scope = DesktopOperationScope(
            type="filter", filter=DesktopFilterSet(boards=["FGV"])
        )

        preview = manager.preview(scope)
        result = manager.run(str(preview["confirmationToken"]), scope)

        self.assertEqual(result["selectedCount"], 2)
        with self.assertRaisesRegex(ValueError, "já utilizada"):
            manager.run(str(preview["confirmationToken"]), scope)


if __name__ == "__main__":
    unittest.main()
