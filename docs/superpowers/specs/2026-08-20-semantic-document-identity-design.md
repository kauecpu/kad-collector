# Identidade semântica e versionamento de documentos

## Objetivo

O Collector deve reconhecer quando dois PDFs representam o mesmo documento lógico, quando
um arquivo é apenas uma republicação do mesmo conteúdo e quando uma prova ou um gabarito
recebeu uma versão realmente nova. A decisão precisa ser determinística, auditável e
independente da banca ou da forma de entrada.

O resultado esperado é impedir trabalho repetido sem esconder alterações relevantes:

- o mesmo arquivo não cria documento, tarefa ou questão novamente;
- bytes diferentes com conteúdo normalizado equivalente são registrados como republicação
  da mesma versão lógica, sem duplicar questões;
- a mesma identidade semântica com conteúdo alterado cria uma versão sucessora ligada à
  anterior;
- documentos conflitantes ou com evidência insuficiente vão para exceção;
- decisões humanas são preservadas somente quando o conteúdo relevante continua idêntico;
- toda resolução informa os dados, evidências, regra e versão do algoritmo usados.

Esta especificação parte do contrato neutro criado no PR 17. Aquisição continua responsável
por localizar e baixar arquivos. Identidade, versionamento, associação e interpretação são
responsabilidades do motor genérico.

## Escopo e limites

### Incluído

- contratos imutáveis para identidade, cobertura, evidência e decisão;
- normalização determinística de campos semânticos;
- impressão digital binária e impressão digital do conteúdo normalizado;
- registro transacional de artefatos, observações, versões, vínculos e eventos;
- associação conservadora entre prova e gabarito;
- sucessão entre gabarito preliminar e definitivo;
- linhagem de questões e preservação seletiva de decisões humanas;
- migração aditiva e compatibilidade com registros antigos;
- informações mínimas de identidade e versão na interface e nos relatórios locais;
- testes unitários, de persistência, concorrência, regressão e desktop.

### Fora do escopo

- OCR e interpretação de prova digitalizada;
- prova e gabarito contidos no mesmo PDF;
- novas bancas ou novas estratégias de coleta;
- coleta agendada;
- Supabase, aplicativo KAD e publicação remota;
- corretores específicos por banca;
- reformulação ampla da interface;
- geração de respostas por inteligência artificial.

PDF sem texto utilizável mantém o comportamento atual: é preservado e encaminhado à exceção
de OCR, sem receber identidade ou associação inventada.

## Diagnóstico da base atual

O PR 17 estabeleceu `NormalizedDocument` como fronteira entre aquisição e interpretação. O
contrato já carrega caminho local, SHA-256, tamanho, tipo declarado, metadados de origem e
evidências. A base SQLite armazena o documento associado a uma tarefa, e o processamento já
invalida uma decisão editorial quando a impressão digital de uma questão muda.

Ainda faltam quatro conceitos persistentes:

1. identidade lógica independente do arquivo e do caminho local;
2. versão de conteúdo dentro da mesma identidade;
3. observações de arquivos diferentes que representam a mesma versão;
4. decisão explicável que associa um gabarito a uma prova.

Hoje parte da deduplicação acontece pelo SHA-256 ou dentro de um lote. Isso resolve cópia
binária, mas não distingue republicação equivalente de alteração real e não cria uma trilha
completa de sucessão.

## Alternativas consideradas

### Guardar tudo em JSON nas tabelas atuais

É a mudança menor, mas dificulta unicidade transacional, consultas, concorrência, migração e
auditoria. Também mistura novamente documento físico, documento lógico e tarefa.

### Event sourcing completo

Uma sequência imutável de eventos poderia reconstruir todo o estado, mas aumentaria muito a
complexidade do desktop, das migrações e dos testes. O projeto ainda não precisa desse custo.

### Registro relacional com eventos de auditoria

Esta é a opção escolhida. O estado consultado fica em tabelas relacionais com chaves únicas,
enquanto decisões importantes também geram eventos imutáveis. Ela fornece idempotência e
concorrência seguras sem substituir o armazenamento existente.

## Princípios obrigatórios

- Ausência de evidência é `unknown`, nunca confirmação.
- Campos conflitantes impedem associação automática.
- Caminho local, horário, ordem do lote e identificador da fonte não compõem identidade.
- Regras de domínio ou da banca não entram no motor semântico.
- A mesma entrada e a mesma versão de algoritmo produzem a mesma chave e a mesma decisão.
- Nenhuma confiança é apresentada sem evidências e motivo legíveis.
- Resoluções incertas falham fechadas e seguem para revisão.
- Dados antigos continuam legíveis; a migração não inventa metadados ausentes.

## Contratos do domínio

Os contratos são dataclasses congeladas ou equivalentes imutáveis, serializáveis em JSON
canônico. Listas semânticas são ordenadas e sem duplicatas antes da serialização.

### Campo semântico

`SemanticField` representa cada afirmação e contém:

- `status`: `known`, `unknown` ou `conflict`;
- `raw_values`: valores originais usados na decisão;
- `normalized_values`: valores canônicos, ordenados e sem duplicatas;
- `evidence`: referências para título, conteúdo, metadado declarado ou decisão humana;
- `method`: extrator determinístico, declaração de origem ou revisão humana;
- `confidence`: valor numérico apenas quando calculável;
- `reason`: explicação curta e estável;
- `algorithm_version`: versão da regra que produziu o campo.

Um campo `unknown` tem valores normalizados vazios e explica por que não pôde ser concluído.
Um `conflict` preserva todas as alternativas e suas evidências.

### Identidade semântica da prova

`ExamSemanticIdentity` contém campos semânticos para:

- banca;
- concurso ou processo seletivo;
- órgão ou instituição;
- ano de aplicação;
- cargos ou funções cobertos;
- etapa ou fase;
- turnos;
- tipos ou versões de caderno.

O papel do documento, como `exam` ou `answer_key`, e o estado do gabarito, como
`preliminary`, `definitive` ou `unknown`, não fazem parte da identidade da prova. Eles
qualificam uma versão de documento relacionada a essa identidade. Isso permite que prova e
gabarito compartilhem o mesmo núcleo semântico.

### Cobertura do gabarito

`AnswerKeyCoverage` usa conjuntos ordenados de cargos, etapas, turnos e tipos de caderno. Um
campo vazio significa cobertura desconhecida, não cobertura universal. Cobertura explícita
de múltiplos cargos ou tipos é permitida e cada correspondência precisa ser comprovada.

### Impressões digitais

`BinaryFingerprint` usa o SHA-256 dos bytes já validado pelo contrato normalizado.

`ContentFingerprint` usa SHA-256 de uma representação canônica do texto extraído. A
normalização:

1. aplica Unicode NFKC;
2. normaliza quebras de linha;
3. remove espaços no fim das linhas e comprime sequências horizontais de espaço;
4. elimina páginas ou linhas vazias redundantes;
5. preserva letras, números, pontuação, ordem das páginas e marcadores de questão;
6. não remove cabeçalhos, números, alternativas ou respostas com base em regras da banca.

Além do hash, o contrato guarda versão do normalizador, quantidade de páginas, tamanho do
texto canônico e hash por página. Os hashes por página permitem explicar onde ocorreu uma
mudança sem armazenar texto adicional na auditoria.

### Chave de identidade

A chave estável é o SHA-256 do JSON canônico dos valores conhecidos da identidade. O JSON
inclui nomes explícitos de campos, conjuntos ordenados e versão do esquema. Ele não inclui
confiança, evidências, caminhos ou datas.

Uma chave só é gerada quando o conjunto mínimo `banca + concurso + ano` é conhecido e não há
conflitos. Cargo, etapa, turno e tipo conhecidos participam da chave. Caso o conjunto mínimo
não seja atingido, a identidade permanece sem chave e o documento vai para exceção. Uma
decisão humana pode completar ou corrigir campos; isso cria novo evento e recalcula a chave,
sem reescrever a evidência histórica.

### Resolução de documento

`IdentityResolution` contém:

- resultado: `exact_duplicate`, `republication`, `new_version`, `new_identity` ou
  `uncertain`;
- chave de identidade e versão lógica escolhidas, quando existirem;
- SHA binário e impressão de conteúdo;
- versão predecessora, quando aplicável;
- evidências favoráveis e conflitos;
- regra, pontuação, limiar e margem usados;
- versão do algoritmo;
- ação tomada e motivo.

### Associação de prova e gabarito

`DocumentAssociationDecision` registra todos os candidatos avaliados, evidências compatíveis,
conflitos, pontuação, limiar, margem para o segundo colocado, candidato escolhido e motivo da
seleção ou recusa. O registro deve permitir reproduzir a decisão sem acessar o site de origem.

## Normalização semântica

Normalizadores vivem em um módulo central e puro. Eles tratam somente variações de escrita
gerais:

- caixa, acentos, espaços e pontuação estrutural;
- anos com quatro dígitos;
- prefixos gerais como `tipo`, `versão`, `turno`, `etapa` e `fase`;
- listas e intervalos explicitamente escritos;
- aliases cadastrados em um vocabulário versionado e neutro.

O vocabulário não pode conter URL, identificador de fonte ou condição exclusiva de uma banca.
Aliases não comprovados permanecem distintos. O valor original sempre é preservado.

Metadados declarados e texto do PDF são fontes independentes. Concordância aumenta a força
da evidência; divergência produz conflito. O título do arquivo sozinho é evidência fraca e não
pode resolver uma associação quando os demais campos estão desconhecidos.

## Persistência SQLite

A migração é aditiva e idempotente. Ela usa o mecanismo atual de inicialização, consulta
`PRAGMA table_info` antes de adicionar colunas e nunca apaga ou reescreve linhas antigas.

### `semantic_identities`

- `identity_key` como chave primária estável;
- `schema_version` e `algorithm_version`;
- `identity_json` canônico;
- `evidence_json` completo;
- `created_at` e `updated_at`.

### `document_versions`

- identificador interno;
- `identity_key` obrigatória para resolução automática;
- papel do documento e estado do gabarito;
- impressão digital de conteúdo e versão do normalizador;
- número ordinal dentro de identidade e papel;
- identificador da versão predecessora;
- resumo da resolução e datas.

Há unicidade para `identity_key + document_role + content_fingerprint`. Há também unicidade
para `identity_key + document_role + version_number`. Uma republicação usa a versão existente
e não cria nova linha.

### `document_observations`

- identificador determinístico da observação;
- versão lógica observada;
- SHA-256 binário, tamanho e caminho local disponível;
- método de entrada, fonte, URL, título e metadados declarados;
- primeira e última observação;
- contrato normalizado recebido.

O SHA-256 binário é único globalmente. Reencontrar o mesmo arquivo atualiza somente
`last_seen_at` e acrescenta um evento, sem criar documento ou tarefa. Para não perder
proveniência, origens adicionais do mesmo SHA são observações filhas ou evidências anexadas de
forma idempotente por uma chave natural canônica.

### `document_links`

- versão da prova e versão do gabarito;
- estado `active`, `superseded` ou `rejected`;
- decisão de associação completa em JSON;
- versão do algoritmo e datas;
- vínculo predecessor quando houver substituição.

Somente uma associação ativa por versão de prova e escopo de cobertura é permitida. Empates
e ambiguidades não criam vínculo ativo.

### `question_lineage`

- questão sucessora e questão predecessora;
- resultado `unchanged`, `changed`, `added` ou `removed`;
- impressão digital comparada e motivo;
- evento de preservação ou invalidação da decisão humana.

### `document_identity_events`

Eventos são imutáveis e registram `observed`, `exact_duplicate`, `republication`,
`version_created`, `identity_corrected`, `association_selected`, `association_rejected`,
`association_superseded`, `decision_carried_forward` e `decision_invalidated`. Cada evento
contém alvo, ator `system` ou humano, versão do algoritmo, dados mínimos da decisão e data.

### Compatibilidade com `documents`

`documents` recebe uma coluna anulável `document_version_id`. Linhas antigas permanecem com
valor nulo e são exibidas como identidade desconhecida. Uma rotina explícita de
reprocessamento pode identificar registros legados; a migração sozinha não tenta inferi-los.

## Fluxo de resolução

### 1. Validação e deduplicação binária

O pipeline valida existência, assinatura, tamanho e SHA-256 como faz hoje. Antes de criar uma
tarefa, abre uma transação curta e procura o SHA em `document_observations`.

Se encontrar, registra a nova ocorrência de origem de modo idempotente, atualiza a última
observação e retorna `exact_duplicate`. Nenhuma linha em `documents`, tarefa de interpretação
ou questão é criada.

### 2. Inspeção de conteúdo

Um SHA novo precisa ser lido para obter texto, impressão de conteúdo e evidências semânticas.
Essa inspeção pode usar a infraestrutura de tarefa existente, mas é uma etapa explícita e não
autoriza a criação de questões antes da resolução.

PDF vazio, ilegível, protegido sem senha válida ou sem evidência mínima termina em exceção.
Seu SHA e sua observação são preservados para impedir repetição infinita do mesmo erro.

### 3. Resolução da identidade e versão

- identidade nova e suficiente cria `semantic_identity` e a primeira `document_version`;
- mesma identidade, papel e impressão de conteúdo liga o novo SHA à versão existente como
  `republication` e encerra antes de interpretar questões;
- mesma identidade e papel com conteúdo diferente cria `new_version`, ligada à versão ativa
  anterior;
- conflito entre identidades ou evidência insuficiente produz `uncertain` e exceção;
- papéis diferentes dentro da mesma identidade seguem para associação, não são versões um
  do outro.

### 4. Interpretação

Somente uma versão lógica nova segue para extração de questões ou respostas. O documento
operacional aponta para essa versão. Republicações e duplicatas exatas reutilizam o resultado
da versão existente.

### 5. Associação conservadora

Uma prova considera somente gabaritos com identidade compatível. Para cada campo:

- valores conhecidos e incompatíveis eliminam o candidato;
- valores conhecidos e compatíveis acrescentam evidência;
- valor desconhecido não acrescenta nem retira pontos;
- cobertura múltipla é compatível quando contém o valor conhecido da prova;
- estado definitivo tem preferência somente depois da compatibilidade semântica.

A seleção automática exige pontuação mínima, campos fortes suficientes e margem mínima para
o segundo candidato. Os valores são constantes versionadas e calibradas por testes, nunca por
fonte. Empate, margem insuficiente, um único sinal fraco ou conflito conhecido gera exceção.

### 6. Gabarito definitivo

Um gabarito definitivo compatível não apaga o preliminar. Ele cria versão sucessora, marca o
vínculo anterior como `superseded`, cria novo vínculo ativo e reaplica respostas oficiais nas
provas cobertas. Questões cuja resposta oficial mudou voltam para pendência e recebem evento
de invalidação. Respostas inalteradas preservam a decisão editorial.

## Concorrência e idempotência

Resolução e gravação usam transação `BEGIN IMMEDIATE` com operações curtas. As restrições
únicas são a autoridade final. Se dois processos analisarem o mesmo arquivo ou conteúdo ao
mesmo tempo, um grava; o outro trata a violação de unicidade, recarrega o vencedor e retorna a
mesma resolução. Não se usa verificação seguida de gravação fora da transação.

Identificadores de observação e eventos idempotentes derivam de JSON canônico dos dados que
definem o fato. Repetir uma solicitação não cria eventos semanticamente duplicados.

## Preservação de decisões humanas

Ao criar versão sucessora de uma prova, questões são comparadas por número, tipo de caderno e
impressão digital normalizada já usada pelo fluxo editorial:

- questão idêntica recebe linhagem `unchanged`; a decisão humana pode ser copiada para a nova
  questão com evento `decision_carried_forward`;
- enunciado, alternativas, resposta oficial ou escopo alterado recebe `changed`; nenhuma
  aprovação é copiada e a questão fica pendente;
- questões novas e removidas recebem linhagem explícita;
- nenhuma decisão histórica é alterada ou apagada.

Uma correção manual da identidade não muda conteúdo editorial por si só. Ela preserva as
decisões, registra o evento e recalcula somente vínculos afetados. Se a correção mudar a
associação de gabarito, aplica-se a regra de comparação de respostas acima.

## Interface e relatórios

A interface desktop recebe somente informações de leitura necessárias à operação:

- identidade resumida ou `Identidade desconhecida`;
- papel, estado e versão lógica do documento;
- selo de duplicata exata, republicação, versão nova ou exceção;
- predecessor e sucessor quando existirem;
- gabarito associado, cobertura e estado;
- confiança acompanhada de evidências e motivo;
- versão do algoritmo e acesso ao histórico de eventos.

O relatório local inclui as mesmas referências e contagens separadas para arquivos observados,
versões lógicas, republicações, duplicatas exatas, associações e exceções. Nenhuma pontuação é
mostrada como percentual quando a identidade estiver desconhecida ou em conflito.

APIs existentes são ampliadas de modo compatível. Campos novos são opcionais para registros
legados e a interface não depende de migração retroativa.

## Tratamento de falhas

- falha antes de registrar a observação não cria estado parcial;
- falha após criar versão deixa a versão retomável, sem questões parcialmente aprovadas;
- erro em um PDF não impede os demais documentos do lote;
- reprocessamento usa a versão e o SHA persistidos, não um arquivo silenciosamente trocado;
- associação ambígua nunca aplica respostas;
- erros técnicos exibem código estável e motivo acionável, sem segredo ou rastreamento bruto
  na interface;
- a auditoria registra falha e retomada sem duplicar eventos.

## Migração e implantação

1. adicionar contratos puros e testes sem alterar o fluxo;
2. adicionar tabelas, índices e leitura compatível;
3. ativar registro de SHA e identidade para novas entradas;
4. ativar resolução de republicação e versões;
5. trocar associação atual pela decisão auditável;
6. ativar linhagem e preservação seletiva;
7. expor dados mínimos na interface e nos relatórios;
8. manter registros legados como desconhecidos até reprocessamento explícito.

Cada etapa deve manter o banco utilizável pela versão anterior quando isso for tecnicamente
possível. O recurso será protegido por uma versão de esquema e pode ser desativado sem apagar
as novas tabelas. A reversão volta a ler o fluxo antigo, preservando os registros para uma nova
tentativa.

## Estratégia de testes

Os testes são escritos antes de cada alteração e usam PDFs mínimos ou contratos sintéticos
determinísticos. O conjunto obrigatório cobre:

1. mesma prova importada duas vezes;
2. mesmo gabarito importado duas vezes;
3. mesmo SHA vindo de coleta e importação direta;
4. bytes diferentes com texto normalizado equivalente;
5. republicação com metadados de origem adicionais;
6. mesma identidade com questão alterada;
7. mesma identidade com questão adicionada ou removida;
8. gabarito preliminar seguido do definitivo;
9. gabarito definitivo repetido;
10. questão anulada na versão definitiva;
11. decisão humana preservada em questão idêntica;
12. decisão humana invalidada em questão alterada;
13. correção manual de identidade com auditoria;
14. identidade com campo desconhecido;
15. conflito entre ano declarado e ano extraído;
16. título fraco sem evidência suficiente;
17. empate entre dois gabaritos;
18. conflito conhecido de cargo, turno, etapa ou tipo;
19. gabarito único cobrindo vários cargos;
20. tipos 1, 2, 3 e 4 sem mistura de respostas;
21. dois processos registrando o mesmo SHA simultaneamente;
22. dois processos registrando republicações equivalentes;
23. retomada depois de falha durante a resolução;
24. migração de banco legado sem perda de dados;
25. interface e relatório exibindo identidade, evidência, versão e exceção.

Também permanecem obrigatórios os testes atuais de separação entre aquisição e interpretação,
integridade do PDF, revisão, exportação e regressão offline. Nenhum teste depende de rede,
relógio real, ordem não garantida ou identificador aleatório para validar igualdade.

## Verificação de conclusão

Antes do Pull Request:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\python.exe -m compileall src tests
```

Também devem ser executados o comando de regressão offline documentado pelo projeto, o smoke
test do aplicativo desktop e `git diff --check`. O Pull Request informa quantos testes foram
executados, quais cenários novos foram cobertos e qualquer limitação restante.

## Critérios de aceite

- as três classes principais, duplicata exata, republicação e versão nova, são distintas e
  reproduzíveis;
- nenhuma delas duplica questões indevidamente;
- identidade desconhecida ou conflitante nunca é apresentada como certeza;
- associações automáticas não ocorrem com conflito, empate ou evidência fraca;
- gabarito definitivo sucede o preliminar sem apagar histórico;
- decisões humanas só são preservadas mediante igualdade do conteúdo relevante;
- migração e concorrência não criam versões ou eventos duplicados;
- interface, relatório e auditoria explicam cada decisão;
- o motor não contém condição específica de banca ou origem;
- nenhum segredo, `.env` ou dado sensível é incluído em código, teste, log ou commit.
