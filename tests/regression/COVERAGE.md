# Matriz de cobertura

| Requisito | Estado | Caso | Evidência ou lacuna |
|---|---|---|---|
| Prova e gabarito separados | `supported` | `fuvest-2026-separated-official` | PDFs oficiais distintos, com integridade e resumos estruturais fixados |
| Prova e resposta no mesmo PDF | `supported` | `same-pdf-inline-answer` | Texto sintético fictício usa a rota de resposta inline do parser |
| Tipos 1 a 4 | `supported` | `multi-grid-answer-key` | Quatro grades sintéticas selecionadas em separado |
| Preliminar e definitivo | `supported` | `preliminary-definitive-selection` | Metadados sintéticos selecionam o definitivo |
| Questão anulada | `supported` | `multi-grid-answer-key` | `*` permanece anulado e sem alternativa oficial |
| Republicação ou revisão | `planned` | `republication-identity` | Falta identificador de revisão que diferencie correção editorial de duplicata por conteúdo |
| Digitalizado e OCR | `planned` | `scanned-document-ocr` | O Collector detecta ausência de texto e envia para exceção, mas não executa OCR |
| Multicargo, turno e versão | `supported` | `multi-grid-answer-key` | Cargo, manhã ou tarde e tipo selecionam uma única grade |
| Associação ambígua bloqueada | `supported` | `ambiguous-association-blocked` | Dois candidatos sem evidência retornam `blocked` |
| Documento não relacionado | `planned` | `unrelated-document-rejection` | A importação desktop não oferece o tipo explícito `other` |

O manifesto é a fonte executável desta tabela. Uma linha muda para `supported` somente quando
recebe fixture, executor, expectativa literal e teste que falha diante da quebra do
comportamento.
