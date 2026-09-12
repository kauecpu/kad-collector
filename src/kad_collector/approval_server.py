from __future__ import annotations

import html
import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from .editorial_approval import (
    ApprovalCampaignState,
    approve_approval_group,
    block_approval_group,
    correct_approval_question,
    decide_audit_item,
    export_staging_package,
    reprocess_group,
)
from .json_utils import read_json
from .models import LocalReviewSession, QuestionRecord

MAX_REQUEST_BYTES = 2_000_000


class ApprovalApplication:
    def __init__(self, state_path: Path, *, staging_output: Path) -> None:
        self.state_path = state_path.resolve()
        self.staging_output = staging_output.resolve()
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        self.state = ApprovalCampaignState.model_validate(read_json(self.state_path))

    def payload(self) -> dict[str, Any]:
        with self._lock:
            self._load()
            exception_counts: dict[str, int] = {}
            for item in self.state.questions:
                if item.state not in {"needs_review", "quarantined"}:
                    continue
                for blocker in item.blockers or [item.state]:
                    exception_counts[blocker] = exception_counts.get(blocker, 0) + 1
            return {
                "campaignId": self.state.campaign_id,
                "publicationStatus": self.state.publication_status,
                "summary": self.state.summary.model_dump(mode="json"),
                "exceptions": dict(sorted(exception_counts.items())),
                "groups": [item.model_dump(mode="json") for item in self.state.groups],
                "sample": [
                    self._question_summary(item)
                    for item in self.state.questions
                    if item.state == "audit_sample" or item.audit_decision is not None
                ],
                "reviewQueue": [
                    self._question_summary(item)
                    for item in self.state.questions
                    if item.state in {"needs_review", "quarantined"}
                ][:500],
                "stagingOutput": str(self.staging_output),
            }

    @staticmethod
    def _question_summary(item: Any) -> dict[str, Any]:
        return {
            "stableId": item.stable_id,
            "number": item.question_number,
            "state": item.state,
            "groupId": item.group_id,
            "board": item.board,
            "organization": item.organization,
            "contest": item.contest,
            "year": item.year,
            "role": item.role,
            "blockers": item.blockers,
            "classificationMethod": item.classification_method,
            "discipline": item.discipline,
            "matter": item.matter,
            "subject": item.subject,
            "level": item.level,
            "difficulty": item.difficulty,
            "decision": (
                item.audit_decision.model_dump(mode="json")
                if item.audit_decision is not None
                else None
            ),
        }

    def question_payload(self, stable_id: str) -> dict[str, Any]:
        with self._lock:
            self._load()
            item = next(
                (value for value in self.state.questions if value.stable_id == stable_id), None
            )
            if item is None:
                raise ValueError("questão não encontrada")
            session = LocalReviewSession.model_validate(read_json(Path(item.session_path)))
            question = next(
                value for value in session.batch.questions if value.number == item.question_number
            )
            return {
                "approval": item.model_dump(mode="json"),
                "question": question.model_dump(mode="json"),
                "examUrl": item.exam_url,
                "answerKeyUrl": item.answer_key_url,
            }

    def decide(self, stable_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            decide_audit_item(
                self.state_path,
                stable_id,
                reviewer=_required_text(payload, "reviewer"),
                status=_required_choice(payload, "status", {"approved", "rejected", "deferred"}),
                structural_correct=_optional_bool(payload, "structuralCorrect"),
                answer_correct=_optional_bool(payload, "answerCorrect"),
                taxonomy_correct=_optional_bool(payload, "taxonomyCorrect"),
                critical_errors=_string_list(payload, "criticalErrors"),
                notes=_optional_text(payload, "notes"),
            )

    def correct(self, stable_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            question = QuestionRecord.model_validate(payload.get("question"))
            correct_approval_question(self.state_path, stable_id, question)

    def approve_group(self, group_id: str) -> None:
        with self._lock:
            approve_approval_group(self.state_path, group_id)

    def block_group(self, group_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            block_approval_group(
                self.state_path,
                group_id,
                reviewer=_required_text(payload, "reviewer"),
                reason=_required_text(payload, "reason"),
            )

    def reprocess(self, group_id: str) -> None:
        with self._lock:
            reprocess_group(self.state_path, group_id)

    def export(self) -> dict[str, Any]:
        with self._lock:
            return export_staging_package(self.state_path, self.staging_output)


def create_approval_server(
    application: ApprovalApplication, *, port: int = 8766
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(application))
    server.daemon_threads = True
    return server


def serve_approval_application(
    state_path: Path,
    *,
    staging_output: Path,
    port: int = 8766,
    open_browser: bool = False,
) -> None:
    if not 0 <= port <= 65_535:
        raise ValueError("a porta deve estar entre 0 e 65535")
    application = ApprovalApplication(state_path, staging_output=staging_output)
    server = create_approval_server(application, port=port)
    actual_port = cast(tuple[str, int], server.server_address)[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Auditoria editorial: {url}")
    print("A aprovação libera somente um pacote local draft para staging.")
    if open_browser:
        try:
            webbrowser.open(url)
        except (OSError, webbrowser.Error) as exc:
            print(f"AVISO: não foi possível abrir o navegador: {exc}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAuditoria local encerrada.")
    finally:
        server.server_close()


def _handler_for(application: ApprovalApplication) -> type[BaseHTTPRequestHandler]:
    class ApprovalRequestHandler(BaseHTTPRequestHandler):
        server_version = "KADApproval/1.0"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                document = _resource_text("approval_ui.html").replace(
                    "__APPROVAL_TOKEN__", html.escape(application.token, quote=True)
                )
                self._send_bytes(document.encode(), "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._send_bytes(
                    _resource_bytes("approval_app.js"), "text/javascript; charset=utf-8"
                )
                return
            if path == "/styles.css":
                self._send_bytes(_resource_bytes("approval_styles.css"), "text/css; charset=utf-8")
                return
            if path == "/api/state":
                self._send_json(application.payload())
                return
            prefix = "/api/questions/"
            if path.startswith(prefix):
                try:
                    self._send_json(application.question_payload(unquote(path[len(prefix) :])))
                except (OSError, ValueError, ValidationError) as exc:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "rota não encontrada")

        def do_PUT(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            prefix = "/api/questions/"
            if not path.startswith(prefix):
                self._send_error(HTTPStatus.NOT_FOUND, "rota não encontrada")
                return
            try:
                application.correct(unquote(path[len(prefix) :]), self._read_json())
                self._send_json(application.payload())
            except (OSError, ValueError, ValidationError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            try:
                if path.startswith("/api/questions/") and path.endswith("/decision"):
                    stable_id = unquote(
                        path.removeprefix("/api/questions/").removesuffix("/decision")
                    )
                    application.decide(stable_id, self._read_json())
                elif path.startswith("/api/groups/") and path.endswith("/approve"):
                    group_id = unquote(path.removeprefix("/api/groups/").removesuffix("/approve"))
                    application.approve_group(group_id)
                elif path.startswith("/api/groups/") and path.endswith("/block"):
                    group_id = unquote(path.removeprefix("/api/groups/").removesuffix("/block"))
                    application.block_group(group_id, self._read_json())
                elif path.startswith("/api/groups/") and path.endswith("/reprocess"):
                    group_id = unquote(path.removeprefix("/api/groups/").removesuffix("/reprocess"))
                    application.reprocess(group_id)
                elif path == "/api/export":
                    result = application.export()
                    self._send_json({"state": application.payload(), "manifest": result})
                    return
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, "rota não encontrada")
                    return
                self._send_json(application.payload())
            except (OSError, ValueError, ValidationError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def _authorized(self) -> bool:
            if self.headers.get("X-KAD-Approval-Token") == application.token:
                return True
            self._send_error(HTTPStatus.FORBIDDEN, "token local inválido")
            return False

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("corpo ausente ou grande demais")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("o corpo precisa ser um objeto JSON")
            return value

        def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send_bytes(
                json.dumps(value, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
                status,
            )

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status)

        def _send_bytes(
            self,
            payload: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return ApprovalRequestHandler


def _resource_bytes(name: str) -> bytes:
    return resources.files("kad_collector").joinpath(name).read_bytes()


def _resource_text(name: str) -> str:
    return resources.files("kad_collector").joinpath(name).read_text(encoding="utf-8")


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or len(value.strip()) < 2:
        raise ValueError(f"{key} é obrigatório")
    return value.strip()


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} deve ser texto")
    return value.strip() or None


def _optional_bool(payload: dict[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{key} deve ser booleano")
    return value


def _string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} deve ser uma lista de textos")
    return [item.strip() for item in value if item.strip()]


def _required_choice(payload: dict[str, Any], key: str, choices: set[str]) -> Any:
    value = _required_text(payload, key)
    if value not in choices:
        raise ValueError(f"{key} inválido")
    return value
