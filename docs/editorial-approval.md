# Aprovação editorial por amostragem

Este fluxo evita a revisão manual de todo o acervo. Ele separa a operação em três filas:

- questões que passaram por todas as verificações automáticas;
- uma amostra humana estratificada;
- exceções e quarentena.

Passar nas verificações automáticas não publica uma questão. A auditoria aprova um grupo para um
pacote local com estado `draft`. Nenhum comando desta etapa escreve no KAD ou no Supabase.

## 1. Avaliar o acervo

Use o índice consolidado produzido pela campanha editorial:

```powershell
kad-collector approval-campaign `
  data/editorial-campaign/<campanha>/review/index.json `
  --output data/editorial-approval/<campanha>/state.json `
  --report-json data/editorial-approval/<campanha>/report.json `
  --report-markdown data/editorial-approval/<campanha>/report.md
```

O padrão seleciona 2% das questões elegíveis, com mínimo de 50 e máximo de 300. Os limites podem
ser alterados por argumento. Os hosts autorizados são lidos das fontes oficiais cadastradas.

## 2. Auditar a amostra

```powershell
kad-collector approval-review `
  data/editorial-approval/<campanha>/state.json `
  --staging-output data/editorial-approval/<campanha>/staging `
  --open-browser
```

A tela mostra:

- totais por estado e motivos das exceções;
- grupos e precisão observada;
- prova e gabarito oficiais;
- enunciado, alternativas, resposta e taxonomia do item amostrado;
- pontuação, regra e evidência de cada dimensão;
- edição de uma exceção, com reavaliação automática.

Uma reprovação sem erro crítico entra nas métricas de precisão. Um erro crítico bloqueia o grupo
imediatamente. A aprovação de um grupo não possui botão de contorno: ela apenas confirma que a
amostra completa atingiu os limites definidos.

## 3. Corrigir uma regra e reprocessar

Depois de corrigir o coletor, invalide a amostra do grupo afetado:

```powershell
kad-collector approval-reprocess data/editorial-approval/<campanha>/state.json <group-id>
```

O conteúdo do grupo é reavaliado e a auditoria recomeça. IDs e fingerprints permanecem estáveis.
Decisões humanas são preservadas em execuções repetidas enquanto o conteúdo da questão não muda.

## 4. Gerar o pacote local de staging

```powershell
kad-collector approval-export `
  data/editorial-approval/<campanha>/state.json `
  --output data/editorial-approval/<campanha>/staging
```

O comando falha se não houver grupo aprovado. O pacote contém `questoes.jsonl`, `linhagem.jsonl`,
`excecoes.jsonl` e `manifesto.json`. Cada questão passa pelo esquema real de importação do KAD.
Duplicatas são excluídas do pacote. A saída continua local e em estado `draft`.

## Critérios que não podem ser compensados

Origem insegura, PDF inválido, estrutura inválida, associação ambígua do gabarito e dependência
visual indispensável bloqueiam a aprovação. Uma classificação sugerida apenas pelo Qwen segue para
revisão, mesmo que a confiança informada seja alta. Se o Ollama estiver indisponível, a avaliação
determinística continua funcionando.
