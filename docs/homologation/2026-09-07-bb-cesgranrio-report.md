# Banco do Brasil 2021/2023: homologacao de fontes

- Data UTC: 2026-09-07
- Base: `3c765d33a6e2` (`Melhora confiabilidade do OCR em PDFs digitalizados (#93)`)
- Matriz: `tests/homologation/bb-cesgranrio-2021-2023.v1.toml`
- Metricas: `docs/homologation/2026-09-07-bb-cesgranrio-results.json`
- Qwen: `qwen3:8b` disponivel, 0 chamadas
- Cloudflare: resolvedor do PCI autorizado explicitamente pelo responsavel nesta etapa

## Resultado

O coletor localizou e validou os **8 documentos esperados**: prova e gabarito de Agente
Comercial e Agente de Tecnologia em 2021 e 2023. Todos foram encontrados somente no PCI.
As fontes oficiais nao entregaram os documentos historicos nesta execucao.

| Fonte | Origem | Resultado | Documentos | Intervencao humana |
| --- | --- | --- | ---: | --- |
| Banco do Brasil | oficial | bloqueada pela politica de `robots.txt` | 0 | fonte incompleta |
| Fundacao Cesgranrio | oficial | catalogo respondeu; BB 2021/2023 ausentes | 0 | nao |
| PCI Concursos | secundaria | concluida com resolvedor autorizado | 8 | nao |

| Metrica | Resultado |
| --- | ---: |
| Cobertura dos 8 documentos esperados | 100,0% |
| Precisao dos aceitos | 100,0% |
| Falsos positivos / falsos negativos | 0 / 0 |
| Associacao prova/gabarito Tipo 1 | 100,0% (4/4) |
| Documentos obtidos em fonte oficial | 0,0% |
| Documentos dependentes do PCI | 100,0% |
| Fontes concluidas sem intervencao humana | 66,7% (2/3) |
| Tempo medio por fonte | 51,96 s |
| Redirecionamentos validados | 8 |
| Extracao de texto | 100,0% (8/8) |
| OCR | dispensado; todos os PDFs eram textuais |

Nenhum edital, resultado, comunicado, host externo ou PDF invalido foi aceito. O Qwen nao
foi chamado: o PCI encontrou os documentos pelo caminho deterministico; o catalogo oficial
respondeu sem os eventos historicos; e o Banco do Brasil foi bloqueado antes da navegacao.

## Fontes oficiais

### Banco do Brasil

- URLs iniciais: `https://www.bb.com.br/site/concurso-bb/` e pagina institucional de
  convocacao/posse
- Paginas de conteudo visitadas: 0
- Falhas isoladas: 2
- Duracao: 3,013 s
- Caminho: deterministico

O `robots.txt` de `www.bb.com.br` respondeu HTTP 403. Como a fonte usa `enforce`, o motor
nao requisitou o conteudo das paginas. Nenhum endpoint foi presumido.

### Fundacao Cesgranrio

- URL inicial: `https://concursos.cesgranrio.org.br/`
- Endpoint visitado: `https://concursos.cesgranrio.org.br/api/PortalEventos`
- Paginas JSON visitadas: 1
- Falhas: 0
- Duracao: 2,155 s
- Caminho: deterministico

O catalogo respondeu normalmente, mas os 13 eventos publicados no momento da coleta nao
incluiam as selecoes externas do Banco do Brasil de 2021 ou 2023. O coletor nao enumerou IDs,
inventou URLs historicas nem entrou em paginas de autenticacao.

## PCI Concursos

Foram visitadas quatro paginas especificas: 2021 e 2023 para Agente Comercial e Agente de
Tecnologia. O resolvedor de Cloudflare configurado no projeto foi utilizado por autorizacao
explicita do responsavel. Depois da pagina HTML, cada download passou pelo cliente HTTP normal,
com host validado antes e depois do redirecionamento de `www.pciconcursos.com.br` para
`arq.pciconcursos.com.br`.

| Ano | Cargo | Tipo | Versao | Paginas | SHA-256 da primeira coleta |
| ---: | --- | --- | --- | ---: | --- |
| 2023 | Agente Comercial | prova | Tipo 1 | 17 | `7f2440c079b81c68441f6e3494986016b827e734759860a60e5712d1a41643e7` |
| 2023 | Agente Comercial | gabarito consolidado | Tipos 1-5 | 10 | `44ed01ed4ce4edb5848a168348a4026b40d56929ac339a75cc08ad7969cf7090` |
| 2023 | Agente de Tecnologia | prova | Tipo 1 | 27 | `7837a948042ead3cfac369efba1abbf7c2feadf091450c38030d81d89763b6ae` |
| 2023 | Agente de Tecnologia | gabarito consolidado | Tipos 1-5 | 10 | `39ae770a86f15b0d380f5ac707c4591e19d96897bc13fdcb17bf51d24a5b4e7a` |
| 2021 | Agente Comercial | prova | Tipo 1 | 17 | `6090ecf04050e9e4f2058a90866cd498eda593bf2b090b1a24a76afc45a9522c` |
| 2021 | Agente Comercial | gabarito consolidado | Tipos 1-5 | 10 | `eb18dd53082d29876b139a62c15a54e33bf092c4b16a1dae263065d61cdc21c3` |
| 2021 | Agente de Tecnologia | prova | Tipo 1 | 27 | `671c10f6e2234e938901a12e72bf0122b4f286ce44e33cc4518d4ad4510534e2` |
| 2021 | Agente de Tecnologia | gabarito consolidado | Tipos 1-5 | 10 | `a70f5530c31bee0b512d56a1b7d022b4cda88980f3f2fbe17387930b47d73c4b` |

Os oito PDFs abriram, tiveram a primeira pagina conferida visualmente e produziram texto
utilizavel em todas as paginas. As provas disponiveis no PCI eram Tipo 1; os gabaritos
consolidados continham os Tipos 1 a 5. Logo, a matriz de oito documentos esta completa, mas
a cobertura de cadernos de prova e apenas **4 de 20 (20%)**: faltam os Tipos 2 a 5 de cada
combinacao ano/cargo. Isso nao e declarado como cobertura completa de versoes.

## Idempotencia

A coleta foi repetida e devolveu as mesmas oito identidades canonicas, sem duplicacao logica.
O conteudo extraido tambem ficou identico depois de remover a linha dinamica `pcimarkpci`.
Os hashes binarios mudaram porque o PCI insere em cada download uma marca d'agua com data/IP;
os dois conjuntos de hashes brutos permanecem nos manifestos locais para auditoria. Portanto,
a idempotencia foi aprovada por identidade e conteudo normalizado, nao por igualdade binaria.

## Defeitos encontrados e corrigidos

1. Parametros de assinatura podiam chegar a estado persistente. Manifestos, cache,
   checkpoints, telemetria e erros agora removem assinaturas. Uma resposta 401/403 renova o
   mesmo documento somente pela origem oficial e valida novamente host e identidade.
2. O PCI repetia a mesma acao como Ver, Baixar e Compartilhar. Quando existe `Baixar`, essa
   acao tem prioridade; paginas antigas apenas com `data-url` continuam compativeis.
3. Gabaritos de 2021 usam o nome incorreto `gabrito.pdf`. A classificacao reconhece essa
   grafia historica sem ampliar a aceitacao de editais ou resultados.
4. A versao estava apenas dentro do PDF. A extracao registra `Tipo N` para provas e todas as
   versoes presentes em um gabarito consolidado, sem sobrescrever metadados conhecidos.
5. Uma falha em endpoint JSON era registrada apenas como aviso. Agora tambem vira falha de
   descoberta estruturada e continua sem interromper as fontes seguintes.

Todos os defeitos possuem testes de regressao com URLs e assinaturas ficticias. Os PDFs e o
texto extraido ficaram na pasta local ignorada; nenhum dado foi publicado no KAD.

## Validacao

- Suíte completa: 887 testes e 114 subtestes aprovados.
- Testes focados de fontes, redirecionamento, assinatura, CAPTCHA e extracao: aprovados.
- Ruff: aprovado.
- Mypy estrito: aprovado em 75 arquivos-fonte.
- Build: `kad_collector-0.4.0.tar.gz` e wheel gerados com sucesso.
- Instalacao limpa do wheel em diretorio vazio: aprovada; 13 fontes empacotadas.
- Busca por tokens e assinaturas reais nos arquivos versionados: nenhuma ocorrencia.

Os manifestos locais usados na auditoria ficam registrados no arquivo de metricas. Eles estao
sob `data/homologation/bb-cesgranrio-live/`, permanecem ignorados pelo Git e nao acompanham o PR.

## Decisao

A integracao e a matriz principal foram homologadas, com 100% de precisao e cobertura dos
oito documentos de alto nivel. Ainda nao ha copia oficial localizada e faltam os cadernos de
prova Tipos 2 a 5. O coletor nao deve ser descrito como tendo cobertura oficial ou completa de
versoes para Banco do Brasil 2021/2023.
