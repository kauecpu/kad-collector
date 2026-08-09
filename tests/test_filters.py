from __future__ import annotations

import unittest

from pydantic import ValidationError

from kad_collector.cli import _filters_from_args, build_parser
from kad_collector.filters import filter_questions, question_matches_filters
from kad_collector.models import Alternative, CollectionFilters, QuestionRecord


def question(**changes: object) -> QuestionRecord:
    data: dict[str, object] = {
        "number": 1,
        "statement": "Enunciado completo.",
        "alternatives": [
            Alternative(letter="A", text="Alternativa A"),
            Alternative(letter="B", text="Alternativa B"),
        ],
        "matter": "Direito",
        "subject": "Direito Administrativo",
        "board": "Fundacao Getulio Vargas (FGV)",
        "organization": "TJ-SP",
        "role": "Escrevente Tecnico",
        "year": 2022,
        "source_pages": [1],
    }
    data.update(changes)
    return QuestionRecord.model_validate(data)


class FilterTests(unittest.TestCase):
    def test_cli_builds_repeatable_filters_with_portuguese_aliases(self) -> None:
        args = build_parser().parse_args(
            ["collect", "--ano", "2022", "--banca", "FGV", "--materia", "Direito"]
        )
        filters = _filters_from_args(args)
        self.assertIsNotNone(filters)
        assert filters is not None
        self.assertEqual(filters.years, [2022])
        self.assertEqual(filters.boards, ["FGV"])
        self.assertEqual(filters.matters, ["Direito"])

    def test_question_filter_is_case_and_accent_insensitive(self) -> None:
        filters = CollectionFilters(
            years=[2022],
            boards=["fgv"],
            organizations=["tj-sp"],
            roles=["escrevente"],
            matters=["direito"],
            subjects=["administrativo"],
        )
        self.assertTrue(question_matches_filters(question(), filters))

    def test_filter_merge_preserves_the_original_collection_request(self) -> None:
        collected = CollectionFilters(years=[2022], boards=["FGV", "FCC"])
        processed = CollectionFilters(boards=["FGV"], subjects=["Administrativo"])
        merged = collected.merged_with(processed)
        self.assertEqual(merged.years, [2022])
        self.assertEqual(merged.boards, ["FGV"])
        self.assertEqual(merged.subjects, ["Administrativo"])

    def test_filter_merge_rejects_a_contradictory_refinement(self) -> None:
        collected = CollectionFilters(years=[2022])
        processed = CollectionFilters(years=[2023])
        with self.assertRaisesRegex(ValueError, "contradiz"):
            collected.merged_with(processed)

    def test_strict_filter_removes_unknown_and_mismatched_questions(self) -> None:
        filters = CollectionFilters(years=[2022], boards=["FGV"])
        selected, removed = filter_questions(
            [question(number=1), question(number=2, year=2023), question(number=3, board=None)],
            filters,
        )
        self.assertEqual([item.number for item in selected], [1])
        self.assertEqual(removed, 2)

    def test_answer_state_rejects_matched_without_answer(self) -> None:
        with self.assertRaises(ValidationError):
            question(answer_status="matched", correct_answer=None)


if __name__ == "__main__":
    unittest.main()
