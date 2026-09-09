# Diagnóstico da taxonomia editorial — 2026-09-09

## Escopo

O diagnóstico reutiliza o acervo local estruturado e seus hashes; não baixa novamente os PDFs
e não publica nada no KAD ou no Supabase. O corpus contém Polícia Federal/Cebraspe (2018, 2021
e 2025), Banco do Brasil/Cesgranrio (2021) e CORE-PI/Quadrix (2026).

Fontes oficiais usadas para limitar a taxonomia:

- [Polícia Federal 2018](https://cdn.cebraspe.org.br/concursos/pf_18/arquivos/ED_1_DPF_2018___ABT.PDF)
- [Polícia Federal 2021](https://cdn.cebraspe.org.br/concursos/pf_21/arquivos/ED_1_DPF_2021_ABT.PDF)
- [Polícia Federal 2025](https://cdn.cebraspe.org.br/concursos/PF_25/arquivos/Ed_1_PF_25_Abertura.html)
- [Banco do Brasil 2021](https://inscricao.cesgranrio.com.br/storage.ashx?file=pdf%2Fbb0121%2Fprovas%2FPROVA+A+-+ESCRITUR%C3%81RIO+-+AGENTE+COMERCIAL.pdf)
- [CORE-PI 2026](https://quadrix.org.br/informacoes/1018/)

## Resultado determinístico

| Banca | Ocorrências | Taxonomia completa | Cobertura | Pendentes |
|---|---:|---:|---:|---:|
| Cebraspe | 4.108 | 658 | 16,0% | 3.450 |
| Cesgranrio | 1.312 | 271 | 20,7% | 1.041 |
| Quadrix | 160 | 99 | 61,9% | 61 |
| **Total** | **5.580** | **1.028** | **18,4%** | **4.552** |

O resultado é melhor que o catálogo anterior, que era centrado em três concursos da FGV, mas
fica muito abaixo da meta de 90%. O principal bloqueio é a ausência de mapas oficiais por bloco
e cargo para as provas da Polícia Federal. Keywords genéricas não são evidência suficiente para
forçar matéria e assunto em milhares de itens.

| Campo | Cobertura |
|---|---:|
| Disciplina | 18,4% |
| Matéria | 20,9% |
| Assunto | 29,4% |
| Nível | 100,0% |
| Dificuldade | 0,0% |

## Amostra de auditoria

| Indicador | Resultado |
|---|---:|
| Questões | 300 |
| Duplicatas semânticas | 0 |
| Estruturalmente aceitas | 276 |
| Em quarentena estrutural | 24 |
| Dependentes de elemento visual | 6 |
| Decisões humanas | 0 |

A amostra é uma fila de trabalho, não uma alegação de precisão. Sem revisão humana não há base
para declarar precisão da estrutura, do gabarito ou da classificação.

## Qwen local

O `qwen3:8b` foi encontrado no Ollama e recebeu uma execução controlada. O primeiro contrato,
com lotes de 40, teve respostas truncadas e sugestões semanticamente fracas. O contrato foi
endurecido para lotes de cinco, uma resposta por ID, IDs fechados e rejeição integral de lotes
com IDs ausentes ou duplicados. Respostas inválidas ficam na telemetria e não avançam para
aprovação ou publicação.

No smoke final, o modelo respondeu aos cinco itens com IDs repetidos e ausentes. O contrato
recusou o lote inteiro: uma chamada, cinco itens enviados, zero sugestões aplicadas e uma falha
registrada. O resultado comprova o isolamento da falha, não qualidade classificatória do modelo.

## Conclusão

O fluxo de revisão, a proveniência e os bloqueios estão prontos para uma auditoria humana, mas o
acervo ainda não está pronto para importação em massa. Para atingir 90%, é necessário ampliar os
mapas oficiais por prova/bloco e revisar uma amostra representativa antes de confiar no fallback
semântico. O Qwen continua apenas como sugestão auditável; nunca como aprovador.
