# Homologação Cebraspe — Polícia Federal

- Data UTC: 2026-09-07T13:17:47.091339+00:00
- Commit: `35b8cd1-dirty`
- Ollama: `{"available": false, "qwen3_8b": false, "mode": "offline_test"}`
- Precisão: 100.0%
- Cobertura: 100.0%
- Associação prova/gabarito: 100.0%
- OCR: não acionado
- Esperados/encontrados: 8/8
- Provas encontradas: 4
- Gabaritos encontrados: 4
- Falsos positivos: 0
- Falsos negativos: 0
- Documentos proibidos aceitos: 0
- Fontes concluídas sem intervenção: 100.0%
- Tempo médio por fonte: 23.71s
- Decisões determinísticas: 8
- Decisões com `qwen_fallback`: 0
- Propostas do Qwen aceitas/recusadas: 0/0

## pf_21 — POLÍCIA FEDERAL

- URL inicial: https://www.cebraspe.org.br/concursos/pf_21
- Banca identificada: CEBRASPE
- Esperados/encontrados: 8/8
- Provas/gabaritos aceitos: 4/4
- Precisão/cobertura: 100.0%/100.0%
- Associação prova/gabarito: 100.0%
- Caminho: deterministic_browser
- Páginas visitadas: 2
- Chamadas ao Qwen: 0
- Tempo: 23.71s
- OCR tentado/suficiente: 0/0
- Falhas tratadas: 0
- Exemplos rejeitados: 20

### Documentos aceitos

- `exam` PROVA OBJETIVA - CARGO 3: ESCRIVÃO DE POLÍCIA FEDERAL — `d10ff2dae65cd8614e076d5e318b8258f12aa7dcf60b858f1738d574b01e7d31` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_3_ESCRIVO_DE_POLCIA_FEDERAL.PDF
- `exam` PROVA OBJETIVA - CARGO 1: DELEGADO DE POLÍCIA FEDERAL — `70543026c4d36f8fd4c0642b5023df69a268a958fe147a48d2fac2a201dc3bfa` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_1_DELEGADO_DE_POLCIA_FEDERAL.PDF
- `exam` PROVA OBJETIVA - CARGO 2: AGENTE DE POLÍCIA FEDERAL — `5464654f02abfcafe921eaa65c4f30bf9c267d80e667b7b35a8ca9e3f115b42f` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_2_AGENTE_DE_POLCIA_FEDERAL.PDF
- `exam` PROVA OBJETIVA - CARGO 4: PAPILOSCOPISTA DE POLÍCIA FEDERAL — `99b59dac252614f67877bdd2dc82888807089796a630dc0f8dcef0d6f0c82c75` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_4_PAPILOSCOPISTA_POLICIAL_FEDERAL.PDF
- `answer_key` GABARITO DEFINITIVO - CARGO 1: DELEGADO DE POLÍCIA FEDERAL — `9bbaa7651a4fe7e236b20bea89313f30e9a45158f5946a255aca45afc4a07fda` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/GAB_DEFINITIVO_MATRIZ_577_PF_001_00.PDF
- `answer_key` GABARITO DEFINITIVO - CARGO 2: AGENTE DE POLÍCIA FEDERAL — `36c70e3ad6bce79ae872333b0bc4cbb4848cd843b6ed7f56516b8a16902dbe3b` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/GAB_DEFINITIVO_MATRIZ_577_PF_002_00.PDF
- `answer_key` GABARITO DEFINITIVO - CARGO 3: ESCRIVÃO DE POLÍCIA FEDERAL — `46805ba4e7f83506a73d4ea21b8ecfd7fd83e28c6536cbcc424f687f44cbb6c7` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/GAB_DEFINITIVO_MATRIZ_577_PF_003_00.PDF
- `answer_key` GABARITO DEFINITIVO - CARGO 4: PAPILOSCOPISTA DE POLÍCIA FEDERAL — `0013fbfa3a74e4ebe0e6d8a9bbd403dcc377724c36f976cfba2fd01683c89e7a` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/GAB_DEFINITIVO_MATRIZ_577_PF_004_00.PDF

### Associações prova/gabarito

- `cargo_1` — associado; provas=CARGO_1_DELEGADO_DE_POLCIA_FEDERAL.PDF; gabaritos=GAB_DEFINITIVO_MATRIZ_577_PF_001_00.PDF
- `cargo_2` — associado; provas=CARGO_2_AGENTE_DE_POLCIA_FEDERAL.PDF; gabaritos=GAB_DEFINITIVO_MATRIZ_577_PF_002_00.PDF
- `cargo_3` — associado; provas=CARGO_3_ESCRIVO_DE_POLCIA_FEDERAL.PDF; gabaritos=GAB_DEFINITIVO_MATRIZ_577_PF_003_00.PDF
- `cargo_4` — associado; provas=CARGO_4_PAPILOSCOPISTA_POLICIAL_FEDERAL.PDF; gabaritos=GAB_DEFINITIVO_MATRIZ_577_PF_004_00.PDF

### Rejeições e falhas

- Rejeitado: PROVA ORAL - CARGO DELEGADO (APLICADO DIA 19/7/2026) SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/B46611F19D70F07B019A795D3367C491A414D10BDA1A195E23AFF756A8FE9CE4.pdf
- Rejeitado: PROVA ORAL E PADRÃO DE RESPOSTAS - CARGO DELEGADO (APLICADO EM 11/08/2024) SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/999_PF_ORAL_001_COMPADRAO.PDF
- Rejeitado: PROVA ORAL E PADRÃO DE RESPOSTAS SUB JUDICE - DELEGADO (APLICADO EM 22/01/23) — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/794_PF_21_ORAL_SUBJUDICE_001_COMPADRO.PDF
- Rejeitado: PROVA ORAL E PADRÃO DE RESPOSTAS SUB JUDICE - CARGO DE DELEGADO — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/753_DGP_PF_ORAL_001_COMPADRAO.PDF
- Rejeitado: PROVA ORAL E PADRÃO DE RESPOSTAS - CARGO DE DELEGADO — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/616_PF_DELEGADO_ORAL_COM_PADRO.PDF
- Rejeitado: PROVA DE DIGITAÇÃO - CARGO DE ESCRIVÃO — `prova_de_digitacao` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/PF_ESCRIVO_617.PDF
- Rejeitado: PADRÃO DE RESPOSTA DEFINITIVO - DELEGADO DE POLÍCIA FEDERAL - ATUALIZADO EM 14/6/2021 — `padrao_de_resposta` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/PF_21_PADRO_DE_RESPOSTA_DEFINITIVO_COMPLETO_VF.PDF
- Rejeitado: PADRÃO DE RESPOSTA DEFINITIVO - DEMAIS CARGOS — `padrao_de_resposta` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/PF_DISC_PADRO_DE_RESPOSTA_DEFINITIVO__DEMAIS_CARGOS.PDF
- Rejeitado: PROVA DISCURSIVA - demais cargos — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/PF_21_PROVA_DISCURSIVA_DEMAIS_CARGOS.PDF
- Rejeitado: PROVA DISCURSIVA - Delegado de Polícia Federal — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/PF_21_PROVA_DISCURSIVA_DELEGADO.PDF
- Rejeitado: Edital nº 4 – Convocação de candidatos sub judice para matrícula no CFP e para a atualização da Ficha de Informações Confidenciais – FIC, referentes aos concursos públicos regidos pelo Edital nº 1/2012 – DGP/DPF, de 14 de março de 2012, e suas alterações, e pelo Edital nº 1 – DGP/PF, de 15 de janeiro de 2021 . — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/3183068855B20D8A63585E5BF28624A662F922E1791FE6948C68763A4713C13C.pdf
- Rejeitado: Edital nº 3 - Retificação das datas constantes dos subitens 5.1 e 10.1 do Edital nº 2 – PF, concursos anteriores – de 30 de abril de 2026 — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/1CA5762B21B857B5B6FBA64E7467C83D8DB9179EF66A6538C0D98FDCD5E17D92.pdf
- Rejeitado: Edital nº 2 – Convocação de candidatos sub judice para matrícula no CFP e para a atualização da Ficha de Informações Confidenciais – FIC, referente aos concursos públicos regidos pelo Edital nº 9/2012 – DGP/DPF; pelo Edital nº 10/2012 – DGP/DPF e pelo Edital nº 1-DGP/PF; e pelo Edital nº 1 – DGP/PF — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/C156978FCCE628DB3E20B553BD265E903E0C981AA12996D06D49B0371D3C6D70.pdf
- Rejeitado: Edital nº 1 – Convocação de candidatos sub judice para matrícula no CFP, referentes aos concursos públicos regidos pelo Edital nº 24/2004 – DGP/DPF – Nacional, de 15 de julho de 2004; pelo Edital nº 15/2009-DGP/APF, de 24 de julho de 2009; pelo Edital nº 1-DGP/PF, de 14 de junho de 2018, e suas alterações; e pelo Edital nº 01 – DGP/PF, de 15 de janeiro de 2021, e alterações — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/3E76C0367BD2EFD1085E630458CFCD7F55B0C793AB4F0855C8FFF7BAA81FFA0B.pdf
- Rejeitado: Edital nº 112 – Reintegração de candidata sub judice. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/ED_112_DPF_2021_REINT_SUB_JUDICE_JESSICA.PDF
- Rejeitado: Edital nº 111 – Inclusão de candidato sub judice. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/ED_111_DPF_2021_INCL_SUB_JUDICE_RES_FINAL_1A_ETAPA_PAULO.PDF
- Rejeitado: Edital nº 109 - Convocação e matrícula no Curso de Formação Profissional (CFP), eliminação de candidatos, rematrículas e retificações — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/ED_109_SANEAMENTO_13454_.PDF
- Rejeitado: Edital nº 108 - Resultado final na avaliação psicológica (primeiro e segundo momentos) dos candidatos aos cargos de Delegado de Polícia Federal, Agente de Polícia Federal e de Escrivão de Polícia Federal matriculados no Curso de Formação Profissional — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/ED_108_PF_RES_FINAL_AVAL_PSICO_1_E_2_MOMENTOS.PDF
- Rejeitado: Edital nº 107 - Inclusão de candidatos no resultado provisório na avaliação psicológica (primeiro e segundo momentos) — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/ED_107_PF_INCLUSAO_DE_CANDIDATOS_NO_RES_PROV_AVAL_PSICO_1_E_2_MOMENTOS13375.PDF
- Rejeitado: Edital nº 106 - Resultado provisório na avaliação psicológica (primeiro e segundo momentos) dos candidatos aos cargos de Delegado de Polícia Federal, Agente de Polícia Federal e de Escrivão de Polícia Federal — `edital` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/ED_106_PF_RES_PROV_AVAL_PSICO_1_E_2_MOMENTOS.PDF
