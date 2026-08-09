from __future__ import annotations

import tempfile
import unittest
from email.message import Message
from pathlib import Path

from kad_collector.collector import (
    RobotsPolicy,
    classify_document,
    extract_links,
    select_document_links,
)
from kad_collector.config import ConfigError, load_config
from kad_collector.models import SourceDefinition
from kad_collector.security import HttpResult, UnsafeUrlError, validate_public_url

FIXTURES = Path(__file__).parent / "fixtures"


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


class SecurityTests(unittest.TestCase):
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
