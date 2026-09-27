# Portas de qualidade do acervo — CLI

Ordem: **contrato de publicação → cobertura/versões → piloto de taxonomia PF**.
Nenhuma etapa importa dados no KAD, publica questões ou altera o banco do desktop.
Os comandos abaixo leem arquivos locais; use saídas temporárias fora de `data/`
para homologação. Não é necessário baixar PDFs nem chamar o Ollama.

## 1. Contrato offline de publicação

`approval-export` valida o pacote inteiro antes de criar os arquivos de saída.
Além do esquema v2, exige identidade e taxonomia completas, alternativas ordenadas,
resposta válida, origem HTTPS, proveniências com vínculo de gabarito e resposta
`matched` compatível com a alternativa canônica. Duplicações e contagens de
ocorrências inconsistentes bloqueiam a exportação. A aprovação humana existente
não é alterada por uma falha.

```powershell
python -m kad_collector.publication_contract CAMINHO\questoes.jsonl
```

Saída JSON, código 0 se válido, 1 se bloqueado. O CI executa o contrato e o fluxo
real de aprovação/exportação com fixtures locais, incluindo versões antigas sem
proveniência. A referência de regras é
`KAD:20260828220803_controlled_question_publication`. Isto **não substitui** os
bloqueios do KAD: regras dependentes do banco continuam sendo responsabilidade do
destino. Ao alterar o contrato no KAD, atualizar a referência e os testes aqui.
Não existe download nem dependência do repositório privado no CI.

## 2. Cobertura e gabaritos versionados

```powershell
python -m kad_collector.coverage_report CAMINHO\manifest.json `
  --package CAMINHO\review-package.json
```

O relatório lista documentos das **páginas registradas**, não afirma que todo o
portal foi percorrido. Em novas coletas, os motivos incluem:

| Motivo | Evidência |
|---|---|
| `downloaded` | Documento presente no manifesto |
| `exclude_pattern` | Link reconhecido, barrado pela regra configurada |
| `include_pattern_mismatch` / `administrative_section` | Filtro da descoberta |
| `year_filter` | Ano fora do recorte |
| `file_limit` | Link retirado no ponto em que o teto de arquivos é aplicado |
| `download_error` / `robots_policy` | Falha registrada para a mesma URL/fonte |
| `not_recorded` | Manifesto antigo ou ausência sem evidência causal |

Excluídos não viram candidatos de download. Assinaturas temporárias das URLs são
removidas. `pairing` usa os hashes do pacote opcional: `paired`, `pairing_error`
ou `not_evaluated`. Pareado não significa aprovado. A porcentagem antiga continua
referente ao inventário elegível; consulte os excluídos para auditar filtros.

No fluxo `structure-pdfs`, um único definitivo/alterado/retificado prevalece sobre
preliminar ou versão sem estado, **dentro do mesmo concurso, ano e escopo**. Dois
finais não são ordenados pela data de download nem pelo nome do arquivo. Exigem
`metadata.predecessor_sha256` no documento sucessor, apontando ao SHA-256 da
predecessora presente no mesmo conjunto compatível. Referência ausente, ciclo,
ramificações concorrentes e regressão de final para preliminar bloqueiam a seleção.
Sem gabarito exato, outro cargo/bloco não pode ser usado como fallback. Mantém-se
o caminho legado de gabarito explicitamente agregado (`prova objetiva` sem cargo).

A associação exportada no pacote estruturado registra `key_version` e
`superseded_key_ids`. Reprocessar os mesmos arquivos produz o mesmo conteúdo;
anulações ficam em quarentena. Não alteramos aprovação já concedida nem pacotes
anteriores: gere outro pacote e refaça a revisão das mudanças. Preserve originais
para reversão. Uma retificação parcial **não é mesclada** por suposição: itens sem
resposta continuam bloqueados. Monitoramento periódico e migração do registro de
versões SQLite para arquivos não fazem parte desta fatia.

## 3. Piloto isolado PF 2021

O catálogo padrão da PF não foi preenchido com disciplinas presumidas. O novo
piloto reutiliza `EditorialTaxonomy`, mas não carrega o catálogo bancário global,
não chama o Qwen e não promove resultados para o acervo.

```powershell
python -m kad_collector.taxonomy_pilot CAMINHO\pf-package.json --draft
python -m kad_collector.taxonomy_pilot CAMINHO\pf-package.json `
  --spec CAMINHO\pf-spec.json --program CAMINHO\edital-extraido.txt
```

O rascunho contém cargos/blocos observados e **listas permitidas vazias**. Complete
o `catalog` no formato existente (`id`, versão, fontes HTTPS e disciplinas com
`topics`, cada um com ID, matéria, assunto e palavras-chave), conferindo o edital
oficial local. Preencha `scopes[].allowed_path_ids` por cargo/bloco exatos. Não
deduza conteúdo programático apenas pelo nome da banca. `program_sha256` é o hash
dos bytes do texto conferido, não o hash do PDF.

Após conferência humana, registre `reviewed_by` e copie o `spec_sha256` do relatório
para `reviewed_digest`. Este hash inclui catálogo, recortes e hash do texto; mudar
qualquer um invalida a confirmação. É um registro local de revisão, não assinatura
digital nem prova automática da autenticidade da fonte.

O relatório escolhe até 100 questões `accepted` de PF/Cebraspe 2021, em rodízio
entre cargos/blocos, com ordenação estável por ID. As sugestões determinísticas
devem pertencer ao recorte permitido; empates, falta de caminho ou bloco ausente
continuam pendentes. `quarantined`, outras bancas/anos e respostas não são alterados.

Uma revisão da amostra é um JSON de objetos:

```json
[
  {
    "stable_id": "ID copiado da amostra",
    "question_sha256": "hash copiado da amostra",
    "spec_sha256": "hash do catálogo e seus recortes",
    "reviewer": "responsável pela conferência",
    "path_id": "caminho correto escolhido pela pessoa"
  }
]
```

Passe `--reviews CAMINHO\revisoes.json`. Exigem-se 100 itens revisados e pelo menos
95 sugestões com **caminho completo** correto, sem sugestão fora da lista permitida.
Duplicações bloqueiam; revisões de texto/resposta ou catálogo anterior não contam.
Código 1 significa critério ainda não satisfeito; 0 libera apenas o **próximo
piloto**, nunca publicação. Amostra menor que 100 não pode passar.

### Evidência local de 27/09/2026

Em cópias temporárias do acervo:

- BB: 10 questões aprovadas geraram 10 proveniências válidas. Repetir a exportação
  manteve o hash. O pacote antigo recebeu 10 bloqueios por falta de proveniência.
- Manifesto BB: 40 documentos baixados e 8 ausências sem causa registrada. Não
  atribuímos retroativamente as oito ao teto; novas coletas registram esse motivo.
- PF: amostra de 100 preparada, **zero revisões humanas, não promovida**. Faltam
  texto do edital e catálogo com recortes confirmados. Não há medição de acurácia
  real da nova taxonomia nem estimativa comprovada de questões destravadas.

Próximo passo operacional: conferir o conteúdo programático PF 2021 por cargo e
bloco, preencher o rascunho e revisar a amostra antes de ampliar a classificação.
