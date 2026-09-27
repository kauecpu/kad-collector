from copy import deepcopy

import pytest

from kad_collector.publication_contract import validate_package


@pytest.fixture
def record():
    return {
        "schemaVersion": 2, "kind": "question",
        "source": {"provider": "fixture", "externalId": "exam:1",
                   "url": "https://example.test/exam.pdf",
                   "collectedAt": "2026-09-27T00:00:00Z", "fingerprint": "a" * 64},
        "data": {
            "id": "q-fixture", "discipline": "Matemática", "subject": "Aritmética",
            "topic": "Adição", "board": "Fixture", "year": 2021,
            "role": "Agente", "institution": "Fixture", "concurso": "Fixture 2021",
            "level": "Superior", "statement": "A soma de um e um é dois.",
            "alternatives": [{"id": "A", "text": "Certo"}, {"id": "B", "text": "Errado"}],
            "correct": "A", "publicationStatus": "draft",
            "canonicalQuestion": {"questionId": "canonical", "groupId": "group",
                "occurrenceCount": 1, "provenances": [{"occurrenceId": "occ-1",
                "questionId": "q-fixture", "questionNumber": 1, "answer": "A",
                "answerStatus": "matched", "answerKeyLinkId": "link-1"}]},
        },
    }


def test_valid_package_repeatable_and_read_only(record):
    original = deepcopy(record)
    assert validate_package([record]) == validate_package([record]) == []
    assert record == original


def test_old_export_is_rejected(record):
    del record["data"]["canonicalQuestion"]
    assert "official answer evidence missing" in " ".join(validate_package([record]))


@pytest.mark.parametrize("field,value", [
    ("answer", "B"), ("answerStatus", "annulled"), ("answerKeyLinkId", " "),
])
def test_incompatible_evidence_rejected(record, field, value):
    record["data"]["canonicalQuestion"]["provenances"][0][field] = value
    assert "incompatible" in " ".join(validate_package([record]))


def test_duplicates_and_missing_taxonomy_rejected(record):
    other = deepcopy(record)
    other["data"]["id"] = "q-other"
    assert "duplicate" in " ".join(validate_package([record, other]))
    record["data"]["subject"] = " "
    assert "taxonomy" in " ".join(validate_package([record]))


@pytest.mark.parametrize("payload", [[], [{}], [None], [{"schemaVersion": 1}]])
def test_malformed_package_fails_closed(payload):
    assert validate_package(payload)
