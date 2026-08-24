# Provedores de IA da classificação canônica

## Estado operacional

As integrações existem, mas nenhuma chamada externa ocorre por padrão. O operador precisa
combinar `--apply`, `--enable-ai` e `--provider`. Sem `--enable-ai`, escolher um provedor na
linha de comando não instancia um cliente nem consome tokens.

O classificador determinístico sempre roda antes da IA. O provedor recebe somente os campos
editoriais ainda ausentes, o enunciado, as alternativas, os campos editoriais conhecidos e as
opções fechadas da taxonomia. Gabarito, identidade oficial, intervalos e dados administrativos
não fazem parte do payload.

## Configuração

| Provedor | CLI | Modelo padrão | Chave | Endpoint |
| --- | --- | --- | --- | --- |
| OpenAI | `openai` | `gpt-5.6-terra` | `OPENAI_API_KEY` | gerenciado pelo SDK |
| Google | `gemini` | `gemini-3.7-flash` | `GEMINI_API_KEY` | `GEMINI_BASE_URL` |
| Alibaba Cloud | `qwen` | `qwen3.7-plus` | `DASHSCOPE_API_KEY` ou `QWEN_API_KEY` | `QWEN_BASE_URL` |
| DeepSeek | `deepseek` | `deepseek-v4-pro` | `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` |

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

- OpenAI usa Responses API, JSON Schema estrito, `store=false` e raciocínio baixo.
- Gemini usa a interface OpenAI-compatible, Structured Outputs com Pydantic e raciocínio baixo.
- Qwen usa a interface OpenAI-compatible, JSON mode e `enable_thinking=false`.
- DeepSeek usa a interface OpenAI-compatible, JSON mode e `thinking.type=disabled`.

Qwen e DeepSeek garantem JSON válido no modo configurado, mas a validação local continua sendo
a autoridade. Propriedades extras, campos não solicitados, taxonomia inválida, baixa confiança
ou resposta vazia são rejeitados e encaminhados para revisão. O SDK repete falhas transitórias
no máximo duas vezes; o coletor não alterna silenciosamente de provedor.

## Fontes técnicas

- OpenAI: <https://developers.openai.com/api/docs/models/gpt-5.6-terra>
- Gemini OpenAI compatibility: <https://ai.google.dev/gemini-api/docs/openai>
- Gemini 3.7 Flash: <https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash>
- Qwen Structured Output: <https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output>
- Qwen OpenAI-compatible API: <https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions>
- DeepSeek JSON Output: <https://api-docs.deepseek.com/guides/json_mode/>
- DeepSeek Thinking Mode: <https://api-docs.deepseek.com/guides/thinking_mode/>

Os testes automatizados usam clientes falsos e não fazem chamadas de rede. A ativação real deve
começar com limite pequeno, banco de avaliação e revisão humana dos resultados.
