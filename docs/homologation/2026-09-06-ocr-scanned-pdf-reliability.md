# Homologação de OCR em PDFs integralmente digitalizados

Data: 2026-09-06

Branch: `codex/ocr-scanned-pdf-reliability`

Base confirmada: `96515e6 Homologa coleta em fontes oficiais reais (#92)`

## Resultado executivo

O OCR recuperou as 41 páginas dos três cadernos oficiais digitalizados em duas
execuções consecutivas. O resultado funcional foi idêntico nas duas execuções:
mesmos textos medidos, decisões de qualidade, estratégias e marcadores de questão.
A diferença ficou somente no tempo de execução.

| Indicador | Resultado |
| --- | ---: |
| PDFs oficiais integralmente digitalizados | 3 |
| Páginas sem camada de texto | 41/41 |
| Páginas recuperadas e aprovadas pela qualidade | 41/41 (100%) |
| Páginas ilegíveis aceitas na amostra real | 0 |
| Qualidade média calculada | 93,28% |
| Menor qualidade calculada | 85,38% |
| Questões identificadas por marcadores estritos | 273/288 (94,79%) |
| Primeira execução | 206,051 s |
| Segunda execução | 210,272 s |
| Resultado funcional idêntico na repetição | Sim |

O critério de aprovação de recuperação mínima de 90% foi atendido. A cobertura de
marcadores é um indicador auxiliar: um marcador não encontrado não significa que a
página falhou no OCR, mas mostra que ainda não há prova de extração estrutural
perfeita de todas as questões.

## Corpus oficial

Os arquivos foram baixados apenas para a pasta local ignorada
`data/homologation/ocr-official`. Os PDFs e o texto extraído não foram adicionados ao
Git. O manifesto versionado contém apenas origem, identidade e expectativas.

| Documento | Página oficial | Páginas | Camada de texto | Questões esperadas | Marcadores encontrados | SHA-256 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| FUVEST 1984, 1ª fase | https://www.fuvest.br/acervo-vestibular-1984/ | 13 | 0 | 96 | 88 (91,67%) | `5337cb40c5458b55379b45d66b7eb180aca1b4f25b737d9921df478647141092` |
| FUVEST 1985, 1ª fase | https://www.fuvest.br/acervo-vestibular-1985/ | 13 | 0 | 96 | 92 (95,83%) | `528f74f3f22420468e16e4239c643442d9151caefd9febbf2640f6543622baea` |
| FUVEST 1987, 1ª fase | https://www.fuvest.br/acervo-vestibular-1987/ | 15 | 0 | 96 | 93 (96,88%) | `dcc95f397d4cba853e8a98ff56f5082275f9be123e19b7792544aec788a146a3` |

Os três acervos oficiais também disponibilizam seus gabaritos. O objetivo desta
rodada foi medir a legibilidade dos cadernos, não alterar a associação entre prova e
gabarito.

## Diagnóstico inicial

Antes da correção, o resultado de uma página era considerado aproveitável quando
possuía pelo menos 20 caracteres e não tinha erro de execução. As 41 páginas reais
passavam nesse teste, com 102.635 caracteres ao todo e duração de 217,858 s, mas o
teste não distinguia conteúdo legível de uma cadeia longa de símbolos repetidos.

Um caso de regressão reproduziu o defeito: uma saída longa e corrompida era aceita
pela regra antiga. A regra nova a rejeita explicitamente por repetição dominante.
Por isso, os dois resultados de 100% não são equivalentes: o baseline só media
quantidade; o resultado final exige evidência de texto reconhecível e registra a
qualidade da decisão.

## Correções

| Problema | Causa | Correção |
| --- | --- | --- |
| Texto corrompido podia ser aceito | Validação apenas por tamanho | Pontuação por caracteres válidos, palavras, confiança, linhas, marcadores e repetição |
| PDF com camada textual ruim não acionava OCR | Decisão baseada só em tamanho bruto | Validação da camada de texto antes de dispensar OCR |
| Imagem fraca ou inclinada tinha uma tentativa única | Ausência de pré-processamento e retentativas | Autocontraste, redução de ruído, correção de inclinação, rotações e limiarização, com limite de tentativas |
| Página muito grande podia consumir memória sem limite explícito | Escala fixa de renderização | Limite de 12 megapixels por página e redução proporcional da escala |
| Uma página problemática podia ocultar o diagnóstico | Telemetria insuficiente | Resultado por página com estratégia, tentativas, rotação, escala, duração e qualidade |
| Falha de uma página ameaçava o lote | Exceções na fronteira do motor/renderizador | Isolamento por página, preservando ordem e cancelamento |

O caminho de OCR continua totalmente local. O coletor usa RapidOCR com ONNX Runtime
e PDFium; Tesseract e Poppler não são dependências de execução.

## Ambiente

| Componente | Estado durante a homologação |
| --- | --- |
| Python | 3.11 |
| RapidOCR | 3.9.2 |
| ONNX Runtime | 1.29.0 |
| PDFium (`pypdfium2`) | 5.13.0 |
| Pillow | 12.3.0 |
| Tesseract | Indisponível; fluxo continuou pelo motor adotado |
| Poppler | 26.07.0, usado somente na inspeção visual independente |
| Ollama | Disponível |
| Modelo local | `qwen3:8b` disponível |

A indisponibilidade simulada de Ollama passou nos testes do fallback: ela não derruba
o fluxo determinístico. O Qwen participa da descoberta de documentos, não do OCR do
conteúdo das páginas.

## Validações executadas

| Validação | Resultado |
| --- | --- |
| Suíte completa, ignorando artefato local não versionado em `data/` | 879 testes + 114 subtestes aprovados |
| Testes de OCR e pipeline | 26 aprovados |
| Descoberta com Ollama disponível/indisponível simulada | 15 testes + 7 subtestes aprovados |
| Lint | Aprovado |
| Tipos | Aprovado em 75 arquivos-fonte |
| Build da distribuição | Aprovado |
| Instalação limpa em diretório temporário | Aprovada; pacote 0.4.0 importado |
| Homologação oficial, execução 1 | 41/41 páginas recuperadas |
| Homologação oficial, execução 2 | 41/41 páginas; resultado funcional idêntico |
| Tesseract indisponível | Teste aprovado; não é dependência do fluxo |
| Poppler indisponível | Teste aprovado; não é dependência do fluxo |

Uma execução direta de `pytest -q` no ambiente de trabalho encontrou primeiro uma
cópia antiga e ignorada do pacote dentro de `data/package-smoke-20260906-v3`.
Esse diretório não existe em checkout limpo. A suíte do código atual foi repetida com
`data/` excluído da coleta e passou integralmente.

## Como repetir

1. Baixe os três PDFs indicados no manifesto para
   `data/homologation/ocr-official`, mantendo os nomes locais especificados.
2. Execute `python scripts/run_ocr_homologation.py --output <arquivo-json>`.
3. Repita a execução e compare os campos funcionais, desconsiderando durações.

O script valida hash, quantidade de páginas e ausência de camada textual antes do
OCR. O JSON gerado fica em `data/`, contém apenas métricas e não armazena o texto das
provas.

## Limitações e próximos passos

- A amostra atende à quantidade solicitada, mas os três documentos são da FUVEST.
  Uma rodada futura deve incluir outras instituições para aumentar a diversidade de
  digitalização.
- As páginas oficiais desta amostra não exigiram correção de rotação ou inclinação;
  esses caminhos foram cobertos por fixtures locais pequenas e permitidas.
- A cobertura estrita de marcadores ficou em 94,79%. Antes de afirmar extração
  estrutural integral, é necessário revisar os 15 marcadores ausentes.
- O limite de 45 segundos é verificado entre tentativas. Uma chamada nativa do motor
  já iniciada não é interrompida no meio; o resultado é descartado se o limite for
  excedido.
- O pico de memória não foi medido nesta rodada. O risco foi limitado por uma
  resolução máxima de 12 megapixels por página.
- A validação de qualidade reduz falsos positivos evidentes, mas não comprova a
  correção semântica de cada palavra reconhecida.

Conclusão: o fluxo de OCR ficou mais confiável, observável e limitado, e passou na
meta desta homologação. Ainda não se deve declarar OCR perfeito ou universal com uma
amostra de uma única instituição.
