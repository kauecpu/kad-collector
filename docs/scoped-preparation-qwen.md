# Preparação e Qwen por escopo

Preparação canônica e classificação local com Qwen exigem um escopo explícito. Nenhuma dessas ações usa o banco inteiro por padrão.

## Formato

Para questões marcadas:

```json
{"type":"selected","questionIds":["id-1","id-2"]}
```

Para o resultado do filtro atual:

```json
{"type":"filter","filter":{"boards":["FGV"],"years":[2023]}}
```

A operação global precisa declarar a intenção:

```json
{"type":"all"}
```

O backend resolve o escopo para IDs e `updated_at` antes da prévia. O token de confirmação fica vinculado a esses registros, ao filtro normalizado e ao algoritmo; qualquer alteração ou mudança de escopo exige uma nova prévia. O token é de uso único.

## Prévia e equivalências

A prévia mostra fonte, prova, número e ID de cada registro incluído ou excluído, com o motivo. Também informa grupos canônicos, cópias equivalentes e IDs fora da seleção. Se uma regra de equivalência precisar atingir uma cópia externa, a execução fica bloqueada até o operador marcar a autorização explícita na própria prévia.

## Qwen

O limite do lote é aplicado somente às unidades elegíveis dentro do escopo. Questões de outras fontes não são usadas para completar o limite. O relatório separa unidades canônicas, cópias que herdam, regras locais, itens enviados ao Qwen, exclusões e falhas. O Qwen só pode preencher Disciplina, Matéria, Assunto e Nível; gabaritos, respostas oficiais, identidade da prova e decisões humanas permanecem fora do alcance.

## Teste local

Os testes usam banco temporário e mocks. Para reproduzir o cenário do PCI, selecione as 70 questões no workbench e use **Preparar selecionadas** ou **Classificar 70 selecionadas com Qwen**. A operação deve exibir apenas esses IDs; para trabalhar no filtro inteiro, aplique o filtro de fonte/prova e use a ação **do filtro atual**.

## Desempenho e retomada

O Qwen local usa `keep_alive` e responde com no máximo 192 tokens por padrão. Ajuste esse
limite com `KAD_OLLAMA_NUM_PREDICT` (128–512) se um benchmark local justificar a mudança.
O processamento grava um checkpoint a cada cinco questões; `KAD_QWEN_CHECKPOINT_INTERVAL`
permite escolher um intervalo de 1 a 50. A verificação de GPU ocorre no aquecimento e a
cada cinco chamadas; configure `KAD_QWEN_HARDWARE_RECHECK_INTERVAL` entre 1 e 50 para
reduzir ou aumentar a frequência. O valor `1` recupera a verificação por questão.

O status registra tempos de classificação, chamada ao Qwen e persistência, quantidade de chamadas e
verificações de hardware. A execução permanece serial para evitar competição pela VRAM.
Uma pausa confirma o trecho já processado e atualiza o cursor; se o provedor falhar, o bloco ainda
não confirmado é revertido. O último checkpoint confirmado continua sendo o ponto de retomada.
