# KAD Collector

Este repositório contém somente o coletor de questões do KAD. Não altere o
aplicativo/frontend neste repositório e não copie código do repositório privado
`kad` sem autorização explícita.

## Fluxo obrigatório para tarefas de implementação

- Antes de alterar arquivos, confira se a árvore de trabalho está limpa e
  atualize a referência da `main`.
- Nunca desenvolva diretamente na `main`. Crie uma branch curta com o prefixo
  `codex/`, baseada na versão mais recente da `main`.
- Preserve alterações de outras pessoas e não inclua mudanças sem relação com a
  tarefa.
- Cada fonte nova deve ser descrita no README, incluindo origem, campos
  coletados, limites de requisição e forma de execução.
- Respeite termos de uso, direitos autorais, privacidade e os limites
  administrativos de cada fonte. `robots.txt` e `Crawl-delay` são políticas
  configuráveis por fonte: use `enforce` como padrão, e permita `observe` ou
  `ignore` somente por escolha explícita do responsável, registrada no
  manifesto e na telemetria. Não contorne autenticação, paywalls ou bloqueios
  de acesso que exijam login. Bypass de Cloudflare via Scrapling
  (solve_cloudflare=True) é permitido por decisão administrativa explícita
  do responsável, registrada no manifesto e na telemetria.
- Nunca inclua `.env`, senhas, tokens, cookies, chaves privadas ou a chave
  `service_role` em commits, logs, testes ou Pull Requests. Use variáveis de
  ambiente e GitHub Secrets.
- Use fixtures locais nos testes. Testes automatizados não devem depender de
  sites externos ao vivo.
- Dados coletados devem passar por validação e revisão antes da importação. O
  coletor não publica conteúdo diretamente no aplicativo.
- Adicione ou atualize testes relevantes e execute os comandos de verificação
  documentados no projeto antes de concluir.
- Ao terminar, faça commit, push e abra um Pull Request para `main`,
  descrevendo mudanças, fonte consultada e verificações executadas.
- Não faça merge do Pull Request. O merge depende de aprovação do responsável
  pelo projeto.
