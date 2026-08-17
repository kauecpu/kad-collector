# KAD Collector

Pipeline privado para localizar provas e gabaritos em fontes autorizadas, baixar PDFs,
extrair texto, estruturar questoes com parser local ou IA e preparar lotes para revisao
editorial. O coletor nunca publica conteudo diretamente no aplicativo KAD.

```text
Fonte oficial -> coleta controlada -> PDFs -> extracao -> parser/IA -> gabarito
              -> validacao -> revisao humana -> questoes.jsonl -> painel KAD
```

## Estado atual

O repositorio fornece o mecanismo generico. O arquivo `config/sources.example.toml` nao
habilita fontes. O arquivo opt-in `config/sources.official.toml` cadastra duas fontes reais
conferidas; revise o contato do `user_agent`, termos, `robots.txt` e limites antes da primeira
execucao no ambiente da equipe.

O MVP processa paginas HTML estaticas que contenham links para PDFs. Paginas que dependem
de JavaScript ainda nao usam Playwright. PDFs digitalizados sem camada de texto sao
marcados como `needs_ocr` e nao seguem automaticamente para a IA.

## Requisitos

- Python 3.11 ou superior;
- uma chave da API OpenAI apenas para os fluxos gerais que usam a etapa `process`;
- acesso local ao painel administrativo do KAD para importar o JSONL revisado.

## Instalacao no Windows (CMD)

```cmd
cd /d "C:\Users\gabri\OneDrive\Documents\GitHub\kad-collector"
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[database,dev]"
copy config\sources.example.toml config\sources.toml
```

Para as etapas que usam IA, defina a chave somente na sessao atual do CMD:

```cmd
set OPENAI_API_KEY=sua_chave
set OPENAI_MODEL=gpt-5.6-terra
```

Nao coloque uma chave real em arquivos versionados, comandos compartilhados, logs ou
capturas de tela. A integracao usa a Responses API com Structured Outputs, `store=false`
e JSON validado. O modelo pode ser trocado por `OPENAI_MODEL` ou `--model`.

Referencias oficiais: [Responses API](https://developers.openai.com/api/docs/guides/responses)
e [modelos](https://developers.openai.com/api/docs/models).

## Teste guiado com dois cliques

No Windows, execute `testar.cmd` na raiz do repositorio. Tambem e possivel inicia-lo pelo
Prompt de Comando:

```cmd
cd /d "C:\Users\gabri\OneDrive\Documents\GitHub\kad-collector"
testar.cmd
```

O lancador:

1. cria o `.venv` somente se ele ainda nao existir;
2. instala o Collector somente se o pacote ainda nao estiver disponivel no ambiente;
3. coleta apenas a prova V1 e o gabarito da FUVEST 2026;
4. estrutura localmente as questoes reais pelos marcadores oficiais do PDF;
5. associa o gabarito, valida o lote e sinaliza alternativas visuais;
6. abre automaticamente o primeiro lote da fila no painel local de revisao.

Esse teste usa `config/sources.test.toml`, limitado a dois PDFs do acervo oficial FUVEST
2026, e registra titulo, URL, SHA-256, banca, orgao, ano e paginas de origem. Ele mantem seu
proprio estado em `data/state/teste-guiado.json`, nao acessa Supabase, nao modifica o KAD e
nao executa promocao. Depois da revisao, volte a janela do lancador e pressione `Ctrl+C` para
encerrar o servidor local. Se a porta 8765 estiver ocupada, outra porta local e escolhida
automaticamente. O modo padrao nao pede chave, nao usa a API OpenAI e nao gera custo de IA.
Ele usa um parser deterministico especifico para os marcadores da prova FUVEST; alternativas
baseadas em figuras ou colunas ficam sinalizadas para conferencia humana. Para comparar com
extracao por IA, use `kad-collector testar --model ID_DO_MODELO`; somente esse modo opcional
solicita a chave e requer uma conta da API com faturamento ou creditos ativos.

O mesmo fluxo tambem pode ser iniciado, dentro do ambiente virtual, com:

```cmd
kad-collector testar
```

## Aplicativo desktop para Windows

O Collector possui uma central editorial local para selecionar PDFs ou pastas, acompanhar
o processamento pagina a pagina, revisar classificacoes e exportar exatamente o recorte
filtrado. O estado fica em SQLite dentro de `%LOCALAPPDATA%\KAD Collector`; nenhuma questao
e enviada ao Supabase pelo aplicativo.

Instale a interface e execute:

```cmd
python -m pip install -e ".[desktop]"
kad-collector-desktop
```

A interface aceita lotes textuais de aproximadamente 300 paginas, trabalha em segundo plano
e salva checkpoints por pagina. **Pausar** encerra o trecho corrente com seguranca; **Retomar**
continua das paginas ja persistidas. PDFs digitalizados ou paginas sem camada de texto entram
em `excecoes.jsonl`; OCR nao faz parte desta versao.

O classificador `local` usa metadados informados e regras conservadoras com confianca por
campo. Para usar a integracao opcional ja existente com a OpenAI, defina `OPENAI_API_KEY` e,
se necessario, `OPENAI_MODEL` somente na sessao que inicia o aplicativo, e selecione OpenAI
ao criar o lote. A chave nunca e salva no SQLite, no executavel ou nos relatorios.

Os filtros usam OR dentro da mesma categoria e AND entre categorias. Origem, conteudo,
qualidade e situacao possuem contagens facetadas, busca, chips ativos e filtros salvos. Uma
exportacao gera uma pasta com `questoes.jsonl`, `excecoes.jsonl`, `relatorio.json`,
`manifesto.json` e os PDFs de evidencia. Somente questoes aprovadas, validas e com URL HTTPS
de origem entram no arquivo importavel.

Para gerar o executavel:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-desktop.ps1
```

O artefato final fica em `dist\KAD-Collector.exe`. O script tambem executa o modo
`--smoke-test`, que abre o banco local, carrega os recursos da interface e encerra sem criar
uma janela.

## Configurando uma fonte

Copie o bloco `[[sources]]` do exemplo e preencha:

- `start_urls`: paginas oficiais que listam provas e gabaritos;
- `allowed_hosts`: lista exata de hosts que o coletor pode acessar;
- `include_patterns` e `exclude_patterns`: expressoes regulares para selecionar links;
- `exam_patterns` e `answer_key_patterns`: classificacao do tipo de PDF;
- `access_mode`: `content` para fontes oficiais/licenciadas ou `reference_only` para
  registrar somente identificador da URL e metadados;
- `authorization_basis`: registro da permissao ou base de uso conferida;
- `requires_written_authorization` e `written_authorization_reference`: bloqueio de
  fontes que exigem permissao escrita antes do acesso automatizado;
- `terms_url`: pagina de termos ou politica aplicavel;
- `metadata`: banca, orgao, cargo e ano conhecidos para a fonte.
- `pagination_patterns`: links HTML estaticos que podem ser seguidos como paginacao;
- `max_pages_per_run`: limite total de paginas de descoberta por fonte e rodada.

Somente depois da conferencia, altere `enabled = false` para `enabled = true`.

### Fontes oficiais iniciais

`config/sources.official.toml` registra:

- **FUVEST - Vestibular USP**: primeiras fases dos acervos oficiais de 2025 e 2026 em
  `www.fuvest.br`; coleta as quatro variantes da prova e o gabarito de cada ano, alem de
  titulo, URL, banca, orgao e ano inferido. O `robots.txt` publico bloqueia apenas
  `/wp-admin/`. A rodada usa intervalo de 3 segundos, no maximo 4 paginas HTML, 40 PDFs e
  50 MB por PDF. Provas e gabaritos permanecem atribuidos ao acervo oficial.
- **COPERVE - Vestibular Unificado UFSC/IFSC/IFC 2026**: pagina oficial de provas e
  gabaritos definitivos. O hotsite oferece esse material para treinamento e responde 404
  para `robots.txt`; a politica conservadora do Collector interpreta apenas esse 404 como
  ausencia de restricoes. A rodada usa uma pagina HTML, intervalo de 3 segundos, no maximo
  40 PDFs e 50 MB por PDF.

Os gabaritos da COPERVE podem usar respostas numericas por soma de proposicoes, enquanto o
schema atual aceita alternativas A-H. Esses casos entram em `exception` e nao podem ser
aprovados silenciosamente. As fontes nao autorizam publicacao automatica no KAD: o conteudo
continua sujeito a revisao editorial e verificacao de direitos antes da promocao.

Para usar a configuracao opt-in:

```cmd
copy config\sources.official.toml config\sources.toml
kad-collector sync --config config\sources.toml
```

As protecoes do coletor incluem:

- obediencia a `robots.txt`, com bloqueio conservador quando ele nao pode ser consultado;
- intervalo minimo de um segundo e valor padrao de tres segundos entre requisicoes;
- limite padrao de 20 PDFs por fonte e por execucao;
- limites de 5 MB para HTML e 50 MB para cada PDF;
- bloqueio de hosts fora da lista, credenciais em URL e enderecos de rede privada;
- revalidacao de todos os redirecionamentos;
- identificacao de arquivos por SHA-256 e manifesto de origem.

## Executando o pipeline

### 1. Coletar PDFs

```cmd
kad-collector collect --config config\sources.toml
```

O comando grava PDFs em `data/raw/` e cria um manifesto em `data/manifests/`.
Esses arquivos sao locais e ignorados pelo Git.

Filtros podem ser repetidos e ficam registrados no manifesto para serem aplicados
novamente, de forma estrita, depois que a IA estruturar cada questao:

```cmd
kad-collector collect --config config\sources.toml --ano 2022 --banca FGV
kad-collector collect --config config\sources.toml --orgao "TJ-SP" --cargo Escrevente
```

Tambem estao disponiveis `--materia` e `--assunto`, alem dos nomes equivalentes em
ingles (`--year`, `--board`, `--organization`, `--role`, `--matter`, `--subject`). Quando
um campo ainda nao aparece no titulo, URL ou metadados do documento, o PDF continua
elegivel e a decisao definitiva ocorre somente depois da extracao. Questoes sem o campo
solicitado ou com valor diferente sao removidas do lote e contabilizadas em
`filtered_out_questions`.

Valores repetidos do mesmo campo funcionam como alternativas (`--banca FGV --banca FCC`);
campos diferentes sao combinados (`--ano 2022 --banca FGV`). Filtros informados em
`process` refinam por intersecao os filtros ja registrados pelo comando `collect`. Uma
combinacao contraditoria, como coletar 2022 e processar 2023, e rejeitada explicitamente.

### Fluxo semiautomatico de ponta a ponta

Quando a equipe ja possui os links, o comando `run` executa coleta, extracao, estruturacao,
filtro, deduplicacao entre documentos e organizacao em uma unica chamada. Cada link precisa
pertencer a uma fonte `content` cadastrada, habilitada e autorizada no TOML. O link pode ser
uma pagina que lista PDFs ou um PDF direto:

```cmd
kad-collector run --config config\sources.toml ^
  --url "https://fonte-oficial.example/prova-2022.pdf" ^
  --url "https://fonte-oficial.example/outra-pagina" ^
  --ano 2022 --banca FGV
```

Para lotes maiores, informe um link por linha. Linhas vazias e linhas iniciadas por `#` sao
ignoradas:

```cmd
kad-collector run --config config\sources.toml --urls-file links.txt --ano 2022
```

O resultado e gravado em `data/results/semi-*.json` e contem:

- `questions`: questoes completas, ordenadas por concurso/orgao e cargo, depois banca e ano;
- `exceptions`: itens incompletos, conflitantes ou duvidosos, com o motivo para revisao;
- `metrics`: documentos e questoes filtrados, duplicatas removidas e itens para revisao;
- `collection_failures` e `warnings`: bloqueios, falhas de fonte e documentos que exigem OCR;
- `artifacts`: caminhos dos manifestos e lotes intermediarios para auditoria.

A deduplicacao compara o enunciado e as alternativas normalizados e agrega as origens dos lotes.
Metadados divergentes entre copias colocam a questao em `exceptions`; eles nao sao resolvidos
silenciosamente. O comando nao aprova lotes e nao escreve no banco do aplicativo.

### 2. Extrair o texto

Use o caminho do manifesto exibido pelo comando anterior:

```cmd
kad-collector extract data\manifests\download-AAAAMMDDTHHMMSSZ.json
```

A saida fica em `data/extracted/`. Documentos com pouco texto sao marcados para OCR.

### 3. Estruturar as questoes com IA

```cmd
kad-collector process data\extracted\download-AAAAMMDDTHHMMSSZ-extracted.json
```

Cada prova gera um JSON em `data/processed/`. O modelo recebe trechos sobrepostos para
reduzir cortes entre paginas e devolve, para cada questao:

- numero, enunciado e alternativas;
- materia e assunto;
- banca, orgao, cargo e ano;
- paginas de origem.

A saida continua com estado `pending`; dados ausentes ou conflitantes geram avisos.

### 4. Relacionar o gabarito

O segundo argumento pode ser um TXT revisado ou o manifesto JSON extraido que contenha
um documento classificado como `answer_key`:

```cmd
kad-collector match-answers data\processed\LOTE.json data\extracted\GABARITO.json
```

Formatos textuais simples como `1 - A`, `2) C` e `3 ANULADA` sao reconhecidos. Tabelas
complexas precisam ser convertidas ou revisadas antes desta etapa.

### 5. Revisar localmente e aprovar

O painel local permite conferir e editar enunciado, alternativas, gabarito comentado,
paginas de origem e todos os campos do contrato editorial; cada questao pode ser aprovada
ou rejeitada individualmente:

```cmd
kad-collector review data\reviewed\LOTE.json --open-browser
```

O servidor aceita conexoes somente em `127.0.0.1`, nao usa Supabase e nao envia o lote
para servicos externos. Se o PDF original ainda estiver no caminho registrado pelo
manifesto, ele pode ser aberto ao lado da revisao. Use `--port 8877` se a porta padrao
`8765` estiver ocupada.

A sessao e salva a cada alteracao em `data/reviews/` e pode ser retomada executando o
mesmo comando. Editar uma questao ja decidida faz ela voltar para `pending`; rejeicoes
exigem justificativa. A exportacao so e liberada quando todas as questoes tiverem uma
decisao e grava somente as aprovadas em `data/approved/`, com revisor, data e hash de
integridade. Ao exportar, tambem cria a pasta `data/exports/LOTE/` pronta para o painel.
PDFs, sessoes e lotes continuam locais e ignorados pelo Git.

Para usar outros caminhos:

```cmd
kad-collector review data\reviewed\LOTE.json ^
  --session data\reviews\revisao-equipe.json ^
  --output data\approved\lote-final.json
```

O fluxo anterior por linha de comando continua disponivel para lotes que nao precisam de
decisoes individuais:

```cmd
kad-collector validate data\reviewed\LOTE.json --require-answers
kad-collector approve data\reviewed\LOTE.json --reviewer "nome.do.revisor"
```

A aprovacao exige todos os campos do `EditorialImportRecord` v1, de duas a cinco
alternativas sequenciais entre A e E, uma explicacao e todas as respostas. Ela grava um
hash do conteudo. Qualquer alteracao
posterior invalida a importacao. Revise tambem direitos, fidelidade do enunciado,
alternativas, classificacao e gabarito antes de aprovar.

### 6. Gerar a pasta para o painel administrativo

A interface de revisao faz isso ao clicar em **Exportar aprovadas**. Para um lote ja
aprovado, o mesmo resultado pode ser gerado pela CLI:

```cmd
kad-collector export-admin data\approved\LOTE.json
```

A pasta `data/exports/LOTE/` contem:

- `questoes.jsonl`, com uma questao valida por linha no contrato versionado;
- `excecoes/questoes.jsonl`, com rejeicoes, anuladas e itens incompletos;
- `fontes/`, com os PDFs da prova e do gabarito preservados como evidencia;
- `manifesto.json` e `relatorio.json`, com hashes, contagens e motivos.

Importe apenas `questoes.jsonl` na tela **Importacoes** do painel KAD. O painel cria um
lote para revisao e aplica itens validos sempre como rascunho. O coletor nao usa o schema
`collector.*`, nao recebe credenciais do Supabase e nao escreve diretamente no banco.
O contrato oficial e sua fixture estao em `contracts/`.

## Execucao automatica por novidades

Depois que as fontes habilitadas estiverem revisadas, `sync` executa uma rodada automatica
usando as `start_urls` cadastradas:

```cmd
kad-collector sync --config config\sources.toml --ano 2022 --banca FGV
```

Cada rodada:

1. verifica as paginas e fontes permitidas;
2. compara o SHA-256 dos documentos com `data/state/automation.json`;
3. extrai e estrutura somente documentos ainda nao processados;
4. associa cada prova ao gabarito textual mais provavel, sem decidir empates;
5. cria lotes em `data/reviewed/`, sessoes locais em `data/reviews/` e um manifesto
   `queue-*.json` com itens `ready` ou `exception`;
6. registra referencias novas, mudancas por fonte e falhas temporarias;
7. mantem uma fila de retentativas com limite de tentativas;
8. grava `data/results/automatic-*.json` com metricas e excecoes para a equipe.

O relatorio automatico lista os IDs em `changed_sources` e detalha tentativas pendentes ou
esgotadas em `retry_queue`, sem esconder falhas persistentes no estado interno.
Provas ainda sem gabarito permanecem no estado pendente e sao reavaliadas quando uma rodada
posterior encontrar um gabarito textual compativel.

Os caminhos e limites podem ser alterados sem versionar dados operacionais:

```cmd
kad-collector sync --config config\sources.toml ^
  --state data\state\producao.json ^
  --output data\results\rodada.json ^
  --max-attempts 4 --retry-delay-seconds 600
```

Execute `sync` periodicamente pelo agendador autorizado do ambiente. O comando termina ao fim
de cada rodada; ele nao instala servico, nao altera infraestrutura e nao publica no aplicativo.
Falhas permanentes, OCR pendente e questoes incompletas continuam visiveis no relatorio. Para
reprocessar todo o acervo com outra politica ou modelo, use um novo arquivo em `--state`.

## Pacote de promocao local legado

Depois da revisao e aprovacao, gere um pacote autocontido com os lotes aprovados:

```cmd
kad-collector package data\approved\LOTE-1.json data\approved\LOTE-2.json
```

Esse formato continua disponivel apenas para compatibilidade com testes e lotes antigos.
Novas entregas ao painel devem usar `export-admin` e `questoes.jsonl`; a interface local
nao cria mais um pacote de promocao automaticamente.

O comando revalida respostas e hashes de aprovacao, rejeita lotes ou questoes duplicadas e
grava `data/promotion/UUID.json`. O pacote recebe SHA-256 e identificador deterministico: o
mesmo conteudo aprovado produz o mesmo ID.

A promocao disponivel nesta fase e somente uma simulacao local:

```cmd
kad-collector promote data\promotion\UUID.json
```

Ela verifica novamente o pacote e informa quantos lotes e questoes seriam enviados. Nao abre
conexao, nao usa Supabase e nao modifica o repositorio ou o aplicativo KAD. Uma integracao
futura podera consumir esse contrato somente depois de autorizacao especifica.

## Scripts equivalentes

Os nomes propostos inicialmente continuam disponiveis como adaptadores da CLI:

```text
coletor/coletar_provas.py
processamento/extrair_pdf.py
processamento/processar_ia.py
processamento/relacionar_gabarito.py
database/salvar_questoes.py
```

Depois de `pip install -e .`, por exemplo:

```cmd
python coletor\coletar_provas.py --config config\sources.toml
```

## Fontes cadastradas

O arquivo de exemplo nao habilita fontes. As configuracoes opt-in `sources.official.toml` e
`sources.test.toml` habilitam somente as fontes oficiais descritas anteriormente. O arquivo de
exemplo inclui os seguintes moldes:

| Fonte | Origem | Campos coletados | Limite | Execucao | Base de uso |
|---|---|---|---|---|---|
| Fonte oficial de exemplo | `example.gov.br` ficticio | PDFs, origem, SHA-256 e metadados configurados | 20 PDFs por execucao, 3 s entre requisicoes | `access_mode = "content"`; substituir por fonte real autorizada | Preencher permissao, termos e `authorization_basis` |
| Qconcursos (desabilitada) | `qconcursos.com/questoes-de-concursos/` | Somente identificador, URL e metadados de referencia | 20 referencias por execucao, 3 s entre requisicoes | `reference_only`; exige autorizacao escrita antes de habilitar | Termos reservam o banco e vedam reproducao sem autorizacao escrita |
| Gran Questoes (desabilitada) | `questoes.grancursosonline.com.br` | Somente identificador, URL e metadados de referencia | 20 referencias por execucao, 3 s entre requisicoes | `reference_only`; exige autorizacao escrita antes de habilitar | Termos protegem os produtos e restringem copia/reproducao |

`reference_only` nunca baixa o enunciado da pagina encontrada, nunca envia esse conteudo
para a OpenAI e nunca o grava em staging. Essas referencias podem ajudar a localizar a
prova oficial ou orientar pesquisa de produto, mas nao podem ser usadas para copiar,
parafrasear ou gerar questoes derivadas de conteudo proprietario. Para usar conteudo de
uma plataforma comercial, obtenha licenca escrita e registre a referencia da autorizacao.

## Testes e verificacoes

Os testes usam somente fixtures locais; nao acessam sites, API OpenAI nem banco.

```cmd
python -m unittest discover -s tests -v
ruff check .
mypy src
```

Artefatos dentro de `data/` podem conter material protegido e nao devem ser enviados ao
GitHub. Nunca contorne autenticacao, CAPTCHA, bloqueios, paywalls ou restricoes tecnicas.
