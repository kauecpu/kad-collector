from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import Field

from .models import QuestionRecord, StrictModel

ImportBlockCode = Literal[
    "missing_classification",
    "missing_metadata",
    "missing_year",
    "missing_source_page",
    "invalid_statement",
    "missing_official_answer",
    "annulled_answer",
    "invalid_alternatives",
    "visual_content",
    "unproved_origin",
    "unresolved_duplicate",
    "ambiguous_association",
    "version_conflict",
]


class ImportReadinessIssue(StrictModel):
    code: ImportBlockCode
    what: str
    why: str
    how_to_resolve: str
    source_document: str
    missing: list[str] = Field(default_factory=list)
    validation_messages: list[str] = Field(default_factory=list, exclude=True)


class ImportReadinessDiagnosis(StrictModel):
    importable: bool
    issues: list[ImportReadinessIssue]


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def _issue(
    code: ImportBlockCode,
    *,
    what: str,
    why: str,
    how: str,
    source_document: str,
    missing: list[str] | None = None,
    messages: list[str] | None = None,
) -> ImportReadinessIssue:
    return ImportReadinessIssue(
        code=code,
        what=what,
        why=why,
        how_to_resolve=how,
        source_document=source_document,
        missing=missing or [],
        validation_messages=messages or [],
    )


def question_import_issues(
    question: QuestionRecord,
    *,
    source_document: str = "Documento de origem não informado",
) -> list[ImportReadinessIssue]:
    """Return canonical app-import issues that depend only on the question record."""

    prefix = f"questao {question.number}"
    issues: list[ImportReadinessIssue] = []
    missing_classification = [
        label
        for label, field_name in (
            ("disciplina", "discipline"),
            ("matéria", "matter"),
            ("assunto", "subject"),
        )
        if not isinstance(getattr(question, field_name), str)
        or len(getattr(question, field_name).strip()) < 2
    ]
    if missing_classification:
        labels = ", ".join(item.capitalize() for item in missing_classification)
        issues.append(
            _issue(
                "missing_classification",
                what=f"{labels} ainda não preenchida(s).",
                why="O app exige uma classificação válida dentro da taxonomia editorial.",
                how="Revise a sugestão de classificação e confirme um caminho da taxonomia.",
                source_document=source_document,
                missing=missing_classification,
                messages=[
                    f"{prefix}: {_normalized(label)} obrigatorio para importacao no app"
                    for label in missing_classification
                ],
            )
        )

    metadata_fields = (
        ("banca", "board"),
        ("órgão", "organization"),
        ("concurso", "concurso"),
        ("cargo", "role"),
        ("nível", "level"),
    )
    missing_metadata = [
        label
        for label, field_name in metadata_fields
        if not isinstance(getattr(question, field_name), str)
        or len(getattr(question, field_name).strip()) < 2
    ]
    if missing_metadata:
        issues.append(
            _issue(
                "missing_metadata",
                what="Metadados obrigatórios da prova estão ausentes.",
                why="Sem identidade objetiva, a questão não pode ser relacionada com segurança.",
                how="Confira a capa, o cabeçalho ou a página oficial e complete os campos.",
                source_document=source_document,
                missing=missing_metadata,
                messages=[
                    f"{prefix}: {_normalized(label)} obrigatorio "
                    "para importacao no app"
                    for label in missing_metadata
                ],
            )
        )
    if len(question.statement.strip()) < 10:
        issues.append(
            _issue(
                "invalid_statement",
                what="Enunciado incompleto.",
                why="O texto não tem conteúdo suficiente para uma questão utilizável.",
                how="Abra o PDF e corrija a extração do enunciado.",
                source_document=source_document,
                messages=[f"{prefix}: enunciado deve ter pelo menos 10 caracteres"],
            )
        )
    if question.year is None:
        issues.append(
            _issue(
                "missing_year",
                what="Ano da prova ausente.",
                why="O ano faz parte da identidade obrigatória da questão.",
                how="Confirme o ano na capa, no cabeçalho ou na página oficial.",
                source_document=source_document,
                missing=["ano"],
                messages=[f"{prefix}: ano obrigatorio para importacao no app"],
            )
        )
    if not question.source_pages:
        issues.append(
            _issue(
                "missing_source_page",
                what="Página de origem ausente.",
                why="A questão precisa apontar para sua evidência no PDF.",
                how="Informe a página em que a questão aparece.",
                source_document=source_document,
                missing=["página de origem"],
                messages=[f"{prefix}: pagina de origem obrigatoria para importacao no app"],
            )
        )
    if question.answer_status != "matched" or question.correct_answer is None:
        annulled = question.answer_status == "annulled"
        issues.append(
            _issue(
                "annulled_answer" if annulled else "missing_official_answer",
                what=(
                    "Questão anulada no gabarito oficial."
                    if annulled
                    else "Resposta oficial ausente."
                ),
                why=(
                    "Questões anuladas não possuem alternativa correta importável."
                    if annulled
                    else "O app não pode receber uma resposta presumida ou sem gabarito comprovado."
                ),
                how=(
                    "Mantenha a questão fora da importação ou trate a anulação no produto."
                    if annulled
                    else "Relacione o gabarito oficial correto e reprocesse a associação."
                ),
                source_document=source_document,
                messages=[
                    f"{prefix}: questao {'anulada' if annulled else 'sem gabarito'} "
                    "nao pode ser transferida"
                ],
            )
        )

    alternatives = question.alternatives
    letters = [item.letter for item in alternatives]
    expected_letters = list("ABCDE"[: len(alternatives)])
    alternative_messages: list[str] = []
    if not 2 <= len(alternatives) <= 5:
        alternative_messages.append(f"{prefix}: exportacao exige de 2 a 5 alternativas")
    elif letters != expected_letters:
        alternative_messages.append(
            f"{prefix}: alternativas devem ser sequenciais de A ate {expected_letters[-1]}"
        )
    if question.correct_answer is not None and question.correct_answer not in letters:
        alternative_messages.append(
            f"{prefix}: gabarito nao corresponde as alternativas exportaveis"
        )
    if alternative_messages:
        issues.append(
            _issue(
                "invalid_alternatives",
                what="Alternativas ou resposta incompatíveis.",
                why="O formato não permite aplicar a resposta oficial com segurança.",
                how="Corrija a extração das alternativas e confira a letra do gabarito.",
                source_document=source_document,
                messages=alternative_messages,
            )
        )

    visual_text = " ".join(
        [question.statement, *(item.text for item in alternatives), *question.review_notes]
    )
    if any(
        marker in _normalized(visual_text)
        for marker in (
            "alternativa visual",
            "imagem necessaria",
            "figura necessaria",
            "requer imagem",
            "requer figura",
        )
    ):
        issues.append(
            _issue(
                "visual_content",
                what="Conteúdo visual requer revisão.",
                why="A imagem ou figura ainda não está representada no conteúdo importável.",
                how="Revise a questão visual e prepare seus recursos antes da importação.",
                source_document=source_document,
                messages=[f"{prefix}: conteudo visual exige tratamento editorial separado"],
            )
        )
    return issues


def diagnose_import_readiness(
    question: QuestionRecord,
    *,
    source_document: str,
    provider: str | None,
    source_url: str | None,
    document_sha256: str | None,
    flags: list[str],
    document_warnings: list[str],
    semantic_resolution: str | None,
) -> ImportReadinessDiagnosis:
    issues = question_import_issues(question, source_document=source_document)
    origin_missing: list[str] = []
    if not (provider or "").strip():
        origin_missing.append("provider")
    if not (source_url or "").startswith("https://"):
        origin_missing.append("URL HTTPS")
    if len(document_sha256 or "") != 64:
        origin_missing.append("SHA-256")
    if origin_missing:
        issues.append(
            _issue(
                "unproved_origin",
                what="Origem oficial não comprovada.",
                why="A importação exige endereço oficial e integridade verificável do PDF.",
                how="Complete provider e URL HTTPS e confirme o SHA-256 do documento.",
                source_document=source_document,
                missing=origin_missing,
            )
        )
    if "duplicate" in flags:
        issues.append(
            _issue(
                "unresolved_duplicate",
                what="Duplicata ainda não resolvida.",
                why="Importar as duas cópias repetiria conteúdo no app.",
                how="Escolha a versão canônica ou relacione a republicação.",
                source_document=source_document,
            )
        )

    warnings = _normalized(" ".join(document_warnings))
    if "associacao" in warnings and "ambigu" in warnings:
        issues.append(
            _issue(
                "ambiguous_association",
                what="Associação com o gabarito é ambígua.",
                why="Mais de um gabarito pode atender à prova sem evidência para desempate.",
                how="Confirme concurso, cargo, turno e tipo antes de escolher o gabarito.",
                source_document=source_document,
            )
        )
    if semantic_resolution == "uncertain" or (
        "versao" in warnings and "conflito" in warnings
    ):
        issues.append(
            _issue(
                "version_conflict",
                what="Versão do documento não resolvida.",
                why="Há conflito ou evidência insuficiente para definir a versão canônica.",
                how="Compare as versões e confirme qual documento substitui ou sucede o anterior.",
                source_document=source_document,
            )
        )

    unique: dict[ImportBlockCode, ImportReadinessIssue] = {}
    for issue in issues:
        unique.setdefault(issue.code, issue)
    normalized_issues = list(unique.values())
    return ImportReadinessDiagnosis(importable=not normalized_issues, issues=normalized_issues)
