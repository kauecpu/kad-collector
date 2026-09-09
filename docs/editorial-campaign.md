# Campanha editorial local

Esta rotina organiza o acervo estruturado em uma fila retomável, sem publicar questões.

## Fluxo

1. O inventário confere manifestos, hashes dos documentos e totais esperados.
2. Cada prova é dividida em lotes estáveis de no máximo 100 questões.
3. Regras locais tentam preencher a taxonomia primeiro.
4. O `qwen3:8b` recebe apenas texto derivado e opções da taxonomia fechada.
5. Sugestões do Qwen continuam com decisão `pending`.
6. O revisor confere enunciado, alternativas, gabarito, origem e classificação.
7. A exportação local aceita somente itens aprovados por uma pessoa e gera um pacote `draft`.

## Retomada e auditoria

Cada alteração é salva na sessão do lote. Uma execução posterior preserva decisões humanas,
classificações já tentadas e o vínculo com prova e gabarito. O relatório inclui o hash do estado,
a contagem de duplicatas semânticas, a amostra estratificada e as distribuições por banca, órgão,
concurso, ano, cargo, matéria, assunto e estado.

Os rastros completos do modelo ficam em `data/` e não entram no Git. O relatório versionado
registra modelo, digest, quantidade de chamadas, sugestões aceitas e falhas, sem copiar respostas
brutas do Qwen ou o conteúdo integral das questões.

## Segurança

- O Qwen não cria taxonomia nem toma decisão editorial final.
- Alterar uma questão depois da aprovação invalida a decisão anterior.
- Duplicatas por ID ou identidade semântica não entram duas vezes no pacote.
- Uma falha do Ollama mantém as regras locais e a revisão disponíveis.
- Nenhuma etapa escreve no Supabase ou no KAD.
- PDFs e dados de trabalho permanecem fora do Git.

## Operação

```powershell
.venv\Scripts\python.exe -m kad_collector.cli editorial-campaign `
  config\editorial-campaign.v1.json `
  --output data\editorial-campaign\pf-bb-corepi
```

Para testar uma pausa, acrescente `--limit 100`. Para operar sem o Ollama, acrescente
`--disable-qwen`. Consulte `review/index.json`, abra o primeiro lote pendente com o comando
`review` e use a sessão indicada pelo índice. Ao terminar a revisão humana:

```powershell
.venv\Scripts\python.exe -m kad_collector.cli campaign-export `
  data\editorial-campaign\pf-bb-corepi\review\index.json `
  --output data\editorial-campaign\pf-bb-corepi\export-draft
```

Essa saída é somente uma simulação local pronta para inspeção posterior.
