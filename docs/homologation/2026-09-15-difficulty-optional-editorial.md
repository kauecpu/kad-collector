# Homologação editorial com dificuldade opcional

Data: 15/09/2026  
Branch: `codex/difficulty-optional-editorial`  
Base: `ae2b74a1634d795769efd19c24fb1bb33728449b` (PR #105)  
Corpus: Polícia Federal/CEBRASPE, Banco do Brasil/CESGRANRIO e CORE-PI/QUADRIX

## Resultado

`difficulty` deixou de participar dos requisitos de completude, aprovação, exportação e importação. O campo continua validado quando existe, conserva a proveniência anterior e é omitido quando não existe. Nenhum valor foi inventado.

| Indicador | Antes | Depois |
|---|---:|---:|
| Ocorrências | 5.580 | 5.580 |
| Questões únicas | 5.292 | 5.292 |
| Estruturalmente aceitas | 5.158 | 5.158 |
| Taxonomia completa | 0 | 1.043 |
| Pendentes de classificação obrigatória | 5.580 | 4.537 |
| Bloqueadas por gabarito | 182 | 182 |
| Dependentes de imagem | 115 | 115 |
| Duplicatas | 288 | 288 |
| Quarentena no gate de aprovação | 441 | 441 |
| Elegíveis para amostragem | 0 | 419 |
| Aptas para staging | 0 | 0 |

O ganho mensurável foi de 518 questões que deixaram de ter bloqueio de taxonomia. Destas, 419 passaram por todos os gates automáticos: 50 entraram na amostra estratificada e 369 ficaram `auto_ready`. Nenhum grupo foi liberado para staging porque a amostra ainda não recebeu decisões humanas válidas. O pipeline não fabricou essas decisões.

A quarentena estrutural do pacote contém 422 ocorrências. O gate consolidado de aprovação registra 441 porque inclui falhas estruturais detectadas na etapa editorial; os contadores se sobrepõem e não devem ser somados ao total.

## Cobertura da taxonomia

| Campo | Antes | Depois | Obrigatório |
|---|---:|---:|---|
| Disciplina | 18,6738% | 18,6918% | sim |
| Matéria | 21,1470% | 21,1649% | sim |
| Assunto | 29,6237% | 29,6416% | sim |
| Nível | 100,0000% | 100,0000% | sim |
| Dificuldade | 0,0000% | 0,0000% | não |

A dificuldade ausente aparece apenas como informação de cobertura. Ela não cria bloqueio, flag de qualidade, filtro de exceção nem valor artificial na exportação.

## Distribuição do corpus

| Banca | Órgão | Ocorrências | `auto_ready` | Amostra | Revisão/exceção | Quarentena |
|---|---|---:|---:|---:|---:|---:|
| CEBRASPE | Polícia Federal | 4.108 | 258 | 31 | 3.551 | 268 |
| CESGRANRIO | Banco do Brasil | 1.312 | 61 | 15 | 1.082 | 154 |
| QUADRIX | CORE-PI | 160 | 50 | 4 | 87 | 19 |

| Concurso | Ano | Ocorrências |
|---|---:|---:|
| Polícia Federal | 2018 e 2021 | 1.710 |
| PF 25 | 2025 | 1.248 |
| PF 25 ADM | 2025 | 1.150 |
| Banco do Brasil `bb0121` | 2021 | 1.312 |
| CORE-PI 2026 | 2026 | 160 |

Por ano: 2018 = 1.230, 2021 = 1.792, 2025 = 2.398 e 2026 = 160. A distribuição completa por cargo está no relatório JSON ao lado; nenhum enunciado, alternativa ou resposta do modelo foi incluído.

## Qwen local

Modelo: `qwen3:8b`, digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.

| Medida | Resultado |
|---|---:|
| Candidatos únicos do piloto | 11 |
| Chamadas, incluindo divisões de lote | 13 |
| Questões enviadas, incluindo tentativas/divisões | 42 |
| Sugestões aceitas | 1 |
| Permaneceram sem sugestão válida | 10 |
| Rejeições dos guardas | 5 |
| Cache reutilizado | 0 |
| Falhas de chamada | 0 |
| Tempo do piloto | 174,804 s |

O Qwen recebeu somente os quatro campos obrigatórios e só pôde devolver IDs da taxonomia fechada. Evidência que não era trecho literal da questão, ID desconhecido, item duplicado, campo extra ou resposta incompleta foi recusado.

O piloto aprovou 1 de 11 candidatos (9,1%). A execução integral dos 4.537 pendentes foi interrompida por baixa efetividade e custo projetado de aproximadamente 17,8 horas. Rodá-la até o fim não resolveria a ausência de caminhos adequados no catálogo. A próxima correção deve ampliar regras e caminhos gerais com evidência, antes de repetir o Qwen em massa.

Com o Ollama desligado, o preflight retornou indisponível, houve zero chamada ao modelo e o lote determinístico terminou sem exceção. A falha do serviço não descartou resultados já processados.

## Bloqueios restantes

| Motivo | Ocorrências |
|---|---:|
| Taxonomia ausente ou confiança abaixo de 0,84 | 5.062 |
| Estrutura | 441 |
| Duplicidade | 288 |
| Associação de gabarito | 182 |
| Dependência visual | 115 |
| Dificuldade | 0 |

Os bloqueios podem se sobrepor. As 4.537 pendências de classificação são questões sem um ou mais campos obrigatórios; as 5.062 ocorrências bloqueadas pelo gate de taxonomia também incluem classificações completas cuja confiança ficou abaixo do limite editorial.

## Correções aplicadas

- Contrato v2, validação, completude, aprovação, interface, relatórios e exportação agora tratam dificuldade como opcional.
- Qwen não recebe nem devolve dificuldade; valores existentes são preservados.
- Sugestão válida do Qwen pode entrar na amostra, mas nunca é liberada diretamente para staging.
- Os lotes usam apenas IDs permitidos e exigem evidência textual literal.
- Caminhos relativos de PDFs verificados são normalizados antes da criação das sessões. Isso evita falsos “arquivo ausente” quando a aprovação roda em outro worktree.
- Reprocessamento integral repetido manteve 5.580 ocorrências, 5.292 identidades e o mesmo hash `77afce5e9a12ba41978d61e34a107ddda3112fcf896fc978ebd7521ac1f9be54`.

Durante a validação, um comando carregou por engano a instalação editável do checkout antigo. A divergência foi isolada pelo caminho do módulo, a execução foi repetida com `PYTHONPATH` apontando para esta branch e as sessões corretas permaneceram intactas. Isso não foi contado como resultado do coletor.

## Desempenho e validação

| Verificação | Resultado |
|---|---|
| Regras determinísticas no corpus completo | 58,904 s |
| Aprovação e amostragem | 7,270 s; 1,303 s/mil |
| Fluxo determinístico + aprovação | 66,174 s; 11,859 s/mil |
| Suíte completa | 973 testes aprovados em 174,63 s |
| Lint | aprovado |
| Tipos | aprovado em 83 arquivos |
| Build | wheel e sdist gerados com `--no-isolation` |
| Instalação do wheel | aprovada em diretório limpo |
| Ollama disponível | piloto executado com `qwen3:8b` |
| Ollama desligado | falha isolada; determinístico preservado |
| Idempotência | dois hashes iguais |

O modo isolado padrão do build não pôde criar ambiente temporário porque o Python portátil disponível não contém o módulo `venv`; o build sem isolamento e a instalação do wheel resultante passaram.

## Segurança

- Nenhum PDF foi adicionado ao Git.
- Nenhum dado foi enviado ao KAD.
- Nenhuma escrita foi feita no Supabase.
- Nenhuma decisão humana foi inventada.
- Nenhuma resposta bruta do Qwen foi versionada.

Relatórios locais usados como evidência, fora do Git:

- `data/editorial-campaign/difficulty-optional-offline/qwen-smoke-v2-report.json`
- `data/editorial-campaign/difficulty-optional-offline/idempotency-report-2.json`
- `data/editorial-campaign/difficulty-optional-ollama-off/report.json`
- `data/editorial-approval/difficulty-optional-final-report.json`
