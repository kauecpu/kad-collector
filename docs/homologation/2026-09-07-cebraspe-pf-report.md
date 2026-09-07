# Homologação Cebraspe — Polícia Federal

- Data UTC: 2026-09-07T13:37:34.455850+00:00
- Commit: `35b8cd1-dirty`
- Ollama: `{"available": true, "qwen3_8b": true, "mode": "online"}`
- Precisão: 100.0%
- Cobertura: 100.0%
- Associação prova/gabarito: 100.0%
- OCR: não acionado
- Esperados/encontrados: 112/112
- Provas encontradas: 56
- Gabaritos encontrados: 56
- Falsos positivos: 0
- Falsos negativos: 0
- Documentos proibidos aceitos: 0
- Fontes concluídas sem intervenção: 100.0%
- Tempo médio por fonte: 65.40s
- Decisões determinísticas: 112
- Decisões com `qwen_fallback`: 0
- Propostas do Qwen aceitas/recusadas: 0/0

## Conclusão

A fonte Cebraspe/Polícia Federal atingiu os critérios mínimos: 100% de precisão,
100% de cobertura, 100% de associação, nenhum documento administrativo aceito e
nenhuma intervenção manual. Os 112 PDFs foram validados pelo coletor e permaneceram
somente em `data/`, fora do Git e sem publicação no KAD ou no Supabase.

## Método e escopo

- A matriz foi congelada antes do download a partir do catálogo e dos detalhes públicos
  oficiais da API do Cebraspe. Ela contém 112 documentos esperados em quatro concursos.
- As páginas públicas são aplicações JavaScript. O HTML foi tentado primeiro e o navegador
  determinístico renderizou os links publicados pela própria API oficial.
- Somente `www.cebraspe.org.br`, `apis.cebraspe.org.br` e `cdn.cebraspe.org.br` foram usados.
  A política de `robots.txt`, os redirecionamentos, a assinatura PDF e os limites do coletor
  permaneceram ativos.
- O acervo PF 2018 cobre a origem histórica CESPE/CESPE-UnB. Os aliases `Cebraspe`, `CESPE`,
  `CESPE/UnB`, `Polícia Federal`, `PF` e abreviações usuais dos cargos federais possuem testes.
- O caminho determinístico encontrou todos os documentos. Por isso o Qwen não foi chamado;
  chamar o modelo mesmo com a seleção completa contrariaria a regra de fallback.
- Os 112 arquivos já tinham texto utilizável. O OCR foi corretamente dispensado; não houve
  PDF digitalizado nessa amostra real para medir o motor de OCR.

## Correções e cobertura adicionada

- Cadastro da fonte oficial nas configurações de desenvolvimento e do pacote distribuído.
- Seleção de provas objetivas e gabaritos preliminares ou definitivos, com rejeição de
  editais, resultados, convocações, comunicados, provas orais e provas discursivas.
- Aliases gerais para Agente (`APF`), Escrivão (`EPF`), Papiloscopista (`PPF`), Perito
  Criminal (`PCF`) e Delegado da Polícia Federal.
- Matriz oficial reproduzível, relatório com hashes e testes de regressão para seleção,
  aliases, emparelhamento e isolamento de falha.
- Nenhum defeito no núcleo geral introduzido pelo PR #95 foi reproduzido. As mudanças no
  núcleo ficaram limitadas à expansão geral de aliases; não foi criada regra de descoberta
  exclusiva para uma URL da Polícia Federal.

## Validação executada

- `.venv\\Scripts\\python.exe -m pytest tests -q`: 903 testes e 114 subtestes aprovados.
- `.venv\\Scripts\\python.exe -m ruff check src tests scripts`: aprovado.
- `.venv\\Scripts\\python.exe -m mypy src`: 76 módulos aprovados.
- `node --check src/kad_collector/desktop_app.js`: aprovado.
- `.venv\\Scripts\\python.exe -m kad_collector.desktop_app --smoke-test`: aprovado.
- `.venv\\Scripts\\python.exe -m build --no-isolation`: sdist e wheel gerados.
- Instalação limpa do wheel com `pip --target` e leitura da configuração empacotada:
  fonte `cebraspe_policia_federal` encontrada com quatro entradas e estratégias HTML/browser.
- Homologação real com `qwen3:8b` disponível: 112/112, sem falhas.
- Homologação real com Ollama indisponível, usando PF 2021: 8/8, sem falhas. Resultado em
  `docs/homologation/2026-09-07-cebraspe-pf-ollama-offline-report.md`.
- Repetição de PF 2021: os mesmos oito URLs e hashes, sem variação.
- `git diff --check`: aprovado.

O build isolado padrão não pôde criar seu ambiente temporário porque a distribuição Python
local não contém o módulo padrão `venv`. O build sem isolamento, usando as dependências já
instaladas, e a instalação limpa do wheel passaram. O CI usa Python oficial com `venv`.

## Limitações restantes

- As quatro páginas oficiais não publicavam gabarito preliminar no momento da matriz. A
  classificação preliminar está coberta por teste, mas não por um arquivo real desta amostra.
- Nenhum PDF da amostra exigiu OCR, então a taxa real de OCR é “não aplicável”, não 100%.
- Não houve paginação nas páginas selecionadas; o limite de navegação continua coberto pela
  suíte geral do coletor.
- O `qwen_fallback` permaneceu em zero porque não ocorreu falha determinística. A telemetria
  e a indisponibilidade do Ollama continuam cobertas pela suíte geral e pelo teste offline.

## Idempotência

- Amostra repetida: `pf_21`
- Mesmo conjunto de URLs e hashes: `True`
- Contagens original/repetição: 8/8

## pf_25 — PF 25

- URL inicial: https://www.cebraspe.org.br/concursos/pf_25
- Banca identificada: CEBRASPE
- Esperados/encontrados: 40/40
- Provas/gabaritos aceitos: 20/20
- Precisão/cobertura: 100.0%/100.0%
- Associação prova/gabarito: 100.0%
- Caminho: deterministic_browser
- Páginas visitadas: 2
- Chamadas ao Qwen: 0
- Tempo: 90.37s
- OCR tentado/suficiente: 0/0
- Falhas tratadas: 0
- Exemplos rejeitados: 20

### Documentos aceitos

- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 1 — `677dacc04423c68867bd3e312bd6f4f36af9d531d1e8a46536ffabd0b593b162` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/D202E48A0B1EBF5039E398F0B49D0186DD625DFF53498E36EE4D88DE4501E3A8.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS BÁSICOS PARA TODOS OS CARGOS DE PERITO CRIMINAL FEDERAL — `ff75dc45f1bf683e5708d18fe943d227bc25f231076a27c6d73b4b1254e80c49` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/5A49198A6084706823226C79C28FFDBB1129FD5D938C9919AD9C1FC97E54701A.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS BÁSICOS – BLOCO I – PARA OS CARGOS 15, 16 E 17 — `7b98a362feefd04ea74422ee2d66307414ce706408fbbb3b8745b8f47c41e46c` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/D0F881898A969B6733B878291FC1201E6CFE079281391E04B7EE322628BC888C.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS BÁSICOS – BLOCO II – PARA OS CARGOS 15, 16 E 17 — `f4f075f7e42dacfa04727f291e2b55c15b50ce1bd51930e7773169cc3b33c783` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/A78F5BA07173AF0B8A5E611A1D7AAB166B7BD473E5D7AC2220F0F1B7DDB641E9.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 2 — `94551ad5e43a4d54514942f67e0841ed67a8e5683fb85689e256bc09fae81a29` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/B918D6A805194D299F15922339CA17D2C3921BF015DFD2B258EEFFE487A53F22.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 3 — `b99ddca03ba945e6ef079e1ff8dfa50b51cc59ba443177c65fa4218a8d455b26` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/2957F96327337126177C0FC57B9996A02B966EAE7A1419F7CEE9D4C686F77B6E.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 4 — `a91c5f0a69f19c8179855b78112368b038b7a0b2595e5ac449b3b06de7f7f182` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/C3612023C0E2243979FBA27DE2DD60A80FA5FCD9054B4D9664D9E51356E599EE.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 5 — `f712bfc36442408fa2f26c4dc4bf98c05ee4c609148e52f6539cfecad0c30ad1` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/9E550CE72E70B14514C5A47A7A0F3CD30B56607FF45010612931D046BBA96D23.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 6 — `77feac7cec73407adf3086765c3e123b13ecf3ffb9596b4c3e499c936a8e3909` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/69579E7D6152E8A1DDCEFA1B6C4A6DCBDAAB69B934E5C17D04FC88BA150BED2B.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 7 — `f4aa9a4c68eb19aa00c2b9f9248dd94ca16b1df0cd6fe3cc1a0ccd70edd1e323` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/69E40522AFDEB12F65A8F018C256D678CAF9787CB383C19E8AD49838B87DDE32.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 8 — `7c939db30295958c171e0d6c856c9fe0517b7ee9fd2af8028d801c6cff676cd6` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/A4317E37ECE8E8AA749DB4D62EEEE09EE11BBEFE4CAD3F6DCA02B0B5B85050EB.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 9 — `f2129c9a6196963196352fe2b11393122599d631277a3fe6c81964f3d4180977` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/DAC7F9CA42277AEBFF4D7B97F7A953DBAEE67FAF37C5FBD06493B01E7E4148B4.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 10 — `250e937dd70cf8c9cb9cebb1eb091c0b1e7564fc689b10ed2d4a458817b3bd35` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/A20B3AA111033AF8554D1FD0FAC6456DB5C0B7B9D86783FB9896D4603D5C2524.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 11 — `26e8920f063321251d0cb8ede0fb6a27bc030a1d206fb8d7d2665964e7dc4922` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/8C568F1F09BC80CF9149D2DFC247EC5784C5E01825928048D37A80F3F7A60D77.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 12 — `1613b6b28b0ab11d185e90095c293b6177463b90dfd2134a7a8724464ca688f7` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/643F5923E964CA1EFCA681AAED5B653BA0136E288E0BF3E23DB9F3F053D1F77E.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 13 — `76c9e6f891c199f3693fe93569864844a057be82fbd9aea6904e1ed0af3c0650` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/2B1B28107AE0168F6AF795DF71CF83536A1AD39CBC1B405DA6BF3D261A1867CF.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 14 — `261aef4037271e7c61413e6fb46c24a1dcc7c757ca05f71fa7c3fb143a9666ce` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/281353E4B98F8BC1C8109000C6D0BC06D2C00D30E8523F484883219764A65855.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 15 — `bac095506af9df1f2a10d76d4f355854bccf6163d227d7fa567a702811acb884` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/570C8865F7DDA1651ED10E330A6297A95466A2393EEEC633290FE754912D6143.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 16 — `629456cbe67ffb38507dd1744719baa260e77dbea8eaca3172190894c29cd2f2` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/CDC24D454696C9FE260E6C50FE758707F322649AEE24B6FB664FDDA10E307BCF.pdf
- `answer_key` GABARITO DEFINITIVO – CONHECIMENTOS ESPECÍFICOS – CARGO 17 — `41fd7c64f73b48d8873bd8a60549c03f666314e295b810ab6c3fab78ce96de5f` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/448F54940D877880045A494008870FC0B25C57C9D7233ECD0CDC29B942A683AD.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 17 — `2801bf8d85ebd35aad40fda33cf87633b0bd4e8b8e68382cacdf19275f8a70af` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_017_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS BÁSICOS PARA TODOS OS CARGOS DE PERITO CRIMINAL FEDERAL — `08574b971d557f9eba566567bab3dc48594ca9b86647530e6576cf5bd1d172bb` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_CB1_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS BÁSICOS – BLOCO I – PARA OS CARGOS 15, 16 E 17 — `61c731f9638643a84a21188a0bcd6e9b85949604e8ba75051c971411641890a7` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_CB2_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS BÁSICOS – BLOCO II – PARA OS CARGOS 15, 16 E 17 — `25c6783ccbfc3d72fbb0913da58372e344ba45fc6e1ba74250727fa3e56e51f3` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_CG1_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 1 — `f486b7d16c70981141660b3067c36ef96e5be23c9f00a2a8ef2922882981814d` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_001_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 2 — `43f01f62df2b140abf3c561191103bd2f6fb3e56777e4ddc0f90b451383ccae0` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_002_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 3 — `163fc806c8db7c68ca8ba370d4d562d6dae6255015de1bfbd459777357a24c36` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_003_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 4 — `8b3b0e8fd407ec24bcf37985518c9bcff14882416befb61faa3448cfac8f1f97` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_004_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 5 — `aeaa15548963cec7af7a0a6b7cdca5ae40cabe5cbae9f154f69bf31a4f73ab78` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_005_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 6 — `8c41972904bafb8a241d6c662580809b2849a2d190b2143059af45e4db76af09` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_006_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 7 — `f9a7025ec245ae54ed0ded29fa6f3df23c9253a567a0390113aab914de32be97` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_007_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 8 — `5a932204f0dfd6f710123d696a14f4eef2b50b0f6d78c59bc4b82d690ef7191b` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_008_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 9 — `c421c7a7c7fdf42647ace50b8c32569c085daf263c4253a46b195bdd42f83cb1` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_009_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 10 — `9b38106d8b388807551afe4bcf73814eddec880e8cbb4360919a570f380f1199` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_010_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 11 — `244a4feca8bcd6be89b703d01fa6298ce99d421e74b37313316fe3c1947fe62c` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_011_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 12 — `71740e6bb7bef2697eff245d6a08b38d370084dd7f0dd89eeb96600ca9824e03` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_012_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 13 — `07a8b28f1734a96760c94cdd860a4d12d666b89593a5addfdd965b9ba16aa917` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_013_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 14 — `9efb1d9708f715a8607c75bd29880b72d5f94fde1e601059c3f7e694dd567cb7` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_014_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 15 — `128c7fe23f87fb3111d2e48b22268dbea164790109fc63dff9f2b1602b954d37` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_015_01.pdf
- `exam` PROVA OBJETIVA – CONHECIMENTOS ESPECÍFICOS – CARGO 16 — `8f5962ae0dde2d3cf2652c3eff3e3c263c12486952f45ab4761b7dc0d1550f06` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/106_PF_016_01.pdf

### Associações prova/gabarito

- `cargo_1` — associado; provas=106_PF_001_01.pdf; gabaritos=D202E48A0B1EBF5039E398F0B49D0186DD625DFF53498E36EE4D88DE4501E3A8.pdf
- `cargo_10` — associado; provas=106_PF_010_01.pdf; gabaritos=A20B3AA111033AF8554D1FD0FAC6456DB5C0B7B9D86783FB9896D4603D5C2524.pdf
- `cargo_11` — associado; provas=106_PF_011_01.pdf; gabaritos=8C568F1F09BC80CF9149D2DFC247EC5784C5E01825928048D37A80F3F7A60D77.pdf
- `cargo_12` — associado; provas=106_PF_012_01.pdf; gabaritos=643F5923E964CA1EFCA681AAED5B653BA0136E288E0BF3E23DB9F3F053D1F77E.pdf
- `cargo_13` — associado; provas=106_PF_013_01.pdf; gabaritos=2B1B28107AE0168F6AF795DF71CF83536A1AD39CBC1B405DA6BF3D261A1867CF.pdf
- `cargo_14` — associado; provas=106_PF_014_01.pdf; gabaritos=281353E4B98F8BC1C8109000C6D0BC06D2C00D30E8523F484883219764A65855.pdf
- `cargo_15` — associado; provas=106_PF_015_01.pdf; gabaritos=570C8865F7DDA1651ED10E330A6297A95466A2393EEEC633290FE754912D6143.pdf
- `cargo_16` — associado; provas=106_PF_016_01.pdf; gabaritos=CDC24D454696C9FE260E6C50FE758707F322649AEE24B6FB664FDDA10E307BCF.pdf
- `cargo_17` — associado; provas=106_PF_017_01.pdf; gabaritos=448F54940D877880045A494008870FC0B25C57C9D7233ECD0CDC29B942A683AD.pdf
- `cargo_2` — associado; provas=106_PF_002_01.pdf; gabaritos=B918D6A805194D299F15922339CA17D2C3921BF015DFD2B258EEFFE487A53F22.pdf
- `cargo_3` — associado; provas=106_PF_003_01.pdf; gabaritos=2957F96327337126177C0FC57B9996A02B966EAE7A1419F7CEE9D4C686F77B6E.pdf
- `cargo_4` — associado; provas=106_PF_004_01.pdf; gabaritos=C3612023C0E2243979FBA27DE2DD60A80FA5FCD9054B4D9664D9E51356E599EE.pdf
- `cargo_5` — associado; provas=106_PF_005_01.pdf; gabaritos=9E550CE72E70B14514C5A47A7A0F3CD30B56607FF45010612931D046BBA96D23.pdf
- `cargo_6` — associado; provas=106_PF_006_01.pdf; gabaritos=69579E7D6152E8A1DDCEFA1B6C4A6DCBDAAB69B934E5C17D04FC88BA150BED2B.pdf
- `cargo_7` — associado; provas=106_PF_007_01.pdf; gabaritos=69E40522AFDEB12F65A8F018C256D678CAF9787CB383C19E8AD49838B87DDE32.pdf
- `cargo_8` — associado; provas=106_PF_008_01.pdf; gabaritos=A4317E37ECE8E8AA749DB4D62EEEE09EE11BBEFE4CAD3F6DCA02B0B5B85050EB.pdf
- `cargo_9` — associado; provas=106_PF_009_01.pdf; gabaritos=DAC7F9CA42277AEBFF4D7B97F7A953DBAEE67FAF37C5FBD06493B01E7E4148B4.pdf
- `conhecimentos_basicos_bloco_i` — associado; provas=106_PF_CB2_01.pdf; gabaritos=D0F881898A969B6733B878291FC1201E6CFE079281391E04B7EE322628BC888C.pdf
- `conhecimentos_basicos_bloco_ii` — associado; provas=106_PF_CG1_01.pdf; gabaritos=A78F5BA07173AF0B8A5E611A1D7AAB166B7BD473E5D7AC2220F0F1B7DDB641E9.pdf
- `conhecimentos_basicos_peritos` — associado; provas=106_PF_CB1_01.pdf; gabaritos=5A49198A6084706823226C79C28FFDBB1129FD5D938C9919AD9C1FC97E54701A.pdf

### Rejeições e falhas

- Rejeitado: PROVA ORAL - CARGO DELEGADO (APLICADO DIA 19/7/2026) SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/CDC7CFAADFFCABACF7DEEF6B1C4EC87CEAC22995AD7BC9550D017C17FE24E431.pdf
- Rejeitado: PROVA ORAL — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/25884E2722C53188F09613E726AA9C1D798AC0810765EE9928BBC06C1D72276C.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTAS – PROVA DISCURSIVA – CARGO 1 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/2FFD50E84784D30C2B2B3D92EE2237059B94820D0DE2FAA9F8417ECC69FD93A9.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 2 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/A42D73A37D7C3C84265EBA68F0A8DD2ED24B7FDF2000A0D0C52DE4DB78A1DCC2.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 3 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/E6F9E8AEDA4323C0F950613F7DD0FF7C50FD53F7EE3C0876EB074028BEF48D39.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 4 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/D2D4C6C3DCB37954F7268220891582B67F81A41042156DFD8140D313792EAD8D.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 5 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/935D90E4167DF7A6595EF6C8F7A647B9CA1FC137B0B72615C93D08823B91654C.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 6 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/30828DE83D445ED1F69232D50CA6359C3AEB5913C14E26F8D58BA750EF52F391.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 7 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/B37BAB34751C90BB427B8CC44158900FDEA3E84854322B4FD1C7D7272E64064D.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 8 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/4FD66C442BA3EA12353908299508FFB5BA2B7181C422C2D3BD4F4857942A481A.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 9 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/E4D673BB1BE75A3DA7BFC0535E3BCB56871D67EB6E73B7B3B73D44BA36F9B2F7.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 10 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/73B0463D22FCF7D013D7945CD32AD82B9B5EF58D98807447D916EFB11DA06A20.pdf
- Rejeitado: Edital nº 45 - Homologação do resultado final do concurso público para provimento de vagas nos cargos de Delegado De Polícia Federal, Agente De Polícia Federal, Escrivão De Polícia Federal e Papiloscopista Policial Federal, regido pelo Edital nº 1 – PF – POLICIAL, de 20 de maio de 2025 — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/2F3403C4C3EB534589D5314F58233D21335450E57D37D98364B754820C92AAD3.pdf
- Rejeitado: PORTARIA DIREN-ANP PF Nº 633 — `fora_do_escopo_objetivo` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/2B60899B2BA66EBFF58F845CA413209BF96DD8CBDA16129A7A0EE83875B992FA.pdf
- Rejeitado: PORTARIA DIREN-ANP PF Nº 632 — `fora_do_escopo_objetivo` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/215130F9C3AF9E88170C8D2BA061DFB318915B71A60D947D5FCE45BE97695EBD.pdf
- Rejeitado: PORTARIA DIREN-ANP PF Nº 631 — `fora_do_escopo_objetivo` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/D2733A2650C691EEE3CC98F68BF95AE068454E2199BB46BA5676A5F921DF9BEC.pdf
- Rejeitado: PORTARIA DIREN-ANP PF Nº 630 — `fora_do_escopo_objetivo` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/4FAF96137BB5E45C1BCFF5FA486AE037D5F7EF6B7537BD1E1A33DD85F25232D5.pdf
- Rejeitado: Edital nº 44 - Convocação, em terceira chamada, para matrícula no Curso de Formação Profissional – segunda turma, somente para o cargo de Agente de Polícia Federal — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/ABCC378DC243DF33F95D5D3D042EFCA85E84ED5BAC2CB30B1B31D4FC4042643E.pdf
- Rejeitado: Edital nº 43 - Convocação, em segunda chamada, para matrícula no Curso de Formação Profissional – segunda turma, somente para o cargo de Agente de Polícia Federal — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/6881C09BA6AA385382C32C81E6294746D6314E9D2B0F8D892A56AC250BB23C08.pdf
- Rejeitado: Edital nº 42 - Resultado final na avaliação psicológica, exceto para o cargo de Agente de Polícia Federal — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25/arquivos/1E0F81E52D8CA55FFE17F71A63942AE959826ADC24AD0366D07B2027C98D4F3D.pdf

## pf_25_adm — PF 25 ADM

- URL inicial: https://www.cebraspe.org.br/concursos/pf_25_adm
- Banca identificada: CEBRASPE
- Esperados/encontrados: 34/34
- Provas/gabaritos aceitos: 17/17
- Precisão/cobertura: 100.0%/100.0%
- Associação prova/gabarito: 100.0%
- Caminho: deterministic_browser
- Páginas visitadas: 2
- Chamadas ao Qwen: 0
- Tempo: 76.24s
- OCR tentado/suficiente: 0/0
- Falhas tratadas: 0
- Exemplos rejeitados: 20

### Documentos aceitos

- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 1 — `c8bb3c0699688525c573f4e7cece938a5e5c64d4d2493443188f90d41b18d8bc` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_001_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS BÁSICOS PARA OS CARGOS DE 1 A 14 — `affbca13cf0309a1bf782f46a00292621e512b9e39233ccdb96a1c18a71ba69d` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_CB1_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS BÁSICOS PARA O CARGO 15 — `088a636f71e355bab3ac5d33584798835757b38432f7400b9c4229ef334bd1b7` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_CB2_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 2 — `c3e224399d7006b92bd4b21afc9d21b72398f490f2ffcf89c0281fd515ac4761` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_002_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 3 — `729430af86a063ab171fc49b7dd10d91f697aa65233e883f390c3adbcfb96b98` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_003_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 4 — `545cd09816d0759b4140454a47b4c0b6795b2be273fe7ab84b39c1364576e802` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_004_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 5 — `fb2a2ce5dcd32f28a92b708e5c7c8d2c0ffec5a39c712af5ab70a273d51daf9f` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_005_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 6 — `9413d5d44ec25659a912a4732e4bcc98ca6639c6c7291a8ffb397beeb3eb6d20` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_006_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 7 — `7f18f904fe090d9120452aa8c5ceb3d8b81bf73c8a8c4b1704f8601cd6861d98` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_007_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 8 — `05194dad2616727b22f291e5f5a1c3994f28054e64cfe6d1d722756d1499d95f` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_008_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 9 — `26b4c80a70763a9acbbab96afcd6d3011f9d737cd1e32aeb027315f77c5959ff` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_009_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 10 — `da35a0aef011cb9179b64744a66169b3e74d16f10aafd2d13c80e9e651c1fa9d` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_010_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 11 — `cc8570249008487e6b5ee5d9db10c78ae7f22208d96916ec11ac9ae71a7a72f5` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_011_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 12 — `fce41fe30d0e598594e516eb4d791d200a713ac1c4c012d3b0a771f94329f3c7` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_012_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 13 — `bc66f2097cb52324ce1c435ce238256cc5c0daa56b127766eabd0363de03417d` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_013_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 14 — `55cef9c83222ed5f52e5ee46c8270aa23d68f5486e173e0c54dfa5c5e4677300` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_014_01.pdf
- `answer_key` GABARITO DEFINITIVO - CONHECIMENTOS ESPECÍFICOS – CARGO 15 — `a3807c460a35ce705bb9287cd01952834a3bf57dd72e34f8c6cebf6f2f111648` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/Gab_Definitivo_094_PF_015_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS BÁSICOS PARA OS CARGOS DE 1 A 14 — `282659632592cb616425e4f15a6c22a6f86d5d47869893e9b81489d73291ba72` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_CB1_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS BÁSICOS PARA O CARGO 15 — `548305f1a6036a32035f1ea413d0c489a194862b25036e38dc55935a4875b042` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_CB2_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 1 — `c987fed23486f6fd7551a629e5169658b49889edfbb0fbee3144f56d0ba5a8ed` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_001_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 2 — `cdc5f886c152804ad849a349235d16c6049fd9dddb42488cc2c9bc9dfbbcfbbb` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_002_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 3 — `3f132798cdc431ba11bdbd35cfbc40a6b2c334ea5fd40ddc162ba1a37cd66bc5` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_003_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 4 — `4e40a25f525281521b34678513e174403faf795c5a776bf5f9bc4229169a548c` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_004_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 5 — `516c412a8f5e716ae9098756a4e966c827c0c069737efcabe7bfddecd258fa37` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_005_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 6 — `4a9103f066b7dd67e05e4db3652a538a7318bc45ea8123d2dd9a0b3b4f547267` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_006_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 7 — `4ae5fd6fa0f336c4feae7bb4d0af02ae07a555f2624c49960ab814b5dcf09c69` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_007_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 8 — `427a2567fb89ceca56f76f7ba36729736781dd838dfbd9bfa371b7dabf1e6751` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_008_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 9 — `933ce683210a1e095ec12a4017ee64d399de1c4ae669910410a94548e7071b7d` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_009_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 10 — `d7c53857284a19112106e51aba63038d9a5393fd2a48fe3683c4b459c63cb83d` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_010_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 11 — `0a4c75f95d736cc3c60ab823172bbb7235135adfd162b38e297d4deb97b9125f` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_011_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 12 — `5692ec0eba5e76d81c24f51b207c66f95e31d6c99389ff47014ddd198dc150a9` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_012_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 13 — `20737595a80a81d0c98d73db63a174e0f844fed233467835cd3f50c12f5e1dca` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_013_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 14 — `c8705c2e6fdee3f3a53e5e6dd935eeff8348da53f9b73fe8751ceb1e81dc0d1b` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_014_01.pdf
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS – CARGO 15 — `8a7134566d26bdec5ef9d5d6c02d8477a3ce5f3dea003958acd07c3dcfe05af2` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/094_PF_015_01.pdf

### Associações prova/gabarito

- `cargo_1` — associado; provas=094_PF_001_01.pdf; gabaritos=Gab_Definitivo_094_PF_001_01.pdf
- `cargo_10` — associado; provas=094_PF_010_01.pdf; gabaritos=Gab_Definitivo_094_PF_010_01.pdf
- `cargo_11` — associado; provas=094_PF_011_01.pdf; gabaritos=Gab_Definitivo_094_PF_011_01.pdf
- `cargo_12` — associado; provas=094_PF_012_01.pdf; gabaritos=Gab_Definitivo_094_PF_012_01.pdf
- `cargo_13` — associado; provas=094_PF_013_01.pdf; gabaritos=Gab_Definitivo_094_PF_013_01.pdf
- `cargo_14` — associado; provas=094_PF_014_01.pdf; gabaritos=Gab_Definitivo_094_PF_014_01.pdf
- `cargo_15` — associado; provas=094_PF_015_01.pdf; gabaritos=Gab_Definitivo_094_PF_015_01.pdf
- `cargo_2` — associado; provas=094_PF_002_01.pdf; gabaritos=Gab_Definitivo_094_PF_002_01.pdf
- `cargo_3` — associado; provas=094_PF_003_01.pdf; gabaritos=Gab_Definitivo_094_PF_003_01.pdf
- `cargo_4` — associado; provas=094_PF_004_01.pdf; gabaritos=Gab_Definitivo_094_PF_004_01.pdf
- `cargo_5` — associado; provas=094_PF_005_01.pdf; gabaritos=Gab_Definitivo_094_PF_005_01.pdf
- `cargo_6` — associado; provas=094_PF_006_01.pdf; gabaritos=Gab_Definitivo_094_PF_006_01.pdf
- `cargo_7` — associado; provas=094_PF_007_01.pdf; gabaritos=Gab_Definitivo_094_PF_007_01.pdf
- `cargo_8` — associado; provas=094_PF_008_01.pdf; gabaritos=Gab_Definitivo_094_PF_008_01.pdf
- `cargo_9` — associado; provas=094_PF_009_01.pdf; gabaritos=Gab_Definitivo_094_PF_009_01.pdf
- `conhecimentos_basicos_cargo_15` — associado; provas=094_PF_CB2_01.pdf; gabaritos=Gab_Definitivo_094_PF_CB2_01.pdf
- `conhecimentos_basicos_cargos_1_14` — associado; provas=094_PF_CB1_01.pdf; gabaritos=Gab_Definitivo_094_PF_CB1_01.pdf

### Rejeições e falhas

- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 1 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_1.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 2 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_2.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 3 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_3.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 4 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_4.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 5 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_5.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 6 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_6.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 7 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_7.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 8 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_8.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 9 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_9.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 10 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_10.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 11 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_11.pdf
- Rejeitado: PADRÃO DEFINITIVO DE RESPOSTA – PROVA DISCURSIVA – CARGO 12 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/PF_25_ADM_PADR%C3%83O_DE_RESPOSTA_DEFINITIVO_CARGO_12.pdf
- Rejeitado: Edital nº 13 -  Inclusão dos candidatos sub judice no resultado final na avaliação biopsicossocial, no resultado final no procedimento de heteroidentificação e no resultado final concurso público — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/06FEACCE317D488C0C277EE10F8D2BBBCD08DF99ECBB8617A29416F803634531.pdf
- Rejeitado: Edital nº 12 - Inclusão de candidata inscrita sob o nº 10010615, no resultado final no concurso público. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/5D2B7681A77F1D6F9AD581EAACDEBEE9869C210F90D08B6A7FB20BDEF45EB3AD.pdf
- Rejeitado: Edital nº 11 – Inclusão de candidatos sub judice. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/81CAC8AC2CA3FE312A1501C722676702784CCF245EFA8BC507F38EB3A5DB4393.pdf
- Rejeitado: Edital nº 10 – Inclusão de candidato sub judice. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/162D7046EDC7A50EF39B0159C22F0D09C5E2BEA07B0BA6702EC0DA15736D0CB5.pdf
- Rejeitado: Edital nº 9 – Resultado final na avaliação biopsicossocial dos candidatos que solicitaram concorrer às vagas reservadas às pessoas com deficiência, o resultado final no procedimento de heteroidentificação complementar à autodeclaração dos candidatos negros e o resultado final concurso público — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/875BC747E4ADAAE8AE3235D9E6B6F76DD41ED3840BA6576C9F3A2C70ADD30ABF.pdf
- Rejeitado: Comunicado – Resultado final na avaliação biopsicossocial, e resultado final no procedimento de  heteroidentificação — `resultado` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/E63B26DF04DF43705457C327B21C8B589C9371069F49549EB9397C9B76B91A6C.pdf
- Rejeitado: Currículos dos integrantes da comissão recursal — `fora_do_escopo_objetivo` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/1B83F3707588F7B5D3F0105DE1FC1E6CD536777EA6026162DAEF90A91EE020CF.pdf
- Rejeitado: Edital nº 8 – Resultado provisório na avaliação biopsicossocial e resultado provisório no procedimento de heteroidentificação — `edital` — https://cdn.cebraspe.org.br/concursos/pf_25_adm/arquivos/B53FCFC2B05A39C9EFE31D3121D1A1C1E9FC036A8A6EFBFD5DA8553F426AA309.pdf

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
- Tempo: 25.18s
- OCR tentado/suficiente: 0/0
- Falhas tratadas: 0
- Exemplos rejeitados: 20

### Documentos aceitos

- `exam` PROVA OBJETIVA - CARGO 1: DELEGADO DE POLÍCIA FEDERAL — `70543026c4d36f8fd4c0642b5023df69a268a958fe147a48d2fac2a201dc3bfa` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_1_DELEGADO_DE_POLCIA_FEDERAL.PDF
- `exam` PROVA OBJETIVA - CARGO 4: PAPILOSCOPISTA DE POLÍCIA FEDERAL — `99b59dac252614f67877bdd2dc82888807089796a630dc0f8dcef0d6f0c82c75` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_4_PAPILOSCOPISTA_POLICIAL_FEDERAL.PDF
- `exam` PROVA OBJETIVA - CARGO 2: AGENTE DE POLÍCIA FEDERAL — `5464654f02abfcafe921eaa65c4f30bf9c267d80e667b7b35a8ca9e3f115b42f` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_2_AGENTE_DE_POLCIA_FEDERAL.PDF
- `exam` PROVA OBJETIVA - CARGO 3: ESCRIVÃO DE POLÍCIA FEDERAL — `d10ff2dae65cd8614e076d5e318b8258f12aa7dcf60b858f1738d574b01e7d31` — https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/CARGO_3_ESCRIVO_DE_POLCIA_FEDERAL.PDF
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

## pf_18 — POLÍCIA FEDERAL

- URL inicial: https://www.cebraspe.org.br/concursos/pf_18
- Banca identificada: CESPE/CESPE-UnB (acervo atual no Cebraspe)
- Esperados/encontrados: 30/30
- Provas/gabaritos aceitos: 15/15
- Precisão/cobertura: 100.0%/100.0%
- Associação prova/gabarito: 100.0%
- Caminho: deterministic_browser
- Páginas visitadas: 2
- Chamadas ao Qwen: 0
- Tempo: 69.80s
- OCR tentado/suficiente: 0/0
- Falhas tratadas: 0
- Exemplos rejeitados: 20

### Documentos aceitos

- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CARGO 13 — `969f10f53cd46ef2c6d1580b023d7f9fa3a4c6a445060eba87db1760253ed317` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF013__PAG_8.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CARGO 12 — `0915a7a3a5f1bd67ad4538d6d8a7a25bc30ccddb7fd363acf6c00586227ae016` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF012__PAG_9.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 4 — `0f1a83fc6bd083dded33ad1c175aa7b35996d422a1151c51bf3dc867df2abfc5` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF004_ATUALIZADO.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CARGO 14 — `50adf96277775c4920f32ce56b5cc707783f445d97dbc05f408f4995299f3306` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF014__PAG_9.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 11 — `aef617b833f27786a1ecc73376931306ef5bb2c885a620a90f726c88fd4eb0da` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF011__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 10 — `97093601040d3c2c63075f9cb874475ac7eeddbb932b86ef65a0c48d08e70540` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF010__PAG_3.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 9 — `e679e87059cdab0b8bbb12b222c45a41ef4616e8d59ac308ff63c95e65cefa29` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF009__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 8 — `21f3dabbce4bb30683a8e697473ca539974dc8a406846e22ae6b9d0e9b353843` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF008__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 7 — `d238de565e61a15d11e757c4b49f78a68a91576317597b0725ffd64746d52f30` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF007__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 6 — `4fe410e675770fa636727372d157a24a4c13e35275c3c36be43cba4d4ccfb066` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF006__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 5 — `54208685ecc0747c0d4b4815722b92e84178ceb5f598c1e39123c9a9195895b9` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF005__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 3 — `a715e569901431fc1d95fb8e3873b758913f1853521fba12f75b6180ea186762` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF003__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 2 — `ffa23e0c71add40f625b0838db57bddd60c353bdf4fa41cb3b03c97e289d5ad9` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF002__PAG_4.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CARGO 1 — `8bc587fc04df0abc2d7e4aebf42fd4dd44df76df05b3daaca462cc168e2d5a27` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPF001__PAG_8.PDF
- `answer_key` GABARITO DEFINITIVO - PROVA OBJETIVA - CONHECIMENTOS BÁSICOS PARA OS CARGOS DE 2 A 11 — `f6a000ef54fed66dc395b44d1623df73035f183235b26be71e97944a0bc38c4d` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/GAB_DEFINITIVO_408_DGPPFCB1__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 14 — `a49b7c33194c416bbeafa2edb31b7ae3531e7dbea7bd131c30125acab5f19669` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF014__PAG_9.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 13 — `9b34d8ecb683dbe6411b558b985d94e4f04bfb5838c6e56f9bd7a982d93af27a` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF013__PAG_8.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 12 — `0db90f82694c174092c4a1271d94acae54b19ad86e093be1abc2f1e38ef1c937` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF012__PAG_9.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 11/ÁREA 14 — `435b3c4e79f54f2df21d41a9068f8e22b83671ed1d3f9f0517e91bac59545318` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF011__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 10/ÁREA 12 — `14ce7411792862935d61c863aaea74e2dd8e27b502a312845fe7ac7776c13254` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF010__PAG_3.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 9/ÁREA 9 — `0134f118a9a439f7a2785407acbb6ce473865940c6cad777300dd8e8266f2e72` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF009__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 8/ÁREA 7 — `23893ecc10c9ef948f3af22089bc088cfcef94cd96107da3aea67772cbc58e07` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF008__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 7/ÁREA 6 — `564cd42252b5d53a35e6ac02261d251b0c401e4098b99a0972fdb4da072d8636` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF007__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 6/ÁREA 5 — `1730ef15691188f10d4eaf8f31e2e15f19ce4d29a79b9f8077b6405b0a04adce` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF006__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 5/ÁREA 4 — `e6e6592f9beb5e7e3702aaa0265d5e2ba612877973b0d3b80c5478c71a2a6d70` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF005__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 3/ÁREA 2 — `f1d9c67259455041cd883bad8c706284685eb5b1e12c08cfcf8989392b706c98` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF003__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 2/ÁREA 1 — `86953efa56ddf1ca3e629a160c81758e9bf3fd9b41587ee32f7a6d5e4eac733e` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF002__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 1 — `7095c473f11e1427a602d1d5156d58eb2844c58df31414fa9a8aeed30221cc43` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF001__PAG_8.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS BÁSICOS PARA O CARGO 2 - TODAS AS ÁREAS — `2ad0e11becb8ac952ba15ac931e290396051e0dc08e33c476fe17310643d7a97` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPFCB1__PAG_4.PDF
- `exam` PROVA OBJETIVA - CONHECIMENTOS ESPECÍFICOS - CARGO 4 - ÁREA 3 — `20ab0219fc2cf088f610a725c392813cda000abe48b3a12c49a73ef931584a59` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MATRIZ_408_DGPPF004__PAG_4.PDF

### Associações prova/gabarito

- `cargo_1` — associado; provas=MATRIZ_408_DGPPF001__PAG_8.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF001__PAG_8.PDF
- `cargo_10` — associado; provas=MATRIZ_408_DGPPF010__PAG_3.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF010__PAG_3.PDF
- `cargo_11` — associado; provas=MATRIZ_408_DGPPF011__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF011__PAG_4.PDF
- `cargo_12` — associado; provas=MATRIZ_408_DGPPF012__PAG_9.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF012__PAG_9.PDF
- `cargo_13` — associado; provas=MATRIZ_408_DGPPF013__PAG_8.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF013__PAG_8.PDF
- `cargo_14` — associado; provas=MATRIZ_408_DGPPF014__PAG_9.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF014__PAG_9.PDF
- `cargo_2` — associado; provas=MATRIZ_408_DGPPF002__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF002__PAG_4.PDF
- `cargo_3` — associado; provas=MATRIZ_408_DGPPF003__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF003__PAG_4.PDF
- `cargo_4` — associado; provas=MATRIZ_408_DGPPF004__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF004_ATUALIZADO.PDF
- `cargo_5` — associado; provas=MATRIZ_408_DGPPF005__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF005__PAG_4.PDF
- `cargo_6` — associado; provas=MATRIZ_408_DGPPF006__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF006__PAG_4.PDF
- `cargo_7` — associado; provas=MATRIZ_408_DGPPF007__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF007__PAG_4.PDF
- `cargo_8` — associado; provas=MATRIZ_408_DGPPF008__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF008__PAG_4.PDF
- `cargo_9` — associado; provas=MATRIZ_408_DGPPF009__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPF009__PAG_4.PDF
- `conhecimentos_basicos` — associado; provas=MATRIZ_408_DGPPFCB1__PAG_4.PDF; gabaritos=GAB_DEFINITIVO_408_DGPPFCB1__PAG_4.PDF

### Rejeições e falhas

- Rejeitado: PROVA ORAL – 04/02/2024 – MALOTE 1 – SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/960_PF_DL_SUBJUDICE_ORAL_COMPADRAO.PDF
- Rejeitado: PROVA ORAL – 25/10/2020 – MALOTE 1 – SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MALOTE01_PF_ORAL_COM_PADRAO.PDF
- Rejeitado: PROVA ORAL – 4/8/2019 – MALOTE 1 – SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/P_MALOTE01_DPF_ORAL_COMPADRAO.PDF
- Rejeitado: PROVA ORAL – 16/6/2019 – MALOTE 1 – SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/P_476_MALOTE01_DGPPF_ORAL.PDF
- Rejeitado: PROVA ORAL - 28/4/2019 - MALOTE 1 - SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/P_467_MALOTE01_DGPPF_ORAL_COMPADRAO.PDF
- Rejeitado: PROVA ORAL - MALOTE 1 - SUB JUDICE — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/SUB_JUDICE_P_MALOTE01_DGPPF_ORAL.PDF
- Rejeitado: PROVA ORAL - MALOTE 3 — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/MALOTE03_DGPPF_ORAL.PDF
- Rejeitado: PROVA ORAL - MALOTE 1 — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/P_MALOTE01_DGPPF_ORAL.PDF
- Rejeitado: PROVA ORAL - MALOTE 4 — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/P_MALOTE04_DGPPF_ORAL.PDF
- Rejeitado: PROVA ORAL - MALOTE 2 — `prova_oral` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/P_MALOTE02_DGPPF_ORAL.PDF
- Rejeitado: PADRÃO DE RESPOSTA DEFINITIVO - PROVA DISCURSIVA - CARGO 14 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/PF_18_PADRO_DE_RESPOSTAS_CARGO_14.PDF
- Rejeitado: PADRÃO DE RESPOSTA DEFINITIVO - PROVA DISCURSIVA - CARGO 13 — `prova_discursiva` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/PF_18_PADRO_DE_RESPOSTAS_CARGO_13.PDF
- Rejeitado: Edital nº 3 - Retificação das datas constantes dos subitens 5.1 e 10.1 do Edital nº 2 – PF, concursos anteriores – de 30 de abril de 2026 — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/1CA5762B21B857B5B6FBA64E7467C83D8DB9179EF66A6538C0D98FDCD5E17D92.pdf
- Rejeitado: Edital nº 2 – Convocação de candidatos sub judice para matrícula no CFP e para a atualização da Ficha de Informações Confidenciais – FIC, referente aos concursos públicos regidos pelo Edital nº 9/2012 – DGP/DPF; pelo Edital nº 10/2012 – DGP/DPF e pelo Edital nº 1-DGP/PF; e pelo Edital nº 1 – DGP/PF — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/C156978FCCE628DB3E20B553BD265E903E0C981AA12996D06D49B0371D3C6D70.pdf
- Rejeitado: Edital nº 1 – Convocação de candidatos sub judice para matrícula no CFP, referentes aos concursos públicos regidos pelo Edital nº 24/2004 – DGP/DPF – Nacional, de 15 de julho de 2004; pelo Edital nº 15/2009-DGP/APF, de 24 de julho de 2009; pelo Edital nº 1-DGP/PF, de 14 de junho de 2018, e suas alterações; e pelo Edital nº 01 – DGP/PF, de 15 de janeiro de 2021, e alterações — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/3E76C0367BD2EFD1085E630458CFCD7F55B0C793AB4F0855C8FFF7BAA81FFA0B.pdf
- Rejeitado: Edital nº 110 – Torna sem efeito o Edital nº 137 – DGP/PF, de 30 de julho de 2020. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/ED_110_PF_18_TORNA_SEM_EFEITO_ED_137.PDF
- Rejeitado: Edital nº 91 – Eliminação de candidato sub judice. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/ED_91_DGP__ELIMINAO_FELIPE_MAIA_XIMENES.PDF
- Rejeitado: Edital nº 74 - Convocação de candidato sub judice para matrícula na segunda etapa — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/ED_74_PF___CONVOCAO_DE_SJ_ROMULLO_DA_SILVA_NOLASCO.PDF
- Rejeitado: Edital nº 58 - Convocação para o Curso de Formação Profissional (CFP) de candidatas sub judice. — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/ED_58_2022___MARIA_BASTOS_PELANDA___ERIKA___DENIELLE___2018.PDF
- Rejeitado: Edital nº 48 - Convocação para o Curso de Formação Profissional (CFP) de candidata sub judice, oriunda do concurso público regido pelo Edital nº 1/2018 – DGP/DPF — `edital` — https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/ED_48_PF_18_CONV_SUB_JUDICE_CFP_DENIELLI.PDF
