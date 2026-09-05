# Homologação com PDFs reais

O corpus combina 19 documentos da FGV já descritos no contrato RFB22 com quatro arquivos
oficiais do Cebraspe e da FCC. Os PDFs ficam em diretórios ignorados pelo Git: os da FGV em
`tests/regression/rfb22/official/` e os demais em `data/homologation/external/`. O manifesto
versionado registra URL oficial, página de origem, tamanho, SHA-256, número de páginas e
metadados editoriais.

## Preparação

```powershell
.venv\Scripts\python.exe scripts\prepare_real_homologation.py
```

O preparador não segue redirecionamentos, limita o download ao tamanho declarado, confere a
assinatura PDF e só aceita o arquivo quando tamanho, SHA-256 e número de páginas coincidem.
Para os quatro arquivos externos, aceita somente os hosts oficiais declarados, exige
`robots.txt` permissivo, respeita `Crawl-delay` e usa timeout de 60 segundos por operação de
download. Se o site impedir a preparação, preserve o erro; não contorne o bloqueio.
O contrato RFB22 mantém a política já registrada em seu próprio manifesto.

As fontes adicionais são o arquivo público PCDF 2013 do Cebraspe (uma prova e seu gabarito)
e a página DPE Bahia 2025 da FCC (gabarito e resultado). O manifesto registra banca, concurso,
ano, cargo, título e referência oficial. Não há descoberta recursiva: são quatro arquivos,
uma requisição de PDF por arquivo ausente, sem retentativas automáticas. Arquivos já válidos
no cache não geram download. A validação técnica não constitui autorização para republicação.

## Execução

```powershell
.venv\Scripts\python.exe scripts\run_real_homologation.py
```

Cada documento passa pelo `DesktopStore` e pelo `DesktopProcessor`, o mesmo caminho usado na
importação do aplicativo. Uma prova com gabarito declarado entra no mesmo lote que o gabarito.
O script cria bancos temporários separados para impedir que uma execução contamine outra.

Os resultados ficam em:

- `data/homologation/real-report.json`, para comparação automatizada;
- `data/homologation/real-report.md`, para revisão humana.

O relatório mede triagem, páginas lidas normalmente, páginas recuperadas por OCR, páginas
ilegíveis, questões, alternativas, respostas, tempo e pico de alocações Python. A memória
nativa usada pelo ONNX Runtime não entra em `peak_python_bytes`.

Tipos declarados manualmente alimentam o processamento. A classificação automática é
calculada separadamente com as mesmas regras do aplicativo, sem usar o tipo declarado.
O relatório guarda as duas decisões. O tempo de uma prova inclui o processamento de seu
gabarito pareado; extração, OCR e triagem são medidos juntos, assim como estruturação e
associação. Não há medição isolada de cada subetapa.

O script mostra o documento em processamento no terminal. Os relatórios completos são
gravados ao final; uma interrupção da própria rotina exige repetir a rodada. O teste de
retomada do aplicativo é separado: pausa após três páginas, instancia novamente o banco e
o processador, conclui o documento e confere preservação do texto e bloqueio de duplicação.

`download_success_rate` fica nulo: a execução mede a integridade dos arquivos presentes,
sem inferir a taxa de sucesso de downloads anteriores. A taxa OCR conta páginas recuperadas
entre páginas candidatas; páginas visualmente vazias também entram nesse denominador.
Ela não mede precisão de transcrição. Um gabarito no estado `extracted` concluiu sua etapa;
o vínculo com uma prova é avaliado separadamente nas respostas dessa prova.

## Cobertura dos cenários solicitados

As referências abaixo são testes executáveis. Cobertura sintética não equivale a uma
homologação manual da interface com os PDFs oficiais.

| # | Cenário | Evidência e limite |
| --- | --- | --- |
| 1 | Prova textual | 16 provas oficiais FGV pelo processador desktop |
| 2 | Prova totalmente digitalizada | Pendente em documento oficial; `test_scanned_pdf_is_processed_after_local_ocr` usa fixture sintética |
| 3 | Páginas parcialmente digitalizadas | PCDF13: capa e última página recuperadas por OCR; corpo com texto |
| 4 | Gabarito simples | Definitivos FGV ligados às provas; comparação com regressão oficial |
| 5 | Gabarito irregular | FCC DPEBA: triagem reconhece gabarito, identidade entra em exceção |
| 6 | Edital como `other` | `test_automatic_triage_excludes_a_synthetic_edital`; sem edital puro real neste corpus |
| 7 | Resultado como `other` | Resultado oficial FCC; zero questões |
| 8 | Ambiguidade em revisão | `test_automatic_triage_sends_an_ambiguous_pdf_to_human_review`; decisão automática do PCDF registrada separadamente |
| 9 | Download interrompido/retomado | `test_range_download_resumes_partial_file`, transporte simulado |
| 10 | Fechamento durante processamento | `test_application_recovers_interrupted_processing_job_as_paused`; fechamento visual manual pendente |
| 11 | Reabertura/checkpoint | Prova FGV pausada após três páginas; novo banco/processador retoma |
| 12 | Cancelamento durante OCR | `test_cancellation_stops_before_next_page` e `test_resume_does_not_repeat_completed_ocr_page` |
| 13 | Correção/reprocessamento | `test_correction_from_other_to_exam_creates_a_new_local_execution` e testes da rota; interação visual pendente |
| 14 | Reprocessamento simultâneo | `test_active_reprocessing_prevents_a_second_execution_for_the_same_pdf` |
| 15 | Falha isolada de página | `test_page_failure_does_not_discard_other_pages` |
| 16 | Arquivo inválido | Validação de assinatura/hash/páginas em `test_real_homologation.py`; limites em `test_desktop_app.py` |
| 17 | URL indisponível/redirect/retries | `test_retryable_http_and_network_errors_use_bounded_retries`, `test_retry_exhaustion_stops_after_configured_attempts` e `test_preparation_rejects_every_redirect` |
| 18 | Reexecução sem duplicação | Prova real reapresentada ao mesmo banco é bloqueada; `test_second_collection_skips_processed_sha_without_creating_another_job` |

Revisão e exportação têm testes em `test_human_review_exports_only_valid_approved_selection`
e `test_review_decisions_persist_and_export_only_approved_questions`. O corpus oficial não
recebe aprovação editorial automática. O teste `--smoke-test` do executável inicializa banco,
OCR e recursos empacotados; não abre nem valida visualmente uma janela.

## Verificações

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests scripts/prepare_real_homologation.py scripts/run_real_homologation.py
.venv\Scripts\python.exe -m mypy src
node --check src/kad_collector/desktop_app.js
.venv\Scripts\python.exe scripts/run_official_regression.py
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm KADCollector.spec
```

Execute o binário com `--smoke-test --data-dir <diretório temporário novo>` e verifique saída
zero. Nunca aponte uma homologação para o banco operacional. Testes de comportamento dos
helpers JavaScript também são executados pela suíte Python quando Node está disponível.

## Manutenção

Quando uma origem substituir um PDF, não atualize o hash sem conferir o documento. Registre a
mudança como nova versão em um PR separado. Testes automatizados continuam offline e usam
fixtures sintéticas; somente os comandos de preparação acessam as fontes oficiais.

Prova e gabarito no mesmo arquivo e identificação completa de republicações continuam fora
desta homologação.
