# Homologação da classificação em lote

Data: 11 de setembro de 2026  
Corpus: Polícia Federal, Banco do Brasil e CORE-PI  
Volume: 5.580 ocorrências, 5.292 questões únicas

## Resultado

| Indicador | Antes | Regras locais | Amostra com Qwen |
|---|---:|---:|---:|
| Disciplina preenchida | 0,0% | 18,7% | 0,1% da campanha* |
| Matéria preenchida | 2,5% | 21,1% | 2,6% da campanha* |
| Assunto preenchido | 15,9% | 29,6% | 16,0% da campanha* |
| Nível preenchido | 0,0% | 100,0% | 0,2% da campanha* |
| Dificuldade preenchida | 0,0% | 0,0% | 0,1% da campanha* |
| Classificação completa | 0 | 0 | 4 de 10 testadas |

\* A execução com Qwen foi limitada a dez questões; os percentuais usam as 5.580
ocorrências do inventário como denominador e, portanto, não representam uma execução completa.

As regras locais reconheceram 18 disciplinas sem chamadas ao modelo. O catálogo ainda não
oferece evidência suficiente para classificar com segurança todas as áreas especializadas e não
atribui dificuldade automaticamente.

## Teste do Qwen local

| Indicador | Resultado |
|---|---:|
| Modelo | `qwen3:8b` |
| Questões avaliadas | 10 |
| Classificações completas aceitas para revisão | 4 |
| Sugestões sem apoio no enunciado e bloqueadas | 6 |
| Falhas que interromperam o lote | 0 |
| Tempo | 128,9 s |

Os quatro casos aceitos foram classificados como híbridos: a taxonomia veio das regras locais e
o Qwen sugeriu apenas a dificuldade. A origem, a confiança e a evidência ficam registradas por
campo. Nenhuma sugestão do Qwen foi aprovada ou publicada automaticamente.

Não foi executado o corpus inteiro pelo Qwen. Extrapolar a amostra daria cerca de 20 horas de
processamento local, antes de considerar variações por documento. O cache persistente, a retomada
e o isolamento de itens defeituosos foram implementados para permitir processamento em lotes sem
refazer chamadas válidas.

## Fila editorial

| Estado | Questões |
|---|---:|
| Aptas automaticamente | 0 |
| Exceções de classificação ou associação | 5.139 |
| Quarentena estrutural | 441 |

O resultado não significa que as questões foram perdidas. Elas permanecem no inventário. A trava
principal é a taxonomia completa, sobretudo dificuldade; 182 ocorrências também têm bloqueio de
associação de gabarito, 288 são duplicadas e 115 dependem de elemento visual.

## Conclusão

O lote ficou mais seguro e operável, mas ainda não está pronto para publicação em massa. O ganho
deste PR é reduzir classificações manuais óbvias, impedir alucinações do Qwen, registrar a origem
de cada campo e tornar a fila filtrável. A próxima melhoria deve ampliar a taxonomia por áreas e
definir uma política calibrada de dificuldade; não deve liberar milhares de itens usando um valor
genérico.

Relatórios brutos sem conteúdo das questões:

- `docs/homologation/2026-09-11-taxonomy-batch-offline.md`
- `docs/homologation/2026-09-11-taxonomy-batch-qwen-smoke.md`
- `docs/homologation/2026-09-11-taxonomy-batch-approval.md`
