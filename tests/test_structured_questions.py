from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from kad_collector.models import DocumentRecord, ExtractedDocument, ExtractedPage
from kad_collector.structured_questions import (
    StructuredQuestionPackage,
    _process_pair,
    build_structured_question_package,
)


def _record(kind: str, title: str, digest: str) -> DocumentRecord:
    return DocumentRecord(
        source_id="cebraspe_policia_federal",
        source_name="Cebraspe",
        document_type=kind,  # type: ignore[arg-type]
        title=title,
        original_url=f"https://cdn.cebraspe.org.br/concursos/pf_21/{digest}.pdf",
        resolved_url=f"https://cdn.cebraspe.org.br/concursos/pf_21/{digest}.pdf",
        local_path=f"ignored/{digest}.pdf",
        sha256=digest * 64,
        content_type="application/pdf",
        size_bytes=100,
        downloaded_at=datetime(2026, 9, 7, tzinfo=UTC),
        authorization_basis="fonte oficial",
        metadata={"board": "Cebraspe", "organization": "Polícia Federal"},
    )


def _document(
    kind: str,
    title: str,
    digest: str,
    text: str,
    *,
    method: str = "text",
) -> ExtractedDocument:
    return ExtractedDocument(
        document=_record(kind, title, digest),
        pages=[
            ExtractedPage(
                number=1,
                text=text,
                character_count=len(text),
                extraction_method=method,  # type: ignore[arg-type]
                confidence=0.91 if method == "ocr" else None,
                duration_ms=17,
                ocr_reason=(
                    "camada de texto ausente ou insuficiente" if method == "ocr" else None
                ),
            )
        ],
        text=text,
    )


def test_package_is_review_first_and_semantically_idempotent(
    tmp_path: Path, monkeypatch: object
) -> None:
    exam_text = (
        "CONHECIMENTOS ESPECÍFICOS – BLOCO III\n"
        "Julgue os itens subsequentes.\n"
        "1 A primeira afirmação está completa.\n"
        "2 A figura precedente confirma a segunda afirmação.\n"
        "3 A terceira afirmação está completa.\n"
    )
    exam = _document(
        "exam", "PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 1", "a", exam_text,
        method="ocr",
    )
    answer_key = _document(
        "answer_key", "GABARITO DEFINITIVO - CARGO 1", "b", "1 - C\n2 - E\n3 - X"
    )
    documents = [exam, answer_key]
    availability = iter((True, False))

    import kad_collector.structured_questions as module

    monkeypatch.setattr(  # type: ignore[attr-defined]
        module,
        "_load_extracted_documents",
        lambda *_args, **_kwargs: (documents, ["c" * 64]),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        module, "_ollama_available", lambda *_args, **_kwargs: next(availability)
    )

    first = build_structured_question_package([Path("manifest.json")], tmp_path / "a.json")
    second = build_structured_question_package([Path("manifest.json")], tmp_path / "b.json")

    assert first.content_sha256 == second.content_sha256
    assert [item.stable_id for item in first.accepted] == [
        item.stable_id for item in second.accepted
    ]
    assert first.metrics.expected_questions == 3
    assert first.metrics.detected_questions == 3
    assert first.metrics.ocr_pages == 1
    assert first.metrics.qwen_calls == 0
    assert len(first.accepted) == 1
    assert len(first.quarantined) == 2
    assert {reason for item in first.quarantined for reason in item.validation_reasons} == {
        "elemento visual exige revisão",
        "item anulado no gabarito oficial",
    }
    StructuredQuestionPackage.model_validate_json((tmp_path / "a.json").read_text("utf-8"))


def test_multiple_choice_answer_is_preserved() -> None:
    exam = _document(
        "exam",
        "PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 1",
        "d",
        "QUESTÃO 1\nQual alternativa está correta?\nA) Alfa\nB) Beta\nC) Gama\nD) Delta",
    )
    answer_key = _document(
        "answer_key", "GABARITO DEFINITIVO - CARGO 1", "e", "1 - D"
    )

    questions, metrics = _process_pair(exam, answer_key, [])

    assert metrics.detected_questions == 1
    assert questions[0].question_format == "multiple_choice"
    assert questions[0].correct_answer == "D"
    assert questions[0].correct_answer_label == "Delta"


def test_qwen_is_used_only_for_a_missing_deterministic_item(monkeypatch: object) -> None:
    exam = _document(
        "exam",
        "PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 1",
        "f",
        "SEÇÃO NÃO RECONHECIDA\n1 A afirmação consta literalmente no PDF.",
    )
    answer_key = _document(
        "answer_key", "GABARITO DEFINITIVO - CARGO 1", "1", "1 - C"
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "message": {
                    "content": (
                        '{"questions":[{"number":1,'
                        '"statement":"A afirmação consta literalmente no PDF.",'
                        '"source_pages":[1]}]}'
                    )
                }
            }

    import kad_collector.structured_questions as module

    monkeypatch.setattr(module.httpx, "post", lambda *_args, **_kwargs: Response())  # type: ignore[attr-defined]
    decisions = []

    questions, metrics = _process_pair(
        exam,
        answer_key,
        [],
        qwen_available=True,
        qwen_decisions=decisions,
    )

    assert len(questions) == 1
    assert questions[0].parser_version == "qwen-fallback-1.0"
    assert questions[0].correct_answer == "A"
    assert metrics.qwen_calls == 1
    assert decisions[0].accepted
    assert decisions[0].response == {"recovered_numbers": [1]}
