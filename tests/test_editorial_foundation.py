from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kad_collector.desktop_classifier import LocalRuleClassifier
from kad_collector.desktop_models import ClassificationRequest, DesktopImportMetadata
from kad_collector.editorial_taxonomy import EditorialTaxonomy

FIXTURES = Path(__file__).parent / "fixtures" / "editorial_programs"


class ExtensibleTaxonomyTests(unittest.TestCase):
    def test_default_bundle_loads_three_independent_official_catalogs(self) -> None:
        taxonomy = EditorialTaxonomy.load_default()

        self.assertEqual(taxonomy.version, "2.0.1")
        self.assertEqual(
            set(taxonomy.catalog_ids),
            {"fgv-rfb22", "fgv-pcam21", "fgv-stn24"},
        )

    def test_directory_loader_adds_a_catalog_without_changing_the_engine(self) -> None:
        catalog = {
            "id": "example-public-notice",
            "version": "1.0.0",
            "sources": [
                {
                    "id": "notice",
                    "title": "Edital oficial de exemplo",
                    "url": "https://example.gov.br/edital.pdf",
                }
            ],
            "disciplines": [
                {
                    "name": "Arquivologia",
                    "aliases": ["Gestão de Arquivos"],
                    "topics": [
                        {
                            "matter": "Gestão Documental",
                            "subject": "Tabela de Temporalidade",
                            "headings": ["TABELA DE TEMPORALIDADE"],
                            "keywords": ["temporalidade documental"],
                        }
                    ],
                }
            ],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "example.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")

            taxonomy = EditorialTaxonomy.load_directory(Path(directory), version="9.0.0")

        self.assertEqual(taxonomy.version, "9.0.0")
        self.assertEqual(taxonomy.catalog_ids, ("example-public-notice",))
        self.assertEqual(
            taxonomy.canonical_name("discipline", "gestao de arquivos"),
            "Arquivologia",
        )

    def test_unknown_alias_is_rejected_instead_of_becoming_a_new_name(self) -> None:
        taxonomy = EditorialTaxonomy.load_default()

        with self.assertRaisesRegex(ValueError, "fora da taxonomia"):
            taxonomy.canonical_name("discipline", "Disciplina inventada")

    def test_operator_alias_is_canonicalized_before_it_is_saved(self) -> None:
        item = LocalRuleClassifier().classify_many(
            [
                ClassificationRequest(
                    question_number=1,
                    statement="Assinale a alternativa correta.",
                    alternatives=["Alternativa A", "Alternativa B"],
                )
            ],
            DesktopImportMetadata(
                provider="fgv_conhecimento",
                source_url="https://conhecimento.fgv.br/concursos/stn",
                concurso="STN",
                board="FGV",
                role="Auditor Federal de Finanças e Controle",
                discipline="TI",
                subject="Infraestrutura de TI",
                topic="Redes de Computadores",
            ),
        )[0].classification

        self.assertEqual(item.discipline.value, "Tecnologia da Informação")
        self.assertEqual(item.subject.value, "Infraestrutura de TI")
        self.assertEqual(item.topic.value, "Redes de Computadores")

    def test_unknown_operator_name_is_rejected_without_forcing_classification(self) -> None:
        item = LocalRuleClassifier().classify_many(
            [
                ClassificationRequest(
                    question_number=1,
                    statement="Enunciado sem evidência temática.",
                    alternatives=["Alternativa A", "Alternativa B"],
                )
            ],
            DesktopImportMetadata(discipline="Disciplina Inventada"),
        )[0].classification

        self.assertIsNone(item.discipline.value)
        self.assertEqual(item.discipline.source, "taxonomy_rejected")


class OfficialProgramTests(unittest.TestCase):
    def test_official_program_fragments_cover_fiscal_police_and_technology(self) -> None:
        taxonomy = EditorialTaxonomy.load_default()
        expected = {
            "rfb22.txt": ("Legislação Aduaneira", "Controle Aduaneiro"),
            "pcam21.txt": ("Direito Penal", "Teoria do Crime"),
            "stn24-ti.txt": ("Tecnologia da Informação", "Redes de Computadores"),
        }

        for filename, (discipline, subject) in expected.items():
            fixture = json.loads(
                (FIXTURES / f"{filename}.json").read_text(encoding="utf-8")
            )
            entries = taxonomy.parse_official_program(
                fixture["text"], source_url=fixture["source_url"]
            )
            paths = {(item.path.discipline, item.path.subject) for item in entries}
            with self.subTest(filename=filename):
                self.assertIn((discipline, subject), paths)

    def test_program_reader_rejects_unregistered_origin(self) -> None:
        taxonomy = EditorialTaxonomy.load_default()

        with self.assertRaisesRegex(ValueError, "origem oficial não registrada"):
            taxonomy.parse_official_program(
                "DIREITO PENAL: Teoria do crime.",
                source_url="https://example.gov.br/edital.pdf",
            )

    def test_classifier_uses_the_matching_catalog_for_three_contests(self) -> None:
        cases = [
            (
                DesktopImportMetadata(
                    concurso="RFB22",
                    organization="Receita Federal do Brasil",
                    source_url="https://conhecimento.fgv.br/concursos/rfb22",
                ),
                "SIGILO FISCAL",
                ("Direito Tributário", "Sigilo Fiscal", "Sigilo Fiscal"),
            ),
            (
                DesktopImportMetadata(
                    concurso="PCAM21",
                    organization="Polícia Civil do Amazonas",
                    source_url="https://conhecimento.fgv.br/concursos/pcam21",
                ),
                "DIREITO PENAL",
                ("Direito Penal", None, None),
            ),
            (
                DesktopImportMetadata(
                    concurso="STN",
                    organization="Secretaria do Tesouro Nacional",
                    source_url="https://conhecimento.fgv.br/concursos/stn",
                ),
                "REDES DE COMPUTADORES",
                (
                    "Tecnologia da Informação",
                    "Infraestrutura de TI",
                    "Redes de Computadores",
                ),
            ),
        ]

        for metadata, heading, expected in cases:
            result = LocalRuleClassifier().classify_many(
                [
                    ClassificationRequest(
                        question_number=1,
                        statement="Assinale a alternativa correta.",
                        alternatives=["Alternativa A", "Alternativa B"],
                        section_title=heading,
                    )
                ],
                metadata,
            )[0].classification
            with self.subTest(concurso=metadata.concurso):
                self.assertEqual(
                    (result.discipline.value, result.subject.value, result.topic.value),
                    expected,
                )

    def test_program_reader_ignores_terms_outside_a_heading(self) -> None:
        taxonomy = EditorialTaxonomy.load_default()

        entries = taxonomy.parse_official_program(
            "O conteúdo de direito penal poderá ser cobrado na prova.",
            source_url=(
                "https://conhecimento.fgv.br/sites/default/files/concursos/"
                "edital_01_pc_am.pdf"
            ),
        )

        self.assertEqual(entries, [])

    def test_classification_records_catalog_and_official_url_as_provenance(self) -> None:
        taxonomy = EditorialTaxonomy.load_default()
        item = LocalRuleClassifier(taxonomy).classify_many(
            [
                ClassificationRequest(
                    question_number=1,
                    statement="Assinale a alternativa correta.",
                    alternatives=["Alternativa A", "Alternativa B"],
                    section_title="DIREITO PENAL",
                    block_id="section-1",
                )
            ],
            DesktopImportMetadata(
                provider="fgv_conhecimento",
                source_url="https://conhecimento.fgv.br/concursos/pcam21/01",
                concurso="PCAM21",
                board="FGV",
                role="Delegado de Polícia",
                year=2021,
            ),
        )[0].classification

        self.assertEqual(item.discipline.value, "Direito Penal")
        self.assertIn("fgv-pcam21", item.discipline.provenance)
        self.assertIn(
            "https://conhecimento.fgv.br/sites/default/files/concursos/edital_01_pc_am.pdf",
            item.discipline.provenance,
        )


class SectionBlockTests(unittest.TestCase):
    def test_multiline_heading_and_repeated_bare_numbers_keep_page_identity(self) -> None:
        from kad_collector.desktop_parser import map_question_sections

        sections = map_question_sections(
            [
                {
                    "page_number": 1,
                    "text": "\n".join(
                        [
                            "Administração Aduaneira e Modelo de",
                            "Controle - MCA",
                            "1",
                            "Primeiro enunciado.",
                        ]
                    ),
                },
                {
                    "page_number": 2,
                    "text": "\n".join(
                        [
                            "SIGILO FISCAL",
                            "1",
                            "Outro enunciado com a mesma numeração.",
                        ]
                    ),
                },
            ],
            EditorialTaxonomy.load_default(),
            catalog_ids=("fgv-rfb22",),
        )

        self.assertEqual(
            sections[(1, 1)].section_title,
            "Administração Aduaneira e Modelo de Controle - MCA",
        )
        self.assertEqual(sections[(1, 2)].section_title, "SIGILO FISCAL")
        self.assertNotEqual(sections[(1, 1)].block_id, sections[(1, 2)].block_id)

    def test_new_exam_part_stops_carrying_the_previous_section(self) -> None:
        from kad_collector.desktop_parser import map_question_sections

        sections = map_question_sections(
            [
                {
                    "page_number": 1,
                    "text": "\n".join(
                        [
                            "LEGISLAÇÃO ADUANEIRA",
                            "60",
                            "Última questão objetiva.",
                            "Prova Discursiva",
                            "Questão 1",
                            "Novo enunciado sem título de disciplina.",
                        ]
                    ),
                }
            ],
            EditorialTaxonomy.load_default(),
            catalog_ids=("fgv-rfb22",),
        )

        self.assertIn((60, 1), sections)
        self.assertNotIn((1, 1), sections)

    def test_official_range_wins_over_a_composite_discipline_heading(self) -> None:
        result = LocalRuleClassifier().classify_many(
            [
                ClassificationRequest(
                    question_number=32,
                    statement="Assinale a alternativa correta.",
                    alternatives=["Alternativa A", "Alternativa B"],
                    section_title="Raciocínio Lógico Matemático e Estatística",
                )
            ],
            DesktopImportMetadata(
                concurso="RFB22",
                source_url=(
                    "https://conhecimento.fgv.br/sites/default/files/concursos/"
                    "cns101-auditor-fiscal-tipo-1.pdf"
                ),
                role="Auditor-Fiscal da Receita Federal do Brasil",
                stage="prova objetiva",
            ),
        )[0].classification

        self.assertEqual(result.discipline.value, "Estatística")
        self.assertEqual(result.discipline.source, "official_exam_range")

    def test_common_page_text_from_another_catalog_is_not_a_section(self) -> None:
        result = LocalRuleClassifier().classify_many(
            [
                ClassificationRequest(
                    question_number=1,
                    statement="Assinale a alternativa correta.",
                    alternatives=["Alternativa A", "Alternativa B"],
                    context=(
                        "A administração tributária utiliza tecnologia da informação "
                        "para apoiar suas atividades institucionais."
                    ),
                )
            ],
            DesktopImportMetadata(
                provider="fgv_conhecimento",
                source_url="https://conhecimento.fgv.br/concursos/rfb22",
                concurso="RFB22",
                board="FGV",
                role="Auditor-Fiscal da Receita Federal do Brasil",
                year=2023,
            ),
        )[0].classification

        self.assertIsNone(result.discipline.value)
        self.assertEqual(result.discipline.source, "unresolved")

    def test_sentence_without_punctuation_is_not_a_discipline_heading(self) -> None:
        result = LocalRuleClassifier().classify_many(
            [
                ClassificationRequest(
                    question_number=1,
                    statement="Assinale a alternativa correta.",
                    alternatives=["Alternativa A", "Alternativa B"],
                    context="Observe o relato de uma testemunha de um processo de auditoria",
                )
            ],
            DesktopImportMetadata(concurso="RFB22"),
        )[0].classification

        self.assertIsNone(result.discipline.value)
        self.assertEqual(result.discipline.source, "unresolved")

    def test_question_sections_follow_headings_and_split_blocks(self) -> None:
        from kad_collector.desktop_parser import map_question_sections

        taxonomy = EditorialTaxonomy.load_default()
        pages = [
            {
                "page_number": 1,
                "text": "\n".join(
                    [
                        "DIREITO PENAL",
                        "QUESTÃO 1 - Primeiro enunciado",
                        "A) alternativa",
                        "QUESTÃO 2 - Segundo enunciado",
                        "A) alternativa",
                        "DIREITO PROCESSUAL PENAL",
                        "QUESTÃO 3 - Terceiro enunciado",
                        "A) alternativa",
                    ]
                ),
            }
        ]

        sections = map_question_sections(pages, taxonomy)

        self.assertEqual(sections[(1, 1)].section_title, "DIREITO PENAL")
        self.assertEqual(sections[(1, 1)].block_id, sections[(2, 1)].block_id)
        self.assertEqual(
            sections[(3, 1)].section_title, "DIREITO PROCESSUAL PENAL"
        )
        self.assertNotEqual(sections[(2, 1)].block_id, sections[(3, 1)].block_id)

    def test_neighbor_evidence_does_not_cross_a_block_boundary(self) -> None:
        results = LocalRuleClassifier().classify_many(
            [
                ClassificationRequest(
                    question_number=1,
                    statement="Primeira questão.",
                    alternatives=["A", "B"],
                    section_title="DIREITO PENAL",
                    block_id="section-1",
                ),
                ClassificationRequest(
                    question_number=2,
                    statement="Enunciado neutro.",
                    alternatives=["A", "B"],
                    block_id="section-2",
                ),
                ClassificationRequest(
                    question_number=3,
                    statement="Terceira questão.",
                    alternatives=["A", "B"],
                    section_title="DIREITO PENAL",
                    block_id="section-2",
                ),
            ],
            DesktopImportMetadata(),
        )

        self.assertIsNone(results[1].classification.discipline.value)
        self.assertEqual(results[1].classification.discipline.source, "unresolved")


if __name__ == "__main__":
    unittest.main()
