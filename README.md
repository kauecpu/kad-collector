# KAD Collector

Pipeline privado para localizar provas e gabaritos em fontes autorizadas, baixar PDFs,
extrair texto, estruturar questoes com IA e preparar lotes para revisao editorial. O
coletor nunca publica conteudo diretamente no aplicativo KAD.

```text
Fonte oficial -> coleta controlada -> PDFs -> extracao -> IA -> gabarito
              -> validacao -> revisao humana -> banco de staging
```

## Estado atual

O repositorio fornece o mecanismo generico, mas **nenhuma fonte real vem habilitada por
padrao**. O arquivo `config/sources.example.toml` e somente um molde. Antes de habilitar
uma fonte, confira seus termos, `robots.txt`, direitos de uso e limites de requisicao.

O MVP processa paginas HTML estaticas que contenham links para PDFs. Paginas que dependem
de JavaScript ainda nao usam Playwright. PDFs digitalizados sem camada de texto sao
marcados como `needs_ocr` e nao seguem automaticamente para a IA.

## Requisitos

- Python 3.11 ou superior;
- uma chave da API OpenAI apenas para a etapa `process`;
- PostgreSQL/Supabase apenas para a etapa opcional `stage --execute`.

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

Somente depois da conferencia, altere `enabled = false` para `enabled = true`.

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

O painel local permite conferir e editar enunciado, alternativas, gabarito, paginas de
origem e classificacao; cada questao pode ser aprovada ou rejeitada individualmente:

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
integridade. PDFs, sessoes e lotes continuam locais e ignorados pelo Git.

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

A aprovacao exige todas as respostas e grava um hash do conteudo. Qualquer alteracao
posterior invalida a importacao. Revise tambem direitos, fidelidade do enunciado,
alternativas, classificacao e gabarito antes de aprovar.

### 6. Enviar para o banco de staging

Um responsavel pelo banco deve aplicar uma vez `database/schema.sql`. O script cria o
schema isolado `collector` e remove acesso dos papeis publicos `anon` e `authenticated`.

Primeiro execute somente a previa, que nao abre conexao com o banco:

```cmd
kad-collector stage data\approved\LOTE.json
```

Para gravar, use uma conexao PostgreSQL de um usuario restrito ao schema de staging:

```cmd
set KAD_DATABASE_URL=postgresql://usuario:senha@host:5432/banco
kad-collector stage data\approved\LOTE.json --execute
```

Nao use chave `service_role`. A escrita e idempotente e termina em
`collector.question_staging` com `editorial_status = 'pending_review'`; promover dados
para as tabelas do aplicativo e uma operacao editorial separada.

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
4. registra referencias novas, mudancas por fonte e falhas temporarias;
5. mantem uma fila de retentativas com limite de tentativas;
6. grava `data/results/automatic-*.json` com metricas e excecoes para a equipe.

O relatorio automatico lista os IDs em `changed_sources` e detalha tentativas pendentes ou
esgotadas em `retry_queue`, sem esconder falhas persistentes no estado interno.

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

Nenhuma fonte vem habilitada. O arquivo de exemplo inclui os seguintes moldes:

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
