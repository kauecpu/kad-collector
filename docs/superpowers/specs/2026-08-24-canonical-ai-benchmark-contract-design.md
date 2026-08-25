# Contrato e amostra do benchmark canônico de IA

## Objetivo

Corrigir o conteúdo derivado enviado aos classificadores, exigir uma decisão taxonômica
coerente e separar falhas de transporte de respostas editoriais inválidas. A preparação deve
produzir uma amostra auditável sem executar modelos ou acessar a rede.

## Limites

- O coletor preserva enunciados, alternativas, hashes e proveniência brutos.
- O sanitizador atua somente na cópia derivada usada pela classificação canônica.
- Gemini, Qwen, DeepSeek e Ollama usam o mesmo formato de pedido e resposta.
- A PR não executa inferência, não lê chaves e não altera scraping, gabaritos, identidade,
  equivalência ou exportação.
- Uma revisão feita pelo agente recebe identificação própria e não vira `human_review`.

## Conteúdo derivado

Um módulo puro recebe enunciado e alternativas e devolve texto limpo, impressão digital e
códigos dos artefatos removidos. Ele reconhece rodapés da FGV, identificação de página,
calendários, cabeçalhos de prova e títulos oficiais anexados à última alternativa. O módulo não
muta o registro bruto. A preparação rejeita resíduos conhecidos e inclui somente contagens nos
relatórios versionados.

## Contrato taxonômico

Cada caminho compatível recebe um identificador estável derivado do catálogo, disciplina,
matéria e assunto. O pedido inclui o identificador, os três rótulos e as palavras-chave do
catálogo. A resposta contém no máximo uma decisão de caminho e uma decisão de nível, ambas com
confiança e evidência.

O validador aceita somente identificadores oferecidos no pedido. Ele extrai do caminho apenas
os campos solicitados e confirma os campos conhecidos. O nível aceita `Fundamental`, `Médio` ou
`Superior`. Uma única opção compatível vira resultado determinístico e não aciona provedor.

## Referências

O manifesto preserva a referência estrutural original e uma decisão editorial separada. Cada
decisão usa um destes estados:

- `agent_reviewed_reference`;
- `ambiguous_reference`;
- `structural_only_reference`;
- `rejected_reference`.

Somente `agent_reviewed_reference` entra na métrica de precisão. O arquivo de revisão não contém
enunciados nem alternativas. A preparação falha quando não encontra 200 referências revisadas e
compatíveis com a versão ativa da taxonomia.

## Benchmark e erros

O benchmark principal exclui casos resolvidos por uma única opção taxonômica. Relatórios podem
contá-los em `deterministicTrivialCases`, sem adicioná-los ao numerador ou denominador de
precisão.

Exceções carregam códigos explícitos. Checkpoints e relatórios distinguem transporte, HTTP, JSON,
schema, repetição, nível, caminho desconhecido, caminho incompatível, campo proibido e baixa
confiança. Mensagens seguras não incluem texto da questão.

## Versionamento e validação

A PR incrementa as versões do schema, algoritmo, prompt e contrato. A impressão digital inclui o
texto sanitizado, a revisão editorial e as opções taxonômicas. Checkpoints anteriores permanecem
no armazenamento local e falham na validação do novo manifesto.

Testes usam SQLite temporário, clientes falsos e fixtures locais. A validação final executa a
suíte completa, Ruff, mypy, compileall, verificação de diff e busca por segredos. Nenhuma etapa
inicia o Ollama ou acessa provedores.
