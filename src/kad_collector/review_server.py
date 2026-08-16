from __future__ import annotations

import html
import json
import re
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from pydantic import ValidationError

from .local_review import (
    decide_review_question,
    export_review_session,
    load_or_create_review_session,
    review_summary,
    save_review_session,
    update_review_question,
)
from .models import QuestionRecord, ReviewDecisionStatus

MAX_REQUEST_BYTES = 2_000_000


class ReviewApplication:
    def __init__(
        self,
        batch_path: Path,
        *,
        session_path: Path | None = None,
        output_path: Path | None = None,
    ) -> None:
        self.session, self.session_path = load_or_create_review_session(
            batch_path, session_path
        )
        self.output_path = output_path
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()

    def payload(self) -> dict[str, Any]:
        with self._lock:
            source_path = self.source_path()
            return {
                "session": self.session.model_dump(mode="json"),
                "summary": review_summary(self.session),
                "source_available": source_path.is_file(),
                "session_path": str(self.session_path),
                "output_path": str(
                    self.output_path
                    or Path("data/approved") / f"{self.session.batch.batch_id}.json"
                ),
            }

    def source_path(self) -> Path:
        return Path(self.session.batch.source_document.local_path).resolve()

    def update_question(self, question_number: int, question: QuestionRecord) -> None:
        with self._lock:
            self.session = update_review_question(
                self.session, question_number, question
            )
            save_review_session(self.session, self.session_path)

    def decide(
        self,
        question_number: int,
        status: ReviewDecisionStatus,
        reviewer: str,
        notes: str | None,
    ) -> None:
        with self._lock:
            self.session = decide_review_question(
                self.session,
                question_number,
                status,
                reviewer,
                notes=notes,
            )
            save_review_session(self.session, self.session_path)

    def export(self, reviewer: str, notes: str | None) -> tuple[int, Path]:
        with self._lock:
            batch, path = export_review_session(
                self.session,
                reviewer,
                notes=notes,
                output_path=self.output_path,
            )
            return len(batch.questions), path


def create_review_server(
    application: ReviewApplication,
    *,
    port: int = 8765,
) -> ThreadingHTTPServer:
    handler = _handler_for(application)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def serve_review_application(
    batch_path: Path,
    *,
    session_path: Path | None = None,
    output_path: Path | None = None,
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    if not 0 <= port <= 65_535:
        raise ValueError("a porta deve estar entre 0 e 65535")
    application = ReviewApplication(
        batch_path,
        session_path=session_path,
        output_path=output_path,
    )
    server = create_review_server(application, port=port)
    actual_port = cast(tuple[str, int], server.server_address)[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Revisao local: {url}")
    print(f"Sessao: {application.session_path}")
    print("Pressione Ctrl+C para encerrar.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRevisao local encerrada.")
    finally:
        server.server_close()


def _handler_for(application: ReviewApplication) -> type[BaseHTTPRequestHandler]:
    class ReviewRequestHandler(BaseHTTPRequestHandler):
        server_version = "KADReview/0.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                document = _resource_text("review_ui.html").replace(
                    "__REVIEW_TOKEN__", html.escape(application.token, quote=True)
                )
                self._send_bytes(document.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._send_bytes(
                    _resource_bytes("review_app.js"), "text/javascript; charset=utf-8"
                )
                return
            if path == "/styles.css":
                self._send_bytes(
                    _resource_bytes("review_styles.css"), "text/css; charset=utf-8"
                )
                return
            if path == "/api/session":
                self._send_json(application.payload())
                return
            if path == "/source.pdf":
                source_path = application.source_path()
                if not source_path.is_file():
                    self._send_error(HTTPStatus.NOT_FOUND, "PDF de origem nao encontrado")
                    return
                self._send_bytes(source_path.read_bytes(), "application/pdf")
                return
            self._send_error(HTTPStatus.NOT_FOUND, "rota nao encontrada")

        def do_PUT(self) -> None:
            if not self._authorized():
                return
            match = re.fullmatch(r"/api/questions/(\d+)", urlparse(self.path).path)
            if match is None:
                self._send_error(HTTPStatus.NOT_FOUND, "rota nao encontrada")
                return
            try:
                question_number = int(match.group(1))
                question = QuestionRecord.model_validate(self._read_json())
                application.update_question(question_number, question)
                self._send_json(application.payload())
            except (OSError, ValueError, ValidationError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            decision_match = re.fullmatch(r"/api/questions/(\d+)/decision", path)
            try:
                payload = self._read_json()
                if decision_match is not None:
                    status_value = payload.get("status")
                    if status_value not in {"approved", "rejected"}:
                        raise ValueError("status deve ser approved ou rejected")
                    reviewer = _required_text(payload, "reviewer")
                    notes = _optional_text(payload, "notes")
                    application.decide(
                        int(decision_match.group(1)),
                        cast(ReviewDecisionStatus, status_value),
                        reviewer,
                        notes,
                    )
                    self._send_json(application.payload())
                    return
                if path == "/api/export":
                    reviewer = _required_text(payload, "reviewer")
                    notes = _optional_text(payload, "notes")
                    question_count, output_path = application.export(reviewer, notes)
                    self._send_json(
                        {
                            "question_count": question_count,
                            "output_path": str(output_path),
                        }
                    )
                    return
                self._send_error(HTTPStatus.NOT_FOUND, "rota nao encontrada")
            except (OSError, ValueError, ValidationError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def _authorized(self) -> bool:
            if secrets.compare_digest(
                self.headers.get("X-KAD-Review-Token", ""), application.token
            ):
                return True
            self._send_error(HTTPStatus.FORBIDDEN, "token local invalido")
            return False

        def _read_json(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length ausente")
            length = int(raw_length)
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("corpo da requisicao excede o limite local")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("o corpo JSON deve ser um objeto")
            return payload

        def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", status)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status)

        def _send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

    return ReviewRequestHandler


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"campo {key} e obrigatorio")
    return value


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"campo {key} deve ser texto")
    return value


def _resource_text(name: str) -> str:
    return resources.files("kad_collector").joinpath(name).read_text(encoding="utf-8")


def _resource_bytes(name: str) -> bytes:
    return resources.files("kad_collector").joinpath(name).read_bytes()
