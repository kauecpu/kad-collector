from __future__ import annotations

import json
from pathlib import Path

from scripts.prepare_cebraspe_pf_matrix import _kind, _pair_key
from scripts.run_cebraspe_pf_homologation import _pairing, _summary

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "tests" / "homologation" / "cebraspe-pf.v1.json"


def test_matrix_freezes_four_real_pf_contests_with_balanced_pairs() -> None:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))

    assert payload["source_id"] == "cebraspe_policia_federal"
    assert [sample["id"] for sample in payload["samples"]] == [
        "pf_25",
        "pf_25_adm",
        "pf_21",
        "pf_18",
    ]
    assert sum(len(sample["expected"]) for sample in payload["samples"]) == 112
    for sample in payload["samples"]:
        exams = [item for item in sample["expected"] if item["kind"] == "exam"]
        answer_keys = [
            item for item in sample["expected"] if item["kind"] == "answer_key"
        ]
        assert len(exams) == len(answer_keys)
        assert all(item["url"].startswith("https://cdn.cebraspe.org.br/") for item in exams)
        assert sample["rejected_examples"]
        pair_keys = {item["pair_key"] for item in sample["expected"]}
        for pair_key in pair_keys:
            paired = [item for item in sample["expected"] if item["pair_key"] == pair_key]
            assert [item["kind"] for item in paired].count("exam") == 1
            assert [item["kind"] for item in paired].count("answer_key") == 1


def test_official_answer_key_wins_over_exam_words_in_same_title() -> None:
    title = "GABARITO DEFINITIVO - PROVA OBJETIVA - CARGO 14"

    assert _kind(title) == "answer_key"
    assert _pair_key(title) == "cargo_14"
    assert _kind("PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 14") == "exam"
    assert _kind("RESULTADO FINAL NA PROVA OBJETIVA") is None


def test_basic_knowledge_pair_keys_preserve_distinct_blocks_and_roles() -> None:
    assert (
        _pair_key("PROVA OBJETIVA - CONHECIMENTOS BÁSICOS - BLOCO II - CARGOS 15 A 17")
        == "conhecimentos_basicos_bloco_ii"
    )
    assert (
        _pair_key("GABARITO DEFINITIVO - CONHECIMENTOS BÁSICOS PARA O CARGO 15")
        == "conhecimentos_basicos_cargo_15"
    )


def test_pairing_requires_both_sides_of_each_expected_pair() -> None:
    expected = [
        {"kind": "exam", "filename": "prova.pdf", "pair_key": "cargo_1"},
        {"kind": "answer_key", "filename": "gabarito.pdf", "pair_key": "cargo_1"},
    ]

    incomplete = _pairing(expected, {"prova.pdf"})
    complete = _pairing(expected, {"prova.pdf", "gabarito.pdf"})

    assert incomplete[0]["associated"] is False
    assert complete[0]["associated"] is True


def test_summary_counts_false_results_and_isolated_failures() -> None:
    results = [
        {
            "expected_count": 4,
            "found_count": 4,
            "exam_count": 2,
            "answer_key_count": 2,
            "false_positives": [],
            "false_negatives": [],
            "prohibited_accepted": [],
            "pairings": [{"associated": True}, {"associated": True}],
            "ocr_attempted": 0,
            "ocr_succeeded": 0,
            "duration_seconds": 2.0,
            "failures": [],
            "paths": ["deterministic_browser"],
            "qwen_calls": [],
        },
        {
            "expected_count": 2,
            "found_count": 0,
            "exam_count": 0,
            "answer_key_count": 0,
            "false_positives": [],
            "false_negatives": [{}, {}],
            "prohibited_accepted": [],
            "pairings": [],
            "ocr_attempted": 0,
            "ocr_succeeded": 0,
            "duration_seconds": 0.0,
            "failures": [{"stage": "discovery"}],
            "paths": ["deterministic_browser"],
            "qwen_calls": [],
        },
    ]

    summary = _summary(results)

    assert summary["precision"] == 1.0
    assert summary["coverage"] == 4 / 6
    assert summary["pairing_rate"] == 1.0
    assert summary["sources_without_intervention_rate"] == 0.5
