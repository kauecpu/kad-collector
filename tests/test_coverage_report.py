from __future__ import annotations

import json
from email.message import Message

from kad_collector.collector import collect_documents
from kad_collector.coverage_report import build_coverage_report
from kad_collector.discovery_intelligence import (
    extract_page_inventory,
    finalize_discovery_inventory,
)
from kad_collector.models import AppConfig, CollectorSettings
from kad_collector.security import HttpResult
from tests.test_discovery_intelligence import source_definition


def test_excluded_revised_key_is_visible_without_becoming_download_candidate():
    inventory = extract_page_inventory(
        '<h2>Provas</h2><a href="gabarito.pdf?token=secret">Gabaritos Alterados</a>',
        "https://example.gov.br/2021/", source_definition(exclude_patterns=["Alterados"]),
    )
    assert not inventory.expected
    assert inventory.excluded[0]["reason"] == "exclude_pattern"
    pages = finalize_discovery_inventory([{
        "source_id": "official_test", "page_url": inventory.page_url,
        "expected": [], "excluded": list(inventory.excluded),
    }], [])
    report = build_coverage_report({"collection_policy": {"discovery_inventory": pages}})
    assert report["counts"] == {"exclude_pattern": 1}
    assert "secret" not in json.dumps(report)


def test_old_manifest_reports_unknown_cause_not_an_invented_limit():
    manifest = {"collection_policy": {"max_files_per_source": 40, "discovery_inventory": [{
        "source_id": "test", "page_url": "https://example.gov.br/",
        "expected": [{"url": "https://example.gov.br/prova.pdf", "status": "missing"}],
    }]}}
    assert build_coverage_report(manifest)["counts"] == {"not_recorded": 1}
    assert not build_coverage_report({})["inventory_available"]


def test_collection_records_actual_file_limit_and_download_failure(tmp_path, monkeypatch):
    class FixtureClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get(self, url, *_args, **_kwargs):
            headers = Message()
            if url.endswith(".pdf"):
                raise OSError("fixture download unavailable")
            headers["Content-Type"] = "text/html"
            body = (b'<h2>Provas</h2><a href="prova.pdf">Prova</a>'
                    b'<a href="gabarito.pdf">Gabarito</a>')
            return HttpResult(url=url, status_code=200, headers=headers, body=body)

        def close(self):
            pass

    monkeypatch.setattr("kad_collector.collector.SafeHttpClient", FixtureClient)
    config = AppConfig(collector=CollectorSettings(
        data_dir=str(tmp_path), max_files_per_source=1, max_retries=0,
        request_interval_seconds=0, ai_discovery_enabled=False,
    ), sources=[source_definition(robots_policy="ignore", crawl_delay_policy="ignore")])
    manifest, _ = collect_documents(config)
    report = build_coverage_report(manifest.model_dump(mode="json"))
    assert report["counts"] == {"file_limit": 1, "download_error": 1}


def test_pairing_report_is_based_on_document_hash_not_title():
    url = "https://example.gov.br/prova.pdf"
    manifest = {"documents": [{"source_id": "test", "original_url": url,
                               "resolved_url": url, "sha256": "a" * 64}],
                "collection_policy": {"discovery_inventory": [{
                    "source_id": "test", "page_url": url,
                    "expected": [{"url": url, "status": "downloaded"}],
                }]}}
    package = {"errors": [{"stage": "pairing", "document_id": "a" * 64}]}
    assert build_coverage_report(manifest, package)["documents"][0]["pairing"] == "pairing_error"
    package = {"accepted": [{"exam_sha256": "a" * 64, "answer_key_sha256": "b" * 64}]}
    assert build_coverage_report(manifest, package)["documents"][0]["pairing"] == "paired"
