# PR2 — classificação taxonômica segura em escala

## Objetivo

Reduzir as pendências da campanha editorial sem permitir que o Qwen invente uma
disciplina ou escolha um assunto apenas por ser a opção mais próxima.

## Problema encontrado

O classificador local conseguia obter a disciplina oficial pelos intervalos da
capa da prova, mas descartava esse resultado quando matéria e assunto ainda
estavam vazios. O Qwen recebia então até 86 caminhos de 31 disciplinas e podia
encaixar uma questão especializada em uma área errada.

## Correção

- a disciplina parcial obtida da estrutura oficial agora é preservada;
- em produção, o Qwen só recebe questões que já tenham disciplina determinada;
- as opções do Qwen ficam limitadas aos caminhos fechados dessa disciplina;
- toda decisão exige evidência copiada do enunciado ou das alternativas;
- confiança informada pelo modelo é limitada a 95%;
- resposta inválida, opção fora do catálogo ou evidência inventada permanece
  como pendência;
- a taxonomia 3.3.0 inclui caminhos gerais reutilizáveis que estavam ausentes,
  como probabilidade, ciclos, programação, desenvolvimento web, estruturas de
  dados, funções da moeda, responsabilidade socioambiental e varejo bancário;
- uma questão por chamada evita perda ou contaminação entre itens do lote.

## Homologação real

Fonte: Banco do Brasil / Cesgranrio, corpus oficial já coletado.

Comando:

```text
python -m kad_collector.cli editorial-campaign <config BB> --output <diretório> --limit 50
```

Modelo local: `qwen3:8b`, digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.

| Indicador | Resultado |
|---|---:|
| Questões processadas | 50 |
| Caminho completo | 42 (84%) |
| Resolvidas por regra local | 38 |
| Com participação do Qwen | 4 |
| Mantidas pendentes | 8 |
| Chamadas ao Qwen | 12 |
| Falhas de contrato/transporte | 0 |
| Tempo total | 51,2 s |

O relatório da campanha contém 1.312 ocorrências porque o inventário registra o
corpus inteiro; o limite restringiu a classificação a 50 questões. Por isso, a
cobertura global mostrada nesse relatório não deve ser usada como cobertura do
PR2. O resultado auditável desta homologação é 42 de 50 itens processados.

As oito pendências ficaram sem matéria e assunto. Nenhuma recebeu caminho fora
da disciplina oficial. As sugestões do Qwen continuam com decisão `pending` e
não foram aprovadas nem publicadas.

## Casos corrigidos pela ampliação geral da taxonomia

| Caso | Antes | Agora |
|---|---|---|
| escala de folgas por dia da semana | porcentagem/proporção | ciclos e periodicidade |
| função histórica da moeda | criptoativos e PIX | funções da moeda |
| trabalho infantil em fornecedor | compliance genérico | responsabilidade socioambiental |
| público-alvo do varejo bancário | produtos bancários | segmentação e varejo |
| Python, TypeScript e Java | engenharia de software genérica | programação |
| HTML e DOM | engenharia de software genérica | desenvolvimento web |
| árvore binária | engenharia de software genérica | algoritmos e estruturas de dados |
| covariância e sensores | inferência estatística | probabilidade |

## Limites

- este PR não transforma as 5.580 ocorrências em questões prontas;
- questões sem disciplina oficial continuam pendentes por segurança;
- a amostra não recebeu revisão humana de referência, portanto não há alegação
  de precisão estatística;
- não houve publicação no KAD ou no Supabase;
- dificuldade editorial continua fora do bloqueio de importação, conforme a
  decisão anterior do projeto.

