from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from kad_collector.json_utils import write_json
from kad_collector.local_review import (
    create_review_session,
    decide_review_question,
    export_review_session,
    save_review_session,
)
from kad_collector.models import DownloadManifest, QuestionBatch
from kad_collector.operator_review import _question_record
from kad_collector.structured_questions import build_structured_question_package
from kad_collector.validation import validate_questions
from scripts.revise_pilot_review import revise_pilot_review
from tests.test_hyphenation_plan import _inputs
from tests.test_structured_questions import _document


def _revision_inputs(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    exam, plan = _inputs(tmp_path)
    key = _document("answer_key", "GABARITO DEFINITIVO - CARGO 1", "b", "1 - D")
    pdf = tmp_path / "key.pdf"
    pdf.write_bytes(b"%PDF-1.4 key fixture")
    key.document.local_path = str(pdf)
    key.document.sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    write_json(manifest, DownloadManifest(
        created_at=datetime.now(UTC), documents=[exam.document, key.document],
    ).model_dump(mode="json"))
    plan_path = tmp_path / "joins.json"
    write_json(plan_path, plan.model_dump(mode="json"))
    import kad_collector.structured_questions as module

    monkeypatch.setattr(module, "_load_extracted_documents", lambda *_: (
        [exam, key], [hashlib.sha256(manifest.read_bytes()).hexdigest()],
    ))
    package_path = tmp_path / "package.json"
    package = build_structured_question_package(
        [manifest], package_path, hyphenation_plan=plan_path, enable_ollama=False,
    )
    question = _question_record(package.accepted[0], state="accepted")
    question.statement = question.statement.replace("investir", "inves-\ntir")
    question.discipline = "Matemática"
    question.matter = "Matemática Comercial"
    question.subject = "Porcentagens e Rendimentos"
    question.level = "Médio"
    batch = QuestionBatch(
        batch_id="fixture", created_at=datetime.now(UTC), model="fixture",
        source_document=exam.document, answer_key_document=key.document,
        questions=[question], validation=validate_questions([question]),
    )
    session = create_review_session(batch)
    session = decide_review_question(session, 1, "approved", "fixture-human")
    source = tmp_path / "original-session.json"
    save_review_session(session, source)
    return source, package_path, manifest


def test_new_revision_never_inherits_approval_or_overwrites_source(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    paths = _revision_inputs(tmp_path, monkeypatch)
    before = paths[0].read_bytes()
    first = revise_pilot_review(*paths, tmp_path / "review")
    second = revise_pilot_review(*paths, tmp_path / "repeat")
    assert first.batch.questions == second.batch.questions
    assert first.source_content_sha256 == second.source_content_sha256
    assert first.batch.questions[0].statement == "Como deve o cliente investir?"
    assert first.decisions[0].status == "pending"
    assert first.decisions[0].reviewed_by is None
    assert first.batch.review.status == "pending"
    assert first.batch.questions[0].discipline == "Matemática"
    assert paths[0].read_bytes() == before
    with pytest.raises(ValueError, match="pendentes"):
        export_review_session(first, "fixture-reviewer", output_path=tmp_path / "export.json")
    assert not (tmp_path / "export.json").exists()
    assert (tmp_path / "review/revision.json").read_bytes() == (
        tmp_path / "repeat/revision.json"
    ).read_bytes()
    with pytest.raises(FileExistsError):
        revise_pilot_review(*paths, tmp_path / "review")


def test_revision_rejects_content_change_and_does_not_create_output(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    import json

    paths = _revision_inputs(tmp_path, monkeypatch)
    payload = json.loads(paths[0].read_text("utf-8"))
    # A changed original is not allowed to carry its classification into different content.
    payload["batch"]["questions"][0]["statement"] = "Outro conteúdo sem equivalência."
    payload["decisions"][0] = {"question_number": 1, "status": "pending"}
    write_json(paths[0], payload)
    with pytest.raises(ValueError, match="conteúdo, página ou resposta"):
        revise_pilot_review(*paths, tmp_path / "review")
    assert not (tmp_path / "review").exists()
