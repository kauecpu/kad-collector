# Aprovação editorial escalável

- Campanha: `pf-bb-corepi-2018-2026-approval`
- Regras: `1.0.0`
- Taxonomia: `3.1.0`
- Estado de publicação: `draft`

## Resultado

| Indicador | Total |
|---|---:|
| Ocorrências | 5580 |
| Questões únicas | 5292 |
| Duplicatas | 288 |
| Elegíveis automaticamente | 0 |
| Amostra de auditoria | 0 |
| Precisam de revisão | 5139 |
| Quarentena | 441 |
| Aprovadas para staging | 0 |
| Grupos | 0 |
| Grupos aprovados | 0 |
| Grupos bloqueados | 0 |
| Grupos aguardando auditoria | 0 |
| Processado sem intervenção | 100.0% |
| Tempo total | 7.522 s |
| Tempo por mil questões | 1.348 s |
| Chamadas ao Qwen | 0 |
| Falhas do Qwen | 0 |

## Regras de aprovação

A origem, a estrutura, a associação do gabarito e a dependência visual são travas críticas: uma falha não pode ser compensada por outra dimensão.
OCR exige confiança mínima de 90%; a taxonomia determinística exige 84%. Sugestão isolada do Qwen nunca recebe aprovação automática.

## Auditoria

A amostra padrão usa 2%, mínimo de 50 e máximo de 300. Foram selecionados 0 fingerprints distintos.
Um grupo só é liberado com toda a amostra decidida, nenhum erro crítico e precisões mínimas de 98% (estrutura), 99% (gabarito) e 95% (taxonomia).

## Bloqueios

| Motivo | Questões |
|---|---:|
| answer_association | 182 |
| deduplication | 288 |
| structure | 441 |
| taxonomy | 5580 |
| visual_dependency | 115 |

## Segurança

A aprovação automática apenas seleciona candidatos. A auditoria libera grupos para um pacote local com estado draft. O fluxo não escreve no KAD nem no Supabase.

## Limitação atual

Nenhuma precisão pode ser alegada antes das decisões humanas sobre a amostra. Grupos incompletos continuam bloqueados.

## Artefatos locais

- Estado retomável: `data/editorial-campaign/pf-bb-corepi-taxonomy-batch-v1-offline-final/review/index.json` → campanha local
- Pacote de staging: ainda não gerado; não há grupos auditados.
- Nenhum PDF, enunciado completo ou resposta bruta do Qwen foi versionado.

## Validação

- `python -m pytest`
- `python -m ruff check .`
- `python -m mypy src/kad_collector`
- `python -m build`
