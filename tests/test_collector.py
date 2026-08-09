from __future__ import annotations

import gzip
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from kad_collector.collector import (
    RobotsPolicy,
    classify_document,
    collect_documents,
    extract_links,
    select_document_links,
)
from kad_collector.config import ConfigError, load_config
from kad_collector.filters import document_might_match_filters
from kad_collector.models import (
    AppConfig,
    CollectionFilters,
    CollectorSettings,
    SourceDefinition,
)
from kad_collector.security import (
    FetchError,
    HttpResult,
    SafeHttpClient,
    UnsafeUrlError,
    validate_public_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parents[1]


def source_definition(**changes: object) -> SourceDefinition:
    data: dict[str, object] = {
        "id": "orgao_teste",
        "name": "Orgao de teste",
        "enabled": True,
        "start_urls": ["https://provas.example.gov.br/lista"],
        "allowed_hosts": ["provas.example.gov.br"],
        "include_patterns": [r"(?i)prova", r"(?i)gabarito", r"(?i)\.pdf(?:$|\?)"],
        "exclude_patterns": [r"(?i)edital"],
        "exam_patterns": [r"(?i)prova|caderno"],
        "answer_key_patterns": [r"(?i)gabarito"],
        "authorization_basis": "Fonte oficial conferida para o teste.",
    }
    data.update(changes)
    return SourceDefinition.model_validate(data)


class LinkParsingTests(unittest.TestCase):
    def test_extracts_nested_anchor_text(self) -> None:
        html = '<a href="/prova.pdf"><strong>Prova</strong> objetiva</a>'
        self.assertEqual(
            extract_links(html, "https://provas.example.gov.br/lista"),
            [("https://provas.example.gov.br/prova.pdf", "Prova objetiva")],
        )

    def test_selects_only_allowed_non_excluded_documents(self) -> None:
        html = (FIXTURES / "source_page.html").read_text(encoding="utf-8")
        links = select_document_links(
            html, "https://provas.example.gov.br/lista", source_definition()
        )
        self.assertEqual([item[2] for item in links], ["exam", "answer_key"])
        self.assertTrue(all("provas.example.gov.br" in item[0] for item in links))

    def test_answer_key_has_priority_when_classifying(self) -> None:
        source = source_definition()
        kind = classify_document(
            "https://provas.example.gov.br/prova-gabarito.pdf", "Gabarito da prova", source
        )
        self.assertEqual(kind, "answer_key")

    def test_document_prefilter_rejects_known_metadata_mismatch(self) -> None:
        filters = CollectionFilters(years=[2022], boards=["FGV"])
        self.assertFalse(
            document_might_match_filters(
                "Prova 2023",
                "https://provas.example.gov.br/prova-2023.pdf",
                {"ano": "2023", "banca": "CESPE"},
                filters,
            )
        )
        self.assertTrue(
            document_might_match_filters(
                "Prova ainda sem metadados",
                "https://provas.example.gov.br/prova.pdf",
                {},
                filters,
            )
        )
        self.assertFalse(
            document_might_match_filters(
                "Prova 2023",
                "https://provas.example.gov.br/prova-2023.pdf",
                {},
                filters,
            )
        )

    def test_reference_only_records_links_without_downloading_content(self) -> None:
        page_url = "https://referencias.example.gov.br/lista"
        question_url = "https://referencias.example.gov.br/questao/123"

        class FixtureClient:
            requested: list[str] = []

            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                pass

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                self.requested.append(url)
                headers = Message()
                if url.endswith("/robots.txt"):
                    headers["Content-Type"] = "text/plain; charset=utf-8"
                    body = b"User-agent: *\nAllow: /\n"
                elif url == page_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = f'<a href="{question_url}">Questao 123</a>'.encode()
                else:
                    raise AssertionError(f"conteudo de referencia nao deveria ser baixado: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

        with tempfile.TemporaryDirectory() as temporary:
            source = source_definition(
                start_urls=[page_url],
                allowed_hosts=["referencias.example.gov.br"],
                include_patterns=[r"/questao/\d+$"],
                exclude_patterns=[],
                access_mode="reference_only",
                requires_written_authorization=True,
                written_authorization_reference="contrato-123",
            )
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)

        self.assertEqual(manifest.documents, [])
        self.assertEqual([item.url for item in manifest.references], [question_url])
        self.assertEqual(manifest.references[0].title, "123")
        self.assertNotIn(question_url, FixtureClient.requested)


class SecurityTests(unittest.TestCase):
    def test_example_commercial_sources_are_disabled_and_reference_only(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "sources.example.toml")
        commercial = {
            source.id: source
            for source in config.sources
            if source.id in {"qconcursos_referencia", "gran_questoes_referencia"}
        }
        self.assertEqual(set(commercial), {"qconcursos_referencia", "gran_questoes_referencia"})
        self.assertTrue(all(not source.enabled for source in commercial.values()))
        self.assertTrue(
            all(source.access_mode == "reference_only" for source in commercial.values())
        )
        self.assertTrue(
            all(source.requires_written_authorization for source in commercial.values())
        )

    def test_blocks_private_ip(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            validate_public_url("http://127.0.0.1/prova.pdf", ["127.0.0.1"])

    def test_blocks_embedded_credentials(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            validate_public_url(
                "https://usuario:senha@provas.example.gov.br/prova.pdf",
                ["provas.example.gov.br"],
                resolve_dns=False,
            )

    def test_rejects_enabled_source_without_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sources.toml"
            path.write_text(
                """
[collector]
[[sources]]
id = "teste"
name = "Teste"
enabled = true
start_urls = ["https://provas.example.gov.br/"]
allowed_hosts = ["provas.example.gov.br"]
authorization_basis = ""
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_rejects_commercial_source_without_written_authorization(self) -> None:
        with self.assertRaises(ValueError):
            source_definition(
                access_mode="reference_only",
                requires_written_authorization=True,
                written_authorization_reference="",
            )

    def test_gzip_expansion_respects_uncompressed_limit(self) -> None:
        compressed = gzip.compress(b"x" * 100_000)
        with self.assertRaisesRegex(FetchError, "descompactada excede"):
            SafeHttpClient._decompress_gzip_limited(compressed, 1_024)

    def test_robots_policy_blocks_disallowed_path(self) -> None:
        class FixtureClient:
            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                headers = Message()
                headers["Content-Type"] = "text/plain; charset=utf-8"
                return HttpResult(
                    url=url,
                    status_code=200,
                    headers=headers,
                    body=(FIXTURES / "robots.txt").read_bytes(),
                )

        policy = RobotsPolicy(FixtureClient(), "KADCollector/0.1")  # type: ignore[arg-type]
        hosts = ["provas.example.gov.br"]
        self.assertFalse(
            policy.can_fetch("https://provas.example.gov.br/restrito/prova.pdf", hosts)
        )
        self.assertTrue(
            policy.can_fetch("https://provas.example.gov.br/publico/prova.pdf", hosts)
        )


if __name__ == "__main__":
    unittest.main()
