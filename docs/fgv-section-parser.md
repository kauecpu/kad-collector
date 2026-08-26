# Adaptador FGV por seções

O coletor seleciona o adaptador `fgv-sections` pela banca normalizada ou pelo provedor
`fgv_conhecimento`. Nomes de arquivos não participam dessa seleção. Documentos de outras
bancas continuam no parser genérico.

## Contrato

`BankParsingContext` entrega ao adaptador a identidade conhecida do documento. O adaptador
confere cargo, turno e tipo no texto das primeiras páginas e procura a expectativa oficial em
`fgv_section_profiles.v1.toml`.

A versão `1.1` usa a mesma regra de turno da identidade semântica e da associação de
gabaritos. A representação canônica é `manhã` ou `tarde`.

`BankParsingResult` contém:

- identificador e versão do adaptador;
- perfil oficial selecionado e identidade encontrada;
- seções, páginas, evidências e confiança;
- questões objetivas extraídas e números discursivos identificados;
- intervalos esperados;
- avisos e exceções estruturadas;
- resumo das contagens e estado `completed` ou `incomplete`.

O banco salva esse resultado em `documents.parsing_result_json`. As questões extraídas
continuam na tabela `questions`.

## Reconhecimento das seções

O adaptador usa os seguintes sinais textuais:

- turno estrutural `MANHÃ`, `MANHA` ou `TARDE` nas quatro páginas iniciais,
  antes do primeiro marcador de questão;
- `TIPO n` para identificar o caderno;
- cargo cadastrado no perfil e presente no cabeçalho;
- título `Prova Discursiva`, `Questões Discursivas`, `Redação` ou `Estudo de Caso` para
  encerrar a extração objetiva;
- títulos exatos de folha ou cartão de respostas;
- páginas finais curtas, como `Realização`, como conteúdo sem questão.

A parte objetiva aceita marcadores numéricos isolados dentro do intervalo oficial. O parser
mantém itens como `1.` e `2.` no enunciado. Depois do título discursivo, o adaptador lê somente
marcadores explícitos `Questão n`; alíneas e linhas das folhas de resposta não viram questões
objetivas.

Uma palavra igual a `manhã` ou `tarde` dentro de um enunciado não altera a
identidade. Duas evidências estruturais diferentes impedem que a prova receba
um turno único. Em gabaritos, cabeçalhos de grade podem declarar os dois turnos
como cobertura válida; isso não é conflito. Um cabeçalho isolado `MANHÃ` ou
`TARDE` também é associado às grades seguintes até o próximo cabeçalho de
turno. Quando existem grades seccionadas, a ausência do turno solicitado falha
de forma fechada e não reutiliza a grade de outro turno.

## Fechamento da numeração

O validador compara cada seção com o perfil versionado. Ele registra:

- uma exceção por questão ausente;
- duplicidades;
- números fora do intervalo;
- quebras de ordem.

Qualquer exceção define o resultado como `incomplete`. No pipeline atual, `completed` vira o
estado de documento `processed`; `incomplete` vira `exception`. O coletor salva as questões que
conseguiu extrair e impede que o documento incompleto apareça como processado.

Exemplo de exceção:

```json
{
  "contest": "RFB22",
  "application_id": "rfb22-main-2023-03-19",
  "document_id": "rfb22-main-2023-auditor-morning-type-1",
  "role": "Auditor-Fiscal da Receita Federal do Brasil",
  "shift": "manhã",
  "booklet_type": 1,
  "section": "objective",
  "expected_number": 2,
  "nearby_pages": [3, 4],
  "reason": "questão esperada não extraída",
  "evidence": ["perfil rfb22-main-auditor-morning: 1-80"],
  "recommended_action": "Revisar o PDF original e corrigir o marcador ou a configuração."
}
```

## Cadastrar outro concurso FGV

1. Crie ou atualize o manifesto oficial do concurso com cargo, turno, tipo e intervalos.
2. Adicione um perfil em `src/kad_collector/fgv_section_profiles.v1.toml` com os mesmos
   intervalos.
3. Registre aliases do concurso e grafias oficiais do cargo.
4. Adicione fixtures locais ignoradas pelo Git e uma regressão offline.
5. Inclua um teste negativo que remova um marcador e exija `incomplete`.
6. Compare o perfil com o manifesto durante a regressão para impedir divergências.

## Execução

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m unittest tests.test_fgv_parser -v
.venv\Scripts\python.exe scripts\run_official_regression.py
```

## Limitações

- O adaptador exige uma camada de texto utilizável; esta etapa não executa OCR.
- Um concurso FGV sem perfil oficial fica `incomplete` até o cadastro dos intervalos.
- O adaptador identifica e contabiliza questões discursivas, mas não estrutura suas respostas.
- Os limites de página usam a extração textual. Uma página pode conter a transição entre duas
  seções.
- O parser não usa IA para corrigir marcadores ou preencher questões ausentes.
