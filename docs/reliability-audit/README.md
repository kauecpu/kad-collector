# Auditoria de confiabilidade do Collector

## Diagnóstico e correções

| Área | Antes | Depois |
| --- | --- | --- |
| Acionamento do Qwen | `qwenEligible` podia iniciar trabalho vazio | O Qwen só inicia quando há `aiCandidates`/`qwenRequired` reais |
| Contadores | Elegibilidade era apresentada como trabalho pendente | Painel separa unidades elegíveis, ocorrências cobertas, completas, regras, Qwen, revisão e bloqueios |
| Progresso | Cada lote podia voltar a zero | Execução possui progresso cumulativo, percentual monotônico e estado persistido |
| Estados | Erro/parada podia parecer processamento | Estados ativos, terminais, `stale`, erro e cancelamento são distintos |
| Cancelamento | Não havia parada segura do fluxo completo | Endpoint, botão, cancelamento cooperativo e lease mantido até a thread terminar |
| Aprovação | Lote limitado a 1.000 itens | Aprovação continua em lotes transacionais até esvaziar a fila |
| Exportação | Nome de pasta podia colidir e marcar antes do fim | Pasta temporária exclusiva, manifesto/hashes validados, publicação atômica e marcação posterior |
| Backups | Crescimento sem política | Retenção configurável, verificação de integridade e diagnóstico no painel |
| Bases especiais | Automação mutável podia atingir referência/teste | Bases de teste/referência ficam protegidas, salvo autorização explícita |

As ocorrências e cópias continuam preservadas no SQLite. A operação usa a unidade canônica; assim, uma cópia confirmada não cria nova chamada ao Qwen nem novo registro no pacote de exportação.

## Estados capturados

- `automation-processing.png`: preparação em andamento.
- `automation-qwen-processing.png`: Qwen executando um lote.
- `automation-no-qwen-work.png`: execução concluída sem trabalho para o Qwen.
- `automation-stopped.png`: cancelamento concluído.
- `automation-error.png`: falha sinalizada como erro.
- `automation-completed.png`: fluxo concluído e pronto para exportação.

## Riscos restantes

- O cancelamento é cooperativo: uma inferência individual do Ollama só termina quando o processo externo devolve o controle.
- O Collector confirma que o arquivo foi criado e validado; ele não confirma que um aplicativo externo importou o arquivo.
- A equivalência semântica continua exigindo revisão humana nos grupos aparentes ou conflitantes.
