# Regressão oficial RFB22

Este pacote fixa a identidade e as contagens da aplicação principal do concurso da Receita
Federal regido pelo Edital nº 1/2022. `RFB22` é um alias; a identidade canônica permanece
separada do ano e da data de cada aplicação.

## Fontes e escopo

- Página oficial: <https://conhecimento.fgv.br/concursos/rfb22>
- Edital consolidado: <https://conhecimento.fgv.br/sites/default/files/concursos/edital_receita_federal_-_3a_retificacao.pdf>
- Origem dos arquivos: links de provas e gabaritos publicados pela FGV na página oficial.
- Campos registrados: URL, SHA-256, tamanho, páginas, órgão, banca, edital, aplicação, cargo,
  etapa, turno, tipo, conteúdo, intervalos, gabarito correspondente e evidências oficiais.

O manifesto v1 cobre 19 PDFs da aplicação de 19/03/2023: 16 cadernos, um gabarito
preliminar e dois gabaritos definitivos. As aplicações do Curso de Formação de 2023, 2024 e
2025 aparecem como `inventory_only` e não entram nas contagens da aplicação principal.

## Contagens oficiais

| Cargo | Turno | Tipos | Objetivas por tipo | Discursivas por tipo |
| --- | --- | ---: | ---: | ---: |
| Auditor-Fiscal | Manhã | 1 a 4 | 80 | 0 |
| Auditor-Fiscal | Tarde | 1 a 4 | 60 | 2 |
| Analista-Tributário | Manhã | 1 a 4 | 70 | 0 |
| Analista-Tributário | Tarde | 1 a 4 | 70 | 1 |

O edital consolidado define 2 questões discursivas para Auditor-Fiscal e 1 para
Analista-Tributário. Os números 3 e 2 usados no diagnóstico inicial correspondiam a falsos
positivos do parser e não foram incorporados ao contrato.

## Preparação local

Os PDFs permanecem fora do Git por poderem conter material protegido. O manifesto registra
o tamanho e o SHA-256 exatos. Prepare as cópias locais com:

```powershell
.venv\Scripts\python.exe scripts\prepare_official_contest_fixtures.py
```

A política administrativa atual da fonte FGV está registrada como `ignore` para
`robots.txt` e `Crawl-delay`, conforme `config/sources.official.toml`. O preparador não segue
redirecionamentos, limita cada download ao tamanho declarado e só promove o arquivo após
validar o SHA-256.

## Execução offline

```powershell
.venv\Scripts\python.exe scripts\run_official_regression.py
```

A regressão não acessa a rede. Para cada caderno, ela valida:

- identidade do cargo, turno e tipo a partir do conteúdo do PDF;
- hash, tamanho e total de páginas;
- sequência completa, sem ausências, duplicatas ou números fora do intervalo;
- separação entre a parte objetiva e a discursiva;
- associação ao gabarito definitivo do mesmo cargo, turno e tipo;
- digest determinístico da extração objetiva.

Qualquer questão ausente encerra o comando com código 2 e registra o caso no relatório local
ignorado pelo Git.
