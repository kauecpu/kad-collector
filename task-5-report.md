# Task 5 — resolução de republicação e versão sucessora

Implementada em `codex/semantic-document-identity`.

- Adicionada decisão pura com os cinco resultados e ordem exigida.
- Adicionada resolução transacional/idempotente com UUIDv5, identidade, versão,
  predecessor, observação e evento.
- Integrada ao fluxo desktop após extração e antes da estruturação; republicações
  não geram questões e casos incertos não são estruturados.
- Adicionados testes focais para os cinco resultados e conteúdo com questões
  adicionadas/removidas.

Verificações:

- RED: teste focal falhou inicialmente por `ModuleNotFoundError` para o módulo de resolução.
- GREEN: testes focais e testes semânticos/desktop executados com sucesso.
- Ruff focal: aprovado.
- Mypy focal: o runtime local encerra com erro interno do mypy 2.3.1 antes da análise.
