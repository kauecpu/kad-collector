from __future__ import annotations

import hashlib
import json
import random
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from kad_collector.collection_state import CollectionStateStore
from kad_collector.collection_transport import CollectionHttpClient
from kad_collector.collector import extract_links
from kad_collector.discovery import parse_feed, parse_json_links, parse_sitemap
from kad_collector.models import JsonDiscoveryEndpoint


class CollectionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.state = CollectionStateStore(self.root / "engine.sqlite3")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def client(self, handler: httpx.MockTransport) -> CollectionHttpClient:
        client = CollectionHttpClient(
            user_agent="KADCollector/Test",
            timeout=2,
            connect_timeout=1,
            interval_seconds=0,
            max_concurrency=4,
            max_retries=2,
            retry_max_delay_seconds=0.01,
            state_store=self.state,
            run_id="run-test",
            source_id="source-test",
            conditional_cache=True,
            disk_quota_bytes=10_000_000,
            random_source=random.Random(1),
        )
        client.client.close()
        client.client = httpx.Client(transport=handler, follow_redirects=False)
        return client

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
            patch("kad_collector.collection_transport.time.sleep"),
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


if __name__ == "__main__":
    unittest.main()
