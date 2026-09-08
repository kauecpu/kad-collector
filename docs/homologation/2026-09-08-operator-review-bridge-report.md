# Homologação da ponte entre operação simples e revisão editorial

Data: 2026-09-08

Base confirmada: `0d57da6 Simplifica operacao completa do coletor (#98)`.

## Escopo

O trabalho conectou o `review-package.json` produzido por `kad-collector run` à revisão
local e ao `export-admin` que já existiam. Não foi criada outra interface, não houve escrita
no Supabase e nenhuma questão foi aprovada ou publicada automaticamente.

Fluxo anterior:

```text
órgão/banca/período -> coleta -> estruturação -> review-package.json
```

Fluxo atual:

```text
órgão/banca/período -> coleta -> estruturação -> lotes e sessões pendentes
-> revisão humana -> export-admin -> questoes.jsonl em draft -> painel KAD
```

## Componentes reutilizados

- `local_review.py`: sessão persistente, edição, decisão humana e retomada;
- `review_server.py`: servidor restrito a `127.0.0.1` e interface local existente;
- `editorial_export.py`: validação, contrato editorial v2, exceções e evidências;
- comando `review`: abertura manual do lote;
- comando `export-admin`: geração do pacote aceito pelo painel administrativo.

O novo módulo `operator_review.py` somente adapta o pacote estruturado para esses componentes.

## Artefatos

Cada operação concluída passa a gerar, dentro de `review/`:

- `index.json`: índice determinístico de todos os lotes da execução;
- `batches/*.json`: um `QuestionBatch` pendente por prova;
- `sessions/*.json`: decisões locais retomáveis e versionadas pelo conteúdo;
- `exceptions/*.json`: rejeições estruturais e seus motivos;
- `exports/`: destino de `questoes.jsonl`, exceções, manifesto, relatório e evidências.

Os metadados do lote preservam o identificador da execução, o hash semântico do pacote,
o tipo original do documento e o tipo confirmado pela estruturação. Cada questão preserva
seu identificador estruturado, hashes da prova e do gabarito, páginas, associação, método de
extração, parser, estado original e motivos de validação.

Questões em quarentena recebem `editorial_blocks`. Esses bloqueios continuam presentes quando
outros campos são editados e impedem aprovação individual, em lote e exportação. A retirada de
um bloqueio exige uma edição explícita do próprio campo depois da conferência editorial.

## Matriz real

Os manifestos e pacotes locais homologados anteriormente foram reutilizados. Nenhum download
externo foi repetido.

| Cenário | Lotes | Questões pendentes | Aceitas na estruturação | Em quarentena | Rejeitadas | Aprovadas automaticamente | Importáveis antes da revisão | Repetição idempotente |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Polícia Federal / Cebraspe / 2021 | 4 | 480 | 447 | 33 | 0 | 0 | 0 | sim |
| Banco do Brasil / Cesgranrio / 2021–2023 | 19 | 1.312 | 1.177 | 135 | 0 | 0 | 0 | sim |
| **Total** | **23** | **1.792** | **1.624** | **168** | **0** | **0** | **0** | **sim** |

Todos os 1.792 registros ficaram com decisão editorial `pending`. “Aceita na estruturação”
significa pronta para revisão, não pronta para publicação.

Hashes dos índices reais:

- PF/Cebraspe: `96b83fc9d1c333a7d36b4acae9a50ff516caac77b4785c3062203e0ff5d32a50`;
- BB/Cesgranrio: `8147945b54ef952ab5a2f0b8bb918a53ef31377a5ab723785514321debc5258b`.

## Bloqueios editoriais reais

Nenhuma questão real foi exportada porque os pacotes estruturados ainda não possuem toda a
classificação editorial exigida pelo KAD. Isso é intencional: a ponte não inventa taxonomia nem
transforma uma questão estruturalmente válida em conteúdo publicado.

| Bloqueio | PF | Banco do Brasil |
|---|---:|---:|
| Disciplina ausente | 480 | 1.312 |
| Matéria ausente | 480 | 1.312 |
| Assunto ausente | 120 | 1.312 |
| Nível ausente | 480 | 1.312 |
| Quantidade de alternativas fora de 2–5 | 0 | 19 |
| Conteúdo visual pendente | 0 | 8 |
| Gabarito incompatível com alternativas exportáveis | 0 | 3 |

Também foram preservadas 18 questões anuladas da PF. Elas não são tratadas como resposta
ausente e continuam impedidas de exportação pelo contrato do aplicativo.

## Fluxo controlado de exportação

O teste de regressão usa três registros locais: um estruturalmente aceito, um em quarentena e
um rejeitado. Todos começam sem aprovação. Depois de uma decisão editorial explícita na cópia
de teste:

- uma questão válida foi aprovada e exportada;
- uma questão foi rejeitada com justificativa;
- a rejeição estrutural foi preservada como exceção;
- `questoes.jsonl` recebeu exatamente um registro;
- `excecoes/questoes.jsonl` recebeu exatamente dois registros;
- o registro exportado foi validado como `EditorialImportRecordV2`;
- `publicationStatus` permaneceu `draft`.

O mesmo pacote preparado novamente conserva o identificador do lote e o caminho da sessão.
Uma mudança de conteúdo cria uma sessão versionada diferente e não herda decisões antigas.
Outro teste preencheu os campos obrigatórios de duas questões, mantendo uma delas em
quarentena. A aprovação em lote aprovou somente o item sem bloqueios; a quarentena permaneceu
pendente. Depois da retirada explícita do bloqueio, o segundo item pôde ser aprovado. O estado
`deferred` também foi salvo com ator, data, hash do conteúdo e observação.

## Segurança

- o navegador é aberto somente por `--open-review` ou confirmação no modo interativo;
- falha ao abrir o navegador não encerra o servidor local;
- o servidor continua aceitando conexões somente em `127.0.0.1`;
- nenhuma credencial do Supabase é solicitada;
- nenhum código novo chama staging, Supabase ou publicação;
- todo registro exportado continua como rascunho;
- PDFs e sessões permanecem fora do Git.

## Comandos de validação

```text
python -m pytest tests/test_operator_review.py tests/test_operator_run.py tests/test_local_review.py tests/test_editorial_export.py -q
python -m pytest tests -q
ruff check src tests
mypy src
node --check src/kad_collector/review_app.js
node --check src/kad_collector/desktop_app.js
node --no-warnings --experimental-strip-types --test tests/editorial-import.test.ts
python -m build --wheel --no-isolation
git diff --check
```

## Resultados da validação

- testes focados: 46 aprovados;
- suíte completa: 941 testes e 114 subtestes aprovados;
- lint: aprovado em `src` e `tests`;
- tipos: aprovados em 79 arquivos;
- testes do importador do painel KAD: 9 aprovados;
- sintaxe dos aplicativos JavaScript: aprovada;
- wheel: construído, instalado isoladamente e importado com sucesso;
- smoke do servidor de revisão sem navegador disponível: aprovado;
- `git diff --check`: aprovado.

## Limitações restantes

- a interface local abre um lote por vez; `review/index.json` lista os 23 lotes e permite
  retomá-los sem duplicação;
- as 1.624 questões aceitas estruturalmente ainda exigem classificação editorial humana ou
  uma etapa canônica já existente antes de se tornarem importáveis;
- nenhuma importação real no painel KAD foi executada, pois o Collector deve produzir somente
  arquivos locais e o painel já valida esses arquivos como rascunho;
- mudanças no repositório KAD não foram necessárias.
