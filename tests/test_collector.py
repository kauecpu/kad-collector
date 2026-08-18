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
    select_pagination_links,
)
from kad_collector.config import ConfigError, config_for_urls, load_config
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

    def test_direct_pdf_is_collected_as_exam_and_duplicate_content_is_removed(self) -> None:
        first_url = "https://provas.example.gov.br/arquivo-1.pdf"
        second_url = "https://provas.example.gov.br/arquivo-2.pdf"
        pdf_body = b"%PDF-1.4\nfixture local\n%%EOF"

        class FixtureClient:
            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                pass

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                headers = Message()
                if url.endswith("/robots.txt"):
                    headers["Content-Type"] = "text/plain; charset=utf-8"
                    return HttpResult(
                        url=url,
                        status_code=200,
                        headers=headers,
                        body=b"User-agent: *\nAllow: /\n",
                    )
                headers["Content-Type"] = "application/pdf"
                return HttpResult(
                    url=url,
                    status_code=200,
                    headers=headers,
                    body=pdf_body,
                )

        with tempfile.TemporaryDirectory() as temporary:
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source_definition(start_urls=[first_url, second_url])],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)

        self.assertEqual(len(manifest.documents), 1)
        self.assertEqual(manifest.documents[0].document_type, "exam")
        self.assertEqual(manifest.duplicate_documents, 1)

    def test_static_pagination_follows_allowed_links_and_stops_at_limit(self) -> None:
        first_page = "https://provas.example.gov.br/lista?page=1"
        second_page = "https://provas.example.gov.br/lista?page=2"
        third_page = "https://provas.example.gov.br/lista?page=3"

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
                elif url == first_page:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = b'<a href="/prova-1.pdf">Prova 1</a><a href="?page=2">Proxima pagina</a>'
                elif url == second_page:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = (
                        b'<a href="/gabarito-1.pdf">Gabarito 1</a>'
                        b'<a href="?page=3">Proxima pagina</a>'
                    )
                elif url.endswith(".pdf"):
                    headers["Content-Type"] = "application/pdf"
                    body = f"%PDF-1.4\n{url}\n%%EOF".encode()
                else:
                    raise AssertionError(f"URL inesperada: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

        with tempfile.TemporaryDirectory() as temporary:
            source = source_definition(
                start_urls=[first_page],
                include_patterns=[r"(?i)\.pdf(?:$|\?)"],
                pagination_patterns=[r"(?i)page=\d+"],
                max_pages_per_run=2,
            )
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)

        self.assertEqual(len(manifest.documents), 2)
        self.assertNotIn(third_page, FixtureClient.requested)
        self.assertTrue(any("paginacao limitada a 2 paginas" in item for item in manifest.warnings))


class SecurityTests(unittest.TestCase):
    def test_professional_collection_settings_accept_unbounded_file_count(self) -> None:
        settings = CollectorSettings(
            capacity_profile="high_performance",
            request_interval_seconds=0,
            max_files_per_source=None,
            max_concurrency=8,
        )
        self.assertIsNone(settings.max_files_per_source)
        self.assertEqual(settings.max_concurrency, 8)

    def test_browser_strategy_requires_explicit_source_enablement(self) -> None:
        with self.assertRaisesRegex(ValueError, "browser_enabled"):
            source_definition(discovery_strategies=["html", "browser"])
        source = source_definition(discovery_strategies=["html", "browser"], browser_enabled=True)
        self.assertTrue(source.browser_enabled)

    def test_official_configuration_registers_authorized_sources(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "sources.official.toml")
        self.assertEqual(
            {source.id for source in config.sources},
            {
                "fuvest_vestibular",
                "coperve_ufsc_2026",
                "fgv_conhecimento",
                "inep_enem",
                "inep_enade",
                "inep_encceja",
                "inep_revalida",
                "comvest_unicamp",
                "obmep_referencias",
                "uerj_vestibular",
            },
        )
        self.assertTrue(all(source.enabled for source in config.sources))
        obmep = next(source for source in config.sources if source.id == "obmep_referencias")
        self.assertEqual(obmep.access_mode, "reference_only")
        fuvest = next(source for source in config.sources if source.id == "fuvest_vestibular")
        base = "https://www.fuvest.br/wp-content/uploads/"
        html = (
            f'<a href="{base}fuvest2026-fase1-prova-V1.pdf">Prova 2026 V1</a>'
            f'<a href="{base}fuvest2026-fase1-gabarito.pdf">Gabarito 2026</a>'
            f'<a href="{base}fuvest2025_primeira_fase_prova_V1.pdf">Prova 2025 V1</a>'
            f'<a href="{base}fuvest2025_gabarito_primeira_fase.pdf">Gabarito 2025</a>'
            f'<a href="{base}fuvest2025_guia-provas.pdf">Guia</a>'
        )
        selected = select_document_links(html, fuvest.start_urls[0], fuvest)
        self.assertEqual(len(selected), 4)
        self.assertEqual(
            [item[2] for item in selected],
            ["exam", "answer_key", "exam", "answer_key"],
        )

        fgv = next(source for source in config.sources if source.id == "fgv_conhecimento")
        fgv_html = (FIXTURES / "fgv_concurso.html").read_text(encoding="utf-8")
        fgv_selected = select_document_links(fgv_html, fgv.start_urls[0], fgv)
        self.assertEqual(
            [(Path(item[0]).name, item[2]) for item in fgv_selected],
            [
                ("auditor-fiscal-frb100-tipo-1.pdf", "exam"),
                ("gabdef_cf.pdf", "answer_key"),
            ],
        )
        self.assertEqual(fgv.max_pages_per_run, 40)
        fgv_index = (FIXTURES / "fgv_index.html").read_text(encoding="utf-8")
        self.assertEqual(
            select_pagination_links(fgv_index, fgv.start_urls[0], fgv),
            [
                "https://conhecimento.fgv.br/concursos/rfb22",
                "https://conhecimento.fgv.br/concursos/senado22",
            ],
        )

    def test_packaged_official_configuration_matches_cli_configuration(self) -> None:
        packaged = load_config(PROJECT_ROOT / "src" / "kad_collector" / "sources.official.toml")
        configured = load_config(PROJECT_ROOT / "config" / "sources.official.toml")
        self.assertEqual(packaged, configured)

    def test_guided_configuration_limits_collection_to_one_exam_and_key(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "sources.test.toml")
        self.assertEqual(len(config.sources), 1)
        self.assertEqual(config.collector.max_files_per_source, 2)
        source = config.sources[0]
        html = (
            '<a href="/wp-content/fuvest2026-fase1-prova-V1.pdf">Prova V1</a>'
            '<a href="/wp-content/fuvest2026-fase1-prova-V2.pdf">Prova V2</a>'
            '<a href="/wp-content/fuvest2026-fase1-gabarito.pdf">Gabarito</a>'
        )

        selected = select_document_links(html, source.start_urls[0], source)

        self.assertEqual([item[2] for item in selected], ["exam", "answer_key"])

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

    def test_explicit_subdomain_pattern_does_not_allow_apex_or_other_domains(self) -> None:
        self.assertEqual(
            validate_public_url(
                "https://arquivos.provas.example.gov.br/prova.pdf",
                ["*.provas.example.gov.br"],
                resolve_dns=False,
            ),
            "https://arquivos.provas.example.gov.br/prova.pdf",
        )
        with self.assertRaises(UnsafeUrlError):
            validate_public_url(
                "https://provas.example.gov.br/prova.pdf",
                ["*.provas.example.gov.br"],
                resolve_dns=False,
            )
        with self.assertRaises(UnsafeUrlError):
            validate_public_url(
                "https://evil-example.gov.br/prova.pdf",
                ["*.example.gov.br"],
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

    def test_ad_hoc_urls_must_belong_to_an_enabled_content_source(self) -> None:
        enabled = source_definition()
        config = AppConfig(sources=[enabled])
        selected = config_for_urls(config, ["https://provas.example.gov.br/prova-direta.pdf"])
        self.assertEqual(
            selected.sources[0].start_urls,
            ["https://provas.example.gov.br/prova-direta.pdf"],
        )

        with self.assertRaisesRegex(ConfigError, "nenhuma fonte cadastrada"):
            config_for_urls(config, ["https://nao-permitida.example/prova.pdf"])

        ambiguous = AppConfig(sources=[enabled, source_definition(id="outra_fonte")])
        with self.assertRaisesRegex(ConfigError, "mais de uma fonte"):
            config_for_urls(ambiguous, ["https://provas.example.gov.br/prova.pdf"])

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
        self.assertTrue(policy.can_fetch("https://provas.example.gov.br/publico/prova.pdf", hosts))

    def test_robots_policy_observe_records_but_does_not_block(self) -> None:
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

        policy = RobotsPolicy(  # type: ignore[arg-type]
            FixtureClient(), "KADCollector/0.1", robots_policy="observe"
        )
        self.assertTrue(
            policy.can_fetch(
                "https://provas.example.gov.br/restrito/prova.pdf",
                ["provas.example.gov.br"],
            )
        )
        self.assertTrue(any("modo observe" in item for item in policy.observations))

    def test_robots_policy_ignore_does_not_fetch_robots_file(self) -> None:
        class FailIfCalled:
            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                raise AssertionError("robots.txt nao deveria ser consultado")

        policy = RobotsPolicy(  # type: ignore[arg-type]
            FailIfCalled(), "KADCollector/0.1", robots_policy="ignore"
        )
        self.assertTrue(
            policy.can_fetch(
                "https://provas.example.gov.br/restrito/prova.pdf",
                ["provas.example.gov.br"],
            )
        )

    def test_crawl_delay_observe_records_but_does_not_wait(self) -> None:
        class FixtureClient:
            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                headers = Message()
                headers["Content-Type"] = "text/plain; charset=utf-8"
                return HttpResult(
                    url=url,
                    status_code=200,
                    headers=headers,
                    body=b"User-agent: *\nCrawl-delay: 7\nAllow: /\n",
                )

        policy = RobotsPolicy(  # type: ignore[arg-type]
            FixtureClient(), "KADCollector/0.1", crawl_delay_policy="observe"
        )
        url = "https://provas.example.gov.br/publico/prova.pdf"
        self.assertTrue(policy.can_fetch(url, ["provas.example.gov.br"]))
        self.assertIsNone(policy.crawl_delay(url))
        self.assertTrue(any("7s observado" in item for item in policy.observations))


if __name__ == "__main__":
    unittest.main()
