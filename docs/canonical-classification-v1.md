# Classificação e enriquecimento de questões canônicas

## Finalidade

`canonical-classification-v2` classifica somente a representante de cada grupo
confirmado por `question-equivalence-v1`. Documentos e ocorrências continuam como evidência;
eles não recebem classificações editoriais independentes.

O fluxo tem uma ordem fixa:

```text
questão canônica elegível -> taxonomia determinística -> IA restrita aos campos ausentes
                           -> validação local -> aplicação ou revisão humana
```

Classificação não faz scraping. Scraping obtém o documento; extração estrutura o conteúdo do
PDF; classificação relaciona esse conteúdo à taxonomia. Dificuldade e explicação são dados
editoriais opcionais fora deste fluxo. Nenhuma dessas etapas pode decidir identidade oficial
ou gabarito.

## Elegibilidade

Uma questão entra no fluxo somente quando:

- o grupo está `confirmed`;
- todas as ocorrências ainda correspondem às versões atuais das questões e dos vínculos
  `semantic-association-v3`;
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

Antes de qualquer chamada, o texto derivado passa por limpeza determinística de rodapés,
cabeçalhos, páginas, calendários e títulos de seção anexados. O conteúdo bruto, seu hash e a
proveniência são preservados. Se restar um artefato conhecido, a chamada é bloqueada.

## Contrato restrito da IA

A IA pode decidir somente o caminho taxonômico ou o nível que continuaram ausentes depois da
taxonomia:

- `discipline`;
- `matter`;
- `subject`;
- `level`;

Disciplina, matéria e assunto não são valores livres. O pedido contém caminhos compatíveis, cada
um com ID estável, os três rótulos e as palavras-chave editoriais. A resposta escolhe no máximo
um `pathId`; o coletor extrai dele apenas os campos solicitados. Quando existe um único caminho,
o código o aplica sem chamar IA. Para `level`, o schema aceita somente `Fundamental`, `Médio` ou
`Superior`, depois de edital e cargo falharem em resolver o campo.

O schema rejeita propriedades extras e chaves JSON repetidas. Uma segunda lista local proíbe,
entre outros, resposta,
letra correta, `answer_status`, vínculo de gabarito, intervalo, concurso, aplicação, cargo,
etapa, turno, caderno, documento, proveniência, representante, grupo e decisão humana. Uma
tentativa de alterar qualquer campo proibido rejeita a resposta inteira e abre revisão.

O provedor recebe somente:

- campos solicitados;
- enunciado e textos das alternativas;
- valores editoriais já conhecidos;
- versão e opções fechadas da taxonomia;
- opções fechadas de nível, quando necessário;
- aviso de que o texto da questão é dado não confiável.

Não são enviados caminhos locais, PDFs, vínculos de gabarito, hashes, respostas corretas ou
dados administrativos. Instruções presentes no enunciado e nas alternativas não alteram o
prompt, o schema ou as regras de aplicação.

O banco registra separadamente a questão canônica, hash do conteúdo, campos solicitados,
provedor, modelo, versão do prompt, horários, payload seguro, resposta estruturada, validação,
tokens e custo informado. As políticas de retenção e região de cada provedor devem ser
consideradas antes de habilitar IA em dados operacionais.

## Validação e confiança

O limiar conservador é `0,78`, alinhado ao classificador já existente. Sugestões abaixo dele não
são aplicadas. Mesmo acima do limiar, a confiança persistida para IA é limitada a `0,86` para
não equiparar sugestão automática a prova oficial ou decisão humana.

Antes de aplicar, o coletor exige:

- nome existente na taxonomia;
- caminho válido `discipline -> matter -> subject`;
- valor de nível dentro do contrato editorial;
- campo solicitado e ainda ausente;
- ausência de propriedade extra ou proibida.

Baixa confiança, schema inválido, caminho desconhecido ou incompatível e falha permanente do provedor entram na
fila. Indisponibilidade temporária do Ollama pausa a execução e não cria revisão. A fila expõe
questão, campos pendentes, sugestão, confiança,
evidência e motivo. O revisor pode `accept`, `correct` ou `reject`; ator, data, justificativa e
valor decidido ficam auditados.

Os estados são:

- `complete`: disciplina, matéria, assunto e nível existem, sem revisão taxonômica pendente;
- `incomplete`: ainda falta um desses quatro campos;
- `needs_review`: existe item pendente ou bloqueio;
- `rejected`: o revisor rejeitou a sugestão;
- `approved`: classificação completa e questão editorial já aprovada/exportada.

## Explicação e dificuldade

`difficulty` e `explanation` são campos editoriais opcionais. Eles não alteram fingerprints de
equivalência, não confirmam grupos e não resolvem gabaritos ou identidade. A ausência deles não
aciona IA, não aparece em `requestedFields`, não aumenta `aiCandidates`, não marca a
classificação como incompleta e não envia a questão para revisão. Valores existentes e decisões
humanas continuam preservados no histórico.

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

Em `apply`, cada questão concluída é confirmada separadamente. Se um provedor local ficar
indisponível, a questão atual é desfeita, a execução passa a `paused` e nenhuma revisão é criada
por causa da indisponibilidade. `Ctrl+C` também preserva o último cursor confirmado. O modo
`dry-run` continua revertendo toda a simulação.

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

Aplicação com um provedor configurado:

```powershell
kad-collector classify-canonical-questions `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22 `
  --apply `
  --enable-ai `
  --provider gemini `
  --run-id classification-rfb22-2026-08 `
  --limit 100 `
  --report data/reports/canonical-classification-rfb22.json
```

Os valores aceitos em `--provider` são `gemini`, `qwen`, `deepseek` e `ollama`. O provedor é
obrigatório quando `--enable-ai` é usado e não existe fallback automático. Seus modelos
padrão, variáveis de ambiente, endpoints e parâmetros de raciocínio estão documentados em
`docs/canonical-ai-providers.md`. Todos permanecem inativos sem `--enable-ai`.

Repita a mesma configuração e o mesmo `run-id` até `remaining` chegar a zero. O relatório inclui
elegíveis, campos determinísticos, completas, candidatas e chamadas de IA, campos solicitados,
sugestões aceitas/rejeitadas, baixa confiança, revisão, falhas, tokens, custo, restantes e
contagens por concurso selecionado, cargo, turno e disciplina.

O benchmark pago de Gemini, Qwen e DeepSeek continua separado. O benchmark local do Ollama usa
as 175 referências válidas da auditoria v2 e está documentado em `docs/ollama-local-ai.md`;
preparar o código não
autoriza download ou inferência.

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
