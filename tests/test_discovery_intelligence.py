from __future__ import annotations

import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from kad_collector.collector import collect_documents, select_document_links
from kad_collector.config import load_config
from kad_collector.discovery import DiscoveredLink
from kad_collector.discovery_intelligence import (
    build_discovery_target,
    expand_aliases,
    extract_page_inventory,
    select_relevant_navigation_links,
)
from kad_collector.models import AppConfig, CollectionFilters, CollectorSettings, SourceDefinition
from kad_collector.security import HttpResult, SafeHttpClient

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def source_definition(**changes: object) -> SourceDefinition:
    data: dict[str, object] = {
        "id": "official_test",
        "name": "Official test source",
        "enabled": True,
        "start_urls": ["https://example.gov.br/"],
        "allowed_hosts": ["example.gov.br"],
        "include_patterns": [r"(?i)prova|gabarito|\.pdf(?:$|\?)"],
        "exclude_patterns": [r"(?i)edital|resultado|comunicado"],
        "exam_patterns": [r"(?i)prova|caderno"],
        "answer_key_patterns": [r"(?i)gabarito"],
        "authorization_basis": "Public official fixture.",
        "metadata": {"orgao": "Policia Federal", "banca": "Cebraspe"},
    }
    data.update(changes)
    return SourceDefinition.model_validate(data)


class DiscoveryIntelligenceTests(unittest.TestCase):
    def test_expands_common_board_and_organization_aliases(self) -> None:
        self.assertIn("cespe", expand_aliases("Cebraspe"))
        self.assertIn("cebraspe", expand_aliases("CESPE"))
        self.assertIn("cebraspe", expand_aliases("CESPE/UnB"))
        self.assertIn("pf", expand_aliases("Polícia Federal"))

    def test_expands_policia_federal_role_names_and_abbreviations(self) -> None:
        self.assertIn("apf", expand_aliases("Agente de Polícia Federal"))
        self.assertIn("escrivao de policia federal", expand_aliases("EPF"))
        self.assertIn("ppf", expand_aliases("Papiloscopista Policial Federal"))
        self.assertIn("perito criminal federal", expand_aliases("PCF"))
        self.assertIn("delegado pf", expand_aliases("Delegado Federal"))

    def test_sitemap_candidate_uses_target_content_even_when_url_year_differs(self) -> None:
        source = source_definition()
        target = build_discovery_target(
            source,
            CollectionFilters(years=[2023], organizations=["Polícia Federal"]),
        )

        selected = select_relevant_navigation_links(
            [
                DiscoveredLink(
                    url="https://example.gov.br/concurso/policia-federal-edital-2022/",
                    title="Polícia Federal - provas aplicadas em 2023",
                ),
                DiscoveredLink(
                    url="https://evil.example/provas-pf-2023/",
                    title="Polícia Federal 2023",
                ),
                DiscoveredLink(
                    url="https://example.gov.br/concurso/policia-federal-2024/",
                    title="Polícia Federal 2024",
                ),
            ],
            target,
            source,
        )

        self.assertEqual(
            [url for url, _reason in selected],
            ["https://example.gov.br/concurso/policia-federal-edital-2022/"],
        )

    def test_cesgranrio_regression_inventory_has_twenty_exams_and_four_keys(self) -> None:
        source = next(
            item
            for item in load_config(ROOT / "config" / "sources.official.toml").sources
            if item.id == "cesgranrio_banco_brasil"
        )
        html = (FIXTURES / "cesgranrio_bb_historical.html").read_text(encoding="utf-8")

        inventory = extract_page_inventory(
            html,
            "https://www.cesgranrio.org.br/concurso/historical-fixture/",
            source,
        )
        selected = select_document_links(html, inventory.page_url, source)

        self.assertEqual((inventory.exams, inventory.answer_keys), (20, 4))
        self.assertEqual(
            (
                sum(item[2] == "exam" for item in selected),
                sum(item[2] == "answer_key" for item in selected),
            ),
            (20, 4),
        )
        self.assertFalse(any("edital" in item.title.casefold() for item in inventory.expected))
        self.assertFalse(any("alterado" in item.title.casefold() for item in inventory.expected))

    def test_inventory_uses_structured_publication_time_for_each_document_block(self) -> None:
        inventory = extract_page_inventory(
            """
            <time datetime="2025-12-22T12:00:00Z">22/12/2025</time>
            <a href="/prova-nova.pdf">Prova nova</a>
            <time datetime="2023-03-20T12:00:00Z">20/03/2023</time>
            <a href="/prova-alvo.pdf">Prova alvo</a>
            """,
            "https://example.gov.br/concurso/receita-2022/",
            source_definition(),
        )

        self.assertEqual([item.year for item in inventory.expected], [2025, 2023])

    def test_sitemap_page_is_enqueued_and_collected_without_final_url_start(self) -> None:
        start_url = "https://example.gov.br/"
        sitemap_url = "https://example.gov.br/sitemap.xml"
        historical_url = "https://example.gov.br/concurso/policia-federal-2018/"
        exam_url = "https://example.gov.br/files/prova-pf-2018.pdf"
        key_url = "https://example.gov.br/files/gabarito-pf-2018.pdf"

        class FixtureClient(SafeHttpClient):
            def __init__(self, user_agent: str, timeout: float, interval_seconds: float) -> None:
                del user_agent, timeout, interval_seconds

            def get(self, url: str, allowed_hosts: list[str], max_bytes: int) -> HttpResult:
                del allowed_hosts, max_bytes
                headers = Message()
                if url == sitemap_url:
                    headers["Content-Type"] = "application/xml"
                    body = (
                        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        f"<url><loc>{historical_url}</loc></url></urlset>"
                    ).encode()
                elif url == start_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = b"<html><body>Official archive</body></html>"
                elif url == historical_url:
                    headers["Content-Type"] = "text/html; charset=utf-8"
                    body = (
                        f'<h2>Provas e Gabaritos</h2><a href="{exam_url}">Prova PF 2018</a>'
                        f'<a href="{key_url}">Gabarito PF 2018</a>'
                        f'<a href="{historical_url}">Concurso PF 2018</a>'
                        '<a href="mailto:concurso@example.gov.br">Contato</a>'
                    ).encode()
                elif url in {exam_url, key_url}:
                    headers["Content-Type"] = "application/pdf"
                    body = b"%PDF-1.4\n" + url.encode() + b"\n%%EOF"
                else:
                    raise AssertionError(f"Unexpected URL: {url}")
                return HttpResult(url=url, status_code=200, headers=headers, body=body)

            def close(self) -> None:
                return

        with tempfile.TemporaryDirectory() as temporary:
            source = source_definition(
                discovery_strategies=["html", "sitemap"],
                sitemap_urls=[sitemap_url],
                collection_url_patterns=[r"/concurso/"],
                max_pages_per_run=4,
                robots_policy="ignore",
                crawl_delay_policy="ignore",
            )
            config = AppConfig(
                collector=CollectorSettings(data_dir=temporary, request_interval_seconds=0),
                sources=[source],
            )
            with patch("kad_collector.collector.SafeHttpClient", FixtureClient):
                manifest, _path = collect_documents(
                    config,
                    CollectionFilters(organizations=["PF"], years=[2018]),
                )

        self.assertEqual(
            {item.document_type for item in manifest.documents}, {"exam", "answer_key"}
        )
        self.assertTrue(
            any(
                item.strategy == "sitemap_navigation" and item.outcome == "queued"
                for item in manifest.telemetry
            )
        )
        summary = manifest.collection_policy["discovery_inventory_summary"]
        self.assertEqual(summary["coverage_percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
