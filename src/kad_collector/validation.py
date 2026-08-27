from __future__ import annotations

import hashlib
import json
import unicodedata

from .import_readiness import question_import_issues
from .models import QuestionBatch, QuestionRecord, ValidationState

_EDITORIAL_TEXT_FIELDS = (
    ("disciplina", "discipline"),
    ("materia", "matter"),
    ("assunto", "subject"),
    ("banca", "board"),
    ("orgao", "organization"),
    ("concurso", "concurso"),
    ("cargo", "role"),
    ("nivel", "level"),
    ("dificuldade", "difficulty"),
)

def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def _validate_question_for_transfer(
    question: QuestionRecord,
    *,
    required_text_fields: tuple[tuple[str, str], ...],
    purpose: str,
) -> list[str]:
    prefix = f"questao {question.number}"
    errors: list[str] = []
    for label, field_name in required_text_fields:
        value = getattr(question, field_name)
        if not isinstance(value, str) or len(value.strip()) < 2:
            errors.append(f"{prefix}: {label} obrigatorio para {purpose}")
    if len(question.statement.strip()) < 10:
        errors.append(f"{prefix}: enunciado deve ter pelo menos 10 caracteres")
    if question.year is None:
        errors.append(f"{prefix}: ano obrigatorio para {purpose}")
    if not question.source_pages:
        errors.append(f"{prefix}: pagina de origem obrigatoria para {purpose}")
    if question.answer_status != "matched" or question.correct_answer is None:
        state = "anulada" if question.answer_status == "annulled" else "sem gabarito"
        errors.append(f"{prefix}: questao {state} nao pode ser transferida")

    alternatives = question.alternatives
    expected_letters = list("ABCDE"[: len(alternatives)])
    letters = [alternative.letter for alternative in alternatives]
    if not 2 <= len(alternatives) <= 5:
        errors.append(f"{prefix}: exportacao exige de 2 a 5 alternativas")
    elif letters != expected_letters:
        errors.append(
            f"{prefix}: alternativas devem ser sequenciais de A ate {expected_letters[-1]}"
        )
    if question.correct_answer is not None and question.correct_answer not in letters:
        errors.append(f"{prefix}: gabarito nao corresponde as alternativas exportaveis")

    visual_text = " ".join(
        [question.statement, *(item.text for item in alternatives), *question.review_notes]
    )
    normalized_visual_text = _normalized(visual_text)
    visual_markers = (
        "alternativa visual",
        "imagem necessaria",
        "figura necessaria",
        "requer imagem",
        "requer figura",
    )
    if any(marker in normalized_visual_text for marker in visual_markers):
        errors.append(f"{prefix}: conteudo visual exige tratamento editorial separado")
    return errors


def validate_app_import_question(question: QuestionRecord) -> list[str]:
    """Validate the safe subset accepted as an app-import candidate.

    Explanation and difficulty deliberately remain outside this gate. They belong to
    publication readiness, while official answers, provenance fields and valid
    alternatives remain mandatory.
    """

    return [
        message
        for issue in question_import_issues(question)
        for message in issue.validation_messages
    ]


def validate_editorial_question(question: QuestionRecord) -> list[str]:
    errors = _validate_question_for_transfer(
        question,
        required_text_fields=_EDITORIAL_TEXT_FIELDS,
        purpose="exportacao",
    )
    if question.explanation is not None and len(question.explanation.strip()) < 10:
        errors.append(
            f"questao {question.number}: explicacao deve ter pelo menos 10 caracteres"
        )
    return errors


def validate_questions(
    questions: list[QuestionRecord], *, require_answers: bool = False,
    require_editorial: bool = False,
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
        if require_editorial:
            errors.extend(validate_editorial_question(question))

    if not questions:
        errors.append("nenhuma questao foi extraida")
    return ValidationState(valid=not errors, errors=errors, warnings=warnings)


def batch_content_sha256(batch: QuestionBatch) -> str:
    content = {
        "batch_id": batch.batch_id,
        "model": batch.model,
        "source_document": batch.source_document.model_dump(mode="json"),
        "answer_key_document": (
            batch.answer_key_document.model_dump(mode="json")
            if batch.answer_key_document is not None
            else None
        ),
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
    import_errors = [
        error
        for question in batch.questions
        for error in validate_app_import_question(question)
    ]
    errors = [*validation.errors, *import_errors]
    if errors:
        raise ValueError("lote aprovado falhou na validacao: " + "; ".join(errors))
