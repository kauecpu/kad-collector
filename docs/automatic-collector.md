# Automação local do KAD Collector

Ao abrir o aplicativo operacional, o Collector inicia um processo local e
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
