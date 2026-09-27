from __future__ import annotations

import hashlib

from kad_collector.structured_questions import StructuredQuestionPackage, _process_pair
from kad_collector.taxonomy_pilot import PilotScope, PilotSpec, draft_spec, run_pilot
from tests.test_structured_questions import _document

PROGRAM = b"Synthetic local program fixture, not an official approved catalog."


def pilot_inputs():
    exam = _document("exam", "PROVA OBJETIVA - CARGO 1", "a",
                     "CONHECIMENTOS ESPECÍFICOS – BLOCO III\n"
                     "Julgue os itens.\n1 O protocolo TCP funciona por conexão.")
    key = _document("answer_key", "GABARITO DEFINITIVO - CARGO 1", "b", "1 - C")
    question = _process_pair(exam, key, [])[0][0]
    items = [question.model_copy(update={
        "stable_id": hashlib.sha256(str(index).encode()).hexdigest(),
        "original_number": index + 1,
    }) for index in range(100)]
    package = StructuredQuestionPackage.model_construct(accepted=items, quarantined=[])
    spec = PilotSpec(catalog={
        "id": "pf21-fixture", "version": "1.0.0",
        "sources": [{"id": "fixture", "title": "Fixture",
                     "url": "https://example.gov.br/programa"}],
        "disciplines": [
            {"name": "Informática", "topics": [{"id": "pf21:tcp", "matter": "Redes",
             "subject": "TCP", "keywords": ["protocolo TCP"]}]},
            {"name": "Bancária", "topics": [{"id": "bank:credit", "matter": "Crédito",
             "subject": "Crédito rural", "keywords": ["crédito rural"]}]},
        ],
    }, scopes=[PilotScope(role=question.role, block=question.block,
                          allowed_path_ids=["pf21:tcp"])],
        program_sha256=hashlib.sha256(PROGRAM).hexdigest())
    spec.reviewed_by = "fixture-reviewer"
    spec.reviewed_digest = spec.digest()
    return package, spec


def test_pilot_requires_frozen_catalog_and_human_sample_not_mass_approval():
    package, spec = pilot_inputs()
    before = package.model_dump()
    first = run_pilot(package, spec, program=PROGRAM)
    assert first["sample_size"] == 100
    assert not first["ready_for_next_pilot"]
    assert first["reviewed"] == 0
    reviews = [{"stable_id": row["stable_id"], "question_sha256": row["question_sha256"],
                "spec_sha256": first["spec_sha256"], "reviewer": "fixture-human",
                "path_id": "pf21:tcp"} for row in first["sample"]]
    passed = run_pilot(package, spec, program=PROGRAM, reviews=reviews)
    assert passed["correct_full_paths"] == 100
    assert passed["ready_for_next_pilot"]
    assert not passed["publication_authorized"]
    assert passed["qwen_calls"] == 0
    assert run_pilot(package, spec, program=PROGRAM, reviews=reviews) == passed
    assert package.model_dump() == before
    assert not run_pilot(package, spec, program=PROGRAM, reviews=reviews[:94])[
        "ready_for_next_pilot"]
    assert "duplicate_review" in run_pilot(
        package, spec, program=PROGRAM, reviews=reviews + reviews[:1])["issues"]


def test_draft_changed_catalog_or_missing_edital_cannot_classify():
    package, spec = pilot_inputs()
    draft = run_pilot(package, draft_spec(package))
    assert "catalog_requires_human_confirmation" in draft["issues"]
    assert all(row["suggested_path_id"] is None for row in draft["sample"])
    spec.scopes[0].allowed_path_ids.append("bank:credit")
    assert "catalog_requires_human_confirmation" in run_pilot(
        package, spec, program=PROGRAM)["issues"]
    assert "official_program_missing_or_changed" in run_pilot(package, spec)["issues"]


def test_no_banking_leak_no_other_year_no_missing_block_fallback():
    package, spec = pilot_inputs()
    package.accepted[0] = package.accepted[0].model_copy(update={
        "statement": "O crédito rural opera como crédito rural."
    })
    package.accepted[1] = package.accepted[1].model_copy(update={"year": 2025})
    package.accepted[2] = package.accepted[2].model_copy(update={"block": "BLOCO II"})
    report = run_pilot(package, spec, program=PROGRAM)
    by_id = {row["stable_id"]: row for row in report["sample"]}
    assert report["sample_size"] == 99
    assert by_id[package.accepted[0].stable_id]["suggested_path_id"] is None
    assert by_id[package.accepted[2].stable_id]["status"] == "scope_missing"
    assert not any(row["suggested_path_id"] == "bank:credit" for row in report["sample"])


def test_stale_review_is_not_reused_after_question_change():
    package, spec = pilot_inputs()
    first = run_pilot(package, spec, program=PROGRAM)
    row = first["sample"][0]
    review = {"stable_id": row["stable_id"], "question_sha256": row["question_sha256"],
              "spec_sha256": first["spec_sha256"], "reviewer": "fixture-human",
              "path_id": "pf21:tcp"}
    for index, question in enumerate(package.accepted):
        if question.stable_id == row["stable_id"]:
            package.accepted[index] = question.model_copy(update={"correct_answer": "B"})
    assert run_pilot(package, spec, program=PROGRAM, reviews=[review])["reviewed"] == 0
