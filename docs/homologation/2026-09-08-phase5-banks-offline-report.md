# Homologação real das fontes do KAD Collector

- Data UTC: 2026-09-08T19:54:30.175147+00:00
- Commit: `b073fb6-dirty`
- Ollama: indisponível de propósito
- Precisão: 100.0%
- Cobertura: 25.0%
- Associação prova/gabarito: 33.3%
- Falsos positivos: 0
- Falsos negativos: 9
- Documentos proibidos aceitos: 0
- Chamadas ao Qwen (descoberta/estruturação): 0/0
- Fontes sem intervenção: 16.7%
- Tempo médio por fonte: 2.39s
- Questões detectadas/prontas: 160/141
- Questões em quarentena: 19

## Critérios de aprovação

- APROVADO — precision_at_least_95_percent
- NÃO ATENDIDO — coverage_at_least_85_percent
- APROVADO — all_four_banks_executed
- APROVADO — no_prohibited_document_accepted
- APROVADO — source_failures_were_isolated
- APROVADO — repeat_is_idempotent
- NÃO ATENDIDO — ocr_sample_completed

## Diagnóstico do modo offline

Com o endpoint do Ollama apontado deliberadamente para um endereço indisponível, o caminho
determinístico produziu os mesmos seis documentos, as mesmas 160 questões e a mesma divisão de
141 prontas e 19 em quarentena. A repetição manteve os hashes dos documentos e o SHA-256 do
pacote estruturado. As limitações de cobertura são as mesmas da execução online e decorrem das
políticas ou respostas dos hosts oficiais; nenhuma foi contornada.

## Resultados por amostra

## fcc_dpeba_2026

- Cenário: FCC atual; visualizador oficial com PDF real em parâmetro file
- URL inicial: https://www.concursosfcc.com.br/concursos/dpeba125/index.html
- Esperados/encontrados/aceitos: 1/0/0
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Chamadas ao Qwen na estruturação: 0
- Tempo: 0.40s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 1
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 0/0/0

- Falha tratada (robots): fcc_concursos: robots.txt bloqueou ou nao liberou https://www.concursosfcc.com.br/concursos/dpeba125/index.html

## fcc_trt4_2022

- Cenário: FCC histórico; gabarito individual em página HTML
- URL inicial: https://www.concursosfcc.com.br/concursos/trt4r122/index.html
- Esperados/encontrados/aceitos: 1/0/0
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Chamadas ao Qwen na estruturação: 0
- Tempo: 0.25s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 1
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 0/0/0

- Falha tratada (robots): fcc_concursos: robots.txt bloqueou ou nao liberou https://www.concursosfcc.com.br/concursos/trt4r122/index.html

## vunesp_dpsp_2022

- Cenário: Vunesp; área de provas e gabaritos anunciada na página pública
- URL inicial: https://www.vunesp.com.br/DPSP2202
- Esperados/encontrados/aceitos: 2/0/0
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Chamadas ao Qwen na estruturação: 0
- Tempo: 0.14s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 1
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 0/0/0

- Falha tratada (robots): vunesp_concursos: robots.txt bloqueou ou nao liberou https://www.vunesp.com.br/DPSP2202

## aocp_mpba_2014

- Cenário: AOCP histórico com muitos editais e dois gabaritos oficiais
- URL inicial: https://www.institutoaocp.org.br/concursos/26
- Esperados/encontrados/aceitos: 2/0/0
- Caminho: deterministic
- Páginas visitadas: 2
- Chamadas ao Qwen: 0
- Chamadas ao Qwen na estruturação: 0
- Tempo: 2.13s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 1
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 0/0/0

- Falha tratada (discovery): instituto_aocp_concursos: falha ao ler https://www.institutoaocp.org.br/concursos/26: HTTP 403 ao acessar https://www.institutoaocp.org.br/concursos/26

## quadrix_corepi_2026

- Cenário: Quadrix; cadernos em ZIP e gabarito em PDF separado
- URL inicial: https://quadrix.org.br/informacoes/1018/
- Esperados/encontrados/aceitos: 3/6/6
- Caminho: deterministic
- Páginas visitadas: 3
- Chamadas ao Qwen: 0
- Chamadas ao Qwen na estruturação: 0
- Tempo: 8.59s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 160/141/19

- `answer_key` Gabarito definitivo (prova objetiva) 20/07/2026 — `d324fc6f8e060fff2dc0880f3cebbc1727d83a7d4d0e98c099284211a0ce9f07` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/bcc1e439-5a3b-43ca-9c58-c6d99f48a923.pdf
- `answer_key` Gabarito preliminar (prova objetiva) 22/06/2026 — `a8fc493950f02c2fee23e14c39df6a21aa8c2f082c2f7d14c68915b9fe460f32` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/6a2abd9c-19b0-4a51-b50c-d63c84925a3a.pdf
- `exam` Provas aplicadas 22/06/2026 - 201 Fiscal QUADRIX Concurso-2026 CORE-PI Prova — `eeddb0b8e32a06722cf8dae0b59a4f301012d864f59b900a5884df3793dde653` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/55114ca4-aa40-4a65-93a2-64b319ea21d2.zip#archive_member=201_Fiscal_QUADRIX_Concurso-2026_CORE-PI_Prova.pdf
- `exam` Provas aplicadas 22/06/2026 - 200 Assistente Administrativo QUADRIX Concurso-2026 CORE-PI Prova — `b355cd6b428270e0ef11042ce94be1edba0eeb1c5f976dc7edf885ede75437de` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/55114ca4-aa40-4a65-93a2-64b319ea21d2.zip#archive_member=200_Assistente%20Administrativo_QUADRIX_Concurso-2026_CORE-PI_Prova.pdf
- `exam` Provas aplicadas 22/06/2026 - 401 Contador QUADRIX Concurso-2026 CORE-PI Prova — `2745553798fedfea40bcd1148e97dd7759ad5a8a9cba7498b480f7f79f7fb477` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/55114ca4-aa40-4a65-93a2-64b319ea21d2.zip#archive_member=401_Contador_QUADRIX_Concurso-2026_CORE-PI_Prova.pdf
- `exam` Provas aplicadas 22/06/2026 - 400 Assistente Jurídico QUADRIX Concurso-2026 CORE-PI Prova — `af0731dfd90a2ca0944fd6d30934ab736e4fd05f209eecc5df505218197bf75f` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/55114ca4-aa40-4a65-93a2-64b319ea21d2.zip#archive_member=400_Assistente%20Jur%C3%ADdico_QUADRIX_Concurso-2026_CORE-PI_Prova.pdf

## quadrix_sessp_2025

- Cenário: Quadrix; página com muitos resultados, cadernos em ZIP e gabarito separado
- URL inicial: https://quadrix.org.br/informacoes/13/
- Esperados/encontrados/aceitos: 3/0/0
- Caminho: deterministic
- Páginas visitadas: 3
- Chamadas ao Qwen: 0
- Chamadas ao Qwen na estruturação: 0
- Tempo: 2.86s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 3
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 0/0/0

- Falha tratada (robots): quadrix_concursos: robots.txt bloqueou ou nao liberou https://anexos.cdn.selecao.net.br/uploads/861/concursos/13/anexos/ba2c194d-f4cf-419c-8a60-d6dc4304167f.pdf
- Falha tratada (robots): quadrix_concursos: robots.txt bloqueou ou nao liberou https://anexos.cdn.selecao.net.br/uploads/861/concursos/13/anexos/3825d07a-bf0e-41cc-9b4f-e1f2a9cc6c4c.pdf
- Falha tratada (robots): quadrix_concursos: robots.txt bloqueou ou nao liberou https://anexos.cdn.selecao.net.br/uploads/861/concursos/13/anexos/300374ed-5b0c-4634-a027-c1b37d829949.zip
