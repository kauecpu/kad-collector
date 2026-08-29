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
habilita fontes. O arquivo opt-in `config/sources.official.toml` cadastra onze fontes oficiais
conferidas: dez de conteudo e uma somente de referencias. Revise o contato do `user_agent`,
termos, `robots.txt` e limites antes da primeira execucao no ambiente da equipe.

O MVP processa paginas HTML estaticas que contenham links para PDFs. Paginas que dependem
de JavaScript ainda nao usam Playwright. PDFs digitalizados sem camada de texto sao
marcados como `needs_ocr` e nao seguem automaticamente para a IA.

## Requisitos

- Python 3.11 ou superior;
- uma chave do provedor escolhido apenas quando uma etapa de IA for habilitada;
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

Para a etapa geral `process`, defina a chave OpenAI somente na sessao atual do CMD:

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

O Collector possui uma central editorial local organizada em cinco etapas: **Coletar**,
**Preparar**, **Completar**, **Revisar** e **Exportar**. A tela inicial destaca o banco ativo,
o tipo de ambiente, as contagens editoriais e a próxima ação necessária. O estado fica em
SQLite dentro de `%LOCALAPPDATA%\KAD Collector`; nenhuma questão é enviada ao Supabase pelo
aplicativo.

Cada questão separa os estados de gabarito, preparação canônica, classificação e importação.
Todo bloqueio visível informa uma causa e uma próxima ação. IDs, fingerprints e evidências
técnicas continuam disponíveis apenas nos detalhes de diagnóstico.

Os cards **Com resposta oficial**, **Anuladas** e **Sem resposta associada** abrem o recorte
correspondente. Questões sem resposta são divididas, sem sobreposição, entre gabarito não
coletado, gabarito coletado mas não associado, questão ausente no gabarito ligado, associação
ambígua e diagnóstico pendente. A classificação é passiva: usa somente vínculos, versões e
evidências já registradas no SQLite e nunca presume um motivo que o banco não consiga provar.
Os mesmos estados aparecem na lista, na revisão e na prévia de exportação.
O Qwen pode sugerir somente a classificação editorial; ele não cria, completa nem confirma
respostas oficiais e por isso nunca aparece como ação para uma pendência de gabarito.

Ao concluir uma coleta, o Collector prepara automaticamente as questões usando apenas evidências
já salvas no banco. O botão **Preparar questões para classificação** permite repetir essa operação
de forma segura. Ela identifica as provas com vínculo ativo de gabarito, separa os cadernos por
cargo, etapa, turno e tipo, reúne cópias pelo conteúdo e escolhe uma ocorrência principal. A preparação
mantém separados dois estados: uma unidade pode receber classificação mesmo quando sua
equivalência ainda exige revisão; somente a importação depende da confirmação canônica. O
painel mostra quantas questões o Qwen cobre, quantas chamadas são necessárias e quantas cópias
herdarão a classificação. A prévia não grava dados e a confirmação não executa o modelo.

O Collector ignora número, letra e ordem das alternativas ao comparar cópias. Também tolera
espaçamento, pontuação e pequenos resíduos de extração, mas mantém alternativas realmente
diferentes em grupos separados. O gabarito é comparado pelo texto da resposta, não pela letra.
A cópia principal é escolhida por evidência oficial, vínculo válido, integridade e avisos; o mesmo
banco sempre produz a mesma escolha enquanto os dados não mudarem. As cópias ficam consultáveis
nos detalhes, herdam a classificação editorial e não aparecem na fila normal nem na importação.
Uma questão que não exista em todos os tipos continua válida e não bloqueia as demais.

A aba **Coletar links** permite escolher uma fonte cadastrada e informar a pagina especifica
de um concurso, exame ou ano. A coleta roda em segundo plano, valida o host, respeita
`robots.txt`, aplica o intervalo e os limites da configuracao, grava o manifesto e cria lotes
locais de no maximo 20 PDFs para processamento. O link informado fica registrado como origem;
o lote continua pendente ate revisao humana.

Instale a interface e execute:

```cmd
python -m pip install -e ".[desktop]"
kad-collector-desktop
```

A interface aceita lotes textuais de aproximadamente 300 paginas, trabalha em segundo plano
e salva checkpoints por pagina. **Pausar** encerra o trecho corrente com seguranca; **Retomar**
continua das paginas ja persistidas. PDFs digitalizados ou paginas sem camada de texto entram
em `excecoes.jsonl`; OCR nao faz parte desta versao.

Cada lote aceita no maximo 20 PDFs, 5.000 paginas no total, 1.000 paginas por arquivo e
50 MB por PDF. Arquivos que ultrapassam os limites entram nas excecoes sem serem processados.
A interface HTTP local aceita somente `127.0.0.1` ou `localhost` na porta iniciada pelo
aplicativo; APIs de leitura e escrita exigem o token efemero da sessao.

O classificador `local` usa a taxonomia versionada do Collector e uma cascata conservadora:
titulo de secao, intervalo do edital, contexto das questoes vizinhas e regras semanticas
locais. Cada valor guarda origem, confianca e justificativa. Quando nao existe evidencia
suficiente, o campo permanece vazio e a interface apresenta **Nao classificada**; nomes fora
da taxonomia nao sao criados. O botao **Reclassificar acervo** reaplica essa classificacao ao
banco local sem baixar os PDFs e sem alterar gabaritos, decisoes humanas ou sugestoes aceitas do
Qwen. Antes das regras locais, ele recupera para a questão principal qualquer classificação
protegida que ainda esteja preservada em uma cópia equivalente.

O botão **Classificar pendentes com Qwen 8B** oferece um lote local assistido. Ao abrir,
o Collector prepara uma cópia em memória e conta questões com resposta oficial, unidades de
classificação, cópias que herdam o resultado, campos completos, regras locais e chamadas de IA.
Uma questão respondida não fica bloqueada por grupo incompleto ou conflitante: o Qwen classifica
cada conjunto de conteúdo equivalente, sem confirmar a importação. Questões com alternativas,
origem ou vínculo inválidos permanecem fora do lote com o motivo visível. A prévia também
confere o endpoint loopback, a tag `qwen3:8b`, a
quantização `Q4_K_M` e o digest aprovado. Nenhuma inferência ou escrita ocorre antes da
confirmação. O limite padrão é 25 e pode ser ajustado entre 1 e 250.

Depois da confirmação, um aquecimento exige contexto 4096 e `100% GPU` em `/api/ps` e
`ollama ps`. O motor determinístico continua vindo primeiro, e o Qwen recebe somente os campos
ausentes entre disciplina, matéria, assunto e nível. O progresso pode ser pausado e retomado
no mesmo identificador; uma sugestão aceita preenche as cópias equivalentes que ainda não têm
decisão editorial e o modelo é descarregado ao
parar ou concluir. Respostas, gabaritos, vínculos, classificações existentes e decisões
humanas não são alterados. O desktop não carrega o 14B nem usa provedor externo nesse fluxo.

O contexto de vizinhanca so e propagado quando as questoes anterior, atual e seguinte possuem
o mesmo identificador explicito de bloco. Ausencia de bloco nunca significa bloco compartilhado.
As regras locais pontuam palavras e expressoes completas; uma palavra isolada so especializa
materia e assunto quando a disciplina ja foi comprovada por uma fonte mais forte.

A taxonomia editorial e composta pelo manifesto
`src/kad_collector/editorial_taxonomy.bundle.v2.json` e por catalogos JSON independentes.
Cada catalogo identifica o concurso ao qual se aplica, registra sua versao, aliases,
hierarquia de disciplina/materia/assunto e URLs oficiais de proveniencia. O pacote inicial
cobre RFB22, PCAM21 e STN24. Para incluir outro concurso, adicione um catalogo com fontes
oficiais e inclua seu caminho no manifesto; o motor nao precisa receber regras exclusivas da
banca. Cabecalhos reconhecidos no PDF e trechos estruturados do conteudo programatico podem
ser testados localmente pelas fixtures pequenas em `tests/fixtures/editorial_programs`.

O contexto completo de uma pagina nunca e tratado como titulo apenas por conter o nome de
uma disciplina. Somente uma linha compativel com cabecalho controlado e o catalogo
correspondente aos metadados do concurso pode acionar essa etapa da cascata. Essa restricao
evita que termos comuns de um concurso recebam silenciosamente a classificacao de outro.

A classificacao canonica aceita `gemini`, `qwen`, `deepseek` e `ollama` e permanece desligada
por padrao. Uma chamada exige `--apply`, `--enable-ai` e um provedor explicito. O Ollama usa
somente o computador local e nao requer chave; os outros tres usam APIs externas. Nao existe
fallback automatico entre eles. Consulte
`docs/canonical-ai-providers.md` antes de configurar chaves e endpoints regionais.

O motor local sempre roda primeiro. A IA recebe o conteúdo derivado limpo e escolhe um único
caminho taxonômico fechado; disciplina, matéria e assunto são extraídos desse caminho. Nível
aceita somente Fundamental, Médio ou Superior. Um único caminho compatível é resolvido sem IA.
Dificuldade e explicacao permanecem
opcionais: nao acionam IA, nao entram na metrica de completude e nao enviam a questao para
revisao. A IA nao pode criar nomes, escolher gabarito, resolver identidade ou substituir
classificacoes locais ou humanas. Sem chave, sem internet ou com resposta invalida, o
processamento preserva o resultado local e encaminha apenas a pendencia taxonomica para
revisao. Chaves nunca sao salvas no SQLite, no executavel ou nos relatorios.

O benchmark controlado compara Gemini, Qwen e DeepSeek apenas em disciplina, materia,
assunto e nivel. A preparacao e offline; piloto e lote final exigem aprovacoes separadas,
identificador exato da amostra e teto de custo. Consulte
[`docs/canonical-ai-benchmark.md`](docs/canonical-ai-benchmark.md). A configuracao das chaves
nao dispara nenhuma chamada automaticamente.

O benchmark local compara `qwen3:8b` e `qwen3:14b` nas mesmas 175 referências selecionadas pela
auditoria v3. A inspecao inicial nao baixa modelos nem gera respostas. O smoke local exige aprovacao
explicita, mede dez questões por modelo, totaliza 20 chamadas e grava cada resultado antes de
seguir. A fase completa de 350 combinações terminou sem falhas e recomenda `qwen3:8b`: 78,286%
de acerto conjunto contra 69,714% do 14B. Consulte
[`docs/ollama-local-ai.md`](docs/ollama-local-ai.md).

Se a cópia SQLite local tiver sido perdida, `export-supabase-benchmark` pode recriar somente o
recorte dessas 175 referências a partir do histórico editorial do Supabase. O comando usa
`KAD_DATABASE_URL`, abre uma transação PostgreSQL somente leitura, confere os fingerprints da
auditoria e grava o resultado em `data/benchmarks/local/`, fora do Git. Consulte a seção
**Restaurar a cópia local pelo Supabase** no guia do Ollama antes de usar `--execute`.

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

O executavel validado para Windows tambem fica disponivel na pagina de
[Releases do KAD Collector](https://github.com/kauecpu/kad-collector/releases). Binarios nao
sao commitados no historico Git; cada versao e publicada como artefato da release correspondente.

Na interface, o card **Importaveis** conta questoes estruturalmente aptas para entrar no app.
Explicacao e dificuldade nao participam dessa conta; resposta oficial, alternativas validas,
classificacao, duplicidade e origem comprovada continuam sendo barreiras. A prontidao para
publicacao editorial permanece separada e ainda pode exigir explicacao e dificuldade.

O card **Pendentes** abre a fila editorial. A revisao permite editar a questao,
consultar o PDF na pagina de origem, enviar para excecoes com justificativa, adiar a decisao e
aprovar individualmente ou em lote. Somente uma aprovacao humana move a questao para
**Exportaveis**. A interface nao solicita nome de revisor nem observacao generica; as mutacoes
locais usam o ator tecnico `operador_local` na auditoria. Excecao e rejeicao continuam exigindo
uma justificativa visivel e auditavel.

### Identidade semântica e histórico do documento

O Collector mantém quatro conceitos separados para não confundir uma cópia do arquivo com uma
mudança editorial:

- **Arquivo:** os bytes locais do PDF, identificados pelo SHA-256.
- **Observação:** o registro de que aqueles bytes foram vistos, inclusive origem e datas. Uma
  nova origem do mesmo arquivo não cria outra versão.
- **Versão lógica:** o conteúdo normalizado de uma prova ou gabarito dentro de uma identidade
  semântica. Conteúdo equivalente em bytes diferentes é uma republicação; conteúdo alterado é
  uma nova versão com predecessora quando aplicável.
- **Vínculo ativo:** a associação selecionada entre uma versão de prova e uma versão de gabarito.
  Um vínculo é ativado somente com evidência suficiente; ele não transforma ausência de evidência
  em confirmação.

Na revisão de uma questão, o bloco **Identidade semântica do documento** mostra os campos
conhecidos (banca, concurso, ano, cargo, turno e tipo), papel, situação do gabarito e versão.
Os detalhes recolhíveis trazem a evidência, o motivo e a versão do algoritmo, mas não mostram
texto integral de páginas. Os selos têm este significado: **Duplicata exata** indica os mesmos
bytes já observados; **Republicação**, conteúdo equivalente de outra observação; **Nova versão**,
conteúdo alterado dentro da mesma identidade; e **Exceção**, identidade insuficiente ou
conflitante. Identidades desconhecidas ou conflitantes não recebem percentual de confiança.

Estados de exceção incluem documento sem identidade mínima, campos com evidência conflitante,
gabarito sem candidato, empate e conflito de escopo. Eles ficam visíveis para revisão e não
recebem respostas oficiais por inferência. O reprocessamento cria uma observação operacional
nova a partir do contrato local preservado, retoma resolução interrompida sem duplicar eventos e
nunca substitui histórico, decisões editoriais ou auditoria.

Uma correção manual exige ator e metadados comprovados no endpoint local autenticado
`PUT /api/documents/{id}`. A operação é auditada, rejeita colisões de versões e reavalia apenas
os vínculos afetados; decisões de questões só são invalidadas quando a mudança relevante exigir.
Para diagnóstico local do banco, estas consultas devem retornar zero linhas em estado coerente:

```sql
SELECT binary_sha256, COUNT(*) FROM document_observations GROUP BY binary_sha256 HAVING COUNT(*) > 1;
SELECT identity_key, document_role, content_sha256, COUNT(*) FROM document_versions GROUP BY 1, 2, 3 HAVING COUNT(*) > 1;
SELECT exam_version_id, COUNT(*) FROM document_links WHERE status = 'active' GROUP BY exam_version_id HAVING COUNT(*) > 1;
SELECT event_key, COUNT(*) FROM document_identity_events GROUP BY event_key HAVING COUNT(*) > 1;
```

### Identidade canônica de concursos e aplicações

O catálogo `canonical-identity-v1` separa concurso, aplicação, cargo, etapa, turno, caderno e
documento oficial. Aliases como `RFB22` funcionam como entrada e resolvem para IDs estáveis; os
nomes de exibição podem mudar sem alterar relações. Documentos locais são vinculados ao catálogo
por SHA-256 ou `external_id`, com conflitos enviados para revisão.

A migração, o diagrama, a regressão RFB22 e os comandos de simulação e aplicação estão em
[`docs/canonical-identity-v1.md`](docs/canonical-identity-v1.md).

### Equivalência e questão canônica

`question-equivalence-v1` preserva todas as ocorrências por caderno, confirma grupos somente
com escopo e resposta oficial compatíveis e direciona classificação e exportação para uma única
representante. O algoritmo, o modelo de auditoria, a CLI e os limites da regressão sintética
RFB22 estão em [`docs/question-equivalence-v1.md`](docs/question-equivalence-v1.md).

O painel e as filas comuns contam somente questões únicas. O total de aparições extraídas dos
PDFs fica separado na etapa de preparação, e as cópias só aparecem em **Ver cópias e origens**.

No desktop, grupos ainda não confirmados ganham uma representante provisória apenas para
classificação. Esse registro não confirma equivalência e não libera exportação.

### Classificação canônica

`canonical-classification-v2` executa a taxonomia local sobre representantes elegíveis, envia
à IA somente disciplina, matéria, assunto e nível que continuarem ausentes e encaminha
incertezas para revisão humana. O contrato restrito, a política de confiança, a auditoria, a
privacidade e os comandos estão em
[`docs/canonical-classification-v1.md`](docs/canonical-classification-v1.md).

Para consulta pela interface local, `GET /api/bootstrap` inclui `semanticSummary` e
`GET /api/documents/{id}/identity` devolve o read model de identidade. Ambos usam o token
efêmero da sessão; o segundo endpoint não devolve `canonicalText` nem texto integral do PDF.

## Documento normalizado: aquisicao e interpretacao

O Collector separa a **aquisicao** da **interpretacao**. Aquisicao e a etapa que conhece a
fonte e aceita ou rejeita um download. Ela descobre links, valida hosts e redirecionamentos,
aplica `robots.txt`, `Crawl-delay`, quotas, MIME, tamanho, SHA-256, termos e autorizacao. A
interpretacao recebe somente um PDF local validado e seu contrato normalizado. Ela extrai
paginas, identifica prova e gabarito pela estrutura, associa documentos com evidencia,
estrutura questoes, valida, preserva duplicatas para revisao e persiste o resultado.

```text
Fonte oficial -> aquisição -> documento local normalizado -> interpretação genérica -> revisão -> exportação
```

As regras de hosts, descoberta, paginacao, padroes de prova e gabarito, politicas de crawl e
metadados declarados por fonte continuam exclusivamente na aquisicao. Elas existem antes de
haver um arquivo aceito e provam de onde ele veio. O interpretador nao escolhe comportamento
por `source_id`, nome de fonte ou configuracao TOML, para que o mesmo PDF local tenha a mesma
entrada quando veio de coleta ou de importacao.

### Contrato `NormalizedDocument`

Todo documento entregue ao interpretador possui os campos abaixo. Ausencia significa
desconhecido ou nao aplicavel, nunca um valor inferido para preencher o contrato.

| Campo | Regra e semantica de ausencia |
|---|---|
| `local_path` | Caminho local preservado do PDF, obrigatorio. Importacoes diretas o resolvem para caminho absoluto; registros coletados pela CLI podem preservar caminho relativo. |
| `sha256` | Hash SHA-256 do arquivo local, obrigatorio; divergencia impede o processamento. |
| `size_bytes` | Tamanho do arquivo em bytes, obrigatorio e maior que zero. |
| `declared_type` | `auto`, `exam`, `answer_key` ou `other`; `auto` significa que o tipo nao foi declarado e sera identificado estruturalmente. |
| `title` | Titulo obrigatorio; na importacao local e o nome do arquivo. |
| `original_url` | URL originalmente solicitada; `null` quando o documento nao veio de URL conhecida. |
| `resolved_url` | URL final apos redirecionamentos; `null` quando nao existe URL conhecida. |
| `source_page_url` | Pagina que levou a descoberta; `null` quando nao foi registrada. |
| `entry_method` | `automated_collection`, `direct_import` ou `reprocessing`, sempre informado. |
| `metadata` | Somente metadados conhecidos, como banca, ano, cargo e orgao; `{}` quando nenhum foi comprovado. Chaves ausentes nao sao inventadas. |
| `evidence` | Evidencias de autorizacao e termos da coleta; `[]` quando nao houve evidencia registrada. |
| `warnings` | Avisos de compatibilidade ou de entrada; `[]` quando nao ha avisos. |
| `external_id` | Identificador externo; `null` quando a fonte nao o forneceu. |
| `source_id` | Identificador interno da fonte de aquisicao; `null` na importacao sem fonte. |
| `source_name` | Nome da fonte de aquisicao; `null` na importacao sem fonte. |
| `content_type` | MIME observado na aquisicao; `null` quando nao foi observado. |
| `acquired_at` | Data e hora da aquisicao; `null` quando nao existem. |

No pipeline desktop, o contrato completo e gravado em `documents.normalized_json` no SQLite.
O arquivo, hash, metadados e evidencias ficam associados ao documento para auditoria e revisao,
inclusive quando a interpretacao falha. Os fluxos de CLI `collect`, `run` e `sync` mantem os
schemas de manifesto e relatorio existentes e normalizam o documento na fronteira local de
extracao, sem `DesktopStore`.

### Como cada entrada chega ao mesmo fluxo

- **Coleta automatica:** na aba **Coletar links** ou nos comandos `collect`, `run` e `sync`, a
  configuracao da fonte primeiro aplica as regras de aquisicao. Cada download aprovado e
  adaptado para `entry_method = "automated_collection"`; falhas de download nao criam trabalho
  de interpretacao.
- **Importacao direta:** no aplicativo desktop, selecione um PDF, varios PDFs ou uma pasta. A
  pasta e expandida para seus PDFs e todos passam pela mesma submissao de documentos locais,
  com `entry_method = "direct_import"`. O hash e o tamanho sao calculados localmente; campos de
  origem que nao existem permanecem `null` ou vazios.
- **Reprocessamento local:** o servico da aplicacao cria um novo trabalho a partir do contrato
  armazenado, valida novamente o arquivo local e usa `entry_method = "reprocessing"`. Ele nao
  chama descoberta, downloader ou configuracao de fonte. Este recurso esta disponivel somente
  como servico da aplicacao; esta versao nao documenta um comando de CLI nem uma rota de UI para
  acionamento manual.

O reprocessamento nunca sobrescreve o documento, a questao, a decisao editorial ou a auditoria
historicos. O novo trabalho preserva sua propria evidencia. Se gerar a mesma questao, a nova
questao recebe a flag `duplicate`; a questao historica continua intacta para comparacao e
revisao.

### Nova fonte e nova estrategia estrutural

Para cadastrar uma nova fonte, altere somente sua configuracao de aquisicao, seguindo
**Configurando uma fonte**: origem autorizada, `allowed_hosts`, estrategias de descoberta,
padroes de links e tipos, limites, politica de `robots.txt` e `Crawl-delay`, base de uso e
metadados comprovados. Comece com `enabled = false` e habilite somente apos a conferencia
administrativa. Nenhuma nova fonte deve introduzir regra de identificador no interpretador.

Uma nova estrategia de documento so e justificada quando a estrutura do PDF nao puder ser
representada por `declared_type`, metadados, titulo, conteudo e evidencia ja disponiveis, e a
mesma estrutura puder ocorrer em mais de uma fonte. Exemplos sao um formato novo de variante,
uma relacao prova-gabarito ou uma organizacao de itens que a interpretacao generica nao consiga
identificar com evidencia. Diferencas de host, URL, catalogo ou politica continuam sendo
configuracao de aquisicao, nao estrategia de interpretacao.

### SQLite, compatibilidade e rollback

A migracao adiciona somente a coluna anulavel `normalized_json` a `documents` com `ALTER TABLE`.
Nao remove nem altera colunas, questoes, paginas, auditoria ou decisoes existentes. Bancos
anteriores continuam legiveis. Ao reprocessar uma linha antiga sem contrato salvo, o Collector
reconstroi apenas os campos comprovados pelas colunas existentes e registra um aviso de
compatibilidade; campos de origem ausentes continuam desconhecidos.

Para rollback desta mudanca:

1. Encerre o Collector.
2. Preserve `collector.sqlite3`, os arquivos `collector.sqlite3-wal` e
   `collector.sqlite3-shm`, e os PDFs locais.
3. Instale ou execute a versao anterior do Collector. Ela ignora a coluna adicional
   `normalized_json` e le as colunas anteriores sem restauracao do banco.
4. Nao apague documentos, questoes, auditoria ou decisoes para desfazer a versao. Se for
   necessario reconstruir somente cache, telemetria e checkpoints de coleta, remova apenas
   `collection-engine.sqlite3`, como descrito em **Operacao e recuperacao**.

Uma copia de seguranca do banco continua recomendada antes de atualizar o aplicativo.

### Limitacoes conhecidas

- O Collector detecta PDF digitalizado ou com pouco texto, mas nao executa OCR.
- Prova e gabarito no mesmo PDF nao recebem suporte completo neste fluxo.
- Associacoes sem evidencia suficiente, inclusive empates, seguem para excecao sem resposta
  oficial aplicada.
- Parsers locais legados permanecem apenas para o teste guiado e a regressao.
- A separacao nao cria fonte, agendamento, identidade semantica completa, publicacao no KAD,
  comando de CLI ou rota de UI para reprocessamento.

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

Todas as fontes usam intervalo de 3 segundos, limite de 40 arquivos por fonte, 5 MB por
pagina HTML e 50 MB por PDF. A aba desktop executa um link por rodada; a CLI `sync` usa as
`start_urls`. Campos comuns: titulo, tipo do documento, URL original e resolvida, SHA-256,
data de coleta, banca, orgao, cargo/exame e ano quando conhecido.

| Fonte | Origem e conteudo | Descoberta | Execucao |
|---|---|---|---|
| FUVEST | `fuvest.br`; provas e gabaritos de 2025 e 2026 | Ate 4 paginas | `content`; aba por link ou `sync` |
| COPERVE | `vestibularunificado2026.ufsc.br`; provas e gabaritos definitivos de 2026 | 1 pagina | `content`; aba por link ou `sync` |
| FGV Conhecimento | `conhecimento.fgv.br/concursos`; somente provas, cadernos e gabaritos por concurso; termos, avisos, editais e resultados sao excluidos | Ate 40 paginas de concursos | `content`; prefira colar a pagina de um concurso na aba; a pagina geral percorre o indice com limite |
| INEP - ENEM | `gov.br/inep` e `download.inep.gov.br`; cadernos e gabaritos por edicao | Ate 30 paginas anuais | `content`; aba por ano/PDF ou `sync` |
| INEP - ENADE | `gov.br/inep` e `download.inep.gov.br`; provas, gabaritos e padroes por curso | Ate 30 paginas anuais | `content`; aba por ano/PDF ou `sync` |
| INEP - Encceja | `gov.br/inep` e `download.inep.gov.br`; cadernos e gabaritos por nivel | Ate 30 paginas anuais | `content`; aba por ano/PDF ou `sync` |
| INEP - Revalida | `gov.br/inep` e `download.inep.gov.br`; provas e padroes de resposta | Ate 30 paginas anuais | `content`; aba por edicao/PDF ou `sync` |
| COMVEST/Unicamp | `comvest.unicamp.br`; acervo historico e provas comentadas | Ate 20 paginas | `content`; reproducao parcial com fonte e ano citados |
| OBMEP | `obmep.org.br`; provas e solucoes de 2005 a 2025 | 20 paginas anuais | `reference_only`; arquivos recentes usam rota do Drive bloqueada pelo `robots.txt` |
| UERJ | `sistema.vestibular.uerj.br`; provas, gabaritos e padroes desde 1997 | Ate 30 paginas | `content`; aba por pagina/PDF ou `sync` |
| PCI Concursos - Banco do Brasil | `pciconcursos.com.br`; indice publico com provas e gabaritos por cargo, ano, caderno e versao | Ate 20 paginas do indice | `content`; aba por pagina do concurso ou `sync` |

#### Piloto PCI Concursos (Banco do Brasil)

O piloto usa as paginas publicas do indice do [PCI Concursos](https://www.pciconcursos.com.br/provas/banco-do-brasil)
para localizar os pares de 2023 e 2021. Cada PDF e gravado com URL original, data, tamanho e
SHA-256; o manifesto tambem guarda cargo, orgao, banca, ano, tipo/caderno e etapa quando esses
dados aparecem na pagina. Prova, gabarito preliminar e definitivo ficam separados por versao e
o definitivo tem prioridade somente depois da conferencia de cobertura e alternativas.

Use a pagina especifica do ano/cargo na aba **Coletar links** para limitar o piloto. O modo `html`
le apenas links estaticos; se o PCI apresentar CAPTCHA, Cloudflare ou outro desafio, a rodada
registra `acao manual necessaria` e segue para outras fontes. O Collector nao tenta contornar
autenticacao, CAPTCHA, Cloudflare ou bloqueios. Por decisao administrativa explicita do
responsavel em 2026-08-29, esta fonte usa `ignore` para `robots.txt` e `Crawl-delay`; essa
decisao fica registrada no manifesto e na telemetria. Nao ha um
limitador artificial adicional criado para o PCI.

Os PDFs ficam fora da exportacao normal ate a revisao editorial confirmar a origem e a permissao
de republicacao. O piloto nao publica nem importa conteudo no KAD.

Na FUVEST, os cadernos V1-V4 repetem as mesmas questoes em ordens diferentes. O Collector
preserva todos os PDFs como evidencia, usa a menor versao disponivel (normalmente V1) como
caderno canonico e seleciona no gabarito o bloco correspondente a essa versao. Assim, uma
prova de 90 itens produz 90 questoes, e nao 360 duplicatas. A coleta so aparece como concluida
quando o processamento termina; questoes sem resposta oficial deixam a rodada em
`needs_attention`. Explicacoes continuam pendentes de enriquecimento e revisao editorial.

## Motor de coleta profissional

O motor separa transporte, descoberta, cache, checkpoints e telemetria. A coleta por link
continua aceitando as configuracoes anteriores; os campos novos possuem valores padrao.

O transporte usa pool de conexoes, HTTP/2 quando o servidor oferece suporte, streaming para
PDFs, SHA-256 incremental, gravacao temporaria e troca atomica do arquivo concluido. Downloads
interrompidos usam `Range` e `If-Range` quando o servidor aceita retomada. Respostas 408, 425,
429, 500, 502, 503 e 504 entram em retentativa com backoff, jitter e `Retry-After` limitado pelo
valor `retry_max_delay_seconds`.

O cache persistente fica em `collection-engine.sqlite3`. Ele armazena ETag, Last-Modified,
hash, tamanho, caminho e data de verificacao. Uma resposta 304 reutiliza o arquivo somente
depois de confirmar tamanho e SHA-256. O banco tambem guarda checkpoints e telemetria sem
cookies, tokens ou query strings.

### Perfis de capacidade

| Perfil | Concorrencia | Intervalo | Uso |
| --- | ---: | ---: | --- |
| Conservador | 2 | 3 s | Servidores pequenos ou instaveis |
| Equilibrado | 4 | 1 s na interface | Coletas oficiais comuns |
| Alto desempenho | 8 | 0 s | Acervos preparados para downloads paralelos |
| Personalizado | 1 a 32 | 0 a 300 s | Ajuste administrativo por execucao |

O perfil nao desativa seguranca de rede. Hosts cadastrados, DNS publico, redirects validados,
TLS, quotas, limites de descompressao e cancelamento continuam ativos. `robots.txt` e
`Crawl-delay` sao politicas administrativas independentes, sempre registradas no manifesto.

Cada fonte aceita `enforce`, `observe` ou `ignore`. `enforce` consulta e aplica a regra;
`observe` consulta e registra o que teria sido bloqueado sem interromper a coleta; `ignore`
nao consulta o arquivo nem aplica o atraso. O modelo e as fontes de exemplo continuam usando
`enforce` como padrao seguro. Por decisao administrativa explicita do responsavel em
2026-08-18, todas as fontes do manifesto oficial usam `ignore` tanto para `robots.txt` quanto
para `Crawl-delay`; a escolha aparece na tela e fica registrada por fonte no manifesto, nos
avisos e na telemetria. Os intervalos, a concorrencia e os demais limites internos continuam
independentes dessas duas politicas. Nenhum desses modos autoriza atravessar login, CAPTCHA,
autenticacao ou um bloqueio explicito do servidor.

### Estrategias de descoberta

Cada fonte escolhe uma sequencia:

```toml
discovery_strategies = ["html", "sitemap", "feed", "json", "browser"]
sitemap_urls = ["https://www.example.gov.br/sitemap.xml"]
feed_urls = ["https://www.example.gov.br/provas.xml"]
browser_enabled = true
```

- `html` le links e paginacao visiveis no documento estatico;
- `sitemap` aceita `urlset`, indices e gzip com limite de expansao;
- `feed` aceita RSS e Atom;
- `json` usa somente endpoints GET publicos declarados na fonte;
- `browser` executa JavaScript com Playwright e Edge/Chromium sem modo stealth.

O navegador bloqueia navegacao fora dos hosts da fonte e identifica CAPTCHA, login e acesso
negado como `manual_action_required`. Ele nao resolve desafios nem importa cookies do navegador
pessoal. No Windows, o adaptador tenta usar Microsoft Edge; uma instalacao Playwright Chromium
tambem pode ser usada quando `PLAYWRIGHT_BROWSERS_PATH` estiver configurada.

Exemplo de JSON publico:

```toml
[[sources.json_endpoints]]
url = "https://www.example.gov.br/api/provas"
items_path = "data.items"
url_field = "download"
title_field = "titulo"
type_field = "tipo"
next_page_field = "data.proxima"
```

Cabecalhos `Authorization`, `Cookie` e `Proxy-Authorization` sao rejeitados na configuracao.

### Operacao e recuperacao

A tela **Coletar de um link** permite escolher o perfil, ativar JavaScript, ajustar concorrencia
e intervalo, pausar, continuar ou cancelar. Cada atividade mostra requisicoes, bytes, cache e
retentativas. O checkpoint preserva paginas pendentes e documentos ja descobertos.

Para rollback, encerre o Collector, preserve os PDFs em `collected/raw`, restaure uma copia do
banco principal quando houver migracao editorial e remova somente `collection-engine.sqlite3`
se quiser reconstruir cache, telemetria e checkpoints. Essa remocao nao apaga questoes do banco
`collector.sqlite3`.

A arquitetura tomou como referencia conceitos de cache, backoff e renderizacao publicados no
artigo da Bright Data sobre bloqueios de scraping. O projeto nao incorporou proxies, falsificacao
de TLS, navegadores stealth ou solucionadores de CAPTCHA.

Os gabaritos da COPERVE podem usar respostas numericas por soma de proposicoes, enquanto o
schema atual aceita alternativas A-H. Esses casos entram em `exception` e nao podem ser
aprovados silenciosamente. A COMVEST autoriza reproducao parcial e nao exclusiva das questoes
com citacao da fonte e do ano; o Collector nao reproduz a prova inteira como uma publicacao.
Nenhuma fonte publica automaticamente no KAD: todo conteudo continua sujeito a revisao
editorial antes da exportacao.

Para usar a configuracao opt-in:

```cmd
copy config\sources.official.toml config\sources.toml
kad-collector sync --config config\sources.toml
```

As protecoes do coletor incluem:

- obediencia a `robots.txt`, com bloqueio conservador quando ele nao pode ser consultado;
- intervalo configuravel por fonte, com valor padrao de tres segundos e suporte a zero;
- limite padrao de 20 PDFs por fonte, removivel explicitamente com valor nulo;
- limites configuraveis, por padrao 5 MB para HTML e 50 MB para cada PDF;
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

A aprovacao exige os campos obrigatorios do `EditorialImportRecord` v2, de duas a cinco
alternativas sequenciais entre A e E e uma resposta oficial. A explicacao e opcional. Quando
presente, leva origem e estado de revisao; conteudo de IA tambem exige provedor, modelo e versao
do prompt. Ela grava um
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

- `questoes.jsonl`, com uma questao principal valida por linha no contrato v2;
- `excecoes/questoes.jsonl`, com rejeicoes, anuladas e itens incompletos;
- `fontes/`, com os PDFs da prova e do gabarito preservados como evidencia;
- `manifesto.json` e `relatorio.json`, com hashes, contagens e motivos.

Importe apenas `questoes.jsonl` na tela **Importacoes** do painel KAD. O painel cria um
lote para revisao e aplica itens validos sempre como rascunho. O coletor nao usa o schema
`collector.*`, nao recebe credenciais do Supabase e nao escreve diretamente no banco.
O contrato v1 permanece disponivel para compatibilidade. A fonte canonica do v2 vive no
repositorio KAD e sua copia, fixture e fingerprint verificado estao em `contracts/`.

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

### Pacote hibrido de regressao

O pacote em `tests/regression/` combina fixtures sinteticas ficticias versionadas com dois
PDFs oficiais da FUVEST 2026 mantidos fora do Git. O manifesto registra URL de origem,
tamanho, SHA-256, formato, caso e estado de cobertura. A preparacao local baixa no maximo a
prova V1 e o gabarito retificado, uma requisicao por arquivo, sem descoberta ou paginacao:

```cmd
.venv\Scripts\python.exe scripts\prepare_regression_fixtures.py
```

Depois da preparacao, um comando valida integridade, bloqueia rede, executa cada caso
suportado duas vezes e grava um relatorio local sem acessar banco operacional:

```cmd
.venv\Scripts\kad-collector.exe regression
```

O pacote cobre prova e gabarito separados, resposta no mesmo documento, tipos 1 a 4,
preliminar e definitivo, anulacao, grades por cargo e turno e associacao ambigua bloqueada.
Republicacao, OCR real e rejeicao explicita de documento nao relacionado permanecem marcados
como `planned`. A [documentacao de manutencao](tests/regression/README.md) e a
[matriz final](tests/regression/COVERAGE.md) explicam os limites e o processo de atualizacao.

### Regressao oficial do RFB22

O contrato versionado em `tests/regression/rfb22/manifest.v1.toml` separa a aplicacao
principal de 19/03/2023 dos cursos de formacao e aplicacoes sub judice posteriores. Ele cobre
os 16 cadernos da primeira etapa e os tres gabaritos publicados para essa aplicacao. Os PDFs
ficam fora do Git e os testes nao acessam a rede.

Prepare e execute o pacote com:

```cmd
.venv\Scripts\python.exe scripts\prepare_official_contest_fixtures.py
.venv\Scripts\python.exe scripts\run_official_regression.py
```

O inventario, as contagens oficiais, as fontes e a politica de manutencao estao descritos em
`tests/regression/rfb22/README.md`.

### Revalidação obrigatória de gabaritos

O banco local pode simular e aplicar a migração de vínculos antigos para
`semantic-association-v3`. A rotina exige cargo, etapa, tipo e intervalo
compatíveis, inclui provas ainda sem vínculo, desativa empates e encaminha toda
associação não resolvida para revisão. Em documentos FGV, `MANHÃ`, `MANHA` e
`TARDE` são lidos somente de regiões estruturais do PDF; gabaritos podem cobrir
mais de um turno. Turno ausente é aceito somente quando não existe divisão por
turno ou quando um único gabarito definitivo compatível declara um único turno.
Gabaritos preliminares ficam aguardando o definitivo e não fornecem respostas
oficiais. Consulte
[`docs/answer-key-revalidation-v2.md`](docs/answer-key-revalidation-v2.md) para o
comando, retomada, formato do relatório e limitações.

No aplicativo, **Auditar vínculos** confere novamente todas as provas, inclusive
as que já possuem vínculo anterior. A auditoria compara concurso, cargo, turno, tipo,
ano, quantidade de questões e cada letra do gabarito com as alternativas da
prova. Ela mantém vínculos confirmados, corrige uma troca quando existe um único
gabarito compatível e envia dúvidas para revisão. O operador pode trocar ou
remover o gabarito do lote; o Collector recalcula todas as respostas afetadas e
registra a mudança no histórico. Antes de aplicar a auditoria, o aplicativo cria
uma cópia íntegra em `backups/`, ao lado do banco operacional.

Na preparação canônica, um vínculo ativo confirmado por
`semantic-association-v3` não exige que o turno esteja repetido no PDF da prova.
O Collector usa o único turno derivado de um gabarito definitivo ou registra
`não se aplica` quando os documentos não dividem a aplicação por turno. Dois
turnos ou candidatos possíveis continuam na revisão. A preparação cria uma
cópia íntegra do banco em `backups/`, mantém as ocorrências repetidas como
evidência e mostra somente a questão principal nas filas e na exportação.

O pipeline usa um [adaptador FGV orientado por secoes](docs/fgv-section-parser.md). O adaptador
separa as partes objetiva e discursiva antes de interpretar numeros isolados e compara o
resultado com `src/kad_collector/fgv_section_profiles.v1.toml`. Uma lacuna, duplicidade, quebra
de ordem ou numero fora do intervalo mantem as questoes extraidas, registra excecoes
estruturadas e impede que o documento seja marcado como processado.

Artefatos dentro de `data/` podem conter material protegido e nao devem ser enviados ao
GitHub. Nunca contorne autenticacao, CAPTCHA, bloqueios, paywalls ou restricoes tecnicas.
