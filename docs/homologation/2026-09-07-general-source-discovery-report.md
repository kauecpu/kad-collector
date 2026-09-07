# Descoberta geral de fontes: homologacao real

- Data UTC: 2026-09-07
- Base: `c189776` (`Merge pull request #94 from kauecpu/codex/bb-cesgranrio-2021-2023`)
- Matriz: `tests/homologation/general-source-discovery.v1.toml`
- Metricas e hashes: `docs/homologation/2026-09-07-general-source-discovery-results.json`
- Modelo local: `qwen3:8b`

## Resultado

O coletor encontrou os 51 documentos definidos antes das execucoes. Todos vieram de fontes
oficiais. A validacao nao aceitou edital, resultado, comunicado, certificado, lista de convocados,
arquivo de outro ano ou host externo.

| Fonte | Estrutura | Provas | Gabaritos | Cobertura | Falhas |
| --- | --- | ---: | ---: | ---: | ---: |
| Cesgranrio, Banco do Brasil 2023 | sitemap, pagina de concurso e `storage.ashx` | 20 | 4 | 100% | 0 |
| FGV, Receita Federal 2023 | pagina longa com blocos datados | 18 | 5 | 100% | 0 |
| FUVEST 2026 | acervo, subpaginas e paginacao | 4 | 0 | 100% | 0 |

| Metrica | Resultado |
| --- | ---: |
| Precisao dos documentos aceitos | 100% (51/51) |
| Cobertura dos documentos esperados | 100% (51/51) |
| Falsos positivos | 0 |
| Falsos negativos | 0 |
| Associacao prova/gabarito na regressao Cesgranrio | 100% (20/20 variantes) |
| Documentos de fonte oficial | 51 |
| Documentos de fonte secundaria | 0 |
| Fontes concluidas sem intervencao | 100% (3/3) |
| Tempo medio observado por fonte | 37,2 s |

O resultado supera os limites de 95% de precisao e 85% de cobertura. A taxa de associacao mede a
regressao Cesgranrio, onde cada uma das 20 provas pertence a um dos quatro grupos com gabarito. A
matriz FGV mede descoberta e filtro por ano; a amostra FUVEST nao definiu gabarito para 2026.

## Regressao Cesgranrio

A configuracao comeca no catalogo geral e na raiz institucional. O sitemap levou o coletor as
paginas historicas do Banco do Brasil. Nenhuma URL final do concurso entrou como URL inicial ou
tratamento exclusivo no codigo.

A pagina de 2023 usa um slug com `2022`. O coletor leu a data publicada no conteudo e manteve os 24
documentos de 2023: quatro grupos, cinco variantes de prova por grupo e quatro gabaritos. Os
documentos de 2021 apareceram na navegacao, mas o filtro de ano os retirou antes do download.

O titulo `PROVA A - AGENTE COMERCIAL - GABARITO 1` descreve uma variante de prova. A classificacao
agora usa a secao e a posicao do termo no titulo; ela nao confunde esse caderno com o gabarito
oficial.

## FGV e FUVEST

A pagina da FGV mistura publicacoes de 2023, 2024 e 2025. O inventario passou a ler o atributo
`datetime` de cada bloco. Ele selecionou 23 documentos de 2023 e rejeitou dez itens fora do filtro.
Tambem ignorou um contato `mailto:` sem interromper a fonte.

A FUVEST expoe quatro cadernos V1 a V4. O Qwen propôs sete PDFs por chamada porque o nome interno
`provao2026_chamada` continha a palavra `prova`. Esses arquivos eram listas de convocados. A lista
positiva da fonte rejeitou todas as propostas e aceitou somente os quatro cadernos previstos.
Links com titulo de 2027 tambem ficaram fora da fila de 2026.

## Qwen e telemetria

O Qwen recebeu listas numeradas com URLs autorizadas em quatro chamadas: duas na Cesgranrio e duas
na FUVEST. Ele propôs 16 documentos; as regras deterministicas aceitaram zero. Na FUVEST, o modelo
indicou cinco paginas de navegacao permitidas. A FGV encontrou todo o inventario sem chamar o
modelo.

Cada evento registra modelo, quantidade de candidatos, motivo, documentos propostos, documentos
aceitos e URLs de navegacao escolhidas. A telemetria mostra que a IA ajudou na navegacao sem poder
ampliar as listas de inclusao ou exclusao.

## Ollama indisponivel

Uma execucao separada apontou o cliente para uma porta local sem servico. O preflight registrou a
indisponibilidade e a coleta deterministica da Cesgranrio entregou os mesmos 24 documentos, com
100% de cobertura e nenhuma falha de fonte.

## Idempotencia

Duas execucoes da Cesgranrio no mesmo diretorio devolveram 24 documentos cada. Os conjuntos de URL
original e SHA-256 ficaram iguais; nenhum manifesto registrou duplicata.

## Defeitos corrigidos

1. O coletor lia o HTML antes do sitemap e podia gastar o limite de paginas em noticias. Agora ele
   consulta o sitemap primeiro e ordena paginas pelo alvo da coleta.
2. Links administrativos e arquivos `ashx`, `aspx`, `php`, ZIP e documentos deixavam a fila de
   paginas crescer. O seletor separa navegacao, acoes e arquivos.
3. O inventario contava itens que a lista de inclusao rejeitaria. Inventario e download agora usam
   as mesmas listas de inclusao e exclusao.
4. Paginas com varios anos atribuiam documentos de 2024 e 2025 ao filtro de 2023. O parser associa
   cada link ao bloco `time[datetime]` correspondente.
5. O caminho do Qwen aplicava exclusoes, mas nao exigia a lista positiva. As duas listas agora
   limitam as escolhas do modelo.
6. Um link `mailto:` podia abortar a leitura de uma pagina valida. O filtro de esquemas roda antes
   da canonicalizacao usada para comparar paginas.
7. A contagem de cobertura nao distinguia origem. O manifesto agora publica inventario total e
   resumos separados para fontes oficiais e secundarias.

Cada defeito possui teste de regressao com fixtures locais. O teste da Cesgranrio exige 20 provas e
quatro gabaritos e falha se edital, resultado ou gabarito alterado entrar no inventario.

## Limites

- O cadastro oficial atual nao possui uma fonte de aquisicao Cebraspe. Os testes cobrem os aliases
  `Cebraspe`, `Cespe` e `PF`, mas esta homologacao nao mede um site real da banca.
- O Qwen nao recuperou um PDF que o caminho deterministico tivesse perdido nesta matriz. O teste
  automatizado cobre esse caso e a execucao real confirmou as travas, a navegacao e a telemetria.
- A coleta baixa e valida PDFs. OCR, extracao de questoes e publicacao no KAD ficam fora desta
  homologacao de descoberta.
- A FUVEST atingiu o limite configurado de quatro paginas. Ela encontrou todo o inventario de 2026,
  mas uma coleta sem filtro pode exigir um limite maior.

Os PDFs e os manifestos completos permanecem em `data/homologation/`, ignorados pelo Git. O PR
versiona somente a matriz, as metricas, os hashes e este relato.

## Validacao

- Testes focados de descoberta, Qwen e coletor: 68 aprovados.
- Suíte completa: 895 testes e 114 subtestes aprovados.
- Ruff: aprovado.
- Mypy estrito: aprovado em 76 arquivos-fonte.
- Build: sdist e wheel `0.4.0` gerados com sucesso. O build usou `--no-isolation` porque o Python
  instalado neste host nao inclui o modulo `venv`.
- Instalacao do wheel em diretorio vazio: aprovada; versao e configuracao oficial empacotada
  conferidas.
- Execucoes reais com Qwen disponivel: Cesgranrio, FGV e FUVEST aprovadas.
- Execucao real com Ollama indisponivel: Cesgranrio aprovada.
- Idempotencia: 24 documentos identicos em duas execucoes.

## Decisao

A descoberta geral atingiu os criterios da matriz para as tres fontes. A equipe ainda precisa
cadastrar e homologar uma fonte Cebraspe e executar o processamento/OCR em um trabalho separado.
Os resultados deste PR sustentam a confiabilidade da descoberta; eles nao comprovam que todo site
de banca ou todo PDF futuro funcionara sem manutencao.
