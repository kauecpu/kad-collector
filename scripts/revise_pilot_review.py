"""Prepare a NEW pending review from a reprocessed package; never inherit approval.

Only whitespace and physical word-break repairs may reuse prior classification
and import identity. Any content/answer/page divergence stops for investigation.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from kad_collector.consolidated_review import _structured_content_sha256
from kad_collector.json_utils import read_json, write_json
from kad_collector.local_review import (
    create_review_session,
    question_content_sha256,
    save_review_session,
    verify_review_session,
)
from kad_collector.models import DownloadManifest, LocalReviewSession, ReviewState
from kad_collector.operator_review import _question_record
from kad_collector.pdf_text import _LINE_HYPHEN
from kad_collector.structured_questions import StructuredQuestionPackage
from kad_collector.validation import validate_questions


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _comparable(text: str) -> str:
    # This compares identities; it does NOT generate corrected content.
    return " ".join(_LINE_HYPHEN.sub(lambda m: m["left"] + m["right"], text).split())


def revise_pilot_review(
    source_session: Path, package_path: Path, manifest_path: Path, output: Path,
) -> LocalReviewSession:
    output = output.resolve()
    if output.exists():
        raise FileExistsError("destino já existe; preserve revisões anteriores")
    original = LocalReviewSession.model_validate(read_json(source_session))
    verify_review_session(original)
    package = StructuredQuestionPackage.model_validate(read_json(package_path))
    if _structured_content_sha256(package) != package.content_sha256:
        raise ValueError("hash do pacote estruturado divergente")
    manifest = DownloadManifest.model_validate(read_json(manifest_path))
    if _digest(manifest_path) not in package.input_manifest_sha256s:
        raise ValueError("manifesto não pertence ao pacote")
    proof_hash = original.batch.source_document.sha256
    numbers = {item.number for item in original.batch.questions}
    if len(numbers) != len(original.batch.questions):
        raise ValueError("sessão original possui numeração duplicada")
    rows = [item for item in package.accepted
            if item.exam_sha256 == proof_hash and item.original_number in numbers]
    if len(rows) != len(numbers) or {item.original_number for item in rows} != numbers:
        raise ValueError("seleção não foi integralmente aceita na nova extração")
    key_hashes = {item.answer_key_sha256 for item in rows}
    if len(key_hashes) != 1:
        raise ValueError("a seleção exige um único gabarito associado")
    key_hash = next(iter(key_hashes))
    documents = {doc.sha256: doc for doc in manifest.documents}
    for digest in (proof_hash, key_hash):
        document = documents.get(digest)
        if document is None or _digest(Path(document.local_path)) != digest:
            raise ValueError("PDF ausente ou alterado")
    revised = []
    comparisons = []
    for old in original.batch.questions:
        row = next(item for item in rows if item.original_number == old.number)
        fresh = _question_record(row, state="accepted")
        if (
            _comparable(old.statement) != _comparable(fresh.statement)
            or {a.letter: _comparable(a.text) for a in old.alternatives}
            != {a.letter: _comparable(a.text) for a in fresh.alternatives}
            or old.correct_answer != fresh.correct_answer
            or old.source_pages != fresh.source_pages
        ):
            raise ValueError(f"Q{old.number}: conteúdo, página ou resposta mudou além das quebras")
        # Copy only classification evidence, not obsolete key/extraction/approval notes.
        classification = [note for note in old.review_notes if note.startswith(
            ("Classificação automática:", "Evidências da classificação:")
        )]
        question = old.model_copy(deep=True, update={
            "statement": fresh.statement,
            "alternatives": fresh.alternatives,
            "correct_answer": fresh.correct_answer,
            "answer_status": fresh.answer_status,
            "review_notes": [*fresh.review_notes, *classification,
                             "Nova revisão: aprovação anterior não transferida.",
                             f"Identidade de importação preservada: {old.source_stable_id}."],
        })
        revised.append(question)
        comparisons.append({
            "number": old.number, "pages": row.source_pages,
            "previous_content_sha256": question_content_sha256(old),
            "proposed_content_sha256": question_content_sha256(question),
            "text_changed": old.statement != question.statement
            or old.alternatives != question.alternatives,
            "answer": question.correct_answer,
            "structured_id": row.stable_id,
            "import_id": question.source_stable_id,
            "decision": "pending",
        })
    batch = original.batch.model_copy(deep=True, update={
        "batch_id": f"{original.batch.batch_id}-revision-{package.content_sha256[:12]}",
        "source_document": documents[proof_hash],
        "answer_key_document": documents[key_hash],
        "model": f"structured:{package.parser_version}",
        "questions": revised,
        "review": ReviewState(),
        "validation": validate_questions(revised),
        "processing_warnings": ["Revisão nova e pendente; não exportar sem decisão humana."],
    })
    session = create_review_session(batch)
    output.mkdir(parents=True)
    write_json(output / "batch.json", batch.model_dump(mode="json"))
    save_review_session(session, output / "session.json")
    write_json(output / "revision.json", {
        "source_session": str(source_session.resolve()),
        "source_session_sha256": _digest(source_session),
        "structured_package": str(package_path.resolve()),
        "structured_package_sha256": package.content_sha256,
        "parser_version": package.parser_version,
        "exam_sha256": proof_hash, "answer_key_sha256": key_hash,
        "comparisons": comparisons,
        "approved": 0, "exported": 0,
    })
    return session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-session", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    session = revise_pilot_review(args.source_session, args.package, args.manifest, args.output)
    print(f"Nova revisão: {len(session.batch.questions)} pendentes; 0 aprovações herdadas.")


if __name__ == "__main__":
    main()
