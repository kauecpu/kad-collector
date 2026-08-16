from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from kad_collector.json_utils import read_json, write_json
from kad_collector.local_review import (
    create_review_session,
    decide_review_question,
    export_review_session,
    load_or_create_review_session,
    save_review_session,
    update_review_question,
)
from kad_collector.models import (
    Alternative,
    DocumentRecord,
    LocalReviewSession,
    QuestionBatch,
    QuestionRecord,
    ValidationState,
)
from kad_collector.review_server import ReviewApplication, create_review_server
from kad_collector.validation import verify_approved_batch


def question(number: int, *, with_answer: bool = True) -> QuestionRecord:
    return QuestionRecord(
        number=number,
        statement=f"Enunciado da questao {number}.",
        alternatives=[
            Alternative(letter="A", text="Alternativa incorreta"),
            Alternative(letter="B", text="Alternativa correta"),
        ],
        matter="Direito",
        subject="Atos administrativos",
        board="FGV",
        organization="Orgao Teste",
        role="Analista",
        year=2026,
        source_pages=[number],
        correct_answer="B" if with_answer else None,
        answer_status="matched" if with_answer else "missing",
    )


def pending_batch() -> QuestionBatch:
    return QuestionBatch(
        batch_id=str(uuid.uuid4()),
        created_at=datetime.now(UTC),
        model="fake-model",
        source_document=DocumentRecord(
            source_id="fonte_teste",
            source_name="Fonte Teste",
            document_type="exam",
            title="Prova para revisao",
            original_url="https://example.test/prova.pdf",
            resolved_url="https://example.test/prova.pdf",
            local_path="data/raw/inexistente.pdf",
            sha256="a" * 64,
            content_type="application/pdf",
            size_bytes=100,
            downloaded_at=datetime.now(UTC),
            authorization_basis="Publicacao oficial conferida.",
        ),
        questions=[question(1), question(2)],
        validation=ValidationState(valid=True),
    )


class LocalReviewTests(unittest.TestCase):
    def test_review_decisions_persist_and_export_only_approved_questions(self) -> None:
        batch = pending_batch()
        session = create_review_session(batch)
        edited = session.batch.questions[0].model_copy(deep=True)
        edited.statement = "Enunciado conferido no PDF."
        session = update_review_question(session, 1, edited)
        session = decide_review_question(session, 1, "approved", "revisor.teste")
        session = decide_review_question(
            session,
            2,
            "rejected",
            "revisor.teste",
            notes="Questao incompleta na fonte.",
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_path = root / "review.json"
            output_path = root / "approved.json"
            save_review_session(session, session_path)
            reloaded = LocalReviewSession.model_validate(read_json(session_path))
            approved, written_path = export_review_session(
                reloaded,
                "editor.chefe",
                notes="Lote revisado localmente.",
                output_path=output_path,
            )

        self.assertEqual(written_path, output_path)
        self.assertEqual([item.number for item in approved.questions], [1])
        self.assertEqual(approved.filtered_out_questions, 0)
        self.assertEqual(approved.review.reviewed_by, "editor.chefe")
        self.assertIn("rejeitou 1 questoes", approved.processing_warnings[-1])
        verify_approved_batch(approved)

    def test_editing_a_decided_question_returns_it_to_pending(self) -> None:
        session = create_review_session(pending_batch())
        session = decide_review_question(session, 1, "approved", "revisor.teste")
        edited = session.batch.questions[0].model_copy(deep=True)
        edited.subject = "Novo assunto"
        session = update_review_question(session, 1, edited)

        decision = next(item for item in session.decisions if item.question_number == 1)
        self.assertEqual(decision.status, "pending")
        self.assertIsNone(decision.content_sha256)

    def test_approval_requires_answer_and_rejection_requires_reason(self) -> None:
        batch = pending_batch()
        batch.questions[0] = question(1, with_answer=False)
        session = create_review_session(batch)

        with self.assertRaisesRegex(ValueError, "gabarito ausente"):
            decide_review_question(session, 1, "approved", "revisor.teste")
        with self.assertRaisesRegex(ValueError, "justificativa"):
            decide_review_question(session, 1, "rejected", "revisor.teste")

    def test_export_stops_while_questions_are_pending(self) -> None:
        session = create_review_session(pending_batch())
        session = decide_review_question(session, 1, "approved", "revisor.teste")
        with self.assertRaisesRegex(ValueError, "1 questoes pendentes"):
            export_review_session(session, "editor.chefe")

    def test_existing_session_detects_changed_source_batch(self) -> None:
        batch = pending_batch()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_path = root / "batch.json"
            session_path = root / "session.json"
            write_json(batch_path, batch.model_dump(mode="json"))
            load_or_create_review_session(batch_path, session_path)
            batch.questions[0].statement = "Fonte alterada depois da abertura."
            write_json(batch_path, batch.model_dump(mode="json"))

            with self.assertRaisesRegex(ValueError, "lote de origem mudou"):
                load_or_create_review_session(batch_path, session_path)


class ReviewServerTests(unittest.TestCase):
    def test_export_creates_promotion_package_after_editorial_approval(self) -> None:
        batch = pending_batch()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_path = root / "batch.json"
            approved_path = root / "approved.json"
            package_path = root / "promotion.json"
            write_json(batch_path, batch.model_dump(mode="json"))
            application = ReviewApplication(batch_path, output_path=approved_path)
            application.decide(1, "approved", "revisor.teste", None)
            application.decide(2, "approved", "revisor.teste", None)

            with patch(
                "kad_collector.review_server.build_promotion_package",
                return_value=(None, package_path),
            ) as build_package:
                count, written_path, written_package = application.export(
                    "editor.chefe", None
                )

        self.assertEqual(count, 2)
        self.assertEqual(written_path, approved_path)
        self.assertEqual(written_package, package_path)
        build_package.assert_called_once_with([approved_path])

    def test_server_exposes_ui_and_protects_mutations_with_local_token(self) -> None:
        batch = pending_batch()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch_path = root / "batch.json"
            session_path = root / "session.json"
            write_json(batch_path, batch.model_dump(mode="json"))
            application = ReviewApplication(batch_path, session_path=session_path)
            server = create_review_server(application, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/", timeout=2) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("Revisão editorial local", html)
                with urllib.request.urlopen(
                    f"http://{host}:{port}/api/session", timeout=2
                ) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["summary"]["pending"], 2)

                body = json.dumps(batch.questions[0].model_dump(mode="json")).encode("utf-8")
                request = urllib.request.Request(
                    f"http://{host}:{port}/api/questions/1",
                    data=body,
                    method="PUT",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(raised.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
