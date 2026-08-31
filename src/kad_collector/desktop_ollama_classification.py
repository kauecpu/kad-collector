from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from .canonical_classification import (
    CanonicalAIProvider,
    CanonicalAIRequest,
    CanonicalAIResult,
    CanonicalClassificationError,
    CanonicalClassificationReport,
    canonical_classification_coverage,
    run_canonical_classification,
)
from .desktop_models import DesktopOperationScope
from .desktop_preparation import DesktopPreparationManager, apply_desktop_preparation
from .desktop_scope import ResolvedDesktopScope, resolve_desktop_scope
from .desktop_store import DesktopStore
from .editorial_taxonomy import EditorialTaxonomy
from .ollama_ai_provider import (
    DEFAULT_CONTEXT_LENGTH,
    OllamaCanonicalEnrichmentProvider,
    OllamaHardwareGateError,
    OllamaUnavailableError,
)
from .ollama_preflight import (
    HttpOllamaAdminClient,
    OllamaAdminClient,
    OllamaCommandRunner,
    ollama_processor_for_model,
)
from .semantic_identity import canonical_json

DESKTOP_OLLAMA_MODEL = "qwen3:8b"
DESKTOP_OLLAMA_DIGEST = (
    "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
)
DESKTOP_OLLAMA_QUANTIZATION = "Q4_K_M"
DESKTOP_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
DESKTOP_OLLAMA_JOB_VERSION = "desktop-qwen8b-classification-v2"
DEFAULT_BATCH_LIMIT = 25
MAX_BATCH_LIMIT = 250
DEFAULT_CHECKPOINT_INTERVAL = 5
DEFAULT_HARDWARE_RECHECK_INTERVAL = 5


def _configured_positive_int(name: str, default: int, maximum: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if 1 <= parsed <= maximum else default


def configured_checkpoint_interval() -> int:
    return _configured_positive_int("KAD_QWEN_CHECKPOINT_INTERVAL", DEFAULT_CHECKPOINT_INTERVAL, 50)


def configured_hardware_recheck_interval() -> int:
    return _configured_positive_int(
        "KAD_QWEN_HARDWARE_RECHECK_INTERVAL", DEFAULT_HARDWARE_RECHECK_INTERVAL, 50
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _model_name(item: Mapping[str, Any]) -> str:
    value = item.get("name", item.get("model", ""))
    return value if isinstance(value, str) else ""


def _model_details(item: Mapping[str, Any]) -> Mapping[str, Any]:
    value = item.get("details")
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _close_admin(client: OllamaAdminClient) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _validate_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("o limite do lote deve ser um número inteiro")
    if not 1 <= value <= MAX_BATCH_LIMIT:
        raise ValueError(f"o limite do lote deve ficar entre 1 e {MAX_BATCH_LIMIT}")
    return value


def inspect_qwen8b_desktop(
    client: OllamaAdminClient,
) -> dict[str, Any]:
    if client.base_url.rstrip("/") != DESKTOP_OLLAMA_ENDPOINT:
        raise CanonicalClassificationError(
            "o classificador desktop exige o endpoint http://127.0.0.1:11434"
        )
    installed = {_model_name(item): item for item in client.tags()}
    model = installed.get(DESKTOP_OLLAMA_MODEL)
    if model is None:
        raise CanonicalClassificationError(
            f"o modelo exato {DESKTOP_OLLAMA_MODEL} não está instalado"
        )
    details = _model_details(model)
    digest = model.get("digest")
    quantization = details.get("quantization_level")
    if digest != DESKTOP_OLLAMA_DIGEST:
        raise CanonicalClassificationError("o digest de qwen3:8b diverge do aprovado")
    if quantization != DESKTOP_OLLAMA_QUANTIZATION:
        raise CanonicalClassificationError("a quantização de qwen3:8b diverge de Q4_K_M")
    running = client.running_models()
    unexpected = [name for item in running if (name := _model_name(item)) != DESKTOP_OLLAMA_MODEL]
    if unexpected:
        raise CanonicalClassificationError(
            "há outro modelo carregado no Ollama; descarregue-o antes de continuar"
        )
    return {
        "ready": True,
        "ollamaVersion": client.version(),
        "endpoint": DESKTOP_OLLAMA_ENDPOINT,
        "networkScope": "loopback",
        "model": DESKTOP_OLLAMA_MODEL,
        "digest": DESKTOP_OLLAMA_DIGEST,
        "quantization": DESKTOP_OLLAMA_QUANTIZATION,
        "sizeBytes": model.get("size"),
        "loadedModels": [_model_name(item) for item in running],
    }


def warmup_and_require_full_gpu(
    client: OllamaAdminClient,
    *,
    command_runner: OllamaCommandRunner | None = None,
) -> dict[str, Any]:
    response = client.chat(
        {
            "model": DESKTOP_OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": "Responda somente com o JSON solicitado."},
                {"role": "user", "content": '{"ok":true}'},
            ],
            "stream": False,
            "format": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean", "const": True}},
            },
            "think": False,
            "keep_alive": "5m",
            "options": {
                "temperature": 0,
                "num_ctx": DEFAULT_CONTEXT_LENGTH,
                "num_predict": 32,
                "seed": 0,
            },
        }
    )
    message = response.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    try:
        structured = json.loads(content) if isinstance(content, str) else None
    except json.JSONDecodeError as exc:
        raise CanonicalClassificationError("o aquecimento retornou JSON inválido") from exc
    if structured != {"ok": True} or response.get("done") is not True:
        raise CanonicalClassificationError("o aquecimento do qwen3:8b falhou")
    hardware = require_qwen8b_full_gpu(client, command_runner=command_runner)
    return {
        "structuredOutput": True,
        **hardware,
    }


def require_qwen8b_full_gpu(
    client: OllamaAdminClient,
    *,
    command_runner: OllamaCommandRunner | None = None,
) -> dict[str, Any]:
    running = client.running_models()
    if len(running) != 1 or _model_name(running[0]) != DESKTOP_OLLAMA_MODEL:
        raise CanonicalClassificationError("/api/ps não confirmou somente qwen3:8b carregado")
    loaded = running[0]
    if loaded.get("digest") != DESKTOP_OLLAMA_DIGEST:
        raise CanonicalClassificationError("o digest carregado diverge do aprovado")
    if loaded.get("context_length") != DEFAULT_CONTEXT_LENGTH:
        raise CanonicalClassificationError("qwen3:8b não carregou com contexto 4096")
    processor = ollama_processor_for_model(
        DESKTOP_OLLAMA_MODEL,
        base_url=DESKTOP_OLLAMA_ENDPOINT,
        command_runner=command_runner,
    )
    if processor != "100% GPU":
        raise CanonicalClassificationError("qwen3:8b não atingiu o requisito de 100% GPU")
    return {
        "processor": processor,
        "contextLength": DEFAULT_CONTEXT_LENGTH,
        "sizeBytes": loaded.get("size"),
        "sizeVramBytes": loaded.get("size_vram"),
    }


class _GatedProvider:
    name = "ollama"
    model = DESKTOP_OLLAMA_MODEL

    def __init__(
        self,
        provider: CanonicalAIProvider,
        admin: OllamaAdminClient,
        command_runner: OllamaCommandRunner | None,
        hardware_recheck_interval: int = DEFAULT_HARDWARE_RECHECK_INTERVAL,
    ) -> None:
        if hardware_recheck_interval < 1:
            raise ValueError("hardware_recheck_interval deve ser positivo")
        if provider.name != self.name or provider.model != self.model:
            raise CanonicalClassificationError("o provedor desktop diverge de qwen3:8b local")
        self._provider = provider
        self._admin = admin
        self._command_runner = command_runner
        self._hardware_recheck_interval = hardware_recheck_interval
        self._request_count = 0
        self.hardware_checks = 0

    def check_hardware(self) -> None:
        try:
            require_qwen8b_full_gpu(self._admin, command_runner=self._command_runner)
        except CanonicalClassificationError as exc:
            raise OllamaHardwareGateError(str(exc)) from exc
        self.hardware_checks += 1

    def enrich(self, request: CanonicalAIRequest) -> CanonicalAIResult:
        result = self._provider.enrich(request)
        self._request_count += 1
        if self._request_count == 1 or self._request_count % self._hardware_recheck_interval == 0:
            self.check_hardware()
        return result

    def close(self) -> None:
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()


@dataclass(frozen=True)
class _PreviewApproval:
    token: str
    limit: int
    preflight: dict[str, Any]
    counts: dict[str, Any]
    scope: ResolvedDesktopScope


class DesktopOllamaClassificationManager:
    def __init__(
        self,
        store: DesktopStore,
        *,
        admin_factory: Callable[[], OllamaAdminClient] | None = None,
        provider_factory: Callable[[], CanonicalAIProvider] | None = None,
        command_runner: OllamaCommandRunner | None = None,
        taxonomy: EditorialTaxonomy | None = None,
    ) -> None:
        self.store = store
        self._admin_factory = admin_factory or (
            lambda: HttpOllamaAdminClient(DESKTOP_OLLAMA_ENDPOINT)
        )
        self._provider_factory = provider_factory or self._default_provider
        self._command_runner = command_runner
        self._taxonomy = taxonomy
        self._preparation = DesktopPreparationManager(store)
        self._checkpoint_interval = configured_checkpoint_interval()
        self._hardware_recheck_interval = configured_hardware_recheck_interval()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kad-qwen8b")
        self._future: Future[None] | None = None
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._pause_events: dict[str, threading.Event] = {}
        self._approvals: dict[str, _PreviewApproval] = {}
        self._initialize()

    @staticmethod
    def _default_provider() -> OllamaCanonicalEnrichmentProvider:
        client = httpx.Client(
            base_url=DESKTOP_OLLAMA_ENDPOINT,
            timeout=180.0,
            trust_env=False,
        )
        return OllamaCanonicalEnrichmentProvider(
            model=DESKTOP_OLLAMA_MODEL,
            client=client,
            base_url=DESKTOP_OLLAMA_ENDPOINT,
        )

    def _initialize(self) -> None:
        with closing(self.store._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS desktop_ollama_classification_jobs (
                    id TEXT PRIMARY KEY,
                    confirmation_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    requested_limit INTEGER NOT NULL,
                    batch_limit INTEGER NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0,
                    remaining INTEGER NOT NULL DEFAULT 0,
                    ai_calls INTEGER NOT NULL DEFAULT 0,
                    accepted_suggestions INTEGER NOT NULL DEFAULT 0,
                    review_required INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    model TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    quantization TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    preflight_json TEXT NOT NULL,
                    hardware_json TEXT NOT NULL DEFAULT '{}',
                    report_json TEXT NOT NULL DEFAULT '{}',
                    scope_json TEXT NOT NULL DEFAULT '{}',
                    scope_snapshot_hash TEXT NOT NULL DEFAULT '',
                    pause_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS desktop_ollama_one_active_job_idx
                ON desktop_ollama_classification_jobs((1))
                WHERE status IN ('starting','running','pause_requested');
                """
            )
            columns = {
                cast(str, row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(desktop_ollama_classification_jobs)"
                ).fetchall()
            }
            if "hardware_json" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_ollama_classification_jobs "
                    "ADD COLUMN hardware_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "scope_json" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_ollama_classification_jobs "
                    "ADD COLUMN scope_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "scope_snapshot_hash" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_ollama_classification_jobs "
                    "ADD COLUMN scope_snapshot_hash TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "UPDATE desktop_ollama_classification_jobs "
                "SET status='paused',pause_reason='aplicativo reiniciado',updated_at=? "
                "WHERE status IN ('starting','running','pause_requested')",
                (_now(),),
            )
            connection.commit()

    def _passive_counts(self, resolved: ResolvedDesktopScope) -> dict[str, Any]:
        started = time.perf_counter()
        source = sqlite3.connect(self.store.path, timeout=30)
        memory = sqlite3.connect(":memory:")
        memory.row_factory = sqlite3.Row
        try:
            source.backup(memory)
            preparation = apply_desktop_preparation(
                memory,
                run_id=f"qwen-preview-{uuid.uuid4().hex}",
                question_ids=set(resolved.question_ids),
            )
            report = run_canonical_classification(
                memory,
                apply=False,
                enable_ai=False,
                taxonomy=self._taxonomy,
                eligibility_scope="answered",
                question_ids=set(resolved.question_ids),
            )
            coverage = canonical_classification_coverage(
                memory,
                eligibility_scope="answered",
                question_ids=set(resolved.question_ids),
            )
            details = self._scope_details(memory, resolved)
        finally:
            memory.close()
            source.close()
        exclusion_reasons: list[dict[str, Any]] = []
        if preparation["rawQuestions"] == 0:
            exclusion_reasons.append({
                "code": "no_questions",
                "label": "Nenhuma questão coletada",
                "count": 0,
                "action": "Colete uma fonte ou adicione PDFs.",
            })
        elif coverage["officialAnswered"] == 0:
            exclusion_reasons.append({
                "code": "no_official_answers",
                "label": "Nenhuma resposta oficial disponível",
                "count": preparation["rawQuestions"],
                "action": "Relacione as provas aos gabaritos oficiais.",
            })
        if coverage["blockedAnswered"]:
            exclusion_reasons.append({
                "code": "answered_but_invalid",
                "label": "Respondidas com outro impedimento comprovado",
                "count": coverage["blockedAnswered"],
                "action": "Confira alternativas, origem ou vínculo dessas questões.",
            })
        if report.already_complete:
            exclusion_reasons.append({
                "code": "already_complete",
                "label": "Classificação já completa",
                "count": report.already_complete,
                "action": "Nenhuma ação de classificação é necessária para essas questões.",
            })
        categorized = (
            report.already_complete
            + report.deterministic_questions
            + report.ai_candidates
            + report.review_required
            + report.provider_failures
        )
        return {
            "rawQuestions": preparation["rawQuestions"],
            "officialAnswered": coverage["officialAnswered"],
            "canonicalQuestions": preparation["canonicalQuestions"],
            "classificationUnits": coverage["classificationUnits"],
            "eligibleQuestions": coverage["eligibleQuestions"],
            "inheritedCopies": coverage["inheritedCopies"],
            "blockedAnswered": coverage["blockedAnswered"],
            "eligible": report.eligible,
            "alreadyComplete": report.already_complete,
            "deterministic": report.deterministic_questions,
            "qwenRequired": report.ai_candidates,
            "needsReview": report.review_required,
            "failures": report.provider_failures,
            "classificationInvariant": {
                "eligible": report.eligible,
                "categorized": categorized,
                "uncategorized": max(report.eligible - categorized, 0),
                "balanced": categorized == report.eligible,
            },
            "missingFields": dict(sorted(report.requested_fields.items())),
            "exclusionReasons": exclusion_reasons,
            "performance": {"previewMs": (time.perf_counter() - started) * 1000},
            **details,
        }

    @staticmethod
    def _scope_details(
        connection: sqlite3.Connection, resolved: ResolvedDesktopScope
    ) -> dict[str, Any]:
        selected = set(resolved.question_ids)
        placeholders = ",".join("?" for _ in selected)
        rows = connection.execute(
            "SELECT q.id,g.id AS group_id,g.status AS group_status,cq.id AS canonical_id, "
            "rep_o.question_id AS representative_question_id "
            "FROM questions q LEFT JOIN question_occurrences o ON o.question_id=q.id "
            "LEFT JOIN question_group_occurrences go ON go.occurrence_id=o.id "
            "AND go.status='active' "
            "LEFT JOIN question_equivalence_groups g ON g.id=go.group_id "
            "LEFT JOIN canonical_questions cq ON cq.group_id=g.id "
            "LEFT JOIN question_occurrences rep_o ON rep_o.id=cq.representative_occurrence_id "
            f"WHERE q.id IN ({placeholders})",  # noqa: S608
            tuple(sorted(selected)),
        ).fetchall()
        by_id = {cast(str, row["id"]): row for row in rows}
        included_by_group: dict[str, list[tuple[dict[str, Any], sqlite3.Row]]] = {}
        excluded: list[dict[str, Any]] = []
        group_ids: set[str] = set()
        for item in resolved.items:
            row = by_id.get(cast(str, item["id"]))
            if row is not None and row["group_status"] == "confirmed" and row["canonical_id"]:
                group_id = cast(str, row["group_id"])
                included_by_group.setdefault(group_id, []).append((dict(item), row))
                group_ids.add(group_id)
            else:
                excluded.append({**item, "reason": "questão sem unidade canônica elegível"})
        included: list[dict[str, Any]] = []
        for candidates in included_by_group.values():
            representative_id = candidates[0][1]["representative_question_id"]
            item, row = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate[0]["id"] == representative_id
                ),
                candidates[0],
            )
            included.append({**item, "canonicalId": row["canonical_id"]})
        affected: set[str] = set()
        if group_ids:
            group_placeholders = ",".join("?" for _ in group_ids)
            affected = {
                cast(str, row["question_id"])
                for row in connection.execute(
                    "SELECT DISTINCT o.question_id FROM question_group_occurrences go "
                    "JOIN question_occurrences o ON o.id=go.occurrence_id "
                    f"WHERE go.status='active' AND go.group_id IN ({group_placeholders})",  # noqa: S608
                    tuple(sorted(group_ids)),
                ).fetchall()
            }
        return {
            "scope": resolved.public(),
            "included": included,
            "excluded": excluded,
            "includedCount": len(included),
            "excludedCount": len(excluded),
            "canonicalUnitIds": sorted(group_ids),
            "outsideScopeQuestionIds": sorted(affected - selected),
            "outsideScopeCount": len(affected - selected),
            "requiresOutsideScopeAuthorization": bool(affected - selected),
        }

    def preview(
        self,
        scope: DesktopOperationScope,
        limit: int = DEFAULT_BATCH_LIMIT,
    ) -> dict[str, Any]:
        selected_limit = _validate_limit(limit)
        resolved = resolve_desktop_scope(self.store, scope)
        admin = self._admin_factory()
        try:
            preflight = inspect_qwen8b_desktop(admin)
        finally:
            _close_admin(admin)
        counts = self._passive_counts(resolved)
        token = uuid.uuid4().hex
        approval = _PreviewApproval(
            token=token,
            limit=selected_limit,
            preflight=preflight,
            counts=counts,
            scope=resolved,
        )
        with self._lock:
            self._approvals = {token: approval}
        return {
            "confirmationToken": token,
            "limit": selected_limit,
            "maximumLimit": MAX_BATCH_LIMIT,
            "counts": counts,
            "scope": resolved.public(),
            "preflight": preflight,
            "warning": (
                "Somente disciplina, matéria, assunto e nível ausentes podem mudar. "
                "Respostas, gabaritos e vínculos permanecem intactos."
            ),
        }

    def start(
        self,
        confirmation_token: str,
        scope: DesktopOperationScope,
        limit: int,
    ) -> dict[str, Any]:
        confirmation_hash = hashlib.sha256(confirmation_token.encode("utf-8")).hexdigest()
        with self._start_lock:
            with closing(self.store._connect()) as connection:
                existing = connection.execute(
                    "SELECT id FROM desktop_ollama_classification_jobs "
                    "WHERE confirmation_hash=?",
                    (confirmation_hash,),
                ).fetchone()
            if existing is not None:
                raise ValueError("o token de confirmação já foi utilizado")
            return self._start(confirmation_token, scope, limit, confirmation_hash)

    def start_automatic(
        self,
        scope: DesktopOperationScope,
        *,
        limit: int = MAX_BATCH_LIMIT,
    ) -> dict[str, Any]:
        """Start a verified Qwen run for the background desktop automator."""
        selected_limit = _validate_limit(limit)
        current = self._job_row()
        if current is not None and current["status"] in {
            "starting",
            "running",
            "pause_requested",
        }:
            return self.status(cast(str, current["id"]))
        resolved = resolve_desktop_scope(self.store, scope)
        counts = self._passive_counts(resolved)
        pending = int(counts.get("qwenRequired", 0) or 0)
        if pending == 0:
            return {
                "state": "completed",
                "target": 0,
                "processed": 0,
                "remaining": 0,
                "aiCalls": 0,
                "noWorkReason": "Nenhuma questão precisa do Qwen",
            }
        preview = self.preview(scope, selected_limit)
        counts = cast(dict[str, Any], preview.get("counts", {}))
        token = preview.get("confirmationToken")
        if not isinstance(token, str) or not token:
            raise CanonicalClassificationError("prévia automática do Qwen inválida")
        return self.start(token, scope, selected_limit)

    def _start(
        self,
        confirmation_token: str,
        scope: DesktopOperationScope,
        limit: int,
        confirmation_hash: str,
    ) -> dict[str, Any]:
        selected_limit = _validate_limit(limit)
        with self._lock:
            approval = self._approvals.pop(confirmation_token, None)
        if approval is None or approval.limit != selected_limit:
            raise ValueError("confirmação da prévia ausente, expirada ou divergente")
        resolved = resolve_desktop_scope(self.store, scope)
        if (
            resolved.snapshot_hash != approval.scope.snapshot_hash
            or resolved.contract.model_dump(mode="json", by_alias=True)
            != approval.scope.contract.model_dump(mode="json", by_alias=True)
        ):
            raise RuntimeError("o banco ou o escopo mudou desde a prévia")
        if (
            approval.counts.get("requiresOutsideScopeAuthorization")
            and not scope.allow_out_of_scope
        ):
            raise RuntimeError(
                "a classificação afetaria cópias fora do escopo; autorize o impacto na prévia"
            )
        pending = int(approval.counts["deterministic"]) + int(
            approval.counts["qwenRequired"]
        )
        if pending == 0:
            return {
                "state": "completed",
                "target": 0,
                "processed": 0,
                "remaining": 0,
                "aiCalls": 0,
                "noWorkReason": "Nenhuma questão precisa do Qwen",
            }
        with closing(self.store._connect()) as connection:
            existing = connection.execute(
                "SELECT id FROM desktop_ollama_classification_jobs WHERE confirmation_hash=?",
                (confirmation_hash,),
            ).fetchone()
            if existing is not None:
                raise ValueError("o token de confirmação já foi utilizado")
            current = connection.execute(
                "SELECT id FROM desktop_ollama_classification_jobs "
                "WHERE status IN ('starting','running','pause_requested') LIMIT 1"
            ).fetchone()
            if current is not None:
                raise RuntimeError("já existe uma classificação com IA em andamento")
            admin = self._admin_factory()
            try:
                current_preflight = inspect_qwen8b_desktop(admin)
            finally:
                _close_admin(admin)
            if self._preflight_contract(current_preflight) != self._preflight_contract(
                approval.preflight
            ):
                raise RuntimeError("o ambiente Ollama mudou desde a prévia")
            self.store.backup_before_preparation()
            apply_desktop_preparation(
                connection,
                run_id=f"qwen-start-{uuid.uuid4().hex}",
                question_ids=set(resolved.question_ids),
            )
            run_id = str(uuid.uuid4())
            now = _now()
            initial_remaining = min(selected_limit, pending)
            connection.execute(
                "INSERT INTO desktop_ollama_classification_jobs "
                "(id,confirmation_hash,status,requested_limit,batch_limit,remaining,model,digest,quantization,"
                "endpoint,algorithm_version,preflight_json,report_json,scope_json,"
                "scope_snapshot_hash,created_at,updated_at) "
                "VALUES (?,?,'starting',?,?,?,?,?,?,?,?,?, '{}',?,?,?,?)",
                (
                    run_id,
                    confirmation_hash,
                    selected_limit,
                    initial_remaining,
                    initial_remaining,
                    DESKTOP_OLLAMA_MODEL,
                    DESKTOP_OLLAMA_DIGEST,
                    DESKTOP_OLLAMA_QUANTIZATION,
                    DESKTOP_OLLAMA_ENDPOINT,
                    DESKTOP_OLLAMA_JOB_VERSION,
                    canonical_json(current_preflight),
                    canonical_json(
                        {
                            "type": scope.type,
                            "questionIds": list(resolved.question_ids),
                            "filter": (
                                scope.filter.model_dump(mode="json")
                                if scope.filter is not None
                                else None
                            ),
                        }
                    ),
                    resolved.snapshot_hash,
                    now,
                    now,
                ),
            )
            connection.commit()
        with self._lock:
            self._approvals.pop(confirmation_token, None)
            event = threading.Event()
            self._pause_events[run_id] = event
            self._future = self._executor.submit(self._run, run_id, event)
        return self.status(run_id)

    @staticmethod
    def _preflight_contract(value: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(
            value.get(name)
            for name in ("endpoint", "model", "digest", "quantization", "ollamaVersion")
        )

    def _job_row(self, run_id: str | None = None) -> sqlite3.Row | None:
        with closing(self.store._connect()) as connection:
            if run_id is None:
                return cast(sqlite3.Row | None, connection.execute(
                    "SELECT * FROM desktop_ollama_classification_jobs "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone())
            return cast(sqlite3.Row | None, connection.execute(
                "SELECT * FROM desktop_ollama_classification_jobs WHERE id=?", (run_id,)
            ).fetchone())

    def status(self, run_id: str | None = None) -> dict[str, Any]:
        row = self._job_row(run_id)
        if row is None:
            return {"state": "idle"}
        report = json.loads(cast(str, row["report_json"]))
        return {
            "runId": row["id"],
            "state": row["status"],
            "limit": int(row["requested_limit"]),
            "target": int(row["batch_limit"]),
            "processed": int(row["processed"]),
            "remaining": int(row["remaining"]),
            "aiCalls": int(row["ai_calls"]),
            "acceptedSuggestions": int(row["accepted_suggestions"]),
            "reviewRequired": int(row["review_required"]),
            "failures": int(row["failures"]),
            "pauseReason": row["pause_reason"],
            "model": row["model"],
            "digest": row["digest"],
            "quantization": row["quantization"],
            "endpoint": row["endpoint"],
            "algorithmVersion": row["algorithm_version"],
            "hardware": json.loads(cast(str, row["hardware_json"])),
            "scope": json.loads(cast(str, row["scope_json"])),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "finishedAt": row["finished_at"],
            "performance": report.get("performance", {}),
        }

    def pause(self, run_id: str) -> dict[str, Any]:
        row = self._job_row(run_id)
        if row is None:
            raise ValueError("execução de classificação não encontrada")
        if row["status"] in {"paused", "completed", "blocked"}:
            return self.status(run_id)
        with self._lock:
            event = self._pause_events.get(run_id)
            if event is not None:
                event.set()
                # The worker owns the classification transaction. Do not issue
                # a competing write while an Ollama call may still hold it.
                return self.status(run_id)
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_ollama_classification_jobs "
                "SET status='pause_requested',pause_reason='pausa solicitada',updated_at=? "
                "WHERE id=? AND status IN ('starting','running')",
                (_now(), run_id),
            )
            connection.commit()
        return self.status(run_id)

    def resume(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if self._future is not None and not self._future.done():
                current = self._job_row()
                if current is not None and current["id"] == run_id:
                    return self.status(run_id)
                raise RuntimeError("já existe uma classificação com IA em andamento")
            row = self._job_row(run_id)
            if row is None:
                raise ValueError("execução de classificação não encontrada")
            if row["status"] == "completed":
                return self.status(run_id)
            if row["status"] not in {"paused", "blocked"}:
                return self.status(run_id)
            admin = self._admin_factory()
            try:
                inspect_qwen8b_desktop(admin)
            finally:
                _close_admin(admin)
            with closing(self.store._connect()) as connection:
                connection.execute(
                    "UPDATE desktop_ollama_classification_jobs "
                    "SET status='starting',pause_reason=NULL,updated_at=? WHERE id=?",
                    (_now(), run_id),
                )
                connection.commit()
            event = threading.Event()
            self._pause_events[run_id] = event
            self._future = self._executor.submit(self._run, run_id, event)
        return self.status(run_id)

    def _update_progress(
        self,
        run_id: str,
        base_processed: int,
        base_metrics: tuple[int, int, int, int],
        base_performance: Mapping[str, Any],
        report: CanonicalClassificationReport,
    ) -> None:
        row = self._job_row(run_id)
        if row is None:
            return
        processed = base_processed + report.processed
        remaining = max(0, int(row["batch_limit"]) - processed)
        persisted_report = deepcopy(report)
        cumulative = dict(report.performance)
        if base_performance:
            for key in (
                "classificationMs",
                "qwenMs",
                "persistenceMs",
                "totalMs",
                "hardwareChecks",
            ):
                value = report.performance.get(key)
                if isinstance(value, (int, float)):
                    cumulative[key] = float(base_performance.get(key, 0)) + value
                elif key in base_performance:
                    cumulative[key] = base_performance[key]
        persisted_report.performance = cumulative
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_ollama_classification_jobs SET processed=?,remaining=?,"
                "ai_calls=?,accepted_suggestions=?,review_required=?,failures=?,"
                "report_json=?,updated_at=? WHERE id=?",
                (
                    processed,
                    remaining,
                    base_metrics[0] + report.ai_sent,
                    base_metrics[1] + report.ai_accepted,
                    base_metrics[2] + report.review_required,
                    base_metrics[3] + report.provider_failures,
                    canonical_json(persisted_report.as_dict()),
                    _now(),
                    run_id,
                ),
            )
            connection.commit()

    def _run(self, run_id: str, pause_event: threading.Event) -> None:
        admin = self._admin_factory()
        provider: _GatedProvider | None = None
        base_row = self._job_row(run_id)
        if base_row is None:
            _close_admin(admin)
            return
        base_processed = int(base_row["processed"])
        base_metrics = (
            int(base_row["ai_calls"]),
            int(base_row["accepted_suggestions"]),
            int(base_row["review_required"]),
            int(base_row["failures"]),
        )
        try:
            stored_report = json.loads(cast(str, base_row["report_json"]))
        except (TypeError, json.JSONDecodeError):
            stored_report = {}
        base_performance = stored_report.get("performance", {})
        if not isinstance(base_performance, Mapping):
            base_performance = {}
        remaining_capacity = max(0, int(base_row["batch_limit"]) - base_processed)
        stored_scope = json.loads(cast(str, base_row["scope_json"]))
        scoped_question_ids = {
            str(question_id) for question_id in stored_scope.get("questionIds", [])
        }
        if remaining_capacity == 0:
            self._finish(run_id, "completed")
            _close_admin(admin)
            return
        try:
            inspect_qwen8b_desktop(admin)
            warmup = warmup_and_require_full_gpu(
                admin, command_runner=self._command_runner
            )
            with closing(self.store._connect()) as connection:
                connection.execute(
                    "UPDATE desktop_ollama_classification_jobs "
                    "SET status='running',hardware_json=?,updated_at=? WHERE id=?",
                    (canonical_json(warmup), _now(), run_id),
                )
                connection.commit()
            provider = _GatedProvider(
                self._provider_factory(),
                admin,
                self._command_runner,
                hardware_recheck_interval=self._hardware_recheck_interval,
            )
            with closing(self.store._connect()) as connection:
                report = run_canonical_classification(
                    connection,
                    apply=True,
                    enable_ai=True,
                    provider=provider,
                    run_id=run_id,
                    limit=remaining_capacity,
                    pending_only=True,
                    should_pause=pause_event.is_set,
                    progress_callback=lambda item: self._update_progress(
                        run_id, base_processed, base_metrics, base_performance, item
                    ),
                    before_block_callback=provider.check_hardware,
                    checkpoint_interval=self._checkpoint_interval,
                    taxonomy=self._taxonomy,
                    queue_ineligible=False,
                    eligibility_scope="answered",
                    question_ids=scoped_question_ids,
                )
            for key in ("classificationMs", "qwenMs", "persistenceMs", "totalMs"):
                value = report.performance.get(key)
                if isinstance(value, (int, float)):
                    report.performance[key] = float(base_performance.get(key, 0)) + value
            report.performance.update(
                {
                    "checkpointInterval": self._checkpoint_interval,
                    "hardwareRecheckInterval": self._hardware_recheck_interval,
                    "hardwareChecks": int(base_performance.get("hardwareChecks", 0))
                    + provider.hardware_checks,
                }
            )
            self._update_progress(run_id, base_processed, base_metrics, {}, report)
            current = self._job_row(run_id)
            processed = int(current["processed"]) if current is not None else base_processed
            state = "paused" if pause_event.is_set() else "completed"
            reason = "pausa solicitada" if pause_event.is_set() else None
            if processed < int(base_row["batch_limit"]) and report.eligible > report.processed:
                state = "paused"
                reason = reason or (
                    "Ollama ou GPU ficou indisponível; verifique antes de retomar"
                    if report.provider_failures
                    else "execução interrompida antes do limite"
                )
            self._finish(run_id, state, reason=reason)
        except OllamaUnavailableError as exc:
            self._finish(run_id, "paused", reason=str(exc), failure=True)
        except Exception as exc:
            self._finish(run_id, "blocked", reason=str(exc), failure=True)
        finally:
            if provider is not None:
                provider.close()
            try:
                admin.unload(DESKTOP_OLLAMA_MODEL)
            except Exception as exc:
                current = self._job_row(run_id)
                if current is not None and current["status"] != "blocked":
                    self._finish(
                        run_id,
                        "blocked",
                        reason=f"falha ao descarregar qwen3:8b: {exc}",
                        failure=True,
                    )
            finally:
                _close_admin(admin)

    def _finish(
        self,
        run_id: str,
        state: str,
        *,
        reason: str | None = None,
        failure: bool = False,
    ) -> None:
        with closing(self.store._connect()) as connection:
            connection.execute(
                "UPDATE desktop_ollama_classification_jobs SET status=?,pause_reason=?,"
                "failures=failures+?,updated_at=?,finished_at=? WHERE id=?",
                (
                    state,
                    reason,
                    int(failure),
                    _now(),
                    _now() if state in {"completed", "blocked"} else None,
                    run_id,
                ),
            )
            connection.commit()

    def wait(self, timeout: float = 30.0) -> None:
        future = self._future
        if future is not None:
            future.result(timeout=timeout)

    def shutdown(self) -> None:
        row = self._job_row()
        if row is not None and row["status"] in {"starting", "running", "pause_requested"}:
            self.pause(cast(str, row["id"]))
        self._executor.shutdown(wait=True, cancel_futures=False)
