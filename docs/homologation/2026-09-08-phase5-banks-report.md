# Homologação real das fontes do KAD Collector

- Data UTC: 2026-09-08T19:49:27.303570+00:00
- Commit: `b073fb6-dirty`
- Ollama: qwen3:8b disponível
- Precisão: 100.0%
- Cobertura: 25.0%
- Associação prova/gabarito: 33.3%
- Falsos positivos: 0
- Falsos negativos: 9
- Documentos proibidos aceitos: 0
- Chamadas ao Qwen (descoberta/estruturação): 0/0
- Fontes sem intervenção: 16.7%
- Tempo médio por fonte: 2.45s
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

## Diagnóstico

A fase não atingiu o critério de liberação geral. O Collector executou as quatro bancas sem
interromper o lote, mas as políticas públicas dos próprios hosts limitaram a cobertura: a FCC
nega as duas páginas pelo `robots.txt`; a consulta de `robots.txt` da Vunesp retorna HTTP 403;
o Instituto AOCP retorna HTTP 403 na página do concurso; e o host antigo de anexos do Quadrix
nega os três arquivos do SES-SP pelo `robots.txt`. Esses bloqueios foram registrados e não foram
contornados.

O cenário CORE-PI comprovou o fluxo que estava acessível: abriu um ZIP oficial, validou limites
e caminhos, recusou conteúdo que não fosse PDF, extraiu quatro provas e dois gabaritos, escolheu
o gabarito definitivo para as quatro provas e estruturou 160 questões. Ficaram 141 prontas e 19
em quarentena editorial. A segunda execução repetiu os mesmos seis hashes e o mesmo SHA-256 do
pacote estruturado (`633f2dc3777519e690c10dbcf341242928fd81ac160942a2af17feaf677ffe9b`).

A linha de base inicial não incluía o gabarito preliminar do Quadrix, embora ele estivesse
publicamente listado na página oficial. A matriz foi corrigida antes desta execução final para
tratar esse documento como gabarito esperado; a estruturação continua usando apenas o definitivo.

Não houve amostra digitalizada acessível nesta matriz, portanto OCR não foi homologado nesta
fase. Também não houve página permitida em que a descoberta determinística falhasse depois de
carregada; por isso o Qwen não foi chamado. Isso não é apresentado como sucesso artificial: os
dois critérios permanecem explicitamente não comprovados ou sem exercício real.

## Correções feitas durante a homologação

- suporte geral e seguro a ZIPs de provas, com proteção contra travessia de diretório, excesso de
  membros, expansão desproporcional, criptografia, arquivo falso e PDF inválido;
- resolução geral de visualizadores públicos que declaram o PDF em `file=`, mantendo todas as
  validações de host, `robots.txt` e conteúdo;
- formulário de login secundário não torna uma página pública bloqueada quando ela expõe links
  públicos para documentos;
- associação de um único gabarito definitivo agregado a várias provas do mesmo concurso;
- gabarito curto com coluna chamada `QUESTÃO` não é mais classificado incorretamente como prova;
- telemetria e relatório incluem hashes, decisões do Qwen, estruturação e idempotência sem
  versionar o conteúdo dos PDFs.

## Validação técnica

- `pytest tests -q`: 952 testes e 114 subtestes aprovados;
- `ruff check src tests scripts`: aprovado;
- `mypy src`: aprovado em 80 arquivos;
- `pip check`: nenhuma dependência quebrada;
- wheel criado com `python -m build --wheel --no-isolation`;
- wheel instalado em diretório isolado e os quatro cadastros de fonte confirmados no pacote;
- execução online e execução com Ollama deliberadamente indisponível: mesmos 160 itens
  estruturados, 141 prontos e 19 em quarentena.

## Resultados por amostra

## fcc_dpeba_2026

- Cenário: FCC atual; visualizador oficial com PDF real em parâmetro file
- URL inicial: https://www.concursosfcc.com.br/concursos/dpeba125/index.html
- Esperados/encontrados/aceitos: 1/0/0
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Chamadas ao Qwen na estruturação: 0
- Tempo: 0.42s
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
- Tempo: 0.26s
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
- Tempo: 0.17s
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
- Tempo: 2.14s
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
- Tempo: 8.72s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 160/141/19

- `answer_key` Gabarito preliminar (prova objetiva) 22/06/2026 — `a8fc493950f02c2fee23e14c39df6a21aa8c2f082c2f7d14c68915b9fe460f32` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/6a2abd9c-19b0-4a51-b50c-d63c84925a3a.pdf
- `answer_key` Gabarito definitivo (prova objetiva) 20/07/2026 — `d324fc6f8e060fff2dc0880f3cebbc1727d83a7d4d0e98c099284211a0ce9f07` — https://anexos-r2.selecao.net.br/uploads/861/concursos/1018/anexos/bcc1e439-5a3b-43ca-9c58-c6d99f48a923.pdf
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
- Tempo: 2.98s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 3
- Arquivos rejeitados por regra: 0
- Questões detectadas/prontas/quarentena: 0/0/0

- Falha tratada (robots): quadrix_concursos: robots.txt bloqueou ou nao liberou https://anexos.cdn.selecao.net.br/uploads/861/concursos/13/anexos/ba2c194d-f4cf-419c-8a60-d6dc4304167f.pdf
- Falha tratada (robots): quadrix_concursos: robots.txt bloqueou ou nao liberou https://anexos.cdn.selecao.net.br/uploads/861/concursos/13/anexos/3825d07a-bf0e-41cc-9b4f-e1f2a9cc6c4c.pdf
- Falha tratada (robots): quadrix_concursos: robots.txt bloqueou ou nao liberou https://anexos.cdn.selecao.net.br/uploads/861/concursos/13/anexos/300374ed-5b0c-4634-a027-c1b37d829949.zip
