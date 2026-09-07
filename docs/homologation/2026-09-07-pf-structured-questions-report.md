# Homologação de PDF até questão estruturada — Polícia Federal

Data: 2026-09-07

Branch: `codex/pdf-question-structured-pipeline`

Base: `bb8046e`, que contém o merge do PR #96.

## Resultado

O Collector passou a transformar manifestos já coletados em um pacote JSON versionado,
auditável e destinado à revisão. O fluxo usa os componentes reais de extração, OCR, parsing e
associação de gabarito. Nada foi enviado ao KAD ou ao Supabase e nenhum PDF ou pacote completo
foi incluído no Git.

| Indicador | Piloto PF 2021 | Lote ampliado |
| --- | ---: | ---: |
| Pares prova/gabarito | 4 | 56 |
| Documentos processados | 8 | 112 |
| Questões esperadas | 480 | 4.108 |
| Questões detectadas | 480 | 4.108 |
| Respostas associadas | 480 | 4.108 |
| Precisão estrutural | 100% | 100% |
| Cobertura estrutural | 100% | 100% |
| Taxa de associação | 100% | 100% |
| Duplicatas | 0 | 0 |
| Aceitas sem ressalva | 447 | 3.840 |
| Em quarentena | 33 | 268 |
| Erros isolados | 0 | 0 |
| Intervenção durante a execução | 0 | 0 |
| Chamadas ao Qwen | 0 | 0 |

“Precisão” e “cobertura” medem numeração, segmentação e correspondência com os gabaritos
oficiais. Não significam que um especialista rejulgou o mérito de todas as 4.108 respostas.
Uma amostra determinística e estratificada de 15 itens, cobrindo 2018, 2021 e 2025, foi
conferida manualmente quanto a enunciado, contexto, página e resposta associada; os 15 estavam
estruturalmente coerentes.

## Destino de cada item

| Estado | Quantidade | Regra |
| --- | ---: | --- |
| `accepted` | 3.840 | Enunciado, origem e resposta presentes, sem ressalva automática |
| `quarantined` | 268 | Exige revisão antes de qualquer importação |
| `rejected` | 0 | Não houve item estruturalmente inválido após as correções |

Entre os itens em quarentena, 178 foram anulados no gabarito oficial e 93 dependem de figura,
gráfico ou tabela; três itens acumulam as duas razões. A quarentena é parte do resultado e não
uma falha escondida. O pacote não possui caminho de publicação automática.

## Resultado por ano

| Ano | Questões | Aceitas | Quarentena |
| ---: | ---: | ---: | ---: |
| 2018 | 1.230 | 1.115 | 115 |
| 2021 | 480 | 447 | 33 |
| 2025 | 2.398 | 2.278 | 120 |

## Defeitos reproduzidos e corrigidos

| Defeito | Causa | Correção geral |
| --- | --- | --- |
| Cadernos por seção retornavam zero itens | A instrução CERTO/ERRADO não aparece novamente em PDFs recortados | O formato do gabarito oficial passa a informar o parser e os títulos de conhecimentos básicos/específicos delimitam a seção objetiva |
| Gabaritos perdiam a primeira ou a última linha | O texto do PDF colava números (`23456789`) e adicionava zeros de preenchimento | A grade é reconstruída por sequência e quantidade de respostas, rejeitando inconsistências |
| Prova e gabarito básicos não formavam par | Um gabarito pode ser compartilhado por intervalo de cargos enquanto o caderno cita um cargo | Associação forte por bloco/cargo e fallback limitado a um único candidato básico no mesmo concurso/ano |
| “Manual de Redação” encerrava a prova | O limite de seção aceitava ocorrência parcial de “redação” | O limite agora exige cabeçalho não objetivo completo |
| Contexto carregava cabeçalhos de página | Cabeçalhos Cebraspe/Matriz não eram filtrados em todas as variações | Filtro estrutural ampliado sem depender de uma URL |
| Hash mudava entre execuções idênticas | Duração e disponibilidade do Ollama entravam no conteúdo hashado | Hash semântico ignora telemetria variável e preserva IDs/conteúdo |

Cada correção possui regressão automatizada. A lógica determinística continua sendo a primeira
opção e nenhuma regra depende de um único endereço do concurso.

## Formato do pacote

O pacote `1.0` contém:

- identificação da banca, órgão, concurso, ano, cargo/área e bloco;
- número original, enunciado, alternativas e texto de apoio;
- resposta oficial e adaptação explícita de `C/E` para `A/B` no formato interno;
- URL, SHA-256 da prova e do gabarito e páginas de origem;
- método de extração por página, confiança, motivo de OCR e versão do parser;
- estado de validação, motivos de quarentena, erros isolados e telemetria do Qwen;
- identificador estável e SHA-256 semântico do pacote.

O comando criado é `kad-collector structure-pdfs`. O pacote real usado nesta homologação está
somente em `data/structured/pf-all/final.json` e tem hash semântico
`5f946b0ab11a03e8890c83eebad16e7ae2ca7ed3212f3e3da93d925afecc2490`.

## Qwen e execução sem Ollama

O `qwen3:8b` estava disponível, mas não foi chamado: todos os blocos foram classificados pelo
caminho determinístico. O piloto foi executado duas vezes com Ollama disponível e uma vez com
Ollama desativado. IDs, conteúdo e hash foram iguais nas três execuções:
`cf822429ef79d105fb0d1fc3d0abb41901ef43f3d520b1319e07ddf973b3d4db`.

Isso demonstra que a indisponibilidade do Ollama não derruba o fluxo determinístico. Toda
eventual decisão futura do Qwen possui contrato de telemetria no pacote; nesta rodada a lista de
chamadas ficou vazia.

## OCR

Os 112 PDFs da PF selecionados possuíam camada textual aproveitável; portanto, zero páginas
precisaram de OCR e não há taxa real de OCR da PF a calcular. O pacote registra método,
confiança, duração e motivo por página, e o teste de integração confirma que uma página OCR é
propagada até as métricas.

A evidência real anterior permanece válida: três PDFs oficiais integralmente digitalizados da
FUVEST tiveram 41/41 páginas recuperadas pelo OCR local. Consulte
[`2026-09-06-ocr-scanned-pdf-reliability.md`](2026-09-06-ocr-scanned-pdf-reliability.md).
Não se afirma, com base nisso, que o OCR é universal ou perfeito.

## Validações

| Verificação | Resultado |
| --- | --- |
| Suíte completa | 909 testes + 114 subtestes aprovados |
| OCR, pipeline e pacote estruturado | 32 testes aprovados |
| Lint | Aprovado |
| Tipos | Aprovado em 77 arquivos-fonte |
| Wheel | Gerado com sucesso |
| Instalação limpa e comando novo | Aprovados a partir do wheel |
| Repetição do piloto | Mesmo hash semântico |
| Ollama desligado | Mesmo conteúdo e hash; sem falha |

A execução inicial de `pytest -q` tentou coletar cópias antigas e ignoradas do pacote dentro de
`data/`. A suíte foi repetida com escopo `tests/` e `PYTHONPATH` apontando para esta branch; o
resultado acima é o da execução limpa.

## Limitações e próximos passos

- Os 268 itens em quarentena precisam de revisão humana e tratamento de imagens antes de entrar
  no KAD.
- A avaliação semântica foi amostral, não uma revisão especializada das 4.108 respostas.
- O piloto real desta branch não exercitou OCR porque os PDFs da PF já tinham texto.
- A importação para a área de revisão do KAD ainda deve ser implementada como etapa separada;
  publicação direta continua proibida.
- A generalização para outras bancas deve reutilizar o mesmo contrato e acrescentar matrizes
  oficiais próprias, sem enfraquecer a validação.

Conclusão: o fluxo Cebraspe/Polícia Federal atingiu os critérios estruturais desta homologação e
produziu um pacote repetível para revisão. Ele ainda não deve publicar automaticamente nem ser
tratado como semanticamente perfeito.
