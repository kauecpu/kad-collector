# Continuidade do coletor — 27/09/2026

Este é o registro corrente. Atualizar este arquivo, sem criar outro relatório de
continuidade. Somente o chat **Testar escopo das 70 questões** executa o coletor.
O outro chat, **Instalar skill Stop Slop**, estava parado na conferência. Nenhuma
tarefa foi enviada a ele. Não ampliar BB nem classificar PF antes de concluir o piloto.

## Código e integração

GitHub consultado nesta execução, não inferido a partir das mensagens:

| PR | Estado | Commit integrado |
|---|---|---|
| [112](https://github.com/kauecpu/kad-collector/pull/112) | integrado | `58877b49d1d72dc99a96066d13ab3702d58e4e43` |
| [113](https://github.com/kauecpu/kad-collector/pull/113) | integrado | `369100bd91237b62e4809af83d0aebe5dd2e57a4` |
| [114](https://github.com/kauecpu/kad-collector/pull/114) | integrado | `16fa03c2199e62776011e5884e837760e2358977` |

Ordem já executada: 112 → 113 → 114. A base desta correção é a `origin/main`
`16fa03c2199e62776011e5884e837760e2358977`.
Branch ativa: `codex/collector-reconciliation`, checkout
`C:\Users\igord\Documents\Codex\KAD\kad-collector-reconciliation`.
Commit da implementação: `ba56bb6e70a1811cb2119f36f76cf8bea1ee1949`
(`fix: reconciliar associação e auditoria do piloto BB`).

Os checkouts `kad-collector` (`3454ac52`) e `kad-collector-corpus-gates`
(`c1653aa3`) foram preservados. A criação de worktree pelo aplicativo falhou
porque o diretório pai não é um repositório; foi usado `git worktree add`.
Nenhum processo existente foi encerrado. Nenhuma alteração no checkout sujo do KAD,
no `data/` original, nas aprovações antigas ou no banco do desktop.

## Defeitos reproduzidos e correções

1. **Associação quebrada pela combinação:** a main extraía zero questões dos dois
   PDFs do piloto. O título do link `Gabaritos Alterados` não identifica o cargo;
   a restrição correta do #114 contra empréstimo de gabarito retirou o fallback
   anterior. Agora um título genérico exige o cabeçalho explícito do PDF em cada
   página, com identidade consistente. O título e as URLs originais permanecem
   intactos; página e cabeçalho entram na justificativa da associação. Ausência,
   conflito ou outro cargo não são resolvidos por aproximação.
2. **Auditoria antiga reaproveitada após mudança de evidência:** reconstruir a
   campanha só comparava o hash da questão. Agora a aprovação também depende do
   hash da evidência documental (SHA, URLs, título, tipo, fonte e metadados).
   `approval-export` revalida esse hash. Estados de campanha legados sem esse vínculo exigem
   nova auditoria; uma decisão local antiga não ressuscita uma auditoria invalidada.
   Caminho local e data de download não integram esse hash.

Os testes reproduziram as falhas antes das correções. A alteração é um seguimento
dos PRs já integrados, não uma cópia deles. Não houve merge automático.

## Artefatos e prova de repetição

Raiz local: `C:\Users\igord\Documents\Codex\KAD`.

- Original aprovado, preservado:
  `kad-collector/data/editorial-approval/bb-2021-tech-pilot10/session.json`.
  SHA-256 confirmado: `ef09e4bf0a3a790312e2a290738b1a4f5ee18d7eb0d52b3bc71c0a2c58030561`.
- Revisão #113, preservada: `output/bb-pilot-pending-review/`.
- Nova revisão **pendente**: `output/bb-pilot-reconciled-review/`.
  `revision.json` registra hashes anteriores/novos e IDs de cada questão;
  `session.json` contém conteúdo, páginas, URLs e decisões pendentes.
- Pacote reprocessado e repetição: `output/bb-pilot-reconciliation/package.json`
  e `repeat-package.json`; hash semântico idêntico:
  `a595363f2d10cec2b7fa4509b333e559947e6b6be4f89ed542bb129f5c6292c1`.
- Propostas de hifenização: `tmp/pilot-text-review/hyphenation-plan.json`.
  Aplicadas antes da segmentação; não houve alteração dos PDFs ou do cache.
- Manifesto de entrada:
  `C:\Users\igord\AppData\Local\Temp\kad-front1-reprocess-5bd7f22097f74191bf6759a26d798adc\manifest.json`.
  Seus dois arquivos locais existem e tiveram os hashes conferidos. Essa entrada
  temporária precisa continuar disponível; não apagá-la antes de preservar os insumos.

Prova: `0517d1e62644c0ccc6a9b52908e56d2bd07d8de94e716433cf8009aaa4db4afd`.
Gabarito alterado: `d8e8db8829a858cab7b42033c48a2e2855b83b53bb85fb76b813124afbe7dee0`.
O campo `key_version` é `revised`; as respostas do tipo 4 estão na página 4.
URLs completas estão no manifesto e na sessão. Nenhum PDF foi incluído no Git.

## Contagens, sem misturar etapas

| Etapa | Resultado desta execução |
|---|---:|
| Questões detectadas no caderno | 69 |
| Aceitas estruturalmente / em quarentena | 59 / 10 |
| Selecionadas para o piloto / IDs distintos | 10 / 10 |
| Comparadas com a revisão #113 | 10 |
| Novas decisões humanas / aprovadas na nova revisão | 0 / 0 |
| Exportadas / importadas / publicadas nesta execução | 0 / 0 / 0 |

As 59 aceitas estruturalmente **não são 59 questões homologadas**. Persistem as
limitações de segmentação/capa/Q11 fora do piloto; o caderno todo não está aprovado.
Não houve OCR nem Qwen nesta repetição (PDFs com texto).

| Questão | Página da prova | Resposta | Texto comparado ao lote original aprovado |
|---|---:|---|---|
| 18 | 7 | D | quebras físicas reparadas |
| 21 | 8 | C | quebras físicas reparadas; `on-line` preservado |
| 36 | 12 | B | quebras físicas reparadas |
| 42 | 14 | C | inalterado |
| 44 | 14 | B | inalterado |
| 45 | 15 | D | quebras físicas reparadas |
| 53 | 20 | B | inalterado |
| 64 | 24 | E | inalterado |
| 68 | 25 | C | inalterado |
| 69 | 26 | B | quebras físicas reparadas |

Comparação com a revisão pendente #113: **zero mudanças em texto, alternativas,
respostas e IDs de importação**. Mudaram o hash do pacote e a rastreabilidade da
associação. Não reutilizar o pacote #113: os campos adicionados pelo #114 mudam
seu hash semântico quando carregado no modelo atual. Nenhuma aprovação foi copiada.
Repetir o processamento produziu os mesmos registros e dez IDs distintos.

## Validação

- Suíte completa da combinação corrigida: **1044 passed, 123 subtests passed**,
  222,07 s. Depois foi acrescentada mais uma regressão para campanha legada sem
  hash de evidência; o arquivo completo de aprovação passou novamente:
  **21 passed, 7 subtests passed**. Nenhum código de produção mudou após a suíte completa.
- `ruff check .`: passou.
- `python -m mypy src/kad_collector`: passou, 89 arquivos.
- `node --check` nos três arquivos JavaScript do coletor: passou.
- `git diff --check`: passou.
- Wheel com `pip wheel . --no-deps --no-build-isolation`, instalação em diretório
  temporário e `python -m kad_collector.desktop_app --smoke-test --data-dir <isolado>`:
  passaram. O executável PyInstaller não foi reconstruído localmente.
- Testes da campanha/exportação cobrem proveniência, alterações após aprovação,
  duplicatas, legado sem hash de evidência e recusa de exportação sem auditoria.
  São fixtures locais, não uma aprovação do lote real.

Testes executados em
`C:\Users\igord\AppData\Local\Temp\kad-reconciliation-tests-b19b13f33f7447c0b4b2d3475c17f220`,
com `PYTHONPATH` e `LOCALAPPDATA` isolados. Usar o Python de
`kad-collector/.venv/Scripts/python.exe`, apontando `PYTHONPATH` para o checkout
correto; a instalação editável dessa venv aponta para o checkout antigo.

Comandos do reprocessamento (substituir as variáveis pelos caminhos acima):

```text
kad-collector structure-pdfs <manifesto> --output <novo-package.json> --hyphenation-plan <plano> --disable-ollama
python scripts/revise_pilot_review.py --source-session <original-session.json> --package <novo-package.json> --manifest <manifesto> --output <diretório-novo>
```

## Próximo bloqueio e sequência autorizada

1. Revisar/integrar o PR desta correção. #113 e #114 já estão na main; não há
   ordem de integração pendente entre eles. Não criar outro PR copiando-os.
2. Confirmar a nova main e preparar a campanha oficial da revisão atual com
   `approval-campaign`, sem reaproveitar estado aprovado ou inventários antigos.
   Apresentar as diferenças e pedir aprovação explícita do lote antes de
   `approval-export`. O script de revisão não é um exportador nem substitui esses comandos.
3. Pedir autorização separada para reativar **kad-dev**, importar e publicar em
   homologação. Conferir identidade do projeto antes de qualquer escrita;
   jamais assumir que `npaoyezfwmgauirrlyog` é dev (foi identificado como kad-prod).
4. Testar abrir → responder → resultado → retornar, sem `VITE_KAD_LOCAL_PILOT`.

**Parada atual:** correções ainda fora da main; revisão nova sem aprovação.
Nenhum acesso ou alteração no Supabase nesta execução. A situação remota não foi
revalidada. O piloto publicado continua **não demonstrado**; JSON e testes locais
não cumprem a meta de dez questões utilizáveis no kad-dev.
