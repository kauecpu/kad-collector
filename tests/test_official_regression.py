from __future__ import annotations

import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from kad_collector.desktop_parser import parse_question_document
from kad_collector.fgv_parser import BankParsingContext
from kad_collector.official_regression import (
    OfficialRegressionError,
    QuestionSectionSpec,
    _assert_numbers,
    _execute_rfb22_exam,
    inspect_rfb22_booklet,
    load_official_manifest,
)

RFB22_MANIFEST = Path("tests/regression/rfb22/manifest.v1.toml")


class OfficialManifestTests(unittest.TestCase):
    def test_rfb22_manifest_is_complete_for_the_supported_main_application(self) -> None:
        manifest = load_official_manifest(RFB22_MANIFEST).spec
        supported = [
            document
            for document in manifest.documents
            if document.support_status == "supported"
        ]
        exams = [document for document in supported if document.kind == "exam"]
        answer_keys = [document for document in supported if document.kind == "answer_key"]

        self.assertEqual(manifest.contest_aliases, ("RFB22",))
        self.assertEqual(manifest.organization, "Receita Federal do Brasil")
        self.assertEqual(manifest.board, "Fundação Getulio Vargas")
        self.assertEqual(len(manifest.applications), 5)
        self.assertEqual(len(supported), 19)
        self.assertEqual(len(exams), 16)
        self.assertEqual(len(answer_keys), 3)
        self.assertEqual(
            sum(
                section.count
                for exam in exams
                for section in exam.sections
                if section.kind == "objective"
            ),
            1120,
        )
        self.assertEqual(
            sum(
                section.count
                for exam in exams
                for section in exam.sections
                if section.kind == "discursive"
            ),
            12,
        )
        self.assertTrue(all(exam.answer_key_id for exam in exams))
        self.assertTrue(all(document.source_url.startswith("https://") for document in supported))

    def test_missing_question_breaks_the_official_interval_contract(self) -> None:
        section = QuestionSectionSpec(kind="objective", first=1, last=4, count=4)

        with self.assertRaisesRegex(OfficialRegressionError, "numbering mismatch"):
            _assert_numbers("synthetic exam", [1, 2, 4], section)


class Rfb22BookletTests(unittest.TestCase):
    def test_official_executor_accepts_title_case_manifest_shift(self) -> None:
        manifest = load_official_manifest(RFB22_MANIFEST)
        exam = next(
            document
            for document in manifest.spec.documents
            if document.id == "rfb22-main-2023-auditor-morning-type-1"
        )
        answer_key = next(
            document
            for document in manifest.spec.documents
            if document.id == exam.answer_key_id
        )
        with (
            patch(
                "kad_collector.official_regression._read_pdf_pages",
                return_value=[
                    {"page_number": number, "text": "MANHÃ"}
                    for number in range(1, exam.page_count + 1)
                ],
            ),
            patch("kad_collector.official_regression.parse_question_document") as parser,
        ):
            parser.return_value.identity.role = exam.roles[0]
            parser.return_value.identity.shift = "manhã"
            parser.return_value.identity.booklet_type = exam.booklet_type
            parser.return_value.discursive_numbers = ()
            parser.return_value.expected_intervals = exam.sections
            parser.return_value.status = "incomplete"
            parser.return_value.exceptions = ()
            with self.assertRaisesRegex(OfficialRegressionError, "incomplete parsing"):
                _execute_rfb22_exam(exam, Path("unused.pdf"), answer_key, "")

    def test_inspection_separates_the_mixed_afternoon_booklet(self) -> None:
        pages: list[dict[str, object]] = [
            {
                "page_number": 1,
                "text": """CONCURSO PÚBLICO DA RECEITA FEDERAL DO BRASIL
Auditor-Fiscal da Receita Federal do Brasil (AFRFB) Tipo Verde - Página 1
1
Enunciado objetivo.
(A) Primeira alternativa.
(B) Segunda alternativa.
Prova Discursiva
Questão 1
Primeira questão discursiva.
Questão 2
Segunda questão discursiva.
""",
            }
        ]

        identity = inspect_rfb22_booklet(pages)

        self.assertEqual(identity.role, "Auditor-Fiscal da Receita Federal do Brasil")
        self.assertEqual(identity.shift, "Tarde")
        self.assertEqual(identity.booklet_type, 2)
        self.assertEqual(identity.content_kinds, ("objective", "discursive"))
        self.assertEqual(identity.discursive_numbers, (1, 2))

    def test_parser_keeps_bare_objective_numbers_and_ignores_internal_lists(self) -> None:
        pages = [
            {
                "page_number": 1,
                "text": """CONCURSO PÚBLICO DA RECEITA FEDERAL DO BRASIL
Analista-Tributário da Receita Federal do Brasil (ATRFB) Tipo Branca - Página 1
1
Primeiro enunciado com conteúdo suficiente.
(A) Primeira alternativa.
(B) Segunda alternativa.
2
Segundo enunciado com uma lista interna:
1. Primeiro item da lista.
2. Segundo item da lista.
(A) Primeira alternativa.
(B) Segunda alternativa.
Prova Discursiva
Questão 1
Texto da questão discursiva.
""",
            }
        ]

        result = parse_question_document(
            pages,
            BankParsingContext(
                document_id="synthetic-rfb22-analyst-afternoon",
                board="Fundação Getulio Vargas",
                provider="fgv_conhecimento",
                contest="RFB22",
                role="Analista-Tributário da Receita Federal do Brasil",
                shift="Tarde",
                booklet_type=1,
            ),
        )

        self.assertEqual([question.number for question in result.objective_questions], [1, 2])
        self.assertEqual(result.warnings, ())
        self.assertIn("Primeiro item da lista", result.objective_questions[1].statement)
        self.assertEqual(result.status, "incomplete")

    def test_manifest_loader_reports_a_malformed_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.toml"
            path.write_text("schema_version = [", encoding="utf-8")

            with self.assertRaisesRegex(OfficialRegressionError, "invalid official manifest"):
                load_official_manifest(path)


class OfficialFixturePreparationTests(unittest.TestCase):
    def test_preparation_downloads_and_verifies_a_declared_official_pdf(self) -> None:
        from scripts.prepare_official_contest_fixtures import (
            prepare_official_contest_fixtures,
        )

        payload = b"%PDF-fixture\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.toml"
            manifest_path.write_text(
                f'''schema_version = 1
id = "contest-test"
contest_name = "Concurso de teste"
contest_aliases = ["TEST"]
organization = "Órgão de teste"
board = "Banca de teste"
notice_year = 2026
source_page_url = "https://example.test/contest"
evidence_urls = ["https://example.test/notice.pdf"]
robots_policy = "ignore"
crawl_delay_policy = "ignore"
policy_basis = "Decisão explícita do teste."

[[applications]]
id = "application-test"
title = "Aplicação de teste"
stage = "Etapa de teste"
application_date = 2026-01-01
support_status = "supported"
notes = "Aplicação sintética para testar o preparador."

[[documents]]
id = "answer-key-test"
kind = "answer_key"
path = "official/answer-key.pdf"
source_url = "https://example.test/answer-key.pdf"
size_bytes = {len(payload)}
page_count = 1
sha256 = "{digest}"
title = "Gabarito de teste"
application_id = "application-test"
published_on = 2026-01-02
roles = ["Cargo de teste"]
content_kinds = ["answer_key"]
answer_key_status = "definitive"
answer_scopes = [
  {{role="Cargo de teste",shift="Manhã",booklet_types=[1],first=1,last=1,count=1}},
]
''',
                encoding="utf-8",
            )

            with patch(
                "scripts.prepare_official_contest_fixtures.urlopen",
                return_value=BytesIO(payload),
            ) as urlopen:
                prepared = prepare_official_contest_fixtures(manifest_path)

            destination = root / "official" / "answer-key.pdf"
            self.assertEqual(prepared, [destination])
            self.assertEqual(destination.read_bytes(), payload)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://example.test/answer-key.pdf")


if __name__ == "__main__":
    unittest.main()
