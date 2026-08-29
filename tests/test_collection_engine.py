from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx

from kad_collector.collection_state import CollectionStateStore
from kad_collector.collection_transport import CollectionHttpClient
from kad_collector.collector import extract_links
from kad_collector.discovery import (
    detect_access_challenge,
    parse_feed,
    parse_json_links,
    parse_sitemap,
)
from kad_collector.models import JsonDiscoveryEndpoint
from kad_collector.security import FetchError
from kad_collector.url_utils import canonicalize_url


class FakeScraplingSession:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = responses
        self.entered = 0
        self.exited = 0
        self.fetches: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> FakeScraplingSession:
        self.entered += 1
        return self

    def __exit__(self, *_args: object) -> None:
        self.exited += 1

    def fetch(self, url: str, **options: object) -> SimpleNamespace:
        self.fetches.append((url, options))
        return self.responses.pop(0)


class CollectionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.state = CollectionStateStore(self.root / "engine.sqlite3")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def client(
        self,
        handler: httpx.MockTransport,
        *,
        max_retries: int = 2,
        development_cache: bool = False,
        page_transport: str = "http",
        scrapling_session_factory: Any = None,
        cloudflare_bypass_enabled: bool = True,
    ) -> CollectionHttpClient:
        client = CollectionHttpClient(
            user_agent="KADCollector/Test",
            timeout=2,
            connect_timeout=1,
            interval_seconds=0,
            max_concurrency=4,
            max_retries=max_retries,
            retry_max_delay_seconds=0.01,
            state_store=self.state,
            run_id="run-test",
            source_id="source-test",
            conditional_cache=True,
            disk_quota_bytes=10_000_000,
            development_cache=development_cache,
            random_source=random.Random(1),
            page_transport=page_transport,
            scrapling_session_factory=scrapling_session_factory,
            cloudflare_bypass_enabled=cloudflare_bypass_enabled,
        )
        client.client.close()
        client.client = httpx.Client(transport=handler, follow_redirects=False)
        return client

    def test_scrapling_session_is_reused_and_preserves_response_contract(self) -> None:
        payloads = [self.fixture("ok.html"), b"<html><body>segunda</body></html>"]
        session = FakeScraplingSession(
            [
                SimpleNamespace(
                    url=f"https://example.test/page/{index}",
                    status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=payload,
                )
                for index, payload in enumerate(payloads, start=1)
            ]
        )
        factory_options: list[dict[str, object]] = []

        def factory(**options: object) -> FakeScraplingSession:
            factory_options.append(options)
            return session

        def unexpected_http(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("httpx nao deveria carregar paginas HTML desta fonte")

        client = self.client(
            httpx.MockTransport(unexpected_http),
            page_transport="scrapling",
            scrapling_session_factory=factory,
        )
        with patch(
            "kad_collector.collection_transport.validate_public_url",
            side_effect=lambda url, _hosts: url,
        ):
            first = client.get("https://example.test/page/1", ["example.test"], 10_000)
            second = client.get("https://example.test/page/2", ["example.test"], 10_000)
        client.close()

        self.assertEqual(first.body, payloads[0])
        self.assertEqual(second.body, payloads[1])
        self.assertEqual(first.headers.get_content_type(), "text/html")
        self.assertEqual(first.original_url, "https://example.test/page/1")
        self.assertEqual(first.canonical_url, "https://example.test/page/1")
        self.assertEqual(session.entered, 1)
        self.assertEqual(session.exited, 1)
        self.assertEqual(len(session.fetches), 2)
        self.assertEqual(len(factory_options), 1)
        self.assertIs(factory_options[0]["real_chrome"], True)
        self.assertIs(factory_options[0]["solve_cloudflare"], True)
        self.assertFalse(session.fetches[0][1]["google_search"])

    def test_cloudflare_bypass_disabled_reaches_scrapling_session_and_skips_retry(self) -> None:
        # Toggle "Usar bypass Cloudflare" desligado no app: solve_cloudflare=False
        # chega ate o PersistentScraplingSession, que faz uma unica tentativa
        # mesmo diante de um 403 (sem fallback headful) -- ao contrario do caso
        # com bypass ligado (test_scrapling_403_keeps_existing_access_denied_handling),
        # que consome duas chamadas de fetch internamente.
        blocked = SimpleNamespace(
            url="https://example.test/denied",
            status=403,
            headers={"Content-Type": "text/html"},
            body=b"denied",
        )
        session = FakeScraplingSession([blocked])
        factory_options: list[dict[str, object]] = []

        def factory(**options: object) -> FakeScraplingSession:
            factory_options.append(options)
            return session

        client = self.client(
            httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
            page_transport="scrapling",
            scrapling_session_factory=factory,
            cloudflare_bypass_enabled=False,
        )
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda url, _hosts: url,
            ),
            self.assertRaises(FetchError) as raised,
        ):
            client.get("https://example.test/denied", ["example.test"], 10_000)
        client.close()

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(len(factory_options), 1)
        self.assertIs(factory_options[0]["solve_cloudflare"], False)
        self.assertEqual(len(session.fetches), 1)

    def test_scrapling_403_keeps_existing_access_denied_handling(self) -> None:
        # scrapling_transport.PersistentScraplingSession agora tenta de novo com
        # headless=False quando a primeira tentativa (headless=True) volta com 403;
        # aqui a segunda tentativa tambem bate em 403, entao o resultado final
        # continua sendo tratado como access_denied por este cliente.
        session = FakeScraplingSession(
            [
                SimpleNamespace(
                    url="https://example.test/denied",
                    status=403,
                    headers={"Content-Type": "text/html"},
                    body=b"denied",
                ),
                SimpleNamespace(
                    url="https://example.test/denied",
                    status=403,
                    headers={"Content-Type": "text/html"},
                    body=b"denied",
                ),
            ]
        )
        client = self.client(
            httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
            page_transport="scrapling",
            scrapling_session_factory=lambda **_options: session,
        )
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda url, _hosts: url,
            ),
            self.assertRaises(FetchError) as raised,
        ):
            client.get("https://example.test/denied", ["example.test"], 10_000)
        client.close()

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(len(session.fetches), 2)
        self.assertEqual(self.state.events("run-test")[-1].outcome, "access_denied")

    def test_scrapling_429_uses_existing_retry_after_policy(self) -> None:
        session = FakeScraplingSession(
            [
                SimpleNamespace(
                    url="https://example.test/busy",
                    status=429,
                    headers={"Retry-After": "0", "Content-Type": "text/html"},
                    body=b"busy",
                ),
                SimpleNamespace(
                    url="https://example.test/busy",
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body=b"ok",
                ),
            ]
        )
        client = self.client(
            httpx.MockTransport(lambda request: httpx.Response(500, request=request)),
            page_transport="scrapling",
            scrapling_session_factory=lambda **_options: session,
        )
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda url, _hosts: url,
            ),
            patch("kad_collector.collection_transport.time.sleep") as sleep,
        ):
            result = client.get("https://example.test/busy", ["example.test"], 10_000)
        client.close()

        self.assertEqual(result.body, b"ok")
        self.assertEqual(len(session.fetches), 2)
        sleep.assert_called_once_with(0.0)

    def test_non_html_strategies_keep_using_existing_http_client(self) -> None:
        session = FakeScraplingSession([])

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b'{"items": []}',
                headers={"Content-Type": "application/json"},
                request=request,
            )

        client = self.client(
            httpx.MockTransport(respond),
            page_transport="scrapling",
            scrapling_session_factory=lambda **_options: session,
        )
        with patch(
            "kad_collector.collection_transport.validate_public_url",
            side_effect=lambda url, _hosts: url,
        ):
            result = client.get(
                "https://example.test/api",
                ["example.test"],
                10_000,
                strategy="json",
            )
        client.close()

        self.assertEqual(result.body, b'{"items": []}')
        self.assertEqual(session.entered, 0)
        self.assertEqual(session.fetches, [])

    @staticmethod
    def fixture(name: str) -> bytes:
        return (Path(__file__).parent / "fixtures" / "transport" / name).read_bytes()

    def test_200_processes_local_fixture(self) -> None:
        payload = self.fixture("ok.html")

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=payload,
                headers={"Content-Type": "text/html"},
                request=request,
            )

        client = self.client(httpx.MockTransport(respond))
        with patch(
            "kad_collector.collection_transport.validate_public_url",
            side_effect=lambda url, _hosts: url,
        ):
            result = client.get("https://example.test/page", ["example.test"], 10_000)
        client.close()

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, payload)
        self.assertEqual(result.original_url, "https://example.test/page")
        self.assertEqual(result.canonical_url, "https://example.test/page")

    def test_403_is_not_retried_and_is_registered_as_access_denied(self) -> None:
        attempts = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, request=request)

        client = self.client(httpx.MockTransport(respond))
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda url, _hosts: url,
            ),
            self.assertRaises(FetchError) as raised,
        ):
            client.get("https://example.test/denied", ["example.test"], 1_000)
        client.close()

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(attempts, 1)
        events = self.state.events("run-test")
        self.assertEqual(events[-1].outcome, "access_denied")
        self.assertEqual(events[-1].status_code, 403)

    def test_retry_after_retries_429_and_records_attempt(self) -> None:
        attempts = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
            return httpx.Response(200, text="ok", request=request)

        client = self.client(httpx.MockTransport(respond))
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda url, _hosts: url,
            ),
            patch("kad_collector.collection_transport.time.sleep") as sleep,
        ):
            result = client.get(
                "https://example.test/page",
                ["example.test"],
                1_000,
                strategy="html",
            )
        client.close()

        self.assertEqual(result.body, b"ok")
        self.assertEqual(result.attempt, 2)
        self.assertEqual(attempts, 2)
        sleep.assert_called_once_with(0.0)

    def test_retryable_http_and_network_errors_use_bounded_retries(self) -> None:
        for first_response in (408, 425, 500, 599, "network"):
            with self.subTest(first_response=first_response):
                attempts = 0

                def respond(
                    request: httpx.Request,
                    response_fixture: int | str = first_response,
                ) -> httpx.Response:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        if response_fixture == "network":
                            raise httpx.ConnectError("fixture offline", request=request)
                        assert isinstance(response_fixture, int)
                        return httpx.Response(response_fixture, request=request)
                    return httpx.Response(200, content=b"ok", request=request)

                client = self.client(httpx.MockTransport(respond))
                with (
                    patch(
                        "kad_collector.collection_transport.validate_public_url",
                        side_effect=lambda url, _hosts: url,
                    ),
                    patch("kad_collector.collection_transport.time.sleep"),
                ):
                    result = client.get(
                        f"https://example.test/{first_response}", ["example.test"], 1_000
                    )
                client.close()
                self.assertEqual(result.body, b"ok")
                self.assertEqual(attempts, 2)

    def test_retry_exhaustion_stops_after_configured_attempts(self) -> None:
        attempts = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, request=request)

        client = self.client(httpx.MockTransport(respond), max_retries=2)
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda url, _hosts: url,
            ),
            patch("kad_collector.collection_transport.time.sleep"),
            self.assertRaises(FetchError) as raised,
        ):
            client.get("https://example.test/unavailable", ["example.test"], 1_000)
        client.close()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(attempts, 3)
        self.assertEqual(self.state.events("run-test")[-1].outcome, "retry_exhausted")

    def test_conditional_cache_reuses_verified_body_after_304(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.headers.get("If-None-Match") == '"fixture"':
                return httpx.Response(304, request=request)
            return httpx.Response(
                200,
                content=b"conteudo estavel",
                headers={"ETag": '"fixture"', "Content-Type": "text/html"},
                request=request,
            )

        client = self.client(httpx.MockTransport(respond))
        with patch(
            "kad_collector.collection_transport.validate_public_url",
            side_effect=lambda url, _hosts: url,
        ):
            first = client.get(
                "https://example.test/page",
                ["example.test"],
                1_000,
                strategy="html",
            )
            client.remember_body(
                original_url="https://example.test/page",
                result=first,
                cache_dir=self.root / "cache",
                strategy="html",
            )
            second = client.get(
                "https://example.test/page",
                ["example.test"],
                1_000,
                strategy="html",
            )
        client.close()

        self.assertEqual(second.body, b"conteudo estavel")
        self.assertEqual(second.cache_status, "revalidated")
        self.assertEqual(len(requests), 2)

    def test_url_canonicalization_preserves_resource_query_and_removes_tracking(self) -> None:
        original = (
            "HTTPS://Example.Test:443//provas/arquivo.pdf?utm_source=newsletter&"
            "version=2&download=1&fbclid=tracking#pagina-2"
        )
        self.assertEqual(
            canonicalize_url(original),
            "https://example.test/provas/arquivo.pdf?download=1&version=2",
        )

    def test_development_cache_replays_by_canonical_url_without_network(self) -> None:
        original = "https://example.test/page?item=1&utm_source=primeira#topo"
        equivalent = "https://EXAMPLE.TEST:443/page?utm_medium=email&item=1#rodape"
        calls = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                content=self.fixture("ok.html"),
                headers={"Content-Type": "text/html"},
                request=request,
            )

        client = self.client(httpx.MockTransport(respond), development_cache=True)
        with patch(
            "kad_collector.collection_transport.validate_public_url",
            side_effect=lambda url, _hosts: url,
        ):
            first = client.get(original, ["example.test"], 10_000)
            client.remember_body(
                original_url=original,
                result=first,
                cache_dir=self.root / "cache",
                strategy="html",
            )
            second = client.get(equivalent, ["example.test"], 10_000)
        client.close()

        self.assertEqual(calls, 1)
        self.assertEqual(second.cache_status, "hit")
        self.assertEqual(second.body, self.fixture("ok.html"))
        entry = self.state.cache_entry(equivalent)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["url"], "https://example.test/page?item=1")
        self.assertEqual(entry["original_url"], original)
        self.assertEqual(self.state.cache_summary()["entries"], 1)

    def test_state_store_migrates_v1_cache_to_canonical_identity(self) -> None:
        legacy_path = self.root / "legacy.sqlite3"
        cached = self.root / "legacy-body"
        cached.write_bytes(b"legacy")
        original = "https://EXAMPLE.TEST:443/page?utm_source=old&item=1#top"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE engine_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO engine_meta(key, value) VALUES ('schema_version', '1');
                CREATE TABLE http_cache (
                    url TEXT PRIMARY KEY, final_url TEXT NOT NULL, etag TEXT,
                    last_modified TEXT, sha256 TEXT NOT NULL, content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, local_path TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL, checked_at TEXT NOT NULL,
                    status_code INTEGER NOT NULL, strategy TEXT NOT NULL
                );
                CREATE TABLE collection_checkpoints (
                    checkpoint_key TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                    status TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE collection_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT NOT NULL,
                    strategy TEXT NOT NULL, outcome TEXT NOT NULL, status_code INTEGER,
                    duration_ms INTEGER NOT NULL, bytes_received INTEGER NOT NULL,
                    attempt INTEGER NOT NULL, wait_seconds REAL NOT NULL,
                    cache_status TEXT NOT NULL, detail TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO http_cache VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    original,
                    original,
                    hashlib.sha256(b"legacy").hexdigest(),
                    "text/html",
                    6,
                    str(cached),
                    "2026-08-29T00:00:00+00:00",
                    "2026-08-29T00:00:00+00:00",
                    200,
                    "html",
                ),
            )
            connection.commit()

        migrated = CollectionStateStore(legacy_path)
        entry = migrated.cache_entry("https://example.test/page?item=1")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["url"], "https://example.test/page?item=1")
        self.assertEqual(entry["original_url"], original)
        with closing(sqlite3.connect(legacy_path)) as connection:
            version = connection.execute(
                "SELECT value FROM engine_meta WHERE key = 'schema_version'"
            ).fetchone()
        self.assertEqual(version, ("2",))

    def test_local_challenge_fixture_requires_manual_action(self) -> None:
        html = self.fixture("challenge.html").decode("utf-8")
        self.assertEqual(
            detect_access_challenge("Verificação de segurança", html, "https://example.test"),
            "captcha",
        )

    def test_range_download_resumes_partial_file(self) -> None:
        url = "https://example.test/prova.pdf"
        payload = b"%PDF-1.4\nfixture\n%%EOF"
        token = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        partial = self.root / f".{token}.part"
        partial.write_bytes(payload[:8])

        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Range"], "bytes=8-")
            return httpx.Response(
                206,
                content=payload[8:],
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Range": f"bytes 8-{len(payload) - 1}/{len(payload)}",
                },
                request=request,
            )

        client = self.client(httpx.MockTransport(respond))
        with patch(
            "kad_collector.collection_transport.validate_public_url",
            side_effect=lambda value, _hosts: value,
        ):
            result = client.download(
                url,
                ["example.test"],
                1_000,
                self.root,
                strategy="download",
            )
        client.close()

        self.assertTrue(result.resumed)
        self.assertEqual(result.path.read_bytes(), payload)
        self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())

    def test_range_download_rejects_mismatched_content_range(self) -> None:
        url = "https://example.test/prova.pdf"
        token = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        (self.root / f".{token}.part").write_bytes(b"partial")

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                206,
                content=b"wrong",
                headers={"Content-Range": "bytes 0-4/12"},
                request=request,
            )

        client = self.client(httpx.MockTransport(respond))
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda value, _hosts: value,
            ),
            self.assertRaisesRegex(Exception, "Content-Range invalido"),
        ):
            client.download(
                url,
                ["example.test"],
                1_000,
                self.root,
                strategy="download",
            )
        client.close()

    def test_checkpoint_round_trip_survives_new_store_instance(self) -> None:
        self.state.save_checkpoint(
            "checkpoint",
            "source",
            "paused",
            {"pending_pages": ["https://example.test/2"]},
        )
        reopened = CollectionStateStore(self.root / "engine.sqlite3")
        self.assertEqual(
            reopened.load_checkpoint("checkpoint"),
            {
                "status": "paused",
                "payload": {"pending_pages": ["https://example.test/2"]},
            },
        )

    def test_sitemap_feed_and_json_discovery(self) -> None:
        sitemap_urls, children = parse_sitemap(
            b"<urlset><url><loc>/prova.pdf</loc></url></urlset>",
            "https://example.test/sitemap.xml",
            max_bytes=10_000,
        )
        self.assertEqual(sitemap_urls, ["https://example.test/prova.pdf"])
        self.assertEqual(children, [])

        feed = parse_feed(
            b"<rss><channel><item><title>Prova</title><enclosure url='/p.pdf'/></item>"
            b"</channel></rss>",
            "https://example.test/feed.xml",
        )
        self.assertEqual(feed[0].url, "https://example.test/p.pdf")

        endpoint = JsonDiscoveryEndpoint(
            url="https://example.test/api",
            items_path="data.items",
            url_field="download",
            title_field="name",
            next_page_field="data.next",
        )
        links, next_page = parse_json_links(
            json.dumps(
                {
                    "data": {
                        "items": [{"download": "/q.pdf", "name": "Questões"}],
                        "next": "/api?page=2",
                    }
                }
            ).encode(),
            endpoint.url,
            endpoint,
        )
        self.assertEqual(links[0].url, "https://example.test/q.pdf")
        self.assertEqual(next_page, "https://example.test/api?page=2")

    def test_static_parser_ignores_hidden_links(self) -> None:
        html = (
            '<a href="/visible.pdf">Visível</a>'
            '<a hidden href="/hidden.pdf">Oculto</a>'
            '<a style="display:none" href="/trap.pdf">Armadilha</a>'
        )
        self.assertEqual(
            extract_links(html, "https://example.test/"),
            [("https://example.test/visible.pdf", "Visível")],
        )

    def test_host_scheduler_is_thread_safe(self) -> None:
        calls: list[int] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(threading.get_ident())
            return httpx.Response(200, text="ok", request=request)

        client = self.client(httpx.MockTransport(respond))

        def fetch() -> None:
            with patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda url, _hosts: url,
            ):
                client.get(
                    "https://example.test/page",
                    ["example.test"],
                    1_000,
                    strategy="html",
                )

        threads = [threading.Thread(target=fetch) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        client.close()
        self.assertEqual(len(calls), 4)


    def test_download_falls_back_to_scrapling_when_turnstile_html_is_returned(self) -> None:
        url = "https://pci.example.test/prova.pdf"
        pdf_payload = b"%PDF-1.4\nresolvido-pelo-solver\n%%EOF"
        challenge_html = (
            "<html><title>Just a moment...</title>"
            '<div class="cf-turnstile"></div></html>'
        ).encode("utf-8")

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=challenge_html,
                headers={"Content-Type": "text/html; charset=utf-8"},
                request=request,
            )

        session = FakeScraplingSession(
            [
                SimpleNamespace(
                    url=url,
                    status=200,
                    headers={"Content-Type": "application/pdf"},
                    body=pdf_payload,
                )
            ]
        )

        def factory(**options: object) -> FakeScraplingSession:
            return session

        client = self.client(
            httpx.MockTransport(respond),
            page_transport="scrapling",
            scrapling_session_factory=factory,
        )
        with patch(
            "kad_collector.collection_transport.validate_public_url",
            side_effect=lambda value, _hosts: value,
        ):
            result = client.download(
                url,
                ["pci.example.test"],
                1_000,
                self.root,
                strategy="download",
            )
        client.close()

        self.assertEqual(result.path.read_bytes(), pdf_payload)
        self.assertEqual(result.sha256, hashlib.sha256(pdf_payload).hexdigest())
        self.assertEqual(len(session.fetches), 1)

    def test_download_reports_clear_error_when_scrapling_also_blocked(self) -> None:
        url = "https://pci.example.test/prova.pdf"
        challenge_html = (
            "<html><title>Just a moment...</title>"
            '<div class="cf-turnstile"></div></html>'
        ).encode("utf-8")

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=challenge_html,
                headers={"Content-Type": "text/html; charset=utf-8"},
                request=request,
            )

        # PersistentScraplingSession agora tenta headless=True e depois
        # headless=False internamente antes de devolver a resposta; como as duas
        # tentativas continuam bloqueadas aqui, sao necessarias duas respostas na
        # fila para simular o desafio persistindo em ambos os modos.
        session = FakeScraplingSession(
            [
                SimpleNamespace(
                    url=url,
                    status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=challenge_html,
                ),
                SimpleNamespace(
                    url=url,
                    status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    body=challenge_html,
                ),
            ]
        )

        def factory(**options: object) -> FakeScraplingSession:
            return session

        client = self.client(
            httpx.MockTransport(respond),
            page_transport="scrapling",
            scrapling_session_factory=factory,
        )
        with (
            patch(
                "kad_collector.collection_transport.validate_public_url",
                side_effect=lambda value, _hosts: value,
            ),
            self.assertRaisesRegex(FetchError, "solve_cloudflare"),
        ):
            client.download(
                url,
                ["pci.example.test"],
                1_000,
                self.root,
                strategy="download",
            )
        client.close()



if __name__ == "__main__":
    unittest.main()
