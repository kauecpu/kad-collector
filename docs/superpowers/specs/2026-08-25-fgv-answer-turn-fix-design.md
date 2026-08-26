# Associação de gabaritos FGV por turno

## Objetivo

Associar provas e gabaritos FGV quando o turno consta apenas no PDF, mantendo a
decisão fechada diante de evidência ausente, conflitante ou encontrada somente
no corpo de uma questão.

## Fluxo de dados

Uma função compartilhada extrai turnos estruturais de páginas FGV e normaliza
`MANHÃ`, `MANHA` e `TARDE` para `manhã` e `tarde`. O parser FGV usa essa função
para identificar uma prova. A identidade semântica usa as mesmas evidências
para formar o turno único da prova ou a cobertura multivalorada do gabarito. A
associação usa o turno da prova para selecionar o bloco do gabarito antes de
comparar o intervalo de questões.

Perfis já armazenados podem ser enriquecidos em memória a partir das páginas e
do contrato normalizado. Isso permite que o dry-run reflita a correção sem
reescrever a identidade persistida ou o banco analisado.

## Regras de segurança

- Provas aceitam exatamente um turno estrutural nas páginas iniciais.
- A busca para ao encontrar o início das questões; palavras em enunciados não
  são evidência de turno.
- Gabaritos podem declarar `manhã` e `tarde` em cabeçalhos independentes; essa
  coleção é cobertura válida, não conflito.
- Ausência de turno nunca vira `não aplicável` por inferência.
- Cargo, etapa, tipo e intervalo continuam obrigatórios e incompatibilidades
  impedem a associação.
- Casos incompletos ou ambíguos entram de forma idempotente na fila de revisão
  quando uma reconciliação é aplicada.
- O banco original e o Supabase não são modificados nesta atividade.

## Validação

Fixtures locais cobrem manhã, tarde, tipos 1 a 4, intervalos de 80, 60 e 70
questões, cobertura multitemporal, falsos positivos no enunciado, ausência de
turno, prioridade do definitivo, conflitos de escopo, fila de revisão e
idempotência. Uma cópia ignorada do SQLite local é usada para o dry-run dos 23
documentos de prova, separando as 1.120 questões objetivas principais das 420
questões de curso de formação ainda excepcionadas.
