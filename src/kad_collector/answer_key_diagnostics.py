from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AnswerKeyDiagnosticCode = Literal[
    "answer_key_not_collected",
    "answer_key_unlinked",
    "question_missing_in_answer_key",
    "ambiguous_answer_key_association",
    "answer_key_diagnosis_pending",
]


@dataclass(frozen=True)
class AnswerKeyEvidence:
    answer_status: str
    answer_key_link_id: str | None
    valid_answer_association: bool
    exam_version_id: str | None
    compatible_candidate_count: int
    review_status: str | None = None
    review_reason: str | None = None
    linked_answer_key_document: str | None = None


_PRESENTATION: dict[AnswerKeyDiagnosticCode, dict[str, str]] = {
    "answer_key_not_collected": {
        "label": "Gabarito oficial não encontrado",
        "explanation": (
            "O Collector possui a questão, mas ainda não encontrou o documento oficial "
            "com as respostas desta prova."
        ),
        "action": "Coletar ou adicionar o gabarito oficial",
    },
    "answer_key_unlinked": {
        "label": "Gabarito aguardando associação",
        "explanation": (
            "A prova e o gabarito foram coletados, mas ainda não foram ligados com segurança."
        ),
        "action": "Revisar associação da prova",
    },
    "question_missing_in_answer_key": {
        "label": "Questão não localizada no gabarito",
        "explanation": (
            "O gabarito foi ligado à prova, mas não contém uma resposta reconhecida "
            "para esta questão."
        ),
        "action": "Conferir número, página e versão do caderno",
    },
    "ambiguous_answer_key_association": {
        "label": "Associação do gabarito em dúvida",
        "explanation": (
            "O Collector encontrou mais de uma combinação possível entre prova e gabarito."
        ),
        "action": "Confirmar caderno, cargo, turno e tipo",
    },
    "answer_key_diagnosis_pending": {
        "label": "Motivo ainda não identificado",
        "explanation": (
            "O Collector sabe que falta a resposta oficial, mas ainda não possui evidência "
            "suficiente para explicar a causa."
        ),
        "action": "Abrir detalhes para diagnóstico",
    },
}


def diagnose_answer_key(evidence: AnswerKeyEvidence) -> dict[str, object]:
    """Classify answer-key state without inference or database mutation."""

    if evidence.answer_status == "matched" and evidence.valid_answer_association:
        return {
            "state": "official",
            "label": "Com resposta oficial",
            "explanation": "A resposta foi vinculada a um gabarito oficial.",
            "action": "Nenhuma ação necessária.",
            "diagnosticCode": None,
        }
    if evidence.answer_status == "annulled" and evidence.valid_answer_association:
        return {
            "state": "annulled",
            "label": "Anulada",
            "explanation": "O gabarito oficial marcou esta questão como anulada.",
            "action": "Nenhuma ação necessária.",
            "diagnosticCode": None,
        }

    code: AnswerKeyDiagnosticCode
    if evidence.answer_key_link_id and evidence.valid_answer_association:
        code = "question_missing_in_answer_key"
    elif evidence.exam_version_id is None:
        code = "answer_key_diagnosis_pending"
    elif evidence.compatible_candidate_count == 0:
        code = "answer_key_not_collected"
    elif evidence.compatible_candidate_count == 1:
        code = "answer_key_unlinked"
    elif evidence.compatible_candidate_count > 1:
        code = "ambiguous_answer_key_association"
    else:
        code = "answer_key_diagnosis_pending"

    return {
        "state": "missing",
        "diagnosticCode": code,
        **_PRESENTATION[code],
    }
