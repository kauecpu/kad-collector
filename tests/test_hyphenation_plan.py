from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kad_collector.hyphenation_plan import HyphenationPlan, apply_hyphenation_plan
from kad_collector.json_utils import write_json
from kad_collector.structured_questions import build_structured_question_package
from tests.test_structured_questions import _document


def _inputs(tmp_path: Path):  # type: ignore[no-untyped-def]
    pdf = tmp_path / "exam.pdf"
    pdf.write_bytes(b"%PDF-1.4 fixture")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    document = _document(
        "exam", "PROVA OBJETIVA - CARGO 1", "a",
        "QUESTÃO 1\nComo deve o cliente inves-\ntir?\nA) Alfa\nB) Beta\nC) Gama\nD) Delta",
    )
    document.document.local_path = str(pdf)
    document.document.sha256 = digest
    plan = HyphenationPlan.model_validate({
        "prepared_by": "assistente (proposta, não aprovação humana)",
        "joins": [{
            "document_sha256": digest, "page_number": 1,
            "before": "inves-\ntir", "after": "investir",
            "reason": "Conferência visual da palavra na página da prova.",
        }],
    })
    return document, plan


def test_proposal_applies_before_segmentation_and_is_reproducible(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    document, plan = _inputs(tmp_path)
    key = _document("answer_key", "GABARITO DEFINITIVO - CARGO 1", "b", "1 - D")
    plan_path = tmp_path / "joins.json"
    write_json(plan_path, plan.model_dump(mode="json"))
    import kad_collector.structured_questions as module

    monkeypatch.setattr(module, "_load_extracted_documents", lambda *_: ([document, key], []))
    first = build_structured_question_package(
        [Path("manifest.json")], tmp_path / "first.json",
        enable_ollama=False, hyphenation_plan=plan_path,
    )
    second = build_structured_question_package(
        [Path("manifest.json")], tmp_path / "second.json",
        enable_ollama=False, hyphenation_plan=plan_path,
    )
    assert first.content_sha256 == second.content_sha256
    assert "investir" in first.accepted[0].statement
    assert "inves-\ntir" in document.pages[0].text  # cache is never mutated
    assert plan.digest() in first.parser_version
    assert first.metrics.intervention_free_percent == 0
    assert (tmp_path / "first.hyphenation.json").read_bytes() == plan_path.read_bytes()
    assert plan.status == "proposed"


@pytest.mark.parametrize("after", ["guardar", "inves-tir", "investir mais", "D"])
def test_cannot_rewrite_content_or_answers(tmp_path, after):  # type: ignore[no-untyped-def]
    _, plan = _inputs(tmp_path)
    payload = plan.model_dump()
    payload["joins"][0]["after"] = after
    with pytest.raises(ValueError, match="só pode remover"):
        HyphenationPlan.model_validate(payload)


@pytest.mark.parametrize("problem", ["page", "ambiguous", "stale", "substring", "pdf"])
def test_stale_or_ambiguous_proposals_fail_closed(tmp_path, problem):  # type: ignore[no-untyped-def]
    document, plan = _inputs(tmp_path)
    if problem == "page":
        document.pages[0].number = 2
    elif problem == "ambiguous":
        document.pages[0].text += " inves-\ntir"
    elif problem == "stale":
        document.pages[0].text = "investir"
    elif problem == "substring":
        document.pages[0].text = "reinves-\ntir"
    else:
        Path(document.document.local_path).write_bytes(b"changed")
    original = document.model_dump()
    with pytest.raises(ValueError):
        apply_hyphenation_plan([document], plan)
    assert document.model_dump() == original


def test_unknown_document_and_double_proposals_rejected(tmp_path):  # type: ignore[no-untyped-def]
    document, plan = _inputs(tmp_path)
    with pytest.raises(ValueError, match="documento"):
        apply_hyphenation_plan([], plan)
    plan.joins.append(plan.joins[0])
    with pytest.raises(ValueError, match="ausente"):
        apply_hyphenation_plan([document], plan)
