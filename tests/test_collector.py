from __future__ import annotations

import gzip
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from kad_collector.collection_state import CollectionStateStore
from kad_collector.collector import (
    RobotsPolicy,
    _checkpoint_key,
    _should_expand_collection_pages,
    classify_document,
    collect_documents,
    extract_dated_link_stages,
    extract_dated_link_variants,
    extract_links,
    extract_page_metadata,
    select_collection_links,
    select_document_links,
    select_pagination_links,
)
from kad_collector.config import ConfigError, config_for_urls, load_config
from kad_collector.discovery import _looks_blocked, detect_access_challenge
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
    def test_public_page_with_login_link_is_not_marked_as_authentication(self) -> None:
        html = """
        <!doctype html><html><body>
        <nav><a href="/login">Área do candidato</a></nav>
        <a href="/prova.pdf">Prova objetiva</a>
        </body></html>
        """

        self.assertIsNone(
            _looks_blocked("Concurso público", html, "https://provas.example.gov.br/concurso")
        )
        self.assertEqual(
            _looks_blocked(
                "Entrar",
                '<form action="/login"><input type="password"></form>',
                "https://provas.example.gov.br/login",
            ),
            "login",
        )

    def test_challenge_after_twenty_kilobytes_is_detected(self) -> None:
        html = "<style>" + ("x" * 25_000) + '</style><div class="cf-turnstile"></div>'

        self.assertEqual(
            detect_access_challenge(
                "Provas",
                html,
                "https://provas.example.gov.br/concurso",
            ),
            "captcha",
        )

    def test_solved_challenge_with_public_pdf_link_is_not_blocked(self) -> None:
        html = (
            '<div class="cf-turnstile"></div>'
            '<a href="https://provas.example.gov.br/prova.pdf">Prova</a>'
        )

        self.assertIsNone(
            detect_access_challenge(
                "Provas",
                html,
                "https://provas.example.gov.br/concurso",
            )
        )

    def test_extracts_nested_anchor_text(self) -> None:
        html = '<a href="/prova.pdf"><strong>Prova</strong> objetiva</a>'
        self.assertEqual(
            extract_links(html, "https://provas.example.gov.br/lista"),
            [("https://provas.example.gov.br/prova.pdf", "Prova objetiva")],
        )

    def test_extracts_public_data_url_when_anchor_has_javascript_placeholder(self) -> None:
        html = (
            '<a href="javascript:void(0);" data-url="/prova.pdf">'
            "Compartilhar prova</a>"
        )
        self.assertEqual(
            extract_links(
                html,
                "https://provas.example.gov.br/lista",
                allow_data_url=True,
            ),
            [("https://provas.example.gov.br/prova.pdf", "Compartilhar prova")],
        )
        self.assertEqual(
            extract_links(html, "https://provas.example.gov.br/lista"),
            [("javascript:void(0);", "Compartilhar prova")],
        )

    def test_selects_only_allowed_non_excluded_documents(self) -> None:
        html = (FIXTURES / "source_page.html").read_text(encoding="utf-8")
        links = select_document_links(
            html, "https://provas.example.gov.br/lista", source_definition()
        )
        self.assertEqual([item[2] for item in links], ["exam", "answer_key"])
        self.assertTrue(all("provas.example.gov.br" in item[0] for item in links))

    def test_pci_banco_brasil_selects_pilot_proofs_and_keys(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "sources.official.toml")
        source = next(item for item in config.sources if item.id == "pci_concursos")
        html = (FIXTURES / "pci_banco_brasil.html").read_text(encoding="utf-8")
        selected = select_document_links(
            html,
            source.start_urls[0],
            source,
        )
        self.assertEqual(
            [(Path(url).name, kind) for url, _title, kind in selected],
            [
                ("banco-do-brasil-2023-escriturario-agente-comercial.pdf", "exam"),
                ("banco-do-brasil-2023-gabarito-definitivo.pdf", "answer_key"),
                ("banco-do-brasil-2021-escriturario-caderno-1.pdf", "exam"),
                ("banco-do-brasil-2021-gabarito.pdf", "answer_key"),
            ],
        )
        self.assertTrue(all("www.pciconcursos.com.br" in url for url, _title, _kind in selected))
        self.assertEqual(
            select_collection_links(html, source.start_urls[0], source),
            [
                "https://www.pciconcursos.com.br/provas/download/escriturario-agente-comercial-banco-do-brasil-cesgranrio-2023",
                "https://www.pciconcursos.com.br/provas/download/escriturario-banco-do-brasil-cesgranrio-2021",
            ],
        )

    def test_pci_banco_brasil_selects_public_data_urls_without_security_bypass(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "sources.official.toml")
        source = next(item for item in config.sources if item.id == "pci_concursos")
        html = (FIXTURES / "pci_banco_brasil_js_links.html").read_text(encoding="utf-8")

        selected = select_document_links(
            html,
            "https://www.pciconcursos.com.br/provas/download/exemplo",
            source,
        )

        self.assertEqual(
            [(Path(url).name, kind) for url, _title, kind in selected],
            [
                ("escriturario_agente_comercial.pdf", "exam"),
                ("gabarito.pdf", "answer_key"),
            ],
        )
        self.assertTrue(
            all(
                url.startswith("https://arq.pciconcursos.com.br/")
                for url, _title, _kind in selected
            )
        )

    def test_pci_public_data_urls_mark_rendered_challenge_as_released(self) -> None:
        url = "https://www.pciconcursos.com.br/provas/download/exemplo"
        html = (FIXTURES / "pci_banco_brasil_js_links.html").read_text(encoding="utf-8")
        html = html.replace("<body>", '<body><div class="cf-turnstile"></div>')

        self.assertIsNone(detect_access_challenge("PCI Concursos", html, url))

    def test_pci_detail_page_does_not_expand_to_index_or_pagination(self) -> None:
        detail = "https://www.pciconcursos.com.br/provas/download/exemplo"
        pci = source_definition(id="pci_concursos", start_urls=[detail])
        self.assertFalse(_should_expand_collection_pages(pci))
        self.assertTrue(_should_expand_collection_pages(source_definition()))

    def test_pci_turnstile_fixture_requires_manual_action(self) -> None:
        url = "https://www.pciconcursos.com.br/provas/download/exemplo"
        html = (FIXTURES / "pci_banco_brasil_turnstile.html").read_text(encoding="utf-8")
        config = load_config(PROJECT_ROOT / "config" / "sources.official.toml")
        source = next(item for item in config.sources if item.id == "pci_concursos")

        self.assertEqual(detect_access_challenge(source.name, html, url), "captcha")
        self.assertEqual(select_document_links(html, url, source), [])

    def test_pci_source_metadata_and_access_policy_are_explicit(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "sources.official.toml")
        source = next(item for item in config.sources if item.id == "pci_concursos")
        self.assertEqual(source.metadata, {"orgao": "Banco do Brasil"})
        self.assertEqual(source.robots_policy, "ignore")
        self.assertEqual(source.crawl_delay_policy, "ignore")
        self.assertEqual(
            source.allowed_hosts,
            ["www.pciconcursos.com.br", "arq.pciconcursos.com.br"],
        )
        self.assertFalse(source.browser_enabled)
        self.assertEqual(source.page_transport, "scrapling")
        self.assertIn("publicos", source.authorization_basis)

    def test_pci_detail_page_metadata_is_captured_without_inference(self) -> None:
        metadata = extract_page_metadata(
            "<p>Cargo: Escriturário - Agente de Tecnologia</p>"
            "<p>Ano: 2021</p><p>Órgão: Banco do Brasil S/A</p>"
            "<p>Organizadora: CESGRANRIO</p><p>Tipo de prova: Caderno 1</p>"
            "<p>Quantidade de questões: 70</p>"
        )
        self.assertEqual(
            metadata,
            {
                "cargo": "Escriturário - Agente de Tecnologia",
                "ano_publicacao": "2021",
                "orgao": "Banco do Brasil S/A",
                "banca": "CESGRANRIO",
                "tipo_prova": "Caderno 1",
                "quantidade_questoes": "70",
            },
        )

    def test_access_challenge_is_reported_for_manual_action(self) -> None:
        reason = detect_access_challenge(
            "Banco do Brasil",
            '<html><title>Just a moment...</title><div class="cf-turnstile"></div></html>',
            "https://www.pciconcursos.com.br/provas/banco-do-brasil",
        )
        self.assertEqual(reason, "captcha")

    def test_access_challenge_does_not_stop_other_sources(self) -> None:
        challenge_url = "https://provas.example.gov.br/bloqueado"
        healthy_url = "https://outro.example.gov.br/lista"
        pdf_url = "https://outro.example.gov.br/prova.pdf"

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
                headers["Content-Type"] = "text/html; charset=utf-8"
                if url == challenge_url:
                    body = b'<html><body><div class="cf-turnstile"></div></body></html>'
                elif url == healthy_url:
                    body = b'<a href="/prova.pdf">Prova</a>'
                elif url == pdf_url:
                    headers["Content-Type"] = "application/pdf"
                    body = b"%PDF-1.4\nfixture\n%%EOF"
                else:
                    raise AssertionError(f"URL inesperada: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

        with tempfile.TemporaryDirectory() as temporary:
            blocked = source_definition(
                id="pci_bloqueado",
                name="PCI bloqueado",
                start_urls=[challenge_url],
                allowed_hosts=["provas.example.gov.br"],
                robots_policy="enforce",
                crawl_delay_policy="enforce",
            )
            healthy = source_definition(
                id="fonte_saudavel",
                name="Fonte saudavel",
                start_urls=[healthy_url],
                allowed_hosts=["outro.example.gov.br"],
            )
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[blocked, healthy],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)

        self.assertEqual([item.original_url for item in manifest.documents], [pdf_url])
        self.assertEqual(len(manifest.failures), 1)
        self.assertIn("acao manual necessaria", manifest.failures[0].message)

    def test_manual_action_resumes_the_same_page_after_confirmation(self) -> None:
        page_url = "https://provas.example.gov.br/bloqueado"
        pdf_url = "https://provas.example.gov.br/prova.pdf"

        class FixtureClient:
            page_requests = 0

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
                if url == page_url:
                    type(self).page_requests += 1
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = (
                        b'<div class="cf-turnstile"></div>'
                        if self.page_requests == 1
                        else b'<a href="/prova.pdf">Prova</a>'
                    )
                    return HttpResult(url=url, status_code=200, headers=headers, body=body)
                if url == pdf_url:
                    headers["Content-Type"] = "application/pdf"
                    return HttpResult(
                        url=url,
                        status_code=200,
                        headers=headers,
                        body=b"%PDF-1.4\nfixture\n%%EOF",
                    )
                raise AssertionError(f"URL inesperada: {url}")

        confirmations: list[tuple[str, str]] = []
        with tempfile.TemporaryDirectory() as temporary:
            source = source_definition(start_urls=[page_url])
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(
                    config,
                    manual_action_callback=lambda url, reason: (
                        confirmations.append((url, reason)) or True
                    ),
                )

        self.assertEqual(confirmations, [(page_url, "captcha")])
        self.assertEqual(FixtureClient.page_requests, 2)
        self.assertEqual([item.original_url for item in manifest.documents], [pdf_url])
        self.assertEqual(manifest.failures, [])

    def test_http_403_pauses_only_the_affected_source_checkpoint(self) -> None:
        denied_url = "https://blocked.example.gov.br/lista"
        healthy_url = "https://healthy.example.gov.br/lista"
        pdf_url = "https://healthy.example.gov.br/prova.pdf"

        class FixtureClient:
            deny_access = True

            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                pass

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                if url == denied_url and self.deny_access:
                    raise FetchError("HTTP 403 ao acessar fonte", 403)
                headers = Message()
                if url == denied_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    return HttpResult(
                        url=url, status_code=200, headers=headers, body=b"<html></html>"
                    )
                if url == healthy_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    return HttpResult(
                        url=url,
                        status_code=200,
                        headers=headers,
                        body=b'<a href="/prova.pdf">Prova</a>',
                    )
                if url == pdf_url:
                    headers["Content-Type"] = "application/pdf"
                    return HttpResult(
                        url=url,
                        status_code=200,
                        headers=headers,
                        body=b"%PDF-1.4\nfixture\n%%EOF",
                    )
                raise AssertionError(f"URL inesperada: {url}")

        with tempfile.TemporaryDirectory() as temporary:
            blocked = source_definition(
                id="fonte_bloqueada",
                start_urls=[denied_url],
                allowed_hosts=["blocked.example.gov.br"],
                robots_policy="ignore",
                crawl_delay_policy="ignore",
            )
            healthy = source_definition(
                id="fonte_saudavel_403",
                start_urls=[healthy_url],
                allowed_hosts=["healthy.example.gov.br"],
                robots_policy="ignore",
                crawl_delay_policy="ignore",
            )
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[blocked, healthy],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)
            state = CollectionStateStore(Path(temporary) / "collection-engine.sqlite3")
            checkpoint = state.load_checkpoint(_checkpoint_key(blocked))
            FixtureClient.deny_access = False
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                resumed_manifest, _ = collect_documents(config)
            resumed_checkpoint = state.load_checkpoint(_checkpoint_key(blocked))

        self.assertEqual([item.original_url for item in manifest.documents], [pdf_url])
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint["status"], "access_denied")
        self.assertEqual(checkpoint["payload"]["pending_pages"], [denied_url])
        self.assertEqual(len(manifest.failures), 1)
        self.assertEqual(manifest.failures[0].url, denied_url)
        self.assertIsNone(resumed_checkpoint)
        self.assertEqual([item.original_url for item in resumed_manifest.documents], [pdf_url])

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

    def test_collection_detail_pages_are_followed_to_find_pdfs(self) -> None:
        listing_url = "https://provas.example.gov.br/banco"
        detail_url = "https://provas.example.gov.br/provas/download/banco-2023"
        exam_url = "https://provas.example.gov.br/arquivos/prova-2023.pdf"
        key_url = "https://provas.example.gov.br/arquivos/gabarito-2023.pdf"

        class FixtureClient:
            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                pass

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                headers = Message()
                if url.endswith("/robots.txt"):
                    headers["Content-Type"] = "text/plain; charset=utf-8"
                    body = b"User-agent: *\nAllow: /\n"
                elif url == listing_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = b'<a href="/provas/download/banco-2023">Banco 2023</a>'
                elif url == detail_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = (
                        b"<p>Cargo: Escriturario</p><p>Ano: 2023</p>"
                        b"<p>Orgao: Banco do Brasil</p><p>Organizadora: CESGRANRIO</p>"
                        b'<a href="/arquivos/prova-2023.pdf">Prova objetiva 2023</a>'
                        b'<a href="/arquivos/gabarito-2023.pdf">Gabarito definitivo 2023</a>'
                    )
                elif url in {exam_url, key_url}:
                    headers["Content-Type"] = "application/pdf"
                    body = f"%PDF-1.4\n{url}\n%%EOF".encode()
                else:
                    raise AssertionError(f"URL inesperada: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

        with tempfile.TemporaryDirectory() as temporary:
            source = source_definition(
                start_urls=[listing_url],
                collection_url_patterns=[r"/provas/download/[a-z0-9-]+$"],
            )
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)

        self.assertEqual(
            {item.original_url for item in manifest.documents},
            {exam_url, key_url},
        )
        self.assertEqual(
            manifest.documents[0].metadata,
            {
                "cargo": "Escriturario",
                "ano_publicacao": "2023",
                "orgao": "Banco do Brasil",
                "banca": "CESGRANRIO",
                "canonical_url": manifest.documents[0].resolved_url,
            },
        )

    def test_pci_detail_page_downloads_public_data_urls_without_crawling_index(self) -> None:
        detail_url = "https://www.pciconcursos.com.br/provas/download/exemplo"
        exam_url = (
            "https://arq.pciconcursos.com.br/provas/29658981/2e4bb8b74228/"
            "escriturario_agente_comercial.pdf"
        )
        key_url = (
            "https://arq.pciconcursos.com.br/provas/29658981/444cd7f0bc5d/gabarito.pdf"
        )
        fixture = (FIXTURES / "pci_banco_brasil_js_links.html").read_bytes()

        class FixtureClient:
            requested: list[str] = []

            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                pass

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                self.requested.append(url)
                headers = Message()
                if url == detail_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = fixture
                elif url in {exam_url, key_url}:
                    headers["Content-Type"] = "application/pdf"
                    body = f"%PDF-1.4\n{url}\n%%EOF".encode()
                else:
                    raise AssertionError(f"URL inesperada: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

        with tempfile.TemporaryDirectory() as temporary:
            source = source_definition(
                id="pci_concursos",
                start_urls=[detail_url],
                allowed_hosts=["www.pciconcursos.com.br", "arq.pciconcursos.com.br"],
                discovery_strategies=["html", "browser"],
                browser_enabled=True,
                collection_url_patterns=[
                    r"^https://www\.pciconcursos\.com\.br/provas/"
                    r"(?:banco-do-brasil|download/[a-z0-9-]+)$"
                ],
                pagination_patterns=[r"/provas/banco-do-brasil(?:/\d+)?$"],
                robots_policy="ignore",
                crawl_delay_policy="ignore",
            )
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source],
            )
            with (
                patch("kad_collector.collector.SafeHttpClient", FixtureClient),
                patch("kad_collector.collector.browser_discover") as browser_discovery,
            ):
                manifest, _ = collect_documents(config)

        self.assertEqual(
            {item.original_url for item in manifest.documents},
            {exam_url, key_url},
        )
        self.assertEqual(FixtureClient.requested[0], detail_url)
        self.assertEqual(set(FixtureClient.requested[1:]), {exam_url, key_url})
        browser_discovery.assert_not_called()
        self.assertEqual(manifest.failures, [])

    def test_valid_html_served_as_text_plain_is_discovered(self) -> None:
        page_url = "https://provas.example.gov.br/concurso"
        pdf_url = "https://provas.example.gov.br/prova-tipo-1.pdf"

        class FixtureClient:
            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                pass

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                headers = Message()
                if url.endswith("/robots.txt"):
                    headers["Content-Type"] = "text/plain; charset=utf-8"
                    body = b"User-agent: *\nAllow: /\n"
                elif url == page_url:
                    headers["Content-Type"] = "text/plain; charset=utf-8"
                    body = (
                        b"<!doctype html><html><head><title>Concurso</title></head>"
                        b'<body><a href="/prova-tipo-1.pdf">Prova Tipo 1</a></body></html>'
                    )
                elif url == pdf_url:
                    headers["Content-Type"] = "application/pdf"
                    body = b"%PDF-1.4\nfixture\n%%EOF"
                else:
                    raise AssertionError(f"URL inesperada: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

        with tempfile.TemporaryDirectory() as temporary:
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source_definition(start_urls=[page_url])],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)

        self.assertEqual([item.original_url for item in manifest.documents], [pdf_url])

    def test_document_inherits_year_from_its_dated_source_page_block(self) -> None:
        page_url = "https://provas.example.gov.br/concurso"
        pdf_url = "https://provas.example.gov.br/prova-tipo-1.pdf"

        class FixtureClient:
            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                pass

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                headers = Message()
                if url.endswith("/robots.txt"):
                    headers["Content-Type"] = "text/plain; charset=utf-8"
                    body = b"User-agent: *\nAllow: /\n"
                elif url == page_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = b"""
                    <!doctype html><html><body>
                      <div class="field__item">
                        <div class="paragraph paragraph--type--texto-data">
                          <div><time datetime="2023-03-21T12:00:00Z">21/03/2023</time></div>
                          <div><p>Prova Objetiva <a href="/prova-tipo-1.pdf">Tipo 1</a></p></div>
                        </div>
                      </div>
                    </body></html>
                    """
                elif url == pdf_url:
                    headers["Content-Type"] = "application/pdf"
                    body = b"%PDF-1.4\nfixture\n%%EOF"
                else:
                    raise AssertionError(f"URL inesperada: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

        with tempfile.TemporaryDirectory() as temporary:
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary),
                sources=[source_definition(start_urls=[page_url])],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _ = collect_documents(config)

        self.assertEqual(len(manifest.documents), 1)
        self.assertEqual(manifest.documents[0].metadata["ano_publicacao"], "2023")
        self.assertEqual(manifest.documents[0].metadata["etapa"], "prova objetiva")

    def test_dated_source_block_preserves_course_and_sub_judice_stage(self) -> None:
        html = """
        <div class="paragraph paragraph--type--texto-data">
          <time datetime="2025-12-22T12:00:00Z">22/12/2025</time>
          <p>Prova Objetiva - Curso de Formação Profissional (Sub Judice)
             <a href="/auditor.pdf">Auditor-Fiscal</a>
          </p>
        </div>
        """

        stages = extract_dated_link_stages(html, "https://provas.example.gov.br/concurso")

        self.assertEqual(
            stages["https://provas.example.gov.br/auditor.pdf"],
            "curso de formação sub judice",
        )

    def test_single_exam_variant_in_dated_block_is_inherited_by_answer_key(self) -> None:
        html = """
        <div class="paragraph paragraph--type--texto-data">
          <time datetime="2024-07-29T12:00:00Z">29/07/2024</time>
          <p>Prova Objetiva - Curso de Formação Profissional (Sub Judice)
             <a href="/auditor-tipo-1.pdf">Auditor-Fiscal</a>
             <a href="/gabarito.pdf">Gabarito Oficial Preliminar</a>
          </p>
        </div>
        """

        variants = extract_dated_link_variants(html, "https://provas.example.gov.br/concurso")

        self.assertEqual(
            variants["https://provas.example.gov.br/gabarito.pdf"],
            "Tipo 1",
        )

    def test_multiple_exam_variants_do_not_invent_answer_key_coverage(self) -> None:
        html = """
        <div>
          <time datetime="2023-03-20T12:00:00Z">20/03/2023</time>
          <a href="/auditor-tipo-1.pdf">Tipo 1</a>
          <a href="/auditor-tipo-2.pdf">Tipo 2</a>
          <a href="/gabarito.pdf">Gabarito Oficial</a>
        </div>
        """

        variants = extract_dated_link_variants(html, "https://provas.example.gov.br/concurso")

        self.assertNotIn("https://provas.example.gov.br/gabarito.pdf", variants)

    def test_same_date_and_stage_connect_sibling_exam_and_answer_key_blocks(self) -> None:
        html = """
        <div>
          <time datetime="2024-07-30T12:00:00Z">30/07/2024</time>
          <p>Gabarito Oficial - Curso de Formação (Aplicação Sub Judice)
             <a href="/gabarito.pdf">Gabarito Oficial</a>
          </p>
        </div>
        <div>
          <time datetime="2024-07-30T12:00:00Z">30/07/2024</time>
          <p>Prova Objetiva - Curso de Formação (Aplicação Sub Judice)
             <a href="/auditor-tipo-1.pdf">Auditor-Fiscal</a>
          </p>
        </div>
        """

        variants = extract_dated_link_variants(html, "https://provas.example.gov.br/concurso")

        self.assertEqual(
            variants["https://provas.example.gov.br/gabarito.pdf"],
            "Tipo 1",
        )

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
                "pci_concursos",
            },
        )
        self.assertTrue(all(source.enabled for source in config.sources))
        legacy_sources = [source for source in config.sources if source.id != "pci_concursos"]
        self.assertTrue(all(source.robots_policy == "ignore" for source in legacy_sources))
        self.assertTrue(all(source.crawl_delay_policy == "ignore" for source in legacy_sources))
        pci = next(source for source in config.sources if source.id == "pci_concursos")
        self.assertEqual(pci.robots_policy, "ignore")
        self.assertEqual(pci.crawl_delay_policy, "ignore")
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
        self.assertEqual(fgv.max_pages_per_run, 1)
        self.assertEqual(fgv.start_urls, ["https://conhecimento.fgv.br/concursos/rfb22"])
        fgv_index = (FIXTURES / "fgv_index.html").read_text(encoding="utf-8")
        self.assertEqual(
            select_pagination_links(fgv_index, fgv.start_urls[0], fgv),
            [],
        )

        comvest = next(source for source in config.sources if source.id == "comvest_unicamp")
        comvest_html = (FIXTURES / "comvest_archive.html").read_text(encoding="utf-8")
        comvest_selected = select_document_links(comvest_html, comvest.start_urls[0], comvest)
        self.assertEqual(
            [(Path(item[0]).name, item[2]) for item in comvest_selected],
            [
                ("F1_2026_Prova-Q.pdf", "exam"),
                ("F2_1o-dia_todos.pdf", "exam"),
                ("F1_historia.pdf", "exam"),
                ("respostas-esperadas-2026.pdf", "answer_key"),
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
