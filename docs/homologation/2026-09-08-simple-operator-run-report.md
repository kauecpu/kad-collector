# Homologação da operação simples do KAD Collector

Data: 2026-09-08

## Escopo

Foi validado o comando único por órgão, banca e período, desde a seleção das fontes
cadastradas até a geração do pacote local de revisão. Nenhuma questão foi publicada no KAD.

## Resultado

| Cenário | PDFs | Provas processadas | Gabaritos usados | Esperadas | Detectadas | Prontas | Quarentena | Precisão | Cobertura |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Polícia Federal / Cebraspe / 2021 | 8 | 4 | 4 | 480 | 480 | 447 | 33 | 100% | 100% |
| Banco do Brasil / Cesgranrio / 2021–2023 | 40 | 19 | 4 | 1.330 | 1.312 | 1.177 | 135 | 100% | 98,65% |

Não houve duplicação de documentos ou questões nos dois cenários. Todas as 1.792 respostas
detectadas foram associadas ao gabarito correspondente. As quarentenas preservam questões
com dependência visual, texto de apoio ausente ou outro motivo que exige revisão humana.

## Ollama e idempotência

A Polícia Federal foi processada com o `qwen3:8b` disponível e novamente com o Ollama
desligado. O caminho determinístico resolveu todas as questões, portanto nenhuma chamada ao
modelo foi necessária. Os dois modos produziram o mesmo hash semântico:

`d00813d29fbc159a1071c1359621e9b4413bb0623bf51dc8ed8b9a21c06f70c9`

Isso confirma que a disponibilidade do fallback não altera um resultado já resolvido pelo
parser local.

## Defeitos reproduzidos e corrigidos

1. O limite fixo de 40 arquivos encerrava uma fonte antes do fim do período solicitado. A
   operação agora reserva 50 arquivos por ano pedido, respeitando o teto de 1.000 por fonte.
2. A Cesgranrio usa “GABARITO 1–5” para identificar a variante do caderno. Além disso, alguns
   caminhos e títulos não representam o papel real do PDF. A classificação passa a considerar
   a densidade da grade de respostas e os blocos internos do documento.
3. Um gabarito consolidado com cinco variantes era consumido após o primeiro pareamento. Ele
   agora pode atender todos os cadernos compatíveis e seleciona a variante correta por prova.
4. A identidade do concurso só reconhecia URLs `/concursos/.../`. Agora também usa metadados,
   `/concurso/.../` e o identificador oficial presente no parâmetro `file` de acervos.
5. Uma repetição em que todas as fontes falhavam substituía o último manifesto válido por um
   resultado vazio. O resultado anterior agora é preservado e a nova falha fica registrada.
6. Uma primeira execução sem documento e com falha em todas as fontes não é mais apresentada
   como sucesso e retorna código de erro ao terminal.
7. O hash semântico dependia de horários e telemetria do manifesto. Esses campos operacionais
   foram retirados do cálculo; os hashes dos PDFs e o conteúdo estruturado continuam incluídos.

Cada correção possui teste de regressão automatizado.

## Limitações observadas

Na segunda tentativa real, `robots.txt` bloqueou as páginas iniciais do Banco do Brasil e da
Cesgranrio. O coletor respeitou a restrição e não tentou contorná-la. A execução anterior,
feita quando o acervo estava liberado, continha 40 PDFs válidos e foi preservada para a
homologação. Os 17 cadernos de 2023 presentes nesse conjunto não tinham seus quatro gabaritos
no manifesto limitado; por isso ficaram sem estruturação. O novo orçamento elimina o corte,
mas uma nova comprovação ao vivo depende de a fonte voltar a permitir a coleta.

O teste de interrupção e retomada está coberto por integração automatizada: a interrupção
ocorre depois do download, o estado fica como `interrupted` e `--resume` reutiliza o manifesto
sem chamar a coleta outra vez. A tentativa de reproduzir o sinal no terminal real terminou
antes do ponto de interrupção porque a fonte bloqueou a navegação.

## Artefatos da execução

Os artefatos reais permanecem em diretórios ignorados pelo Git:

- `data/homologation/operator-run-pf-2021/`
- `data/homologation/operator-run-bb-2021-2023/`

Este relatório e `2026-09-08-simple-operator-run-results.json` contêm apenas métricas,
hashes e diagnóstico versionáveis; nenhum PDF ou conteúdo de questão foi adicionado ao Git.

## Validação final

- `python -m pytest tests -q`: 930 testes e 114 subtestes aprovados;
- `ruff check src tests`: aprovado;
- `python -m mypy src`: aprovado em 78 arquivos;
- `node --check src/kad_collector/desktop_app.js`: aprovado;
- smoke test dos recursos empacotados do desktop: aprovado em diretório isolado;
- construção do wheel sem dependências: aprovada;
- instalação isolada do wheel e leitura do catálogo oficial empacotado: aprovada.

O comando `pytest -q` sem limitar o caminho encontrou cópias antigas do pacote mantidas em
`data/package-smoke-*` e tentou coletá-las como testes. Esses diretórios locais são ignorados
pelo Git e não existem em checkout limpo; a suíte oficial em `tests/` foi executada por
completo.
