# PCI Banco do Brasil 2023: `unresolved_scope`

## Reprodução local protegida

Investigamos uma cópia somente leitura do SQLite operacional e cópias locais dos quatro
PDFs PCI necessários. Nenhum comando escreveu nos originais. As fixtures automatizadas
usam somente textos sanitizados e não dependem de rede.

## Cadeia observada

- Documento de prova: `2f309a05-ed46-4afd-8725-c71fece1757c`
- Versão da prova: `064ff41a-c3e5-5411-b393-cf4cf20e794b`
- Documento de gabarito: `23ea5821-ebbb-4bd9-a43f-6f99737ec474`
- Versão do gabarito: `4cc93003-a923-5fd1-9718-5871a172ebaa`
- Vínculo ativo: `e060247e-b29a-5a73-a6c3-7c84bc13d3f8`
- Execução de preparação: `66e2d8b5-c02a-4370-979a-0cbcb84faada`

O vínculo `semantic-association-v3` selecionou o gabarito e confirmou banca CESGRANRIO,
concurso PCI Concursos - Banco do Brasil, ano 2023, instituição Banco do Brasil, cargo
Escriturário – Agente Comercial, etapa, variante, intervalo 1–70 e grade de respostas.
A comparação de turno também era compatível: prova e gabarito não separavam a aplicação
por turno. O PDF do gabarito, porém, não declarava que era definitivo e sua versão ficou
com `answer_key_state='unknown'`.

## Causa raiz

`desktop_preparation._resolved_turn` aceitava o valor “não se aplica” somente quando o
gabarito estava marcado como `definitive`. A preparação descartou a prova por falta de
turno, embora a decisão de associação registrasse evidência explícita de ausência de
partição por turno. Sem um contexto de prova, a preparação não criou o catálogo canônico
nem `canonical_document_scopes`.

A preparação gravou as 70 ocorrências com `scope_id`, concurso, aplicação, cargo, etapa,
turno e caderno canônicos nulos, portanto com `occurrence_status='unresolved_scope'`. Ela
não criou `question_group_occurrence`, `question_equivalence_group` nem
`canonical_question` PCI. A prévia mostrou 0 incluídas e 70 excluídas.

## Correção e garantias

- Ausência explícita de partição por turno agora resolve para “não se aplica”, mesmo se o
  estado editorial do gabarito for `unknown`.
- Derivar um turno existente do gabarito continua exigindo estado `definitive`.
- Provas PCI sem vínculo entram na associação automática determinística; outras fontes não
  modeladas continuam fora desse caminho.
- Evidência de intervalo que fica disponível após a extração pode atualizar o mesmo vínculo
  sistêmico, sem criar outro vínculo e sem alterar decisões humanas.
- A prévia agrupa a confirmação por documento e informa quantas questões a ação única afeta.
