# Automação local do KAD Collector

Ao abrir o aplicativo operacional, o Collector apenas restaura o estado do
banco. O botão **Iniciar processamento** inicia um processo local e
retomável. Ele prepara a identidade das provas, agrupa equivalências,
reaplica a classificação determinística e aprova somente representantes que
já passaram por todos os diagnósticos de importação. As ocorrências e cópias
continuam preservadas no SQLite.

Quando ainda faltam campos editoriais, a automação usa a mesma prévia e os
mesmos bloqueios de segurança da ação manual para iniciar o Qwen 3 8B. O Qwen
recebe uma unidade por grupo canônico; cópias não geram chamadas extras. Se o
Ollama ou a GPU não estiverem disponíveis, o banco fica em `waiting_qwen`, com
o erro visível e novas tentativas automáticas.

O cartão “Automação local” mostra a etapa atual. `completed` significa que o
Collector terminou as validações locais e deixou o resumo pronto para revisão
e exportação; não significa que um aplicativo externo importou o arquivo.

As decisões humanas são preservadas: uma questão pendente que já tenha
revisor ou observações nunca é aprovada automaticamente. O processo é
idempotente e não apaga documentos, ocorrências, grupos ou proveniências.

Depois de uma nova importação ou coleta, o cartão volta para **Iniciar
processamento** sem iniciar nada sozinho. Durante a execução, ele informa a
etapa, a última atualização, o erro e a próxima tentativa. Se o aplicativo
for fechado, a próxima abertura mostra **Retomar processamento** e continua
do estado persistido. A exportação continua sendo uma decisão manual.

## Sugestão manual de resposta

Questões **Sem gabarito oficial** exibem a ação **Sugerir resposta com Qwen**
na revisão. A chamada retorna alternativa, explicação, confiança, modelo e
data, sempre com o aviso de que não é oficial. O registro fica na tabela de
sugestões, separado da questão e do gabarito. O operador pode confirmar,
escolher outra alternativa ou rejeitar; nenhuma dessas ações altera
`correct_answer`, transforma a sugestão em gabarito oficial ou inclui a
questão automaticamente na exportação.
