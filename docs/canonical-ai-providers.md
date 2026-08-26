# Provedores de IA da classificação canônica

## Estado operacional

As integrações existem, mas nenhuma chamada externa ocorre por padrão. O operador precisa
combinar `--apply`, `--enable-ai` e `--provider`. Sem `--enable-ai`, escolher um provedor na
linha de comando não instancia um cliente nem consome tokens.

O classificador determinístico sempre roda antes da IA. O provedor recebe o conteúdo derivado
sanitizado, os campos conhecidos e os caminhos taxonômicos compatíveis, com ID e palavras-chave.
Ele escolhe no máximo um caminho e, quando necessário, um dos três níveis editoriais. Um único
caminho é aplicado localmente sem chamada. Gabarito, identidade oficial, intervalos e dados
administrativos não fazem parte do payload.

## Configuração

| Provedor | CLI | Modelo padrão | Chave | Endpoint |
| --- | --- | --- | --- | --- |
| Google | `gemini` | `gemini-3.7-flash` | `GEMINI_API_KEY` | `GEMINI_BASE_URL` |
| Alibaba Cloud | `qwen` | `qwen3.7-plus` | `DASHSCOPE_API_KEY` ou `QWEN_API_KEY` | `QWEN_BASE_URL` |
| DeepSeek | `deepseek` | `deepseek-v4-pro` | `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` |
| Ollama local | `ollama` | nenhum; informe a tag | nenhuma | `OLLAMA_BASE_URL` |

Os valores vazios e os defaults documentados estão em `.env.example`. Defina segredos apenas
na sessão ou no gerenciador de segredos do ambiente. O endpoint padrão do Qwen aponta para US
(Virginia); a chave precisa pertencer à mesma região. Para outra região, copie o endpoint
OpenAI-compatible mostrado no workspace do Alibaba Cloud para `QWEN_BASE_URL`.

Exemplo de configuração, sem executar chamadas:

```powershell
$env:GEMINI_API_KEY = "<chave>"
$env:DASHSCOPE_API_KEY = "<chave-da-regiao-us>"
$env:DEEPSEEK_API_KEY = "<chave>"
```

Exemplo futuro de ativação controlada do Gemini:

```powershell
kad-collector classify-canonical-questions `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --contest RFB22 `
  --apply `
  --enable-ai `
  --provider gemini `
  --run-id classification-rfb22-gemini-eval `
  --limit 20 `
  --report data/reports/classification-rfb22-gemini-eval.json
```

Troque apenas o provedor e o `run-id` para preparar avaliações separadas. Não reutilize um
`run-id` entre modelos, porque a configuração do lote faz parte da auditoria.

## Contrato por provedor

- Gemini usa a interface OpenAI-compatible, Structured Outputs com Pydantic e raciocínio baixo.
- Qwen usa a interface OpenAI-compatible, JSON mode e `enable_thinking=false`.
- DeepSeek usa a interface OpenAI-compatible, JSON mode e `thinking.type=disabled`.
- Ollama usa `POST /api/chat`, JSON Schema nativo, `think=false`, contexto 4096 e temperatura
  zero. O endpoint aceita somente `127.0.0.1`, `localhost` ou `::1` por HTTP.

Qwen e DeepSeek solicitam JSON válido no modo configurado, mas a validação local continua sendo
a autoridade. Propriedades extras, chaves repetidas, campos não solicitados, caminho inválido, baixa confiança
ou resposta vazia são rejeitados e encaminhados para revisão. O SDK repete falhas transitórias
no máximo duas vezes; o coletor não alterna silenciosamente de provedor.

O adaptador Ollama não repete chamadas. Se o serviço local parar, o Collector desfaz apenas a
questão em andamento, marca a execução como pausada e preserva os itens já confirmados. Repetir
o mesmo comando e o mesmo `run-id` continua do checkpoint. Indisponibilidade não cria item de
revisão; conteúdo inválido continua seguindo a regra normal de revisão.

Exemplo local, depois de instalar e validar uma das tags:

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

## Fontes técnicas

- Gemini OpenAI compatibility: <https://ai.google.dev/gemini-api/docs/openai>
- Gemini 3.7 Flash: <https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash>
- Qwen Structured Output: <https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output>
- Qwen OpenAI-compatible API: <https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions>
- DeepSeek JSON Output: <https://api-docs.deepseek.com/guides/json_mode/>
- DeepSeek Thinking Mode: <https://api-docs.deepseek.com/guides/thinking_mode/>
- Ollama Chat API: <https://docs.ollama.com/api/chat>
- Ollama Structured Outputs: <https://docs.ollama.com/capabilities/structured-outputs>
- Ollama GPU: <https://docs.ollama.com/gpu>

Os testes automatizados usam clientes falsos e não fazem chamadas de rede. A ativação real deve
começar com limite pequeno, banco de avaliação e revisão humana dos resultados.

O executor e as travas de custo do benchmark estão descritos em
[`canonical-ai-benchmark.md`](canonical-ai-benchmark.md). O benchmark desativa retentativas
automáticas sem alterar as duas retentativas usadas pelo fluxo normal.

## Campos avaliados

O benchmark compara Gemini, Qwen e DeepSeek somente nos quatro campos taxonômicos:
`discipline`, `matter`, `subject` e `level`. Dificuldade e explicação são campos editoriais
opcionais. A ausência deles não aciona IA, não altera a completude e não aumenta a fila de
revisão. Cada questão incompleta gera no máximo uma chamada, contendo todos os campos
taxonômicos ausentes.

O preparo, o preflight e o benchmark dos modelos locais estão em
[`ollama-local-ai.md`](ollama-local-ai.md). Esse fluxo não usa Gemini, Qwen Cloud ou DeepSeek.
