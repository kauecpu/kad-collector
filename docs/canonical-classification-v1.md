# Classificação e enriquecimento de questões canônicas

## Finalidade

`canonical-classification-v1` classifica e enriquece somente a representante de cada grupo
confirmado por `question-equivalence-v1`. Documentos e ocorrências continuam como evidência;
eles não recebem classificações editoriais independentes.

O fluxo tem uma ordem fixa:

```text
questão canônica elegível -> taxonomia determinística -> IA restrita aos campos ausentes
                           -> validação local -> aplicação ou revisão humana
```

Classificação não faz scraping. Scraping obtém o documento; extração estrutura o conteúdo do
PDF; classificação relaciona esse conteúdo à taxonomia; enriquecimento acrescenta dificuldade
e explicação. Nenhuma dessas etapas pode decidir identidade oficial ou gabarito.

## Elegibilidade

Uma questão entra no fluxo somente quando:

- o grupo está `confirmed`;
- todas as ocorrências ainda correspondem às versões atuais das questões e dos vínculos
  `semantic-association-v2`;
- a questão canônica não está bloqueada;
- a representante não foi rejeitada;
- não existe decisão humana conflitante em um campo ausente.

Grupos `incomplete`, `conflict` e `needs_review` permanecem sob responsabilidade da fila de
equivalência e não geram uma questão canônica classificável. Uma canônica que ficou
desatualizada é enviada para a fila desta etapa e não chega ao provedor.

## Taxonomia antes da IA

O classificador local existente recebe apenas enunciado, alternativas, contexto textual das
páginas já extraídas e metadados editoriais do documento. Cada valor aplicado registra:

- origem `deterministic`;
- versão da taxonomia;
- regra local que produziu o valor;
- confiança;
- evidência.

Os campos determinísticos são `discipline`, `matter`, `subject` e `level` quando existe
evidência oficial suficiente. Um valor existente não é sobrescrito. Valores com origem
`human_review` sempre têm precedência.

## Contrato restrito da IA

A IA pode sugerir somente campos que continuaram ausentes depois da taxonomia:

- `discipline`;
- `matter`;
- `subject`;
- `level`;
- `difficulty`;
- `explanation`.

O schema rejeita propriedades extras. Uma segunda lista local proíbe, entre outros, resposta,
letra correta, `answer_status`, vínculo de gabarito, intervalo, concurso, aplicação, cargo,
etapa, turno, caderno, documento, proveniência, representante, grupo e decisão humana. Uma
tentativa de alterar qualquer campo proibido rejeita a resposta inteira e abre revisão.

O provedor recebe somente:

- campos solicitados;
- enunciado e textos das alternativas;
- valores editoriais já conhecidos;
- versão e opções fechadas da taxonomia;
- aviso de que o texto da questão é dado não confiável.

Não são enviados caminhos locais, PDFs, vínculos de gabarito, hashes, respostas corretas ou
dados administrativos. Instruções presentes no enunciado e nas alternativas não alteram o
prompt, o schema ou as regras de aplicação.

O banco registra separadamente a questão canônica, hash do conteúdo, campos solicitados,
provedor, modelo, versão do prompt, horários, payload seguro, resposta estruturada, validação,
tokens e custo informado. O cliente usa `store=false`; a política de retenção do provedor ainda
deve ser considerada antes de habilitar IA em dados operacionais.

## Validação e confiança

O limiar conservador é `0,78`, alinhado ao classificador já existente. Sugestões abaixo dele não
são aplicadas. Mesmo acima do limiar, a confiança persistida para IA é limitada a `0,86` para
não equiparar sugestão automática a prova oficial ou decisão humana.

Antes de aplicar, o coletor exige:

- nome existente na taxonomia;
- caminho válido `discipline -> matter -> subject`;
- valor de nível ou dificuldade dentro do contrato editorial;
- explicação com lógica mínima e sem referência normativa ausente do conteúdo fornecido;
- campo solicitado e ainda ausente;
- ausência de propriedade extra ou proibida.

Baixa confiança, schema inválido, conflito taxonômico, explicação sem suporte e falha permanente
do provedor entram na fila. A fila expõe questão, campos pendentes, sugestão, confiança,
evidência e motivo. O revisor pode `accept`, `correct` ou `reject`; ator, data, justificativa e
valor decidido ficam auditados.

Os estados são:

- `complete`: todos os campos editoriais existem, sem revisão pendente;
- `incomplete`: ainda há campo ausente;
- `needs_review`: existe item pendente ou bloqueio;
- `rejected`: o revisor rejeitou a sugestão;
- `approved`: classificação completa e questão editorial já aprovada/exportada.

## Explicação e dificuldade

`difficulty` e `explanation` são enriquecimento editorial. Eles não alteram fingerprints de
equivalência, não confirmam grupos, não resolvem gabaritos ou identidade e não tornam uma
questão publicável sem os demais requisitos. Alterá-los atualiza apenas a versão editorial da
canônica.

Uma mudança no enunciado ou nas alternativas invalida resultados derivados ativos e itens de
revisão pendentes. O grupo de equivalência também é bloqueado até revalidação. Registros
anteriores permanecem no histórico com estado `invalidated` ou `obsolete`.

## Persistência e auditoria

O esquema aditivo usa foreign keys para `canonical_questions`:

- `canonical_classification_runs`: configuração, cursor e relatório da execução;
- `canonical_classification_run_items`: antes/depois por canônica processada;
- `canonical_classification_field_results`: valor, origem, confiança e evidência por campo;
- `canonical_ai_requests`: requisição segura, resposta, validação e uso do provedor;
- `canonical_classification_review_queue`: fila e decisão humana;
- `canonical_classification_states`: estado editorial corrente;
- `canonical_classification_events`: histórico append-only protegido por triggers.

IDs derivados incluem questão, hash do conteúdo, taxonomia, modelo e prompt. Repetir a mesma
entrada não cria outro resultado ativo. `run-id` e `limit` permitem retomar lotes sem refazer os
itens concluídos.

## CLI

Simulação determinística, sem persistir resultados, filas ou eventos:

```powershell
kad-collector classify-canonical-questions `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22 `
  --limit 250 `
  --report data/reports/canonical-classification-dry-run.json
```

Aplicação sem IA:

```powershell
kad-collector classify-canonical-questions `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22 `
  --apply `
  --run-id classification-rfb22-2026-08 `
  --limit 250 `
  --report data/reports/canonical-classification-rfb22.json
```

Aplicação com o provedor permitido:

```powershell
kad-collector classify-canonical-questions `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22 `
  --apply `
  --enable-ai `
  --provider openai `
  --model gpt-5.6-terra `
  --run-id classification-rfb22-2026-08 `
  --limit 100 `
  --report data/reports/canonical-classification-rfb22.json
```

Repita a mesma configuração e o mesmo `run-id` até `remaining` chegar a zero. O relatório inclui
elegíveis, campos determinísticos, completas, candidatas e chamadas de IA, campos solicitados,
sugestões aceitas/rejeitadas, baixa confiança, revisão, falhas, tokens, custo, restantes e
contagens por concurso selecionado, cargo, turno e disciplina.

Operação da fila:

```powershell
kad-collector list-canonical-classification-review `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22

kad-collector review-canonical-classification `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --item <id> `
  --decision correct `
  --actor <revisor> `
  --value "Média" `
  --evidence "Justificativa editorial"
```

## Limitações da regressão

Os testes usam um catálogo e questões sintéticas genéricas, sem rede e com provedor falso. Eles
validam o fluxo, não a qualidade classificatória dos PDFs reais. Os PDFs oficiais do RFB22 não
estão no Git; a meta aproximada de 280 canônicas não prova cobertura taxonômica nem qualidade de
explicações. Custos também dependem do modelo e do volume real, por isso o relatório mede uso e
o operador deve começar com `--limit` baixo.
