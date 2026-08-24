# IA local com Ollama

Este fluxo usa o Ollama no mesmo computador do Collector. Ele não usa n8n, não chama Gemini,
Qwen Cloud ou DeepSeek e não troca para um provedor pago quando o serviço local falha.

A integração está desligada por padrão. Adicionar as variáveis do `.env.example`, preparar um
manifesto ou gerar um relatório não inicia inferência.

## Modelos fixados

O benchmark aceita estas tags exatas:

| Modelo | Quantização esperada | Download aproximado |
| --- | --- | ---: |
| `qwen3.5:9b-q4_K_M` | `Q4_K_M` | 6,6 GB |
| `qwen3:14b-q4_K_M` | `Q4_K_M` | 9,3 GB |
| `gemma3:12b-it-qat` | `Q4_0` | 8,9 GB |

O total aproximado é 25 GB. O tamanho informado por `/api/tags` é a referência para a
instalação real. O preflight exige 35 GiB livres quando falta algum modelo.

Cada modelo é executado sozinho, com concorrência 1, temperatura 0, contexto 4096, resposta
curta e thinking desativado. O contexto reduzido deixa margem na VRAM de 16 GB da RX 9060 XT.
O benchmark descarrega um modelo antes de iniciar o seguinte.

## Windows e GPU

Instale o Ollama pelo procedimento oficial para
[Windows](https://docs.ollama.com/windows) e mantenha o driver AMD atualizado. O nome da placa
não prova que houve offload. O suporte depende da versão do Ollama e do driver instalados.

O preflight consulta `/api/ps` e executa `ollama ps`. O probe só passa quando a coluna
`PROCESSOR` informa `100% GPU` e `size_vram` cobre o tamanho carregado. Uso parcial de CPU ou
ausência dessas métricas bloqueia o benchmark. A documentação de referência está em
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
$env:OLLAMA_MODEL = "qwen3:14b-q4_K_M"
```

Nenhuma chave de API é necessária.

## Preflight

Com o Ollama em execução, faça primeiro a inspeção passiva:

```powershell
kad-collector preflight-ollama-ai `
  --report data\benchmarks\local\ollama-ai\preflight.json
```

Esse comando consulta versão e modelos, verifica espaço livre e escreve os comandos de pull no
relatório. Ele não baixa modelos e não envia prompt. Se faltarem tags, revise os tamanhos e,
somente depois de autorizar o download, execute manualmente os comandos informados, por exemplo:

```powershell
ollama pull qwen3.5:9b-q4_K_M
ollama pull qwen3:14b-q4_K_M
ollama pull gemma3:12b-it-qat
```

Depois dos downloads, repita a inspeção. Copie o `probeId` do relatório atualizado e autorize
três gerações curtas, uma por modelo:

```powershell
kad-collector preflight-ollama-ai `
  --report data\benchmarks\local\ollama-ai\preflight.json `
  --probe-models `
  --approved-probe-id ollama-probe-...
```

O identificador é derivado da versão, digests, quantizações e estado do preflight. Alterar esses
dados invalida a aprovação.

## Classificação comum

O Ollama também pode preencher campos ausentes fora do benchmark:

```powershell
kad-collector classify-canonical-questions `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22 `
  --apply `
  --enable-ai `
  --provider ollama `
  --model qwen3:14b-q4_K_M `
  --run-id classification-rfb22-ollama `
  --limit 100
```

Sem `--enable-ai`, o cliente não é criado. O motor determinístico roda primeiro. O modelo recebe
uma chamada por questão, somente com os campos ausentes entre disciplina, matéria, assunto e
nível. Resposta, gabarito, identidade, dificuldade e explicação ficam fora do contrato.

Cada questão aplicada é confirmada antes da próxima. Se o Ollama parar ou o computador for
desligado, execute novamente o mesmo comando com o mesmo `run-id`. Itens concluídos não são
reenviados. `Ctrl+C` preserva o último checkpoint.

## Preparação do benchmark

O bundle canônico de 200 questões continua sendo a fonte dos textos e referências. Depois de
um probe válido, fixe os modelos, digests, parâmetros e amostra:

```powershell
kad-collector prepare-ollama-ai-benchmark `
  --canonical-bundle data\benchmarks\local\canonical-ai\bundle.json `
  --preflight data\benchmarks\local\ollama-ai\preflight.json `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v1.json
```

Essa etapa não cria provedores. O manifesto versionável contém IDs, fingerprints, campos
ocultos, referências, modelos, digests, quantizações, parâmetros e versão do Ollama. Enunciados
e alternativas ficam somente no bundle local ignorado pelo Git.

## Smoke test

Revise o manifesto e copie seu `benchmarkId`. O smoke usa as mesmas dez questões para os três
modelos:

```powershell
kad-collector run-ollama-ai-benchmark `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v1.json `
  --preflight data\benchmarks\local\ollama-ai\preflight.json `
  --checkpoint data\benchmarks\local\ollama-ai\checkpoint.json `
  --phase smoke `
  --approved-benchmark-id ollama-local-... `
  --max-new-calls 30
```

Há um aquecimento registrado por modelo antes das 30 chamadas medidas. Portanto, um smoke novo
faz três aquecimentos e no máximo 30 medições. Não há retentativa automática. Resposta inválida
é registrada como falha; indisponibilidade deixa a combinação atual pendente. Repetir o comando
pula todos os pares modelo/questão já gravados.

Gere o relatório agregado:

```powershell
kad-collector report-ollama-ai-benchmark `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v1.json `
  --checkpoint data\benchmarks\local\ollama-ai\checkpoint.json `
  --report docs\benchmarks\ollama-ai-smoke-results.v1.json
```

O relatório contém precisão por campo e conjunta, validade de JSON e schema, campos proibidos,
valores fora da taxonomia, cobertura, revisão, latência, tokens, tokens por segundo, carga,
VRAM, falhas, interrupções e comparações pareadas. Ele não contém enunciados, alternativas,
respostas brutas, erros textuais ou caminhos locais.

## Fase completa

A fase `full` permanece bloqueada até existirem 30 registros `completed` no smoke. Depois de
revisar o relatório e autorizar outra execução, o limite máximo é 570 chamadas medidas:

```powershell
kad-collector run-ollama-ai-benchmark `
  --local-bundle data\benchmarks\local\ollama-ai\bundle.json `
  --manifest docs\benchmarks\ollama-ai-manifest.v1.json `
  --preflight data\benchmarks\local\ollama-ai\preflight.json `
  --checkpoint data\benchmarks\local\ollama-ai\checkpoint.json `
  --phase full `
  --approved-benchmark-id ollama-local-... `
  --max-new-calls 570
```

`--max-new-calls` pode ser menor para dividir o trabalho entre os períodos em que o computador
fica ligado. O checkpoint continua sendo escrito após cada medição. O código desta PR prepara
essa fase, mas não a executa.

## Arquivos locais

Mantenha em `data/benchmarks/local/`:

- bundle com texto das questões;
- checkpoint com respostas brutas;
- preflight operacional;
- logs detalhados.

Esse diretório já está no `.gitignore`. Somente manifesto sem texto, relatório agregado,
documentação e testes devem entrar no Git.
