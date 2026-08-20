# Separação entre aquisição e interpretação de documentos

## Objetivo

O Collector deve transformar qualquer PDF local em um documento normalizado antes de
interpretá-lo. A coleta por link e a importação de arquivos ou pastas devem entregar o mesmo
contrato ao mesmo processador. O processador não deve importar configuração de fonte,
descoberta HTML, transporte HTTP, política de crawl ou validação de host.

Esta mudança preserva o banco operacional, as respostas oficiais, a revisão e a exportação.
Ela não cria fonte, parser, OCR, executável ou fluxo de publicação.

## Diagnóstico do fluxo atual

### CLI e automação

1. `collector.collect_documents` descobre links, classifica o link como prova ou gabarito,
   valida o download e grava `DocumentRecord` em `DownloadManifest`.
2. `pdf_extractor.extract_manifest` valida tamanho e SHA-256, lê páginas e marca OCR.
3. `ai_processor.process_extraction_manifest` processa provas e cria `QuestionBatch`.
4. `review_queue.prepare_review_queue` escolhe um gabarito, aplica respostas, valida e cria a
   fila de revisão.
5. `reporting`, `local_review` e `editorial_export` preservam origem, decisão humana e
   evidência até a exportação.

`workflow.run_semiautomatic` e `automation.run_automatic` repetem essa sequência. A automação
mantém hashes processados, gabaritos extraídos e lotes pendentes para evitar duplicação.

### Aplicativo desktop

`DesktopApplication.import_pdfs` expande arquivos e pastas e chama
`DesktopStore.create_job`. `DesktopCollectionManager._run` chama `collect_documents`, converte
cada `DocumentRecord` em `DesktopImportMetadata`, divide os arquivos em lotes e também chama
`DesktopStore.create_job`. Os dois caminhos chegam a `DesktopProcessor`, mas usam adaptadores
diferentes e não persistem um contrato comum de entrada.

`DesktopProcessor` valida limites, extrai páginas com checkpoint, identifica prova e
gabarito, separa questões, escolhe o gabarito, aplica respostas oficiais, classifica, valida e
persiste. `DesktopStore` encaminha as questões para revisão e `desktop_export` mantém a
exportação atual.

### Regras específicas e mistura indevida

As regras abaixo pertencem à aquisição e permanecem lá:

- hosts, padrões de links, paginação, estratégias de descoberta, `robots.txt`, Crawl-delay,
  quotas, MIME, tamanho, SHA-256, URL e termos da fonte;
- restrição da FGV à página de um concurso, apresentação do catálogo e metadados declarados
  pelas fontes;
- padrões de prova e gabarito presentes nos manifestos TOML.

O processamento contém três dependências indevidas da origem:

- `desktop_processor._canonical_exam_documents` testa o identificador
  `fuvest_vestibular` para escolher V1;
- `desktop_processor._document_group` usa `provider` para agrupar provas e gabaritos e buscar
  gabaritos armazenados;
- `review_queue._select_answer_key` exige igualdade de `source_id` antes de comparar a
  estrutura dos nomes.

`desktop_collection._processing_batches` também mistura a saída da aquisição com decisões de
preparação do corretor. A função escolhe candidatos a gabarito e monta lotes antes de existir
um contrato neutro.

### Comportamentos que a mudança deve preservar

- validação de host, MIME, tamanho e hash antes de aceitar um download;
- limites de 20 PDFs por lote, 1.000 páginas por PDF, 5.000 páginas e 50 MB;
- checkpoints por página, pausa, retomada e isolamento de PDF ilegível ou sem texto;
- escolha canônica de V1 entre cadernos V1 a V4 sem apagar evidências;
- associação de prova e gabarito por versão, cargo, turno, ano e conteúdo disponível;
- preferência por gabarito definitivo e bloqueio de empate ou associação sem evidência;
- uso exclusivo do gabarito oficial para preencher `correct_answer`;
- classificação conservadora, decisões humanas, deduplicação, revisão e exportação.

### Riscos de migração

- converter registros antigos sem conhecer o método de entrada poderia inventar origem;
- retirar `provider` de uma associação sem substituir a evidência poderia ligar documentos
  incompatíveis;
- alterar o agrupamento de variantes poderia duplicar as 90 questões da FUVEST ou descartar
  cadernos distintos;
- reprocessar um lote no mesmo registro poderia invalidar decisões editoriais existentes;
- uma migração obrigatória ou destrutiva poderia impedir a leitura do SQLite atual.

## Alternativas consideradas

### Substituir os dois pipelines por uma implementação nova

Uma reescrita unificaria CLI e desktop em uma única operação, mas mudaria extração por IA,
checkpoints, fila e persistência no mesmo PR. O risco de alterar resultados e decisões humanas
supera o ganho desta etapa.

### Tratar `DocumentRecord` como contrato comum

`DocumentRecord` exige fonte, URLs, autorização e data de download. Uma importação local não
possui esses dados. Preencher os campos com valores artificiais violaria a regra de não
inventar metadados.

### Adicionar um contrato neutro e adaptar os caminhos atuais

Esta é a opção escolhida. O Collector mantém os mecanismos de aquisição e o interpretador
desktop. Dois adaptadores criam o mesmo contrato, uma orquestração curta monta lotes e o
interpretador deixa de consultar identificadores de fonte. A CLI mantém seus comandos e passa
a usar o contrato neutro na fronteira de extração sem substituir o processamento por IA.

## Contrato neutro

`NormalizedDocument` terá estes campos:

- `local_path`, `sha256` e `size_bytes` obrigatórios;
- `declared_type`: `auto`, `exam`, `answer_key` ou `other`;
- `title` obrigatório, usando o nome do arquivo quando a entrada local não informa título;
- `original_url`, `resolved_url` e `source_page_url` opcionais;
- `entry_method`: `automated_collection`, `direct_import` ou `reprocessing`;
- `metadata` com os valores conhecidos, sem chaves criadas para campos ausentes;
- `evidence` e `warnings` como listas, vazias quando não há registro;
- `external_id`, `source_id`, `source_name`, `content_type` e `acquired_at` opcionais.

O construtor de arquivo local calcula hash e tamanho, valida que o caminho aponta para um PDF
e conserva metadados fornecidos. O adaptador de aquisição copia os dados do `DocumentRecord` e
registra a página que iniciou a descoberta. O reprocessamento copia um contrato persistido e
troca apenas `entry_method` por `reprocessing`.

O contrato não importa módulos de aquisição nem tipos do aplicativo desktop.

## Componentes e dependências

### Aquisição

`collector`, `collection_transport`, `discovery`, `security`, `config` e
`desktop_collection` continuam conhecendo fonte, rede e crawl. A camada entrega
`DocumentRecord` e falhas. O adaptador converte cada download concluído em
`NormalizedDocument`; uma falha não chama a orquestração.

### Orquestração

Um módulo pequeno recebe documentos normalizados, aplica somente os limites do lote, mantém
provas e candidatos a gabarito no mesmo trabalho e chama o interpretador. A orquestração não
extrai texto, não escolhe gabarito e não aplica resposta.

O módulo oferece três entradas:

- documentos coletados e normalizados;
- arquivos ou pastas transformados em documentos de `direct_import`;
- contratos persistidos transformados em documentos de `reprocessing`.

Todos os caminhos chamam o mesmo método de submissão e o mesmo `DesktopProcessor`.

### Interpretação

`DesktopProcessor` continua responsável por páginas, identificação estrutural, separação de
questões, associação, respostas, classificação, validação e persistência. Ele recebe registros
criados a partir de `NormalizedDocument` e não importa aquisição.

O processador escolherá o caderno canônico pelo padrão estrutural de variantes `V1`, `V2`,
etc. dentro do mesmo conjunto de metadados. Ele não testará `fuvest_vestibular`. A associação
usará metadados conhecidos, ano, período, cargo, variante e tokens de título ou conteúdo. Um
único candidato presente no mesmo lote continua elegível; empates ou vários candidatos sem
evidência permanecem bloqueados.

`review_queue` aplicará a mesma regra neutra e deixará de filtrar por `source_id`.

### Persistência e compatibilidade

`DesktopStore` adicionará a coluna anulável `normalized_json` à tabela `documents` por
`ALTER TABLE` compatível. Novos registros guardarão o contrato completo. Registros antigos
continuarão legíveis com as colunas atuais. Ao reprocessar um registro antigo, o adaptador
montará apenas os campos comprovados pelas colunas existentes, marcará o método como
`reprocessing` e registrará um aviso de compatibilidade.

A mudança não altera nem apaga questões, páginas, auditoria ou decisões. O código não executa
migração no banco operacional durante testes; cada teste e smoke test usa um diretório
temporário.

## Fluxos após a mudança

```text
Fonte oficial
  -> aquisição
  -> documento local normalizado
  -> orquestração
  -> interpretação genérica
  -> revisão
  -> exportação

Arquivo, vários PDFs ou pasta
  -> documento local normalizado
  -> orquestração
  -> interpretação genérica
  -> revisão
  -> exportação

Documento armazenado
  -> contrato com método reprocessing
  -> orquestração
  -> interpretação genérica
  -> revisão
  -> exportação
```

## Falhas e retomada

- A aquisição registra falha de download e não cria trabalho de interpretação.
- A orquestração persiste o contrato antes de iniciar o processador.
- O processador registra falha e conserva contrato, arquivo e checkpoints.
- O reprocessamento usa o arquivo e o contrato persistidos. Ele não chama downloader,
  descoberta ou configuração de fonte.
- Um PDF com associação ambígua segue para exceção sem respostas aplicadas.

## Testes

Os testes usarão PDFs e textos locais. Eles cobrirão:

- igualdade do resultado estruturado entre coleta e importação do mesmo PDF;
- origem, hash, tamanho, caminho e ausência de metadados inventados;
- falha de download sem interpretação e falha de interpretação sem novo download;
- reprocessamento local e fonte fictícia sem mudança no interpretador;
- prova e gabarito separados, V1 canônica, gabarito definitivo e ambiguidade bloqueada;
- importação de um PDF, vários PDFs e pasta pelo mesmo método de submissão;
- proibição de imports de aquisição nos módulos de interpretação;
- leitura de schema SQLite anterior à coluna nova;
- regressões existentes, suíte completa, Ruff, mypy e smoke test desktop.

## Rollback

1. Encerre o Collector antes de trocar a versão do código.
2. Preserve `collector.sqlite3`, seus arquivos WAL/SHM e os PDFs locais.
3. Volte para a versão anterior do aplicativo. O SQLite ignora a coluna adicional
   `normalized_json`; nenhuma tabela ou coluna antiga muda.
4. Não remova documentos ou decisões. Se quiser limpar apenas cache de coleta, remova
   `collection-engine.sqlite3` conforme a documentação atual.

O rollback não exige restauração do banco. Uma cópia de segurança continua recomendada antes
de qualquer atualização do aplicativo.

## Limitações mantidas

- o Collector detecta OCR necessário, mas não executa OCR;
- o fluxo não adiciona suporte completo a prova e gabarito no mesmo PDF;
- parsers locais legados continuam disponíveis para o teste guiado e a regressão;
- a mudança não cria identidade semântica completa, fonte nova, agendamento ou publicação.
