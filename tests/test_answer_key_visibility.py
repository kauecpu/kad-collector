from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kad_collector.answer_key_diagnostics import AnswerKeyEvidence, diagnose_answer_key
from kad_collector.desktop_models import DesktopFilterSet
from kad_collector.desktop_store import DesktopStore


def _evidence(
    *,
    status: str = "missing",
    link_id: str | None = None,
    valid_link: bool = False,
    exam_version_id: str | None = "exam-v1",
    candidates: int = 0,
    review_reason: str | None = None,
) -> AnswerKeyEvidence:
    return AnswerKeyEvidence(
        answer_status=status,
        answer_key_link_id=link_id,
        valid_answer_association=valid_link,
        exam_version_id=exam_version_id,
        compatible_candidate_count=candidates,
        review_reason=review_reason,
    )


def _view(state: str, diagnostic: str | None) -> dict[str, object]:
    answer_status = {"official": "matched", "annulled": "annulled"}.get(
        state, "missing"
    )
    return {
        "status": "pending",
        "question": {"answer_status": answer_status},
        "metadata": {},
        "filename": "prova.pdf",
        "confidence": 0.0,
        "flags": [],
        "exportable": False,
        "importable": False,
        "publication_ready": False,
        "readiness_states": ["blocked"],
        "block_reasons": ["missing_official_answer"] if state == "missing" else [],
        "answer_key_state": state,
        "answer_key_diagnosis": {"diagnosticCode": diagnostic},
    }


class AnswerKeyDiagnosticTests(unittest.TestCase):
    def test_empty_bank_has_zero_counts_and_no_diagnostics(self) -> None:
        summary = DesktopStore._summary([])

        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["answer_matched"], 0)
        self.assertEqual(summary["answer_annulled"], 0)
        self.assertEqual(summary["answer_missing"], 0)
        self.assertEqual(summary["answer_key_diagnostics"], {})

    def test_six_missing_answer_scenarios_are_exclusive_and_evidence_based(self) -> None:
        fixtures = {
            "answer_key_not_collected": _evidence(candidates=0),
            "answer_key_unlinked": _evidence(candidates=1),
            "question_missing_in_answer_key": _evidence(
                link_id="link-v1", valid_link=True, candidates=1
            ),
            "ambiguous_answer_key_association": _evidence(candidates=2),
            "answer_key_awaiting_definitive": _evidence(
                candidates=1,
                review_reason=(
                    "somente gabarito preliminar compatível; aguardando definitivo"
                ),
            ),
            "answer_key_diagnosis_pending": _evidence(
                exam_version_id=None, candidates=0
            ),
        }

        observed = {
            str(diagnose_answer_key(evidence)["diagnosticCode"])
            for evidence in fixtures.values()
        }

        self.assertEqual(observed, set(fixtures))
        for expected, evidence in fixtures.items():
            result = diagnose_answer_key(evidence)
            self.assertEqual(result["state"], "missing")
            self.assertEqual(result["diagnosticCode"], expected)
            self.assertTrue(result["label"])
            self.assertTrue(result["explanation"])
            self.assertTrue(result["action"])

    def test_official_and_annulled_answers_have_no_missing_diagnostic(self) -> None:
        matched = diagnose_answer_key(_evidence(status="matched", valid_link=True))
        annulled = diagnose_answer_key(_evidence(status="annulled", valid_link=True))

        self.assertEqual(matched["state"], "official")
        self.assertEqual(annulled["state"], "annulled")
        self.assertIsNone(matched["diagnosticCode"])
        self.assertIsNone(annulled["diagnosticCode"])

    def test_unproved_stale_answer_is_not_presented_as_official(self) -> None:
        stale = diagnose_answer_key(
            _evidence(status="matched", valid_link=False, candidates=0)
        )

        self.assertEqual(stale["state"], "missing")
        self.assertEqual(stale["diagnosticCode"], "answer_key_not_collected")

    def test_ambiguity_can_be_recorded_for_notebook_role_or_turn(self) -> None:
        for reason in ("caderno em conflito", "cargo em conflito", "turno em conflito"):
            with self.subTest(reason=reason):
                evidence = AnswerKeyEvidence(
                    answer_status="missing",
                    answer_key_link_id=None,
                    valid_answer_association=False,
                    exam_version_id="exam-v1",
                    compatible_candidate_count=2,
                    review_status="pending",
                    review_reason=reason,
                )
                self.assertEqual(
                    diagnose_answer_key(evidence)["diagnosticCode"],
                    "ambiguous_answer_key_association",
                )

    def test_fixture_preserves_1540_total_and_partitions_all_420_missing(self) -> None:
        distribution = {
            "answer_key_not_collected": 40,
            "answer_key_unlinked": 90,
            "question_missing_in_answer_key": 80,
            "ambiguous_answer_key_association": 70,
            "answer_key_diagnosis_pending": 80,
            "answer_key_awaiting_definitive": 60,
        }
        views = [_view("official", None) for _ in range(1092)]
        views.extend(_view("annulled", None) for _ in range(28))
        for code, count in distribution.items():
            views.extend(_view("missing", code) for _ in range(count))

        summary = DesktopStore._summary(views)

        self.assertEqual(summary["total"], 1540)
        self.assertEqual(summary["answer_matched"], 1092)
        self.assertEqual(summary["answer_annulled"], 28)
        self.assertEqual(summary["answer_missing"], 420)
        self.assertEqual(summary["answer_key_diagnostics"], distribution)
        self.assertEqual(sum(summary["answer_key_diagnostics"].values()), 420)

    def test_cards_and_each_diagnostic_filter_return_the_expected_partition(self) -> None:
        codes = [
            "answer_key_not_collected",
            "answer_key_unlinked",
            "question_missing_in_answer_key",
            "ambiguous_answer_key_association",
            "answer_key_awaiting_definitive",
            "answer_key_diagnosis_pending",
        ]
        views = [_view("official", None), _view("annulled", None)] + [
            _view("missing", code) for code in codes
        ]
        with TemporaryDirectory() as directory:
            store = DesktopStore(Path(directory) / "collector.sqlite3")
            states = Counter(
                view["answer_key_state"]
                for view in views
                if store._matches(
                    view,
                    DesktopFilterSet(answer_states=[view["answer_key_state"]]),
                )
            )
            self.assertEqual(states, {"official": 1, "annulled": 1, "missing": 6})
            for code in codes:
                selected = [
                    view
                    for view in views
                    if store._matches(
                        view,
                        DesktopFilterSet(
                            answer_states=["missing"], answer_diagnostics=[code]
                        ),
                    )
                ]
                self.assertEqual(len(selected), 1)
                self.assertEqual(
                    selected[0]["answer_key_diagnosis"]["diagnosticCode"], code
                )

    def test_answer_filter_combines_with_board_contest_year_and_search(self) -> None:
        view = _view("missing", "answer_key_not_collected")
        view["question"] = {
            "answer_status": "missing",
            "board": "FGV",
            "concurso": "RFB22",
            "year": 2023,
            "statement": "Qual alternativa está correta?",
            "alternatives": [],
        }
        with TemporaryDirectory() as directory:
            store = DesktopStore(Path(directory) / "collector.sqlite3")
            filters = DesktopFilterSet(
                answer_states=["missing"],
                answer_diagnostics=["answer_key_not_collected"],
                boards=["FGV"],
                concursos=["RFB22"],
                years=[2023],
                search="alternativa",
            )

            self.assertTrue(store._matches(view, filters))
            self.assertFalse(
                store._matches(view, filters.model_copy(update={"years": [2024]}))
            )

    def test_passive_diagnostic_does_not_modify_evidence_or_offer_qwen(self) -> None:
        evidence = _evidence(candidates=0)
        before = evidence

        result = diagnose_answer_key(evidence)

        self.assertEqual(evidence, before)
        self.assertNotIn("qwen", str(result).casefold())

    def test_review_detail_uses_the_same_enriched_evidence_as_the_list(self) -> None:
        with TemporaryDirectory() as directory:
            store = DesktopStore(Path(directory) / "collector.sqlite3")
            row = {"id": "question-v1"}
            diagnosed = {
                "id": "question-v1",
                "answer_key_diagnosis": {
                    "diagnosticCode": "ambiguous_answer_key_association"
                },
            }
            with (
                patch.object(store, "_all_question_rows", return_value=[row]),
                patch.object(store, "_question_view", return_value=diagnosed),
            ):
                detail = store.question("question-v1")

        self.assertEqual(
            detail["answer_key_diagnosis"]["diagnosticCode"],
            "ambiguous_answer_key_association",
        )


if __name__ == "__main__":
    unittest.main()
