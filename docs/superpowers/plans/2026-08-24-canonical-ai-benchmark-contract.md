# Canonical AI Benchmark Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir o conteúdo, o contrato de resposta, as referências e as métricas do benchmark canônico sem executar inferência.

**Architecture:** Um módulo puro sanitiza o conteúdo derivado antes de montar o pedido. A classificação canônica oferece caminhos identificados e valida uma decisão única. Um registro editorial seguro governa as referências aceitas, enquanto os dois executores de benchmark compartilham códigos de erro e excluem casos determinísticos da precisão.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLite, `unittest`, Ruff e mypy.

**Spec:** `docs/superpowers/specs/2026-08-24-canonical-ai-benchmark-contract-design.md`

## Global Constraints

- Não iniciar Ollama nem chamar Gemini, Qwen, DeepSeek ou outro modelo.
- Não ler chaves locais nem carregar arquivos `.env`.
- Preservar dados brutos, hashes, proveniência e checkpoints anteriores.
- Não alterar scraping, gabaritos, identidade, equivalência ou exportação.
- Manter enunciados, alternativas e caminhos locais fora dos artefatos versionados.
- Implementar cada comportamento com teste vermelho antes do código de produção.

---

### Task 1: Sanitização do conteúdo derivado

**Files:**
- Create: `src/kad_collector/canonical_ai_input.py`
- Create: `tests/test_canonical_ai_input.py`

**Interfaces:**
- Produces: `SanitizedAIContent`, `CanonicalAIInputError`, `sanitize_canonical_ai_content()` e `find_canonical_ai_artifacts()`.
- Consumes: strings brutas e títulos oficiais fornecidos pelo chamador.

- [ ] **Step 1: Escrever testes para rodapé, página, calendário e título vazado**

Crie fixtures literais que mantenham a alternativa válida e anexem um artefato por caso. Confirme
que o resultado preserva os argumentos de entrada, remove somente o sufixo e retorna códigos
distintos em `removed_artifacts`.

- [ ] **Step 2: Executar os testes e confirmar falha por módulo ausente**

Run: `python -m unittest tests.test_canonical_ai_input -v`

- [ ] **Step 3: Implementar o sanitizador puro**

Use uma dataclass congelada:

```python
@dataclass(frozen=True)
class SanitizedAIContent:
    statement: str
    alternatives: tuple[str, ...]
    prompt_content_fingerprint: str
    removed_artifacts: tuple[str, ...]
```

`sanitize_canonical_ai_content()` deve cortar blocos de rodapé a partir do primeiro marcador
conhecido, remover título oficial apenas quando ele aparecer como linha adicional no fim da última
alternativa e calcular a impressão digital com `stable_sha256`.

- [ ] **Step 4: Testar resíduos e ausência de mutação**

`find_canonical_ai_artifacts()` deve retornar códigos sem incluir o texto encontrado.

- [ ] **Step 5: Executar teste focado e Ruff**

Run: `python -m unittest tests.test_canonical_ai_input -v`

Run: `ruff check src/kad_collector/canonical_ai_input.py tests/test_canonical_ai_input.py`

### Task 2: Opções taxonômicas identificadas

**Files:**
- Modify: `src/kad_collector/editorial_taxonomy.py`
- Modify: `src/kad_collector/canonical_classification.py`
- Modify: `tests/test_editorial_foundation.py`
- Modify: `tests/test_canonical_classification.py`

**Interfaces:**
- Produces: `EditorialTaxonomy.keywords_for_path(path)`, `canonical_taxonomy_path_id(path)` e `canonical_taxonomy_options(taxonomy, catalog_ids, known_fields)`.
- Consumes: `TaxonomyPath`, catálogo relevante e campos editoriais conhecidos.

- [ ] **Step 1: Testar palavras-chave e estabilidade do identificador**

O teste deve confirmar a lista literal do caminho usado na fixture e o mesmo identificador para o
mesmo catálogo e rótulos, sem depender da ordem de carregamento.

- [ ] **Step 2: Executar os testes e observar falha pelas APIs ausentes**

Run: `python -m unittest tests.test_editorial_foundation tests.test_canonical_classification -v`

- [ ] **Step 3: Implementar consulta de palavras-chave e construtor comum de opções**

Cada opção deve conter `pathId`, `discipline`, `matter`, `subject` e `keywords`. Filtre caminhos
incompatíveis com disciplina, matéria ou assunto conhecidos antes de gerar a tupla.

- [ ] **Step 4: Substituir os construtores duplicados por `canonical_taxonomy_options()`**

Mantenha um único formato para produção, benchmark pago e benchmark Ollama.

- [ ] **Step 5: Executar testes focados e verificações estáticas**

Run: `python -m unittest tests.test_editorial_foundation tests.test_canonical_classification -v`

Run: `ruff check src/kad_collector/editorial_taxonomy.py src/kad_collector/canonical_classification.py tests/test_editorial_foundation.py tests/test_canonical_classification.py`

### Task 3: Resposta única e códigos explícitos

**Files:**
- Modify: `src/kad_collector/canonical_classification.py`
- Modify: `src/kad_collector/canonical_ai_providers.py`
- Modify: `src/kad_collector/ollama_ai_provider.py`
- Modify: `tests/test_canonical_classification.py`
- Modify: `tests/test_canonical_ai_providers.py`
- Modify: `tests/test_ollama_ai_provider.py`

**Interfaces:**
- Produces: `CanonicalAITaxonomyDecision`, `CanonicalAILevelDecision`, `CanonicalAIResponse`, `CanonicalAIValidationError.code` e `canonical_ai_error_code(exc)`.
- Consumes: `CanonicalAIRequest` com opções identificadas e os campos solicitados.

- [ ] **Step 1: Escrever testes para decisão de caminho, nível e propriedades extras**

Cubra caminho válido, desconhecido, incompatível, nível inválido, tentativa de campo proibido e a
impossibilidade estrutural de repetir uma decisão.

- [ ] **Step 2: Executar testes e confirmar falhas do contrato antigo**

Run: `python -m unittest tests.test_canonical_classification tests.test_canonical_ai_providers tests.test_ollama_ai_provider -v`

- [ ] **Step 3: Implementar modelos e schema dinâmico**

O schema deve expor `taxonomy` somente quando algum dos três campos taxonômicos for solicitado e
`level` somente quando nível for solicitado. `pathId` usa enum dos caminhos oferecidos; `value` do
nível usa os três valores editoriais.

- [ ] **Step 4: Implementar validação e extração dos campos solicitados**

Uma decisão aceita gera resultados por campo com a mesma confiança e evidência. Baixa confiança
continua na fila de revisão. Campos conhecidos devem coincidir com o caminho escolhido.

- [ ] **Step 5: Mapear falhas por tipo, sem examinar mensagens**

Provedores devem levantar erros tipados para transporte, HTTP, JSON e schema. O código seguro deve
ser gravável no checkpoint sem conteúdo bruto.

- [ ] **Step 6: Atualizar os quatro adaptadores e executar testes focados**

Run: `python -m unittest tests.test_canonical_classification tests.test_canonical_ai_providers tests.test_ollama_ai_provider -v`

### Task 4: Resolução determinística de caminho único

**Files:**
- Modify: `src/kad_collector/canonical_classification.py`
- Modify: `tests/test_canonical_classification.py`

**Interfaces:**
- Consumes: opções retornadas por `canonical_taxonomy_options()`.
- Produces: campos taxonômicos derivados com fonte determinística e zero chamadas ao provedor.

- [ ] **Step 1: Escrever teste que oferece um único caminho e ativa IA**

O teste deve confirmar os campos preenchidos, `provider.calls == []`, ausência de pedido de IA e
contagem em `deterministic_classified`.

- [ ] **Step 2: Executar o teste e confirmar que o provedor recebe a questão**

Run: `python -m unittest tests.test_canonical_classification.CanonicalClassificationTests.test_single_compatible_path_is_deterministic -v`

- [ ] **Step 3: Aplicar os campos ausentes antes de calcular candidatos à IA**

Não sobrescreva valor conhecido e não use essa regra quando não houver caminho ou houver mais de
um.

- [ ] **Step 4: Executar a suíte de classificação canônica**

Run: `python -m unittest tests.test_canonical_classification -v`

### Task 5: Referências auditadas e amostra não trivial

**Files:**
- Create: `src/kad_collector/canonical_ai_reference_review.py`
- Modify: `src/kad_collector/canonical_ai_benchmark.py`
- Modify: `tests/test_canonical_ai_benchmark.py`
- Create: `docs/benchmarks/canonical-ai-reference-review.v2.json`

**Interfaces:**
- Produces: `ReferenceReviewDecision`, `load_reference_review()` e seleção que aceita somente `agent_reviewed_reference`.
- Consumes: referência estrutural, versão da taxonomia e registro seguro de revisão.

- [ ] **Step 1: Testar estados, correção auditada e rejeição de referência ambígua**

O loader deve rejeitar estado desconhecido, `human_review`, rótulo fora da taxonomia, duplicidade e
versão incompatível.

- [ ] **Step 2: Executar teste e confirmar falhas das APIs ausentes**

Run: `python -m unittest tests.test_canonical_ai_benchmark -v`

- [ ] **Step 3: Implementar o registro seguro e integrar à preparação**

O manifesto deve preservar `structuralExpected`, `reviewedExpected`, `referenceStatus` e
`reasonCode`. Somente a decisão revisada alimenta `expected` e a precisão.

- [ ] **Step 4: Impedir máscaras taxonômicas triviais**

`assign_benchmark_masks()` deve escolher apenas combinações com mais de um caminho compatível
quando a máscara solicitar disciplina, matéria ou assunto. Casos recusados devem aparecer em
`deterministicTrivialCases`, fora da métrica principal.

- [ ] **Step 5: Auditar as referências locais sem provedores**

Revise o bundle local em lotes. Registre somente IDs, rótulos, estado e motivo. Não registre texto.
Marque a origem como `agent_reviewed_reference`, sem usar `human_review`.

- [ ] **Step 6: Executar preparação offline ou registrar insuficiência**

A preparação deve produzir 200 itens revisados ou falhar com a quantidade disponível. Confirme
`networkCallsPerformed == 0`.

### Task 6: Benchmark Ollama, métricas e documentação

**Files:**
- Modify: `src/kad_collector/ollama_ai_benchmark.py`
- Modify: `tests/test_ollama_ai_benchmark.py`
- Modify: `docs/canonical-classification-v1.md`
- Modify: `docs/canonical-ai-benchmark.md`
- Modify: `docs/canonical-ai-providers.md`
- Modify: `docs/ollama-local-ai.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: novo manifesto, novo contrato e `canonical_ai_error_code()`.
- Produces: checkpoint com `validationCode` e relatório agregado com `deterministicTrivialCases`.

- [ ] **Step 1: Escrever testes de categoria e métrica principal**

Confirme que nível inválido não vira falha de provedor, baixa confiança mantém categoria própria,
casos triviais não aumentam precisão e relatórios não contêm conteúdo bruto.

- [ ] **Step 2: Executar testes e confirmar falhas do relatório antigo**

Run: `python -m unittest tests.test_ollama_ai_benchmark -v`

- [ ] **Step 3: Migrar checkpoint, resumo e validação de versão**

Incremente schema, algoritmo e prompt. Checkpoints v1 devem falhar antes de qualquer chamada.

- [ ] **Step 4: Atualizar documentação e comandos offline**

Documente a revisão do agente, a separação dos casos triviais e a necessidade de nova autorização
para qualquer smoke.

- [ ] **Step 5: Executar testes focados**

Run: `python -m unittest tests.test_canonical_ai_benchmark tests.test_ollama_ai_benchmark -v`

### Task 7: Verificação e Pull Request

**Files:**
- Modify: somente arquivos já listados, conforme correções de verificação.

**Interfaces:**
- Produces: branch verificada e Pull Request aberto para `main`.

- [ ] **Step 1: Executar a suíte completa**

Run: `python -m unittest discover -s tests`

- [ ] **Step 2: Executar verificações estáticas e de compilação**

Run: `ruff check .`

Run: `mypy src`

Run: `python -m compileall -q src tests`

- [ ] **Step 3: Verificar diff, segredos e ausência de artefatos brutos**

Run: `git diff --check origin/main...HEAD`

Run: `git grep -n -I -E "(AIza|sk-[A-Za-z0-9_-]{16,}|service_role|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY)" -- . ":(exclude)tests"`

Confirme que `data/benchmarks/local/` permanece ignorado e que nenhum processo Ollama foi iniciado.

- [ ] **Step 4: Revisar o diff contra a especificação**

Liste cada requisito e o teste ou artefato que o comprova. Corrija lacunas antes do commit final.

- [ ] **Step 5: Fazer commit, push e abrir PR**

Título: `fix: corrigir contrato e amostra do benchmark local de IA`

Não faça merge.
