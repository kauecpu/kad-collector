# Aprovação editorial escalável

- Campanha: `pf-bb-corepi-2018-2026-approval`
- Regras: `1.0.0`
- Taxonomia: `3.0.0`
- Estado de publicação: `draft`

## Resultado

| Indicador | Total |
|---|---:|
| Ocorrências | 5580 |
| Questões únicas | 5292 |
| Duplicatas | 288 |
| Elegíveis automaticamente | 415 |
| Amostra de auditoria | 50 |
| Precisam de revisão | 4724 |
| Quarentena | 441 |
| Aprovadas para staging | 0 |
| Grupos | 193 |
| Grupos aprovados | 0 |
| Grupos bloqueados | 0 |
| Grupos aguardando auditoria | 193 |
| Processado sem intervenção | 100.0% |
| Tempo total | 9.827 s |
| Tempo por mil questões | 1.761 s |
| Chamadas ao Qwen | 0 |
| Falhas do Qwen | 0 |

## Regras de aprovação

A origem, a estrutura, a associação do gabarito e a dependência visual são travas críticas: uma falha não pode ser compensada por outra dimensão.
OCR exige confiança mínima de 90%; a taxonomia determinística exige 84%. Sugestão isolada do Qwen nunca recebe aprovação automática.

## Auditoria

A amostra padrão usa 2%, mínimo de 50 e máximo de 300. Foram selecionados 50 fingerprints distintos.
Um grupo só é liberado com toda a amostra decidida, nenhum erro crítico e precisões mínimas de 98% (estrutura), 99% (gabarito) e 95% (taxonomia).

## Bloqueios

| Motivo | Questões |
|---|---:|
| answer_association | 182 |
| deduplication | 288 |
| structure | 441 |
| taxonomy | 5066 |
| visual_dependency | 115 |

## Segurança

A aprovação automática apenas seleciona candidatos. A auditoria libera grupos para um pacote local com estado draft. O fluxo não escreve no KAD nem no Supabase.

## Limitação atual

Nenhuma precisão pode ser alegada antes das decisões humanas sobre a amostra. Grupos incompletos continuam bloqueados.

O gargalo dominante não é a quantidade de revisão humana: 5.066 ocorrências ainda falham no gate de taxonomia. A campanha isolou esses casos na fila de exceções em vez de permitir publicação silenciosa.

## Qwen e tolerância a falhas

O Ollama estava disponível com `qwen3:8b`. No ensaio limitado a cinco questões houve uma chamada, cinco sugestões permaneceram sem resolução e a resposta inválida foi registrada como uma falha. Nenhuma sugestão foi aceita e a coleta determinística continuou.

O mesmo ensaio foi repetido com o Qwen desativado: não houve chamadas, falhas ou aprovações indevidas. A indisponibilidade do modelo não interrompe o caminho determinístico.

## Idempotência

Duas execuções consecutivas sobre o mesmo índice produziram o mesmo hash de conteúdo: `0db3a1d4228246be1f572c339de98700782725aec8f34c76fb1e620ec54b7d4f`. A amostra manteve 50 fingerprints distintos e não criou decisões humanas fictícias.

## Artefatos locais

- Estado retomável: `data/editorial-campaign/pf-bb-corepi-taxonomy-v3-offline/review/index.json` → campanha local
- Pacote de staging: ainda não gerado; não há grupos auditados.
- Nenhum PDF, enunciado completo ou resposta bruta do Qwen foi versionado.

## Validação

- Suíte completa: 968 testes e 114 subtestes aprovados.
- Interface local: carregada com o corpus real, sem erros no console.
- Lint, checagem de tipos e sintaxe do JavaScript: aprovados.
- Pacote: `python -m build --no-isolation` aprovado; instalação limpa e presença dos três arquivos da interface confirmadas.
- Limitação do ambiente: o build isolado não pôde criar o ambiente temporário porque esta instalação do Python não inclui o módulo padrão `venv`.
