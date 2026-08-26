# IA local com Ollama

Este fluxo usa o Ollama no mesmo computador do Collector. Ele não usa n8n, não chama Gemini,
Qwen Cloud ou DeepSeek e não troca para um provedor pago quando o serviço local falha.

A integração está desligada por padrão. Adicionar as variáveis do `.env.example`, preparar um
manifesto ou gerar um relatório não inicia inferência.

O benchmark local v3 usa 175 referências semânticas selecionadas pela auditoria v3. A reconstrução
do banco encontrou 170 das referências anteriores pelo fingerprint do conteúdo. Cinco referências
históricas não apareceram na nova coleta. A revisão v3 acrescentou 27 candidatas confirmadas e o
sanitizador escolheu 175 itens não triviais. O benchmark pago mantém seu contrato separado.
Manifestos e checkpoints locais anteriores são incompatíveis com esta versão.

## Modelos fixados

O benchmark aceita estas tags exatas:

| Modelo | Quantização esperada | Download aproximado |
| --- | --- | ---: |
| `qwen3:8b` | `Q4_K_M` | 5,2 GB |
| `qwen3:14b` | `Q4_K_M` | 9,3 GB |

O total aproximado é 14,5 GB. O tamanho informado por `/api/tags` é a referência para a
instalação real. O preflight exige 35 GiB livres quando falta algum modelo.

Cada modelo é executado sozinho, com concorrência 1, temperatura 0, contexto 4096, resposta
curta e thinking desativado. O contexto reduzido deixa margem na VRAM de 16 GB da RX 9060 XT.
O benchmark descarrega um modelo antes de iniciar o seguinte.

## Windows e GPU

Instale o Ollama pelo procedimento oficial para
[Windows](https://docs.ollama.com/windows) e mantenha o driver AMD atualizado. O nome da placa
não prova que houve offload. O suporte depende da versão do Ollama e do driver instalados.

O preflight consulta `/api/ps` e executa `ollama ps` contra o mesmo `OLLAMA_BASE_URL`. O probe
só passa quando a coluna `PROCESSOR` informa exatamente `100% GPU`. `size` e `size_vram` ficam
como telemetria; o critério operacional é a coluna do próprio Ollama. Uso parcial de CPU
bloqueia o benchmark. A documentação de referência está em
[GPU](https://docs.ollama.com/gpu) e
[Troubleshooting](https://docs.ollama.com/troubleshooting).

No Windows, o log fica em `%LOCALAPPDATA%\Ollama\server.log`. Quando existe uma linha recente
no formato `offloaded X/Y layers`, o relatório registra os dois números. Se a linha não existir,
o campo permanece nulo com o motivo; o Collector não estima a quantidade de camadas.

## Limite de rede

`OLLAMA_BASE_URL` aceita apenas HTTP em `127.0.0.1`, `localhost` ou `::1`. Endereços da rede
local, credenciais na URL, query string e caminhos adicionais são rejeitados. O cliente também
ignora proxies do ambiente. Não configure o Ollama para escutar em `0.0.0.0` para este fluxo.

Configuração opcional da sessão:

```powershell
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen3:14b"
```

Nenhuma chave de API é necessária.

## Restaurar a cópia local pelo Supabase

O benchmark precisa de um SQLite com o conteúdo e a proveniência das referências. Depois de uma
formatação do Windows, você pode recriar esse recorte pelo histórico de importações editoriais do
Supabase. Instale a dependência `database` e defina a conexão PostgreSQL na sessão atual:

```powershell
python -m pip install -e ".[database,dev]"
$env:KAD_DATABASE_URL = "postgresql://..."
```

Use a URL de conexão do banco fornecida pelo Supabase. Não salve essa URL em arquivo versionado,
log ou relatório. O papel conectado precisa de `SELECT` em
`private.editorial_import_items`; o comando não precisa de permissão de escrita.

Confira o plano sem abrir conexão:

```powershell
kad-collector export-supabase-benchmark
```

A prévia deve informar 175 referências. Execute a exportação depois de configurar a variável:

```powershell
kad-collector export-supabase-benchmark --execute
```

O comando consulta os registros `question` importados, seleciona os IDs marcados como
`agent_reviewed_reference` e cria
`data\benchmarks\local\canonical-ai\collector-copy.sqlite3`. A transação remota usa modo somente
leitura. A exportação exige número, páginas, URL oficial e SHA-256 de cada ocorrência. Ela também
recalcula o fingerprint do enunciado e das alternativas. Falta de registro, mudança de conteúdo,
URL fora de `conhecimento.fgv.br` ou proveniência incompleta interrompe a execução. O comando só
substitui a cópia anterior depois que o leitor do benchmark aceita todas as 175 referências.

O arquivo contém enunciados e alternativas. Ele fica sob `data/benchmarks/local/`, que o Git
ignora. A exportação não recupera as 25 referências excluídas e não inicia o Ollama ou qualquer
provedor externo.

## Preflight

Com o Ollama em execução, faça primeiro a inspeção passiva:

```powershell
kad-collector preflight-ollama-ai `
  --report data\benchmarks\local\ollama-ai\preflight.json
```

Esse comando consulta versão e modelos e verifica espaço livre. Os comandos de pull só aparecem
no relatório quando há pelo menos 35 GiB livres. Ele não baixa modelos e não envia prompt. Se
faltarem tags, revise os tamanhos e, somente depois de autorizar o download, execute manualmente
os comandos informados, por exemplo:

```powershell
ollama pull qwen3:8b
ollama pull qwen3:14b
```

Depois dos downloads, repita a inspeção. Copie o `probeId` do relatório atualizado e autorize
duas gerações curtas, uma por modelo:

```powershell
kad-collector preflight-ollama-ai `
  --report data\benchmarks\local\ollama-ai\preflight.json `
  --probe-models `
  --approved-probe-id ollama-probe-...
```

O identificador é derivado da versão, digests, quantizações e estado do preflight. O relatório
do probe também recebe um fingerprint próprio. Alterar qualquer desses dados invalida a
aprovação.

## Classificação comum

### Pelo aplicativo desktop

Na tela principal, use **Classificar pendentes com Qwen 8B**. A primeira tela é uma prévia
passiva e não chama `/api/chat`: ela separa questões brutas, questões canônicas, elegíveis,
já completas, resolvidas pelas regras locais e dependentes do Qwen. Campos ausentes só são
contabilizados quando existem questões elegíveis para análise. Se a preparação canônica ainda
não ocorreu, a interface explica o bloqueio e mantém a confirmação desativada. O lote começa
em 25 questões e aceita limites entre 1 e 250.

O contrato do desktop é fixo e não segue `OLLAMA_MODEL`: endpoint
`http://127.0.0.1:11434`, modelo `qwen3:8b`, quantização `Q4_K_M` e digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`. Tag, digest,
quantização ou endpoint divergentes bloqueiam a confirmação. A prévia também recusa outro
modelo carregado. Ela não baixa, atualiza ou troca modelos.

Somente **Confirmar e iniciar** autoriza o aquecimento local. O Collector verifica
`/api/ps` e `ollama ps`, exige contexto 4096 e `100% GPU` antes da primeira questão. A interface
permanece disponível e mostra apenas totais: processadas, restantes, chamadas de IA, sugestões
aceitas, itens enviados à revisão e falhas. Ela nunca expõe enunciados ou respostas nesse painel.

**Pausar** termina a questão corrente, grava o checkpoint e descarrega o modelo antes de parar.
**Retomar** usa o mesmo `runId` e pula itens concluídos, inclusive depois de reiniciar o
aplicativo. Perda do Ollama pausa o lote; digest, quantização, endpoint ou GPU incompatíveis
bloqueiam a execução. Respostas inválidas ou sugestões abaixo da confiança mínima seguem para a
fila canônica de revisão. Não existe retentativa automática, carregamento do 14B ou fallback
para Gemini, Qwen Cloud ou DeepSeek.

O fluxo só considera grupos canônicos confirmados, atuais e não bloqueados/rejeitados. Regras
determinísticas vêm primeiro. A IA pode preencher somente disciplina, matéria, assunto e nível
que ainda estejam vazios. Resposta oficial, estado do gabarito, vínculo semântico, dificuldade,
explicação e qualquer valor existente ou decidido por uma pessoa permanecem intactos.

### Pela linha de comando

O Ollama também pode preencher campos ausentes fora do benchmark:

```powershell
kad-collector classify-canonical-questions `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22 `
  --apply `
  --enable-ai `
  --provider ollama `
  --model qwen3:14b `
  --run-id classification-rfb22-ollama `
  --limit 100
```

Sem `--enable-ai`, o cliente não é criado. O motor determinístico roda primeiro. O modelo recebe
uma chamada por questão, somente com os campos ausentes entre disciplina, matéria, assunto e
nível. Resposta, gabarito, identidade, dificuldade e explicação ficam fora do contrato.

Cada questão aplicada é confirmada antes da próxima. Falta do modelo, perda do serviço ou falha
do requisito de GPU pausam a execução sem mandar a questão para revisão. Se o Ollama parar ou o
computador for desligado, execute novamente o mesmo comando com o mesmo `run-id`. Itens
concluídos não são reenviados. `Ctrl+C` preserva o último checkpoint.

## Preparação do benchmark

O bundle canônico local usa as 175 referências selecionadas pela auditoria v3. Prepare-o com uma cópia
do SQLite operacional e sem chamar provedores externos:

```powershell
kad-collector prepare-canonical-ai-benchmark `
  --database C:\caminho\para\copia\collector.sqlite3 `
  --reference-review docs\benchmarks\canonical-ai-reference-review.v3.json `
  --local-bundle data\benchmarks\local\canonical-ai\bundle.json `
  --manifest docs\benchmarks\canonical-ai-manifest.v3.json `
  --report docs\benchmarks\canonical-ai-preflight.v3.json `
  --sample-size 175
```

Depois de um probe válido, fixe os modelos, digests, parâmetros e amostra:

```powershell
kad-collector prepare-ollama-ai-benchmark `
  --canonical-bundle data\benchmarks\local\canonical-ai\bundle.json `
  --preflight data\benchmarks\local\ollama-ai\preflight.json `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v3.json
```

Essa etapa não cria provedores. O manifesto versionável contém IDs, fingerprints da amostra e
do conteúdo bruto, campos ocultos, referências, modelos, digests, quantizações, parâmetros e
versão do Ollama. Enunciados e alternativas ficam somente no bundle local ignorado pelo Git.

## Smoke test

Revise o manifesto e copie seu `benchmarkId`. O smoke usa as mesmas dez questões para os dois
modelos:

```powershell
kad-collector run-ollama-ai-benchmark `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v3.json `
  --preflight data\benchmarks\local\ollama-ai\preflight.json `
  --checkpoint data\benchmarks\local\ollama-ai\checkpoint.json `
  --phase smoke `
  --approved-benchmark-id ollama-local-... `
  --max-new-calls 20
```

Antes da primeira inferência, a execução confere novamente endpoint, versão, tags, digests e
quantizações no Ollama vivo. Há um aquecimento registrado por modelo antes das 20 chamadas
medidas. Portanto, um smoke novo faz dois aquecimentos e no máximo 20 medições. Não há
retentativa automática. Resposta inválida é registrada como falha; indisponibilidade, modelo
ausente ou perda do requisito de GPU pausam antes de gravar a combinação atual. Repetir o
comando pula todos os pares modelo/questão já gravados. Se um unload falhar, o checkpoint
mantém a pendência e tenta descarregar esse modelo antes de qualquer inferência seguinte. A
fase full e a recomendação positiva permanecem bloqueadas enquanto houver limpeza pendente.

Gere o relatório agregado:

```powershell
kad-collector report-ollama-ai-benchmark `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v3.json `
  --checkpoint data\benchmarks\local\ollama-ai\checkpoint.json `
  --report docs\benchmarks\ollama-ai-smoke-results.v3.json
```

O relatório contém precisão por campo e conjunta, validade de JSON e schema, códigos explícitos
de validação, campos proibidos,
valores fora da taxonomia, cobertura, revisão, latência, tokens, tokens por segundo, carga,
VRAM, falhas, interrupções e comparações pareadas. Ele não contém enunciados, alternativas,
respostas brutas, erros textuais ou caminhos locais.

### Smoke v3 executado

O smoke `ollama-local-c39c70edbf6871aa` concluiu 20 chamadas em 25 de agosto de 2026. Cada modelo
respondeu às mesmas dez questões com JSON válido, sem falhas, campos proibidos ou valores fora
da taxonomia. Os dois modelos acertaram todos os campos pedidos em 7 de 10 questões. O 8B teve
latência mediana de 3.064 ms e pico de VRAM de 5.578.204.118 bytes. O 14B teve latência mediana
de 5.234 ms e pico de VRAM de 9.646.353.939 bytes. O preflight e as medições registraram
`100% GPU`. Consulte
[`ollama-ai-smoke-results.v3.json`](benchmarks/ollama-ai-smoke-results.v3.json).

## Fase completa

A fase `full` permanece bloqueada até existirem 20 registros `completed` no smoke. Depois de
revisar o relatório e autorizar outra execução, ela acrescenta 330 chamadas medidas. Com o smoke,
o resultado final contém 350 combinações, 175 por modelo:

```powershell
kad-collector run-ollama-ai-benchmark `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v3.json `
  --preflight data\benchmarks\local\ollama-ai\preflight.json `
  --checkpoint data\benchmarks\local\ollama-ai\checkpoint.json `
  --phase full `
  --approved-benchmark-id ollama-local-... `
  --max-new-calls 330
```

`--max-new-calls` pode ser menor para dividir o trabalho entre os períodos em que o computador
fica ligado. O checkpoint continua sendo escrito após cada medição.

### Resultado completo v3

A fase `full` acrescentou 330 chamadas em 25 de agosto de 2026. O checkpoint terminou com 350
resultados, 175 por modelo, sem falhas ou interrupções. O 8B acertou todos os campos pedidos em
137 questões, 78,286%. O 14B acertou 122, 69,714%. Na comparação pareada, o 8B venceu 21
questões, o 14B venceu seis e 148 empataram. O teste exato de McNemar calculou `p = 0,005925`.

O 8B registrou latência mediana de 3.523 ms e pico de VRAM de 5.578.204.118 bytes. O 14B
registrou 6.825 ms e 9.646.353.939 bytes. Os dois modelos produziram JSON válido em 100% das
chamadas e usaram `100% GPU`. O relatório recomenda `qwen3:8b`. Consulte
[`ollama-ai-results.v3.json`](benchmarks/ollama-ai-results.v3.json).

O gerador só escolhe um vencedor quando o checkpoint contém as 350 combinações e o teste pareado
atinge `p < 0,05`. Durante o smoke, ou diante de um resultado inconclusivo, ele mantém a decisão
em aberto.

## Arquivos locais

Mantenha em `data/benchmarks/local/`:

- bundle com texto das questões;
- checkpoint com respostas brutas;
- preflight operacional;
- logs detalhados.

O Collector rejeita esses artefatos fora dessa raiz, inclusive em outro repositório ou pasta
sincronizada. Testes automatizados só podem usar uma raiz temporária quando ela é injetada
explicitamente no código de teste. Esse diretório já está no `.gitignore`. Somente manifesto
sem texto, relatório agregado, documentação e testes devem entrar no Git.
