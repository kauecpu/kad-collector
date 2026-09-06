from pathlib import Path

from kad_collector.answer_key import parse_answer_key
from kad_collector.document_contract import NormalizedDocument
from kad_collector.semantic_identity import extract_semantic_profile

TEXT = """RELAÇÃO GERAL DOS GABARITOS PRELIMINARES
A01 - DEFENSOR(A) PÚBLICO(A) - Tipo 1 Folha: 1
001 - D 002 - B 003 - X
A01 - DEFENSOR(A) PÚBLICO(A) - Tipo 2 Folha: 1
001 - E 002 - C 003 - A
"""


def test_fcc_blocks_select_variant_without_overwriting_answers():
    first = parse_answer_key(TEXT, variant="Tipo 1", role="Defensor Público")
    second = parse_answer_key(TEXT, variant="Tipo 2", role="Defensor Público")
    assert first[1].answer == "D"
    assert first[3].annulled
    assert second[1].answer == "E"


def test_fcc_blocks_require_unambiguous_role_and_variant():
    assert parse_answer_key(TEXT) == {}
    assert parse_answer_key(TEXT, variant="Tipo 9") == {}
    assert parse_answer_key(TEXT, variant="Tipo 1", role="Analista") == {}
    different_role = TEXT + "B02 - ANALISTA - Tipo 1 Folha: 1\n001 - A 002 - E\n"
    assert parse_answer_key(different_role, variant="Tipo 1") == {}


def test_answer_key_coverage_can_span_pages_without_false_conflict():
    document = NormalizedDocument(
        local_path=str(Path("synthetic.pdf").resolve()), sha256="a" * 64,
        size_bytes=100, declared_type="answer_key", title="Gabarito preliminar",
        entry_method="direct_import", metadata={"board": "FCC"},
    )
    profile = extract_semantic_profile(document, [
        (1, TEXT), (2, "A01 - DEFENSOR(A) PÚBLICO(A) - Tipo 3 Folha: 1\n001 - C")
    ])
    assert profile.coverage.variants.status == "known"
    assert profile.coverage.variants.normalized_values == ("tipo 1", "tipo 2", "tipo 3")


def test_repeated_fcc_block_preserves_pages_and_rejects_conflicting_answers():
    continued = TEXT + "A01 - DEFENSOR(A) PÚBLICO(A) - Tipo 1 Folha: 2\n004 - C\n"
    assert parse_answer_key(continued, variant="Tipo 1")[4].answer == "C"
    conflicting = continued + "A01 - DEFENSOR(A) PÚBLICO(A) - Tipo 1 Folha: 3\n001 - A\n"
    assert parse_answer_key(conflicting, variant="Tipo 1") == {}


def test_fcc_does_not_consume_numbered_prose_as_answers():
    text = TEXT + "4 - A comissão recebe os pedidos.\n"
    assert 4 not in parse_answer_key(text, variant="Tipo 2")


def test_exam_variants_on_different_pages_still_conflict():
    document = NormalizedDocument(
        local_path=str(Path("synthetic.pdf").resolve()), sha256="a" * 64,
        size_bytes=100, declared_type="exam", title="Prova",
        entry_method="direct_import", metadata={"board": "FCC"},
    )
    profile = extract_semantic_profile(document, [(1, "Tipo 1"), (2, "Tipo 2")])
    assert profile.identity.variants.status == "conflict"
