# Regressão offline

Este diretório contém o pacote híbrido de regressão do KAD Collector. O Git guarda o
manifesto, quatro fixtures sintéticas fictícias e a matriz de cobertura. Os PDFs oficiais
ficam em `official/`, diretório ignorado pelo Git.

## Fonte oficial local

O manifesto usa dois documentos do [Acervo FUVEST 2026](https://www.fuvest.br/acervo-vestibular-2026/):

| Documento | Origem | Tamanho | SHA-256 |
|---|---|---:|---|
| Prova V1 da primeira fase | [PDF oficial](https://www.fuvest.br/wp-content/uploads/fuvest2026-fase1-prova-V1.pdf) | 8.019.288 bytes | `93b417ad6ea7e81a3b6adc46337920fd71c213306849846723f13583074f9025` |
| Gabarito retificado da primeira fase | [PDF oficial](https://www.fuvest.br/wp-content/uploads/fuvest2026-fase1-gabarito.pdf) | 218.795 bytes | `a9e1084a125fa35bcefb875610328c0a8cb9b1a298880f2f0c75b928bd860e8e` |

O pacote lê caminho local, URL HTTPS de origem, formato, tamanho e SHA-256. A regressão
extrai contagem de páginas e questões da prova, além das grades V1 a V4 do gabarito. O
relatório contém somente contagens e resumos SHA-256 das estruturas extraídas. Ele não copia
as respostas oficiais.

A preparação baixa no máximo os dois PDFs declarados. Ela faz uma requisição por documento,
sem descoberta, paginação ou acesso ao banco. O script mantém um arquivo local válido e
substitui um arquivo divergente somente depois que o novo download passa por todas as
verificações. O acervo registra o gabarito como retificado em 26 de novembro de 2025.

O manifesto registra `robots_policy = "ignore"` e `crawl_delay_policy = "ignore"` para os
dois arquivos. A decisão administrativa de 18 de agosto de 2026 já consta em
`config/sources.official.toml`; o relatório repete as políticas e a referência da decisão.
Uma nova fixture usa `enforce` por padrão. `observe` ou `ignore` exigem uma decisão explícita
em `policy_basis`.

## Preparação local

Instale o projeto e execute:

```powershell
.venv\Scripts\python.exe scripts\prepare_regression_fixtures.py
```

O script aceita `--manifest CAMINHO` quando você precisa validar uma cópia de trabalho. Ele
encerra com código 2 se faltar cadastro, se a origem não usar HTTPS ou se tamanho, hash ou
assinatura PDF divergirem.

O script de preparação é o único comando deste pacote que usa rede. Ele não contorna
autenticação, CAPTCHA, `robots.txt` ou bloqueios. As duas URLs apontam direto para documentos
públicos cadastrados na configuração oficial do Collector. O script rejeita redirects; uma
mudança da URL final exige atualização e revisão do manifesto.

## Comando único

Depois da preparação, execute:

```powershell
.venv\Scripts\kad-collector.exe regression
```

O runner bloqueia conexões, não abre banco e não grava em `data/`. Ele valida todas as
fixtures, executa cada caso suportado duas vezes e compara os resultados. O relatório fica em
`tests/regression/report.json`, fora do Git. Use `--report CAMINHO` para escolher outro local.

O comando retorna 0 quando todos os casos suportados passam. Ausência de fixture, quebra de
integridade, formato inválido, resultado diferente ou não determinístico produz código 2.
Casos planejados não afetam o código de saída e aparecem como `planned`, nunca como `passed`.

## Relatório

O JSON registra:

- hash e caminho do manifesto usado;
- identificador, tipo, tamanho e SHA-256 de cada fixture;
- estado e resultado resumido de cada caso;
- erro e expectativa do caso quando uma regressão suportada falha;
- linha de cobertura para cada requisito;
- totais separados de casos suportados aprovados e lacunas planejadas.

`generated_at` identifica a execução. Os demais campos permanecem iguais quando código,
manifesto, runtime e fixtures não mudam.

## Manutenção

1. Confira o aviso e o documento no acervo oficial antes de aceitar uma mudança.
2. Atualize URL, tamanho e SHA-256 no manifesto somente depois da revisão humana.
3. Execute o script de preparação e o comando de regressão.
4. Revise a mudança dos resumos estruturais. Não copie respostas do PDF para fixtures
   sintéticas e não peça a uma IA para completar respostas.
5. Execute `unittest`, Ruff e mypy antes do commit.

Uma nova fonte precisa de origem, campos coletados, limite de requisições e forma de execução
documentados. PDFs com licença, política ou tamanho incompatível com o Git continuam apenas
na máquina do mantenedor.

Consulte [COVERAGE.md](COVERAGE.md) para a matriz vigente.
