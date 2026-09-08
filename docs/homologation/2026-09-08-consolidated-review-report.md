# Inventário consolidado da revisão editorial

- Especificação: `config/consolidated-pf-bb.v1.json`
- Identificador: `pf-bb-2018-2025`
- Base: `dcb4d245db96da2fb6d3a29ddf33be4b134b3b1c` (PR #99 integrado).
- Branch: `codex/consolidated-editorial-inventory`.
- Hash do inventário: `8ade7f46ed1df175f310d6ec375b44f6b899edd806a57e7d487ec047869851df`

## Números principais

| Indicador | Total |
|---|---:|
| Acervo total estruturado | 5420 |
| Acervo colocado na revisão | 5420 |
| Acervo apto para exportação | 0 |

Nenhuma aprovação foi criada automaticamente. `publicationStatus` permanece `draft` no fluxo de exportação controlada.

## Pacotes e integridade

| Pacote | Período | Documentos | Usados | Não usados | Provas estruturadas | Questões | Aceitas | Quarentena | Integridade |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Polícia Federal / Cebraspe | 2018/2021/2025 | 112 | 112 | 0 | 56 | 4108 | 3840 | 268 | verified |
| Banco do Brasil / Cesgranrio | referência 2021/2023 | 40 | 23 | 17 | 19 | 1312 | 1177 | 135 | verified |

### Caminhos e hashes

#### Polícia Federal / Cebraspe

- Pacote: `data/structured/pf-all/final.json`; arquivo `c53b76fec76444088ceb27d1ba7a88acb1dac127952a6991caae9ca0c0fa4417`; conteúdo `5f946b0ab11a03e8890c83eebad16e7ae2ca7ed3212f3e3da93d925afecc2490` (legacy-with-manifest).
- Anos no manifesto: [2018, 2021, 2025]; anos estruturados: [2018, 2021, 2025]; esperados: [2018, 2021, 2025].
- Manifesto: `data/homologation/cebraspe-pf-20260907/pf_18/manifests/download-20260907T133705Z-ba8f5d96.json`; SHA-256 `18bfc84e99d7bc43f87f787fea73dd8cfe491c748e933413447b61b9f6d1a6c0`.
- Manifesto: `data/homologation/cebraspe-pf-20260907/pf_21/manifests/download-20260907T133551Z-40527516.json`; SHA-256 `71b3e4191acb5111327953098365c7a66de0df96ad7bc1832ad6bbaa9d03fe75`.
- Manifesto: `data/homologation/cebraspe-pf-20260907/pf_25/manifests/download-20260907T133358Z-a5b8720e.json`; SHA-256 `48c99f5dcedb69fb1068dd5839d1e539b744b3aa988d480356e6afab06563833`.
- Manifesto: `data/homologation/cebraspe-pf-20260907/pf_25_adm/manifests/download-20260907T133520Z-b16dcaca.json`; SHA-256 `904df797cd913db98fb2e217c09b73dd45f87e3eee380dd4744dbc23f231522c`.

#### Banco do Brasil / Cesgranrio

- Pacote: `data/homologation/operator-run-bb-2021-2023/review-package.json`; arquivo `0a2021c6e345989cd34598810c7a57720434a48ed2ba72246bd6c7a93324695e`; conteúdo `4561eb36ecaec4adbabaa3bb5e2f7cb2b58c6d41b0f26544bf69749704b118cf` (current).
- Anos no manifesto: [2021, 2023]; anos estruturados: [2021]; esperados: [2021, 2023].
- Manifesto: `data/homologation/operator-run-bb-2021-2023/manifest.json`; SHA-256 `7c93079760d47e92f412deb78e23867d92b3b68d5bafb004e9cb06a42d5ac6e7`.


## Escopos

| Escopo | Questões | Aceitas | Quarentena | Anuladas | Classificadas | Decisões | Aptas para exportação |
|---|---:|---:|---:|---:|---:|---:|---:|
| Acervo total | 5420 | 5017 | 403 | 178 | 0 | 0 | 0 |
| Amostra homologada anteriormente | 1792 | 1624 | 168 | 18 | 0 | 0 | 0 |
| Lote aberto | 70 | 67 | 3 | 2 | 0 | 0 | 0 |

## Quarentena

### Por motivo

- elemento visual exige revisão: 98
- item anulado no gabarito oficial: 178
- texto de apoio não associado: 130

### Combinações exclusivas

- elemento visual exige revisão: 95
- elemento visual exige revisão + item anulado no gabarito oficial: 3
- item anulado no gabarito oficial: 175
- texto de apoio não associado: 130

## Classificação editorial

- Taxonomia canônica: `2.0.1`.
- Regras determinísticas aceitas: 0.
- Classificações Qwen aceitas: 0.
- Pendentes por falta de catálogo seguro: 5420.
- Chamadas ao Qwen: 0.
- Precisão da amostra: não medida; nenhuma classificação foi liberada sem correção humana.
- Rastro local: `data/consolidated/pf-bb/classification/traces.jsonl`.

## Alertas de linhagem

- Banco do Brasil / Cesgranrio: anos estruturados [2021] diferem dos esperados [2021, 2023].
- Banco do Brasil / Cesgranrio: 17 documento(s) do manifesto não têm questão no pacote estruturado.
- Banco do Brasil / Cesgranrio: a referência de quarentena para 'quantidade inválida de alternativas' é 19, mas o pacote final registra 0.
- Banco do Brasil / Cesgranrio: a referência de quarentena para 'elemento visual exige revisão' é 8, mas o pacote final registra 5.
- Banco do Brasil / Cesgranrio: a referência de quarentena para 'incompatibilidade entre alternativas e gabarito' é 3, mas o pacote final registra 0.
- Banco do Brasil / Cesgranrio: o pacote final registra 130 ocorrência(s) de 'texto de apoio não associado', categoria ausente na referência fornecida.

## Por órgão

| Valor | Questões | Aceitas | Quarentena | Rejeitadas | Anuladas |
|---|---:|---:|---:|---:|---:|
| Banco do Brasil | 1312 | 1177 | 135 | 0 | 0 |
| Policia Federal | 4108 | 3840 | 268 | 0 | 178 |

## Por banca

| Valor | Questões | Aceitas | Quarentena | Rejeitadas | Anuladas |
|---|---:|---:|---:|---:|---:|
| CEBRASPE | 4108 | 3840 | 268 | 0 | 178 |
| CESGRANRIO | 1312 | 1177 | 135 | 0 | 0 |

## Por concurso

| Valor | Questões | Aceitas | Quarentena | Rejeitadas | Anuladas |
|---|---:|---:|---:|---:|---:|
| PF 25 | 1248 | 1171 | 77 | 0 | 66 |
| PF 25 ADM | 1150 | 1107 | 43 | 0 | 39 |
| POLÍCIA FEDERAL | 1710 | 1562 | 148 | 0 | 73 |
| bb0121 | 1312 | 1177 | 135 | 0 | 0 |

## Por ano

| Valor | Questões | Aceitas | Quarentena | Rejeitadas | Anuladas |
|---|---:|---:|---:|---:|---:|
| 2018 | 1230 | 1115 | 115 | 0 | 55 |
| 2021 | 1792 | 1624 | 168 | 0 | 18 |
| 2025 | 2398 | 2278 | 120 | 0 | 105 |

## Por cargo

| Valor | Questões | Aceitas | Quarentena | Rejeitadas | Anuladas |
|---|---:|---:|---:|---:|---:|
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 1 | 68 | 60 | 8 | 0 | 0 |
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 2 | 68 | 60 | 8 | 0 | 0 |
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 3 | 69 | 60 | 9 | 0 | 0 |
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 4 | 69 | 60 | 9 | 0 | 0 |
| CARGO 1: DELEGADO DE POLÍCIA FEDERAL | 120 | 116 | 4 | 0 | 4 |
| CARGO 2: AGENTE DE POLÍCIA FEDERAL | 120 | 110 | 10 | 0 | 5 |
| CARGO 3: ESCRIVÃO DE POLÍCIA FEDERAL | 120 | 110 | 10 | 0 | 5 |
| CARGO 4: PAPILOSCOPISTA DE POLÍCIA FEDERAL | 120 | 111 | 9 | 0 | 4 |
| CONHECIMENTOS BÁSICOS PARA O CARGO 15 | 50 | 50 | 0 | 0 | 0 |
| CONHECIMENTOS BÁSICOS PARA O CARGO 2 - TODAS AS ÁREAS | 50 | 48 | 2 | 0 | 2 |
| CONHECIMENTOS BÁSICOS PARA OS CARGOS DE 1 A 14 | 50 | 49 | 1 | 0 | 1 |
| CONHECIMENTOS BÁSICOS PARA TODOS OS CARGOS DE PERITO CRIMINAL FEDERAL | 50 | 46 | 4 | 0 | 4 |
| CONHECIMENTOS BÁSICOS – BLOCO I – PARA OS CARGOS 15, 16 E 17 | 60 | 58 | 2 | 0 | 2 |
| CONHECIMENTOS BÁSICOS – BLOCO II – PARA OS CARGOS 15, 16 E 17 | 36 | 31 | 5 | 0 | 5 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 1 | 120 | 116 | 4 | 0 | 4 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 10/ÁREA 12 | 70 | 66 | 4 | 0 | 4 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 11/ÁREA 14 | 70 | 56 | 14 | 0 | 2 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 12 | 120 | 109 | 11 | 0 | 6 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 13 | 120 | 115 | 5 | 0 | 2 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 14 | 120 | 113 | 7 | 0 | 0 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 2/ÁREA 1 | 70 | 69 | 1 | 0 | 1 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 3/ÁREA 2 | 70 | 69 | 1 | 0 | 1 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 4 - ÁREA 3 | 70 | 56 | 14 | 0 | 11 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 5/ÁREA 4 | 70 | 67 | 3 | 0 | 3 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 6/ÁREA 5 | 70 | 55 | 15 | 0 | 10 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 7/ÁREA 6 | 70 | 54 | 16 | 0 | 2 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 8/ÁREA 7 | 70 | 55 | 15 | 0 | 5 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 9/ÁREA 9 | 70 | 67 | 3 | 0 | 2 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 1 | 190 | 183 | 7 | 0 | 7 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 10 | 140 | 136 | 4 | 0 | 4 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 11 | 140 | 129 | 11 | 0 | 11 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 12 | 140 | 132 | 8 | 0 | 8 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 13 | 140 | 132 | 8 | 0 | 8 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 14 | 140 | 133 | 7 | 0 | 7 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 15 | 94 | 91 | 3 | 0 | 3 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 16 | 24 | 21 | 3 | 0 | 3 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 17 | 24 | 23 | 1 | 0 | 1 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 2 | 140 | 124 | 16 | 0 | 9 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 3 | 140 | 139 | 1 | 0 | 1 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 4 | 140 | 135 | 5 | 0 | 5 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 5 | 140 | 132 | 8 | 0 | 8 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 6 | 140 | 130 | 10 | 0 | 6 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 7 | 140 | 132 | 8 | 0 | 4 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 8 | 140 | 135 | 5 | 0 | 5 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 9 | 140 | 137 | 3 | 0 | 3 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 1 | 68 | 64 | 4 | 0 | 0 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 2 | 69 | 65 | 4 | 0 | 0 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 3 | 69 | 65 | 4 | 0 | 0 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 4 | 69 | 65 | 4 | 0 | 0 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 5 | 68 | 63 | 5 | 0 | 0 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 1 | 69 | 61 | 8 | 0 | 0 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 2 | 69 | 61 | 8 | 0 | 0 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 3 | 69 | 61 | 8 | 0 | 0 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 4 | 69 | 61 | 8 | 0 | 0 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 5 | 69 | 61 | 8 | 0 | 0 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 1 | 70 | 62 | 8 | 0 | 0 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 2 | 70 | 62 | 8 | 0 | 0 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 3 | 70 | 62 | 8 | 0 | 0 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 4 | 70 | 62 | 8 | 0 | 0 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 5 | 70 | 62 | 8 | 0 | 0 |

## Por estado

| Valor | Questões | Aceitas | Quarentena | Rejeitadas | Anuladas |
|---|---:|---:|---:|---:|---:|
| accepted | 5017 | 5017 | 0 | 0 | 0 |
| quarantined | 403 | 0 | 403 | 0 | 178 |
| rejected | 0 | 0 | 0 | 0 | 0 |

## Limitações e ação humana

- 5420 questões aguardam revisão humana explícita.
- A classificação editorial ainda não foi aceita sem correção: os catálogos canônicos atuais não cobrem integralmente PF e Banco do Brasil.
- O Qwen não é chamado sem opções canônicas suficientes; indisponibilidade do Ollama não impede a criação da fila.
- As categorias históricas da quarentena são comparadas com os motivos efetivamente presentes nos pacotes, sem apagar divergências.
- Nenhum PDF, sessão completa, conteúdo de prova, credencial ou dado foi incluído neste relatório.

## Defeitos encontrados

| Problema | Causa | Tratamento nesta fase |
|---|---|---|
| O adaptador do PR #99 aceitava apenas uma execução | A criação dos lotes estava acoplada ao diretório de um `operator run` | O gerador de lotes foi extraído e reutilizado pelo inventário consolidado |
| O pacote histórico da PF não validava pela política de hash atual | O pacote foi criado antes de o hash dos manifestos ser retirado do conteúdo semântico | O validador reconhece exatamente as duas políticas, registra `legacy-with-manifest` e continua verificando todos os PDFs |
| O pacote chamado BB 2021/2023 contém somente questões de 2021 | Os 17 cadernos de 2023 presentes no manifesto não foram associados a gabaritos nem estruturados | Não corrigido silenciosamente; os documentos ficam inventariados como não usados e a linhagem é bloqueada para correção posterior |
| A quarentena do BB não coincide com a referência histórica | O pacote final registra 130 textos de apoio não associados e cinco dependências visuais, enquanto a referência descreve categorias de uma etapa anterior | Ambas as visões foram preservadas no relatório; nenhum bloqueio foi removido |

## Validação executada

| Verificação | Resultado |
|---|---|
| Testes do inventário e da ponte | 14 testes aprovados |
| Suíte completa | 945 testes e 114 subtestes aprovados |
| Lint | aprovado |
| Tipos | 80 arquivos aprovados |
| JavaScript da interface | sintaxe aprovada |
| Integridade dos artefatos reais | 152 documentos locais conferidos por SHA-256 |
| Execução real | 5.420 questões, 75 lotes ativos, 403 quarentenas e zero aprovações automáticas |
| Idempotência | mesmo hash `8ade7f46ed1df175f310d6ec375b44f6b899edd806a57e7d487ec047869851df`; nenhuma sessão alterada na repetição |
| Ollama disponível | Ollama 0.33.3; `qwen3:8b` Q4_K_M com digest aprovado |
| Ollama indisponível simulado | mesmos 5.420 itens e mesmo hash do inventário |
| Exportação real sem aprovação | recusada; nenhum arquivo importável criado |
| Build | wheel `kad_collector-0.4.0-py3-none-any.whl`, SHA-256 `29fd99d1ffb90e4109ed0d2d9f11a8da06bab7d4e03976b1c1ec061ea0224582` |
| Instalação isolada | pacote instalado e consolidação dos 5.420 itens concluída |
| Smoke test desktop e preparo do OCR | aprovados |

Comandos principais: `pytest tests -q`, `ruff check src tests`, `mypy src`,
`node --check src/kad_collector/desktop_app.js`, `pip wheel . --no-deps` e
`kad-collector consolidate-review config/consolidated-pf-bb.v1.json`.

Não houve escrita no Supabase, importação no KAD ou publicação. A precisão editorial
permanece sem valor medido porque nenhuma classificação PF/BB foi aceita; portanto, o
limite de 95% não foi alegado nem contornado.
