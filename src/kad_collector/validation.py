from __future__ import annotations

import hashlib
import json

from .models import QuestionBatch, QuestionRecord, ValidationState


def validate_questions(
    questions: list[QuestionRecord], *, require_answers: bool = False
) -> ValidationState:
    errors: list[str] = []
    warnings: list[str] = []
    seen_numbers: set[int] = set()
    for question in questions:
        prefix = f"questao {question.number}"
        if question.number in seen_numbers:
            errors.append(f"{prefix}: numero duplicado")
        seen_numbers.add(question.number)

        letters = [alternative.letter for alternative in question.alternatives]
        if len(set(letters)) != len(letters):
            errors.append(f"{prefix}: letras de alternativas duplicadas")
        if question.correct_answer and question.correct_answer not in letters:
            errors.append(f"{prefix}: resposta nao existe entre as alternativas")
        if question.answer_status == "matched" and question.correct_answer is None:
            errors.append(f"{prefix}: resposta matched sem alternativa correta")
        if (
            question.answer_status in {"missing", "annulled"}
            and question.correct_answer is not None
        ):
            errors.append(f"{prefix}: estado {question.answer_status} contradiz a resposta correta")
        if require_answers and question.answer_status == "missing":
            errors.append(f"{prefix}: resposta do gabarito ausente")
        if not question.source_pages:
            warnings.append(f"{prefix}: pagina de origem nao identificada")
        for field_name, value in (
            ("materia", question.matter),
            ("assunto", question.subject),
            ("banca", question.board),
            ("orgao", question.organization),
            ("cargo", question.role),
            ("ano", question.year),
        ):
            if value is None or value == "":
                warnings.append(f"{prefix}: {field_name} requer revisao")

    if not questions:
        errors.append("nenhuma questao foi extraida")
    return ValidationState(valid=not errors, errors=errors, warnings=warnings)


def batch_content_sha256(batch: QuestionBatch) -> str:
    content = {
        "batch_id": batch.batch_id,
        "model": batch.model,
        "source_document": batch.source_document.model_dump(mode="json"),
        "filters": batch.filters.model_dump(mode="json"),
        "filtered_out_questions": batch.filtered_out_questions,
        "questions": [question.model_dump(mode="json") for question in batch.questions],
    }
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_approved_batch(batch: QuestionBatch) -> None:
    if batch.review.status != "approved":
        raise ValueError("o lote ainda nao foi aprovado")
    expected = batch_content_sha256(batch)
    if batch.review.content_sha256 != expected:
        raise ValueError("o conteudo mudou depois da aprovacao")
    validation = validate_questions(batch.questions, require_answers=True)
    if not validation.valid:
        raise ValueError("lote aprovado falhou na validacao: " + "; ".join(validation.errors))
