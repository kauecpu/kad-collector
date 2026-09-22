"""Isolate a numbered set from an existing official review session for one audit.

This prepares a local review only. It never records an audit decision or exports.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from kad_collector.consolidated_review import ConsolidatedReviewIndex
from kad_collector.editorial_approval import (
    ApprovalCampaignState,
    build_approval_campaign,
)
from kad_collector.json_utils import read_json, write_json
from kad_collector.local_review import (
    create_review_session,
    question_content_sha256,
    save_review_session,
)
from kad_collector.models import LocalReviewSession
from kad_collector.validation import validate_questions


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_pilot_review(
    *,
    source_index: Path,
    source_state: Path,
    exam_sha256: str,
    numbers: list[int],
    output: Path,
) -> ApprovalCampaignState:
    if len(numbers) != len(set(numbers)) or not numbers:
        raise ValueError("informe números únicos de questões")
    index = ConsolidatedReviewIndex.model_validate(read_json(source_index))
    state = ApprovalCampaignState.model_validate(read_json(source_state))
    source = next((item for item in index.batches if item.exam_sha256 == exam_sha256), None)
    if source is None or source.session_path is None:
        raise ValueError("caderno não encontrado no índice de revisão")
    session = LocalReviewSession.model_validate(read_json(Path(source.session_path)))
    if session.batch.source_document.sha256 != exam_sha256:
        raise ValueError("hash da prova diverge do índice")
    for document in (session.batch.source_document, session.batch.answer_key_document):
        if document is None or _sha256(Path(document.local_path)) != document.sha256:
            raise ValueError("PDF oficial ausente ou com hash divergente")
        if not document.resolved_url.startswith("https://"):
            raise ValueError("URL oficial HTTPS ausente")
    audited = {
        item.question_number: item
        for item in state.questions
        if item.exam_sha256 == exam_sha256 and item.state == "audit_sample"
    }
    selected = []
    for number in numbers:
        if number not in audited:
            raise ValueError(f"questão {number} não está na amostra auditável")
        question = next((q for q in session.batch.questions if q.number == number), None)
        if question is None:
            raise ValueError(f"questão {number} ausente da sessão")
        if audited[number].content_sha256 != question_content_sha256(question):
            raise ValueError(f"questão {number} mudou desde a avaliação")
        selected.append(question)

    output = output.resolve()
    if output.exists():
        raise FileExistsError("lote local já existe; não sobrescrever revisão anterior")
    output.mkdir(parents=True)
    batch = session.batch.model_copy(deep=True)
    batch.batch_id = f"{batch.batch_id}-pilot-{len(selected)}"
    batch.questions = selected
    batch.validation = validate_questions(selected)
    pilot_session = create_review_session(batch)
    session_path = output / "session.json"
    save_review_session(pilot_session, session_path)
    pilot_index = index.model_copy(deep=True)
    pilot_index.corpus_id = f"{index.corpus_id}-pilot-{len(selected)}"
    pilot_index.batches = [
        source.model_copy(
            update={
                "batch_id": batch.batch_id,
                "batch_path": None,
                "session_path": str(session_path),
                "accepted": len(selected),
                "quarantined": 0,
                "rejected": 0,
            }
        )
    ]
    index_path = output / "index.json"
    write_json(index_path, pilot_index.model_dump(mode="json"))
    return build_approval_campaign(index_path, output / "state.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--source-state", type=Path, required=True)
    parser.add_argument("--exam-sha256", required=True)
    parser.add_argument("--numbers", required=True, help="números separados por vírgulas")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state = prepare_pilot_review(
        source_index=args.source_index,
        source_state=args.source_state,
        exam_sha256=args.exam_sha256,
        numbers=[int(value) for value in args.numbers.split(",")],
        output=args.output,
    )
    print(
        f"Revisão local: {len(state.questions)} questões; "
        f"{state.summary.audit_sample} aguardam decisão; "
        f"{state.summary.approved_for_staging} aprovadas para staging"
    )


if __name__ == "__main__":
    main()
