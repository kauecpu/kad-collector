# Benchmark controlado da classificação canônica

Este documento trata apenas das APIs pagas Gemini, Qwen e DeepSeek. O benchmark sem custo de
API executado pelo Ollama tem preflight, checkpoints e aprovações próprios em
[`ollama-local-ai.md`](ollama-local-ai.md). Um fluxo não faz fallback para o outro.

O benchmark compara Gemini, Qwen e DeepSeek sobre a mesma amostra e o mesmo contrato de
produção. Ele avalia somente disciplina, matéria, assunto e nível. Dificuldade, explicação,
gabarito e dados de identidade não entram no payload.

## Referência e limite da amostra

A referência estrutural deixou de ser tratada como verdade semântica. Cada candidata precisa
constar no artefato de revisão como `agent_reviewed_reference`. A revisão preserva o rótulo
estrutural, o rótulo revisado, o motivo, o ID, o fingerprint bruto e a versão da taxonomia.
`ambiguous_reference`, `structural_only_reference` e `rejected_reference` não entram na
precisão. Revisão pelo agente não equivale a `human_review`.

A auditoria v2 examinou offline as 200 questões preservadas do benchmark anterior: 175 são
utilizáveis, uma é ambígua, 18 dependem apenas da estrutura e seis foram rejeitadas pela
sanitização porque perderiam conteúdo obrigatório. A taxonomia não possui um
caminho semântico suficientemente específico. O banco que continha os 314 candidatos não está
disponível neste ambiente para substituir as 25 referências. Por isso
[`canonical-ai-reference-audit.v2.json`](benchmarks/canonical-ai-reference-audit.v2.json)
mantém `readyForPreparation: false`. A preparação encerra sem reduzir os critérios enquanto não
existirem 200 referências utilizáveis.

Na mesma auditoria, 56 questões foram limpas e nenhum padrão conhecido permaneceu depois da
limpeza. Seis foram rejeitadas porque a remoção do bloco contaminado também eliminaria uma
alternativa obrigatória; elas não são enviadas ao classificador.

## Fases e aprovação

### 1. Preparação offline

Trabalhe sempre com uma cópia do SQLite operacional:

```powershell
kad-collector prepare-canonical-ai-benchmark `
  --database C:\caminho\para\copia\collector.sqlite3 `
  --reference-review docs\benchmarks\canonical-ai-reference-review.v2.json `
  --local-bundle data\benchmarks\local\canonical-ai\bundle.json `
  --manifest docs\benchmarks\canonical-ai-manifest.v2.json `
  --report docs\benchmarks\canonical-ai-preflight.v2.json
```

Esse comando não cria clientes de API e informa `networkCallsPerformed: 0`. O bundle local
contém o texto necessário para as chamadas e fica ignorado pelo Git. O manifesto versionado não
contém enunciados nem alternativas.

Antes de montar o pedido, o sanitizador remove rodapé FGV, cabeçalho repetido, página,
calendário e título da seção seguinte. O bruto e sua proveniência não mudam. O conteúdo derivado
recebe fingerprint próprio; alteração no sanitizador invalida manifesto e checkpoint.

### 2. Piloto pago

O piloto só pode ser iniciado depois de o responsável aprovar o identificador e o teto de custo
mostrados no preflight:

```powershell
kad-collector run-canonical-ai-benchmark `
  --local-bundle data\benchmarks\local\canonical-ai\bundle.json `
  --checkpoint data\benchmarks\local\canonical-ai\checkpoint.json `
  --phase pilot `
  --approved-benchmark-id canonical-ai-... `
  --max-cost-usd 0.10
```

São no máximo dez chamadas por provedor, sempre com concorrência um e sem retentativa. Cada
resposta é gravada antes da próxima chamada. Reexecutar o comando não repete combinações já
concluídas.

### 3. Lote restante

O lote final exige nova aprovação e só começa quando as trinta combinações do piloto estão no
checkpoint:

```powershell
kad-collector run-canonical-ai-benchmark `
  --local-bundle data\benchmarks\local\canonical-ai\bundle.json `
  --checkpoint data\benchmarks\local\canonical-ai\checkpoint.json `
  --phase full `
  --approved-benchmark-id canonical-ai-... `
  --max-cost-usd 0.60
```

Essa fase usa as 190 questões restantes por provedor. O executor interrompe antes de ultrapassar
o teto e também para quando a taxa de falhas de um provedor ultrapassa 5%.

## Relatórios

As respostas brutas e os checkpoints permanecem somente em `data/benchmarks/local/`. O relatório
agregado pode ser produzido sem expor texto ou respostas:

```powershell
kad-collector report-canonical-ai-benchmark `
  --local-bundle data\benchmarks\local\canonical-ai\bundle.json `
  --checkpoint data\benchmarks\local\canonical-ai\checkpoint.json `
  --report docs\benchmarks\canonical-ai-results.v2.json
```

O relatório calcula acerto por campo, acerto conjunto, intervalos de confiança de 95%, precisão
das sugestões aceitas, cobertura, revisão, falhas, respostas inválidas, campos proibidos, tokens,
custo, latência mediana e p95. A comparação usa pares da mesma questão. Nenhum vencedor é
declarado enquanto o benchmark estiver incompleto.

Casos com um único caminho compatível são resolvidos pelo código. Eles aparecem apenas em
`deterministicTrivialCases` e não aumentam a precisão dos modelos. Falhas usam códigos estáveis
(`provider_transport_failure`, `provider_http_failure`, `invalid_json`,
`invalid_response_schema`, `duplicate_field`, `invalid_level`, `unknown_taxonomy_path`,
`incompatible_taxonomy_path`, `prohibited_field` e `low_confidence`), sem inferência baseada no
texto da mensagem.

## Preços do preflight atual

O snapshot de 24 de agosto de 2026 usa preços oficiais por um milhão de tokens em dólar:

| Provedor | Modelo | Entrada | Saída | Base |
|---|---|---:|---:|---|
| Gemini | `gemini-3.7-flash` | US$ 0,75 | US$ 3,75 | preço introdutório até 31/12/2026 |
| Qwen | `qwen3.7-plus` | US$ 0,40 | US$ 1,60 | lista internacional, sem thinking, até 256K tokens |
| DeepSeek | `deepseek-v4-pro` | US$ 0,435 | US$ 0,87 | entrada sem cache e saída sem thinking |

Fontes oficiais: [Gemini](https://ai.google.dev/gemini-api/docs/pricing),
[Qwen](https://www.alibabacloud.com/help/en/model-studio/model-pricing) e
[DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/). A conversão usa PTAX venda de
R$ 5,1625, publicada pelo [Banco Central do Brasil](https://www.bcb.gov.br/) em 21 de agosto de
2026. Os preços e modelos devem ser conferidos novamente antes de qualquer fase paga.

## Segurança

- As chaves são lidas apenas das variáveis de ambiente já documentadas.
- O identificador aprovado precisa coincidir com o bundle local.
- Alteração na amostra ou na taxonomia invalida o checkpoint.
- Alteração no conteúdo sanitizado, schema, prompt ou algoritmo também invalida o checkpoint.
- Cada provedor usa o modelo exato registrado no snapshot; não há substituição ou fallback.
- O teto é verificado antes de cada chamada.
- Os testes usam provedores falsos e não acessam a internet.

O smoke v1 e seu checkpoint local permanecem como histórico. Eles são incompatíveis com o
schema v2, o prompt `canonical-taxonomy-v3` e os algoritmos v2 e não podem ser retomados.
