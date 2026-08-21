from __future__ import annotations

import html
import json
import os
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from pydantic import ValidationError

from .desktop_collection import DesktopCollectionManager
from .desktop_export import DesktopExportResult, export_filtered_questions
from .desktop_limits import (
    MAX_BATCH_PDFS,
    MAX_PDF_BYTES,
    PDF_STREAM_CHUNK_BYTES,
)
from .desktop_models import (
    ClassifierProviderName,
    DesktopFilterSet,
    DesktopImportMetadata,
    QuestionClassification,
)
from .desktop_processor import DesktopProcessor
from .desktop_store import DesktopStore
from .document_pipeline import DocumentPipeline
from .models import QuestionRecord

MAX_REQUEST_BYTES = 5_000_000


def default_desktop_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "KAD Collector"
    return Path.cwd() / "data" / "desktop"


class DesktopApplication:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = (data_dir or default_desktop_data_dir()).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = DesktopStore(self.data_dir / "collector.sqlite3")
        self.processor = DesktopProcessor(self.store)
        self.pipeline = DocumentPipeline(self.store, self.processor)
        self.collection_manager = DesktopCollectionManager(
            self.data_dir,
            self.store,
            self.processor,
            self.pipeline,
        )
        self.token = secrets.token_urlsafe(32)

    def bootstrap(self) -> dict[str, Any]:
        query = self.store.query(DesktopFilterSet())
        return {
            **query,
            "jobs": self.store.list_jobs(),
            "collectionJobs": self.collection_manager.list_jobs(),
            "sources": self.collection_manager.catalog(),
            "collectionEngine": self.collection_manager.engine_summary(),
            "savedFilters": self.store.saved_filters(),
            "semanticSummary": self.store.semantic_presentation_summary(),
            "config": {
                "dataDirectory": str(self.data_dir),
                "openaiConfigured": bool(os.environ.get("OPENAI_API_KEY")),
                "openaiModel": os.environ.get("OPENAI_MODEL", "gpt-5.6-terra"),
                "localOnly": True,
            },
        }

    @staticmethod
    def _expand_paths(paths: list[str]) -> list[Path]:
        selected: list[Path] = []
        seen: set[str] = set()
        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            candidates = sorted(path.rglob("*.pdf")) if path.is_dir() else [path]
            for candidate in candidates:
                key = str(candidate).casefold()
                if key not in seen:
                    seen.add(key)
                    selected.append(candidate)
                    if len(selected) > MAX_BATCH_PDFS:
                        raise ValueError(f"o lote excede o limite de {MAX_BATCH_PDFS} PDFs")
        return selected

    def import_pdfs(self, payload: dict[str, Any]) -> list[str]:
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list) or not all(isinstance(item, str) for item in raw_paths):
            raise ValueError("paths deve ser uma lista de caminhos locais")
        paths = self._expand_paths(raw_paths)
        metadata = DesktopImportMetadata.model_validate(payload.get("metadata", {}))
        classifier_provider = payload.get("classifierProvider", "local")
        if classifier_provider not in {"local", "openai"}:
            raise ValueError("classificador deve ser local ou openai")
        if classifier_provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY não está configurada nesta sessão")
        return self.pipeline.import_paths(paths, metadata, classifier_provider)

    def reprocess_documents(
        self,
        document_ids: list[str],
        classifier_provider: ClassifierProviderName = "local",
    ) -> list[str]:
        if not document_ids or not all(document_id.strip() for document_id in document_ids):
            raise ValueError("documentIds deve conter ao menos um identificador")
        if classifier_provider not in {"local", "openai"}:
            raise ValueError("classificador deve ser local ou openai")
        if classifier_provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY não está configurada nesta sessão")
        return self.pipeline.reprocess(document_ids, classifier_provider)

    def collect_from_link(self, payload: dict[str, Any]) -> str:
        return self.collection_manager.start(payload)

    def collection_action(self, collection_id: str, action: str) -> None:
        self.collection_manager.action(collection_id, action)

    def update_question(self, question_id: str, payload: dict[str, Any]) -> None:
        question = QuestionRecord.model_validate(payload.get("question"))
        classification_payload = payload.get("classification")
        classification = (
            QuestionClassification.model_validate(classification_payload)
            if classification_payload is not None
            else None
        )
        actor = _required_text(payload, "actor")
        notes = _optional_text(payload, "notes")
        self.store.update_question(
            question_id,
            question,
            classification,
            actor=actor,
            notes=notes,
        )

    def update_document(self, document_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        extra = set(payload) - {"metadata", "actor"}
        if extra:
            raise ValueError("campos extras não são aceitos na correção do documento")
        try:
            metadata = DesktopImportMetadata.model_validate(payload.get("metadata"))
        except ValidationError:
            raise ValueError("metadados do documento inválidos") from None
        actor = _required_text(payload, "actor")
        result = self.store.update_document_metadata(document_id, metadata, actor=actor)
        return result.model_dump(mode="json")

    def decide(self, question_id: str, payload: dict[str, Any]) -> None:
        status = payload.get("status")
        if status not in {"pending", "approved", "rejected", "exception"}:
            raise ValueError("decisão inválida")
        self.store.decide_question(
            question_id,
            status,
            actor=_required_text(payload, "actor"),
            notes=_optional_text(payload, "notes"),
        )

    def approve_batch(self, payload: dict[str, Any]) -> int:
        question_ids = payload.get("questionIds")
        if not isinstance(question_ids, list) or not all(
            isinstance(question_id, str) for question_id in question_ids
        ):
            raise ValueError("questionIds deve ser uma lista de identificadores")
        return self.store.approve_questions(
            question_ids,
            actor=_required_text(payload, "actor"),
            notes=_optional_text(payload, "notes"),
        )

    def export(self, payload: dict[str, Any]) -> DesktopExportResult:
        filters = DesktopFilterSet.model_validate(payload.get("filters", {}))
        output_path = payload.get("outputPath")
        output_root = (
            Path(output_path).resolve()
            if isinstance(output_path, str)
            else self.data_dir / "exports"
        )
        output_root.mkdir(parents=True, exist_ok=True)
        return export_filtered_questions(self.store, filters, output_root=output_root)


def create_desktop_server(
    application: DesktopApplication,
    *,
    port: int = 0,
) -> ThreadingHTTPServer:
    if not 0 <= port <= 65_535:
        raise ValueError("a porta deve estar entre 0 e 65535")
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(application))
    server.daemon_threads = True
    return server


def start_desktop_server(
    application: DesktopApplication,
    *,
    port: int = 0,
) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = create_desktop_server(application, port=port)
    actual_port = cast(tuple[str, int], server.server_address)[1]
    thread = threading.Thread(target=server.serve_forever, name="kad-desktop-http", daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{actual_port}/"


def _handler_for(application: DesktopApplication) -> type[BaseHTTPRequestHandler]:
    class DesktopRequestHandler(BaseHTTPRequestHandler):
        server_version = "KADCollectorDesktop/1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            if not self._trusted_request(require_origin=False):
                return
            path = urlparse(self.path).path
            if path == "/":
                html_document = _resource_text("desktop_ui.html").replace(
                    "__DESKTOP_TOKEN__", html.escape(application.token, quote=True)
                )
                self._send_bytes(html_document.encode("utf-8"), "text/html; charset=utf-8")
                return
            resources_by_path = {
                "/desktop.css": ("desktop_styles.css", "text/css; charset=utf-8"),
                "/desktop.js": ("desktop_app.js", "text/javascript; charset=utf-8"),
            }
            if path in resources_by_path:
                name, content_type = resources_by_path[path]
                self._send_bytes(_resource_bytes(name), content_type)
                return
            if path.startswith("/api/") and not self._authorized():
                return
            if path == "/api/bootstrap":
                self._send_json(application.bootstrap())
                return
            question_match = re.fullmatch(r"/api/questions/([a-f0-9-]+)", path)
            if question_match is not None:
                try:
                    question_id = question_match.group(1)
                    question = application.store.question(question_id)
                    question["documentIdentity"] = application.store.document_identity(
                        cast(str, question["document_id"])
                    )
                    self._send_json(
                        {
                            "question": question,
                            "audit": application.store.audit_log(question_id),
                        }
                    )
                except ValueError as exc:
                    self._send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            identity_match = re.fullmatch(r"/api/documents/([a-f0-9-]+)/identity", path)
            if identity_match is not None:
                try:
                    self._send_json(application.store.document_identity(identity_match.group(1)))
                except ValueError as exc:
                    self._send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            pdf_match = re.fullmatch(r"/api/documents/([a-f0-9-]+)/pdf", path)
            if pdf_match is not None:
                try:
                    document_payload = application.store.document(pdf_match.group(1))
                    source = Path(cast(str, document_payload["local_path"]))
                    if not source.is_file():
                        raise ValueError("PDF de origem não encontrado")
                    self._send_file(source, "application/pdf")
                except (OSError, ValueError) as exc:
                    self._send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "rota não encontrada")

        def do_POST(self) -> None:
            if not self._trusted_request(require_origin=True) or not self._authorized():
                return
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/query":
                    filters = DesktopFilterSet.model_validate(payload.get("filters", payload))
                    self._send_json(application.store.query(filters))
                    return
                if path == "/api/import":
                    job_ids = application.import_pdfs(payload)
                    self._send_json(
                        {"jobIds": job_ids, "exactDuplicate": not job_ids},
                        HTTPStatus.CREATED,
                    )
                    return
                if path == "/api/collections":
                    collection_id = application.collect_from_link(payload)
                    self._send_json({"collectionId": collection_id}, HTTPStatus.CREATED)
                    return
                collection_action = re.fullmatch(
                    r"/api/collections/([a-f0-9-]+)/(pause|resume|cancel)", path
                )
                if collection_action is not None:
                    collection_id, action = collection_action.groups()
                    application.collection_action(collection_id, action)
                    self._send_json({"ok": True})
                    return
                job_action = re.fullmatch(r"/api/jobs/([a-f0-9-]+)/(cancel|resume)", path)
                if job_action is not None:
                    job_id, action = job_action.groups()
                    application.store.job(job_id)
                    if action == "cancel":
                        application.processor.cancel(job_id)
                    else:
                        application.processor.start(job_id)
                    self._send_json({"ok": True})
                    return
                decision_match = re.fullmatch(r"/api/questions/([a-f0-9-]+)/decision", path)
                if decision_match is not None:
                    application.decide(decision_match.group(1), payload)
                    self._send_json({"ok": True})
                    return
                if path == "/api/questions/batch-approve":
                    approved = application.approve_batch(payload)
                    self._send_json({"approved": approved})
                    return
                if path == "/api/filters":
                    saved = application.store.save_filter(
                        _required_text(payload, "name"),
                        DesktopFilterSet.model_validate(payload.get("filters", {})),
                    )
                    self._send_json(saved, HTTPStatus.CREATED)
                    return
                if path == "/api/export":
                    result = application.export(payload)
                    self._send_json(
                        {
                            "directory": str(result.directory),
                            "questionsPath": str(result.questions_path),
                            "exceptionsPath": str(result.exceptions_path),
                            "exported": result.exported_count,
                            "exceptions": result.exception_count,
                        }
                    )
                    return
                self._send_error(HTTPStatus.NOT_FOUND, "rota não encontrada")
            except (OSError, RuntimeError, ValueError, ValidationError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_PUT(self) -> None:
            if not self._trusted_request(require_origin=True) or not self._authorized():
                return
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                question_match = re.fullmatch(r"/api/questions/([a-f0-9-]+)", path)
                if question_match is not None:
                    application.update_question(question_match.group(1), payload)
                    self._send_json({"ok": True})
                    return
                document_match = re.fullmatch(r"/api/documents/([a-f0-9-]+)", path)
                if document_match is not None:
                    result = application.update_document(document_match.group(1), payload)
                    self._send_json({"ok": True, "identityResolution": result})
                    return
                self._send_error(HTTPStatus.NOT_FOUND, "rota não encontrada")
            except (OSError, RuntimeError, ValueError, ValidationError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_DELETE(self) -> None:
            if not self._trusted_request(require_origin=True) or not self._authorized():
                return
            match = re.fullmatch(r"/api/filters/([a-f0-9-]+)", urlparse(self.path).path)
            if match is None:
                self._send_error(HTTPStatus.NOT_FOUND, "rota não encontrada")
                return
            application.store.delete_filter(match.group(1))
            self._send_json({"ok": True})

        def _authorized(self) -> bool:
            if secrets.compare_digest(
                self.headers.get("X-KAD-Desktop-Token", ""), application.token
            ):
                return True
            self._send_error(HTTPStatus.FORBIDDEN, "token local inválido")
            return False

        def _trusted_request(self, *, require_origin: bool) -> bool:
            host_values = self.headers.get_all("Host") or []
            if len(host_values) != 1:
                self._send_error(HTTPStatus.FORBIDDEN, "Host local inválido")
                return False
            authority = _local_authority(host_values[0])
            actual_port = cast(tuple[str, int], self.server.server_address)[1]
            if authority is None or authority[1] != actual_port:
                self._send_error(HTTPStatus.FORBIDDEN, "Host local inválido")
                return False

            origin_values = self.headers.get_all("Origin") or []
            if not origin_values:
                if require_origin:
                    self._send_error(HTTPStatus.FORBIDDEN, "Origin local ausente")
                    return False
                return True
            if len(origin_values) != 1 or not _origin_matches(origin_values[0], authority):
                self._send_error(HTTPStatus.FORBIDDEN, "Origin local inválido")
                return False
            return True

        def _read_json(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length ausente")
            length = int(raw_length)
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("corpo da requisição excede o limite local")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("o corpo JSON deve ser um objeto")
            return payload

        def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status)

        def _send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self._send_headers(len(body), content_type, status)
            self.wfile.write(body)

        def _send_file(self, source: Path, content_type: str) -> None:
            size = source.stat().st_size
            if size > MAX_PDF_BYTES:
                self._send_error(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"PDF excede o limite de {MAX_PDF_BYTES // (1024 * 1024)} MB",
                )
                return
            with source.open("rb") as handle:
                self._send_headers(size, content_type, HTTPStatus.OK)
                while chunk := handle.read(PDF_STREAM_CHUNK_BYTES):
                    self.wfile.write(chunk)

        def _send_headers(
            self,
            content_length: int,
            content_type: str,
            status: HTTPStatus,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'self'; frame-ancestors 'none'; "
                "connect-src 'self'",
            )
            self.end_headers()

    return DesktopRequestHandler


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"campo {key} é obrigatório")
    return value.strip()


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"campo {key} deve ser texto")
    return value.strip() or None


def _local_authority(value: str) -> tuple[str, int] | None:
    parsed = urlparse(f"//{value}")
    try:
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname.casefold() if parsed.hostname else None
    if (
        hostname not in {"127.0.0.1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    return hostname, port


def _origin_matches(value: str, authority: tuple[str, int]) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    hostname = parsed.hostname.casefold() if parsed.hostname else None
    return (
        parsed.scheme == "http"
        and hostname == authority[0]
        and port == authority[1]
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def _resource_text(name: str) -> str:
    return resources.files("kad_collector").joinpath(name).read_text(encoding="utf-8")


def _resource_bytes(name: str) -> bytes:
    return resources.files("kad_collector").joinpath(name).read_bytes()
