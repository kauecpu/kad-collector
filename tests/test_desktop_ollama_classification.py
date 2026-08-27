from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import closing
from http import HTTPStatus
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_canonical_classification import (
    FakeProvider,
    _clear_fields,
    _level_decision,
    _seed_canonical,
    _taxonomy,
)
from test_question_equivalence import SyntheticCatalog, _question

from kad_collector.canonical_classification import (
    CanonicalClassificationError,
    run_canonical_classification,
)
from kad_collector.desktop_ollama_classification import (
    DESKTOP_OLLAMA_DIGEST,
    DESKTOP_OLLAMA_ENDPOINT,
    DESKTOP_OLLAMA_MODEL,
    DESKTOP_OLLAMA_QUANTIZATION,
    DesktopOllamaClassificationManager,
    inspect_qwen8b_desktop,
)
from kad_collector.desktop_server import DesktopApplication, start_desktop_server


class FakeAdmin:
    base_url = DESKTOP_OLLAMA_ENDPOINT

    def __init__(
        self,
        *,
        digest: str = DESKTOP_OLLAMA_DIGEST,
        quantization: str = DESKTOP_OLLAMA_QUANTIZATION,
        running: list[dict[str, Any]] | None = None,
    ) -> None:
        self.digest = digest
        self.quantization = quantization
        self.running = list(running or [])
        self.chat_calls = 0
        self.unloads: list[str] = []

    def version(self) -> str:
        return "0.32.15"

    def tags(self) -> list[dict[str, Any]]:
        return [
            {
                "name": DESKTOP_OLLAMA_MODEL,
                "digest": self.digest,
                "size": 5_225_388_164,
                "details": {"quantization_level": self.quantization},
            }
        ]

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.chat_calls += 1
        self.running = [
            {
                "name": DESKTOP_OLLAMA_MODEL,
                "digest": self.digest,
                "context_length": 4096,
                "size": 5_225_388_164,
                "size_vram": 6_000_000_000,
            }
        ]
        return {"message": {"content": json.dumps({"ok": True})}, "done": True}

    def running_models(self) -> list[dict[str, Any]]:
        return list(self.running)

    def unload(self, model: str) -> None:
        self.unloads.append(model)
        self.running = [item for item in self.running if item.get("name") != model]


def _gpu_runner(command: tuple[str, ...], environment: Mapping[str, str]) -> str:
    assert command == ("ollama", "ps")
    assert environment["OLLAMA_HOST"] == DESKTOP_OLLAMA_ENDPOINT
    return "NAME PROCESSOR\nqwen3:8b 100% GPU\n"


class DesktopOllamaClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.taxonomy = _taxonomy()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _manager(
        self,
        fixture: Any,
        admin: FakeAdmin,
        provider: FakeProvider | None = None,
        *,
        runner: Any = _gpu_runner,
    ) -> DesktopOllamaClassificationManager:
        active_provider = provider or FakeProvider(_level_decision())
        active_provider.name = "ollama"
        active_provider.model = DESKTOP_OLLAMA_MODEL
        return DesktopOllamaClassificationManager(
            fixture.store,
            admin_factory=lambda: admin,
            provider_factory=lambda: active_provider,
            command_runner=runner,
            taxonomy=self.taxonomy,
        )

    def test_preview_is_passive_and_reports_exact_contract(self) -> None:
        fixture, rows = _seed_canonical(self.root)
        _clear_fields(fixture, rows[0][1], {"level"})
        admin = FakeAdmin()
        manager = self._manager(fixture, admin)
        with closing(fixture.store._connect()) as connection:
            before_runs = connection.execute(
                "SELECT COUNT(*) FROM canonical_classification_runs"
            ).fetchone()[0]

        preview = manager.preview(25)

        with closing(fixture.store._connect()) as connection:
            after_runs = connection.execute(
                "SELECT COUNT(*) FROM canonical_classification_runs"
            ).fetchone()[0]
            jobs = connection.execute(
                "SELECT COUNT(*) FROM desktop_ollama_classification_jobs"
            ).fetchone()[0]
        self.assertEqual(admin.chat_calls, 0)
        self.assertEqual((before_runs, after_runs, jobs), (0, 0, 0))
        self.assertEqual(preview["counts"]["qwenRequired"], 1)
        self.assertEqual(preview["counts"]["missingFields"], {"level": 1})
        self.assertEqual(preview["preflight"]["model"], DESKTOP_OLLAMA_MODEL)
        self.assertEqual(preview["preflight"]["digest"], DESKTOP_OLLAMA_DIGEST)
        self.assertEqual(preview["preflight"]["quantization"], "Q4_K_M")
        self.assertIn("Respostas, gabaritos e vínculos", preview["warning"])

    def test_answered_question_does_not_wait_for_canonical_confirmation(self) -> None:
        fixture = SyntheticCatalog(self.root, booklets=("1", "2"))
        question_id = fixture.add("Analista", "1", _question())
        _clear_fields(fixture, question_id, {"level"})
        provider = FakeProvider(_level_decision())
        admin = FakeAdmin()
        manager = self._manager(fixture, admin, provider)

        preview = manager.preview(25)
        started = manager.start(preview["confirmationToken"], 25)
        manager.wait()
        status = manager.status(started["runId"])

        self.assertEqual(preview["counts"]["officialAnswered"], 1)
        self.assertEqual(preview["counts"]["eligibleQuestions"], 1)
        self.assertEqual(preview["counts"]["classificationUnits"], 1)
        self.assertEqual(preview["counts"]["qwenRequired"], 1)
        self.assertEqual(status["processed"], 1)
        self.assertEqual(fixture.store.question(question_id)["question"]["level"], "Superior")

    def test_cancel_after_preview_does_not_create_job_or_call_model(self) -> None:
        fixture, rows = _seed_canonical(self.root)
        _clear_fields(fixture, rows[0][1], {"level"})
        admin = FakeAdmin()
        manager = self._manager(fixture, admin)

        manager.preview(25)

        with closing(fixture.store._connect()) as connection:
            jobs = connection.execute(
                "SELECT COUNT(*) FROM desktop_ollama_classification_jobs"
            ).fetchone()[0]
        self.assertEqual(jobs, 0)
        self.assertEqual(admin.chat_calls, 0)

    def test_complete_collection_does_not_warm_up_model(self) -> None:
        fixture, _ = _seed_canonical(self.root)
        admin = FakeAdmin()
        manager = self._manager(fixture, admin)
        preview = manager.preview(25)

        status = manager.start(preview["confirmationToken"], 25)

        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["target"], 0)
        self.assertEqual(admin.chat_calls, 0)
        self.assertEqual(admin.unloads, [])

    def test_confirmed_run_uses_qwen_once_and_preserves_answer(self) -> None:
        fixture, rows = _seed_canonical(self.root)
        _, question_id = rows[0]
        _clear_fields(fixture, question_id, {"level"})
        before = fixture.store.question(question_id)["question"]
        provider = FakeProvider(_level_decision())
        admin = FakeAdmin()
        manager = self._manager(fixture, admin, provider)
        preview = manager.preview(25)

        started = manager.start(preview["confirmationToken"], 25)
        manager.wait()
        status = manager.status(started["runId"])
        repeated = manager.start(preview["confirmationToken"], 25)
        after = fixture.store.question(question_id)["question"]

        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["processed"], 1)
        self.assertEqual(status["aiCalls"], 1)
        self.assertEqual(status["acceptedSuggestions"], 1)
        self.assertEqual(status["hardware"]["processor"], "100% GPU")
        self.assertEqual(repeated["runId"], started["runId"])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].requested_fields, ("level",))
        self.assertEqual(after["level"], "Superior")
        self.assertEqual(after["correct_answer"], before["correct_answer"])
        self.assertEqual(after["answer_status"], before["answer_status"])
        self.assertEqual(admin.unloads, [DESKTOP_OLLAMA_MODEL])

    def test_gpu_loss_after_inference_pauses_without_creating_review(self) -> None:
        fixture, rows = _seed_canonical(self.root)
        _clear_fields(fixture, rows[0][1], {"level"})
        provider = FakeProvider(_level_decision())
        admin = FakeAdmin()
        checks = 0

        def changing_runner(
            command: tuple[str, ...], environment: Mapping[str, str]
        ) -> str:
            nonlocal checks
            checks += 1
            return (
                "qwen3:8b 100% GPU"
                if checks == 1
                else "qwen3:8b 50% GPU 50% CPU"
            )

        manager = self._manager(fixture, admin, provider, runner=changing_runner)
        preview = manager.preview(25)
        started = manager.start(preview["confirmationToken"], 25)
        manager.wait()
        status = manager.status(started["runId"])
        with closing(fixture.store._connect()) as connection:
            reviews = connection.execute(
                "SELECT COUNT(*) FROM canonical_classification_review_queue "
                "WHERE run_id=?",
                (started["runId"],),
            ).fetchone()[0]

        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["processed"], 0)
        self.assertEqual(status["failures"], 1)
        self.assertEqual(reviews, 0)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(admin.unloads, [DESKTOP_OLLAMA_MODEL])

    def test_wrong_digest_quantization_endpoint_or_loaded_model_blocks_preview(self) -> None:
        fixture, _ = _seed_canonical(self.root)
        cases = [
            FakeAdmin(digest="wrong"),
            FakeAdmin(quantization="Q8_0"),
            FakeAdmin(running=[{"name": "qwen3:14b"}]),
        ]
        for admin in cases:
            with self.subTest(admin=admin.__dict__):
                manager = self._manager(fixture, admin)
                with self.assertRaises(CanonicalClassificationError):
                    manager.preview(25)
                self.assertEqual(admin.chat_calls, 0)
        endpoint = FakeAdmin()
        endpoint.base_url = "http://192.168.1.20:11434"
        with self.assertRaises(CanonicalClassificationError):
            inspect_qwen8b_desktop(endpoint)

    def test_partial_cpu_blocks_before_question_and_unloads_model(self) -> None:
        fixture, rows = _seed_canonical(self.root)
        _clear_fields(fixture, rows[0][1], {"level"})
        provider = FakeProvider(_level_decision())
        admin = FakeAdmin()

        def partial_runner(
            command: tuple[str, ...], environment: Mapping[str, str]
        ) -> str:
            return "qwen3:8b 50% GPU 50% CPU"

        manager = self._manager(fixture, admin, provider, runner=partial_runner)
        preview = manager.preview(25)
        started = manager.start(preview["confirmationToken"], 25)
        manager.wait()
        status = manager.status(started["runId"])

        self.assertEqual(status["state"], "blocked")
        self.assertIn("100% GPU", status["pauseReason"])
        self.assertEqual(provider.requests, [])
        self.assertEqual(admin.unloads, [DESKTOP_OLLAMA_MODEL])

    def test_pause_happens_before_next_item_and_resume_skips_checkpoint(self) -> None:
        fixture, rows = _seed_canonical(self.root, second_number=True)
        for _, question_id in rows:
            _clear_fields(fixture, question_id, {"level"})
        first_provider = FakeProvider(_level_decision())
        progress: list[int] = []
        with closing(fixture.store._connect()) as connection:
            paused = run_canonical_classification(
                connection,
                apply=True,
                enable_ai=True,
                provider=first_provider,
                run_id="desktop-pause",
                pending_only=True,
                should_pause=lambda: len(first_provider.requests) == 1,
                progress_callback=lambda report: progress.append(report.processed),
                taxonomy=self.taxonomy,
            )
            resumed_provider = FakeProvider(_level_decision())
            resumed = run_canonical_classification(
                connection,
                apply=True,
                enable_ai=True,
                provider=resumed_provider,
                run_id="desktop-pause",
                pending_only=True,
                taxonomy=self.taxonomy,
            )
            items = connection.execute(
                "SELECT COUNT(*) FROM canonical_classification_run_items "
                "WHERE run_id='desktop-pause'"
            ).fetchone()[0]

        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.processed, 1)
        self.assertEqual(progress, [1])
        self.assertEqual(len(resumed_provider.requests), 1)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(items, 2)

    def test_only_one_active_ai_job_is_allowed(self) -> None:
        fixture, rows = _seed_canonical(self.root)
        _clear_fields(fixture, rows[0][1], {"level"})
        admin = FakeAdmin()
        manager = self._manager(fixture, admin)
        preview = manager.preview(25)
        with closing(fixture.store._connect()) as connection:
            connection.execute(
                "INSERT INTO desktop_ollama_classification_jobs "
                "(id,confirmation_hash,status,requested_limit,batch_limit,remaining,model,digest,"
                "quantization,endpoint,algorithm_version,preflight_json,report_json,"
                "created_at,updated_at) "
                "VALUES ('00000000-0000-0000-0000-000000000456','occupied','running',"
                "25,1,1,?,?,?,?,?,'{}','{}','now','now')",
                (
                    DESKTOP_OLLAMA_MODEL,
                    DESKTOP_OLLAMA_DIGEST,
                    DESKTOP_OLLAMA_QUANTIZATION,
                    DESKTOP_OLLAMA_ENDPOINT,
                    "desktop-qwen8b-classification-v1",
                ),
            )
            connection.commit()

        with self.assertRaisesRegex(RuntimeError, "já existe"):
            manager.start(preview["confirmationToken"], 25)
        self.assertEqual(admin.chat_calls, 0)

    def test_restart_changes_active_job_to_paused_without_changing_run_id(self) -> None:
        fixture, _ = _seed_canonical(self.root)
        admin = FakeAdmin()
        manager = self._manager(fixture, admin)
        preview = manager.preview(25)
        token_hash = __import__("hashlib").sha256(
            preview["confirmationToken"].encode("utf-8")
        ).hexdigest()
        run_id = "00000000-0000-0000-0000-000000000123"
        with closing(fixture.store._connect()) as connection:
            connection.execute(
                "INSERT INTO desktop_ollama_classification_jobs "
                "(id,confirmation_hash,status,requested_limit,batch_limit,remaining,model,digest,"
                "quantization,endpoint,algorithm_version,preflight_json,report_json,"
                "created_at,updated_at) "
                "VALUES (?,?,'running',25,1,1,?,?,?,?,?,'{}','{}','now','now')",
                (
                    run_id,
                    token_hash,
                    DESKTOP_OLLAMA_MODEL,
                    DESKTOP_OLLAMA_DIGEST,
                    DESKTOP_OLLAMA_QUANTIZATION,
                    DESKTOP_OLLAMA_ENDPOINT,
                    "desktop-qwen8b-classification-v1",
                ),
            )
            connection.commit()

        restarted = self._manager(fixture, admin)
        status = restarted.status(run_id)

        self.assertEqual(status["runId"], run_id)
        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["pauseReason"], "aplicativo reiniciado")

    def test_packaged_ui_contains_preview_progress_pause_and_resume_controls(self) -> None:
        package = resources.files("kad_collector")
        html = package.joinpath("desktop_ui.html").read_text(encoding="utf-8")
        javascript = package.joinpath("desktop_app.js").read_text(encoding="utf-8")

        for control_id in (
            "qwen-classify-open",
            "qwen-classification-dialog",
            "qwen-classification-limit",
            "qwen-job-strip",
            "qwen-job-pause",
            "qwen-job-resume",
        ):
            self.assertIn(f'id="{control_id}"', html)
        for endpoint in ("preview", "start", "status", "pause", "resume"):
            self.assertIn(endpoint, javascript)
        self.assertIn("preview.warning", javascript)

    def test_local_ai_endpoints_require_desktop_token(self) -> None:
        application = DesktopApplication(self.root / "desktop")
        application.ollama_classification = DesktopOllamaClassificationManager(
            application.store,
            admin_factory=FakeAdmin,
            provider_factory=lambda: FakeProvider(_level_decision()),
            command_runner=_gpu_runner,
        )
        server, thread, url = start_desktop_server(application)
        try:
            with self.assertRaises(HTTPError) as get_error:
                urlopen(f"{url}api/local-ai/classification/status", timeout=3)
            self.assertEqual(get_error.exception.code, HTTPStatus.FORBIDDEN)
            request = Request(
                f"{url}api/local-ai/classification/preview",
                data=b'{"limit":25}',
                method="POST",
                headers={"Content-Type": "application/json", "Origin": url.rstrip("/")},
            )
            with self.assertRaises(HTTPError) as post_error:
                urlopen(request, timeout=3)
            self.assertEqual(post_error.exception.code, HTTPStatus.FORBIDDEN)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
