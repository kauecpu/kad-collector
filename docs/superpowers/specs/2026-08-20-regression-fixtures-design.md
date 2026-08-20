# Pacote híbrido de regressão

## Objetivo

Criar uma regressão offline para o KAD Collector que trave comportamentos já suportados e
mostre lacunas sem confundi-las com suporte. O pacote usa PDFs oficiais reais somente no
diretório local ignorado pelo Git. O repositório guarda o manifesto, fixtures sintéticas
pequenas, testes, documentação e a matriz de cobertura.

## Estrutura

- `tests/regression/manifest.toml` registra cada fixture por origem, caminho, formato,
  tamanho e SHA-256. O mesmo arquivo classifica casos como `supported` ou `planned`.
- `tests/regression/synthetic/` guarda textos fictícios mínimos, identificados como
  sintéticos. Eles exercitam formatos sem atribuir respostas a uma fonte oficial.
- `tests/regression/official/` guarda PDFs oficiais preparados na máquina do mantenedor e
  fica fora do Git.
- `kad_collector.regression` valida o manifesto e as fixtures, executa os casos suportados
  sem rede e gera um relatório JSON. O subcomando `kad-collector regression` oferece a
  execução única.
- `scripts/prepare-regression-fixtures.py` baixa somente as URLs HTTPS declaradas no
  manifesto e aceita cada arquivo após conferir tamanho, SHA-256 e assinatura PDF.

## Execução e isolamento

O runner bloqueia conexões de rede durante os casos e não importa nem instancia o banco do
Collector. Cada executor recebe caminhos locais e devolve dados simples. O runner executa
cada caso suportado duas vezes e exige resultados idênticos. Ele encerra com erro quando um
arquivo obrigatório falta, o hash diverge, o formato não corresponde ou um caso suportado
falha.

O relatório contém o hash do manifesto, o resultado por caso e uma linha por requisito de
cobertura. Casos planejados recebem estado `planned` e uma justificativa obrigatória. Eles
não executam e não aumentam a contagem de suporte.

## Casos suportados

- FUVEST 2026: prova V1 e gabarito em PDFs oficiais separados, com contagens e resumos
  determinísticos derivados dos próprios documentos verificados.
- Prova comentada com resposta no mesmo documento, usando texto sintético.
- Gabarito sintético com tipos 1 a 4, item anulado e seleção por cargo, turno e versão.
- Escolha do gabarito definitivo diante de versões preliminar e definitiva.
- Bloqueio de associação quando dois gabaritos têm a mesma pontuação e nenhum vínculo
  suficiente com a prova.

As respostas sintéticas são fictícias. Os resumos esperados para os PDFs oficiais serão
calculados a partir dos arquivos baixados da origem declarada e fixados no manifesto junto
com os hashes dos PDFs.

## Lacunas planejadas

- Republicação: falta um identificador de revisão que diferencie correção editorial de
  duplicata por conteúdo sem depender do nome do arquivo.
- OCR real: o Collector detecta ausência de camada textual e encaminha o documento para
  exceção, mas não executa OCR. A regressão não deve sugerir extração que o produto não faz.
- Documento não relacionado: o fluxo desktop aceita `exam`, `answer_key` ou `auto`; ele não
  oferece uma decisão explícita `other` na importação. O manifesto registra a lacuna até o
  contrato impedir o processamento desse documento.

## Validação e manutenção

Testes unitários criam árvores temporárias e nunca dependem dos PDFs locais. Eles cobrem
schema, unicidade, cadastro de arquivos, SHA-256, tamanho, assinatura, ausência de fixture,
cobertura completa da matriz, diferenciação entre estados e determinismo. O mantenedor roda
o script de preparação somente quando precisa obter ou atualizar um PDF oficial. Uma mudança
na origem exige revisão humana do documento antes de atualizar hash, tamanho ou resumo
esperado.

O trabalho não altera coleta, extração, associação ou classificação para acomodar fixtures.
Ele não usa IA, não gera EXE e não acessa o aplicativo KAD ou Supabase.
