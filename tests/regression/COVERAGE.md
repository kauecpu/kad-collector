# Matriz de cobertura

| Requisito | Estado | Caso | Evidência ou lacuna |
|---|---|---|---|
| Prova e gabarito separados | `supported` | `fuvest-2026-separated-official` | PDFs oficiais distintos, com integridade e resumos estruturais fixados |
| Prova e resposta no mesmo PDF | `supported` | `same-pdf-inline-answer` | Texto sintético fictício usa a rota de resposta inline do parser |
| Tipos 1 a 4 | `supported` | `multi-grid-answer-key` | Quatro grades sintéticas selecionadas em separado |
| Preliminar e definitivo | `supported` | `preliminary-definitive-selection` | Metadados sintéticos selecionam o definitivo |
| Questão anulada | `supported` | `multi-grid-answer-key` | `*` permanece anulado e sem alternativa oficial |
| Republicação ou revisão | `planned` | `republication-identity` | Falta identificador de revisão que diferencie correção editorial de duplicata por conteúdo |
| Digitalizado e OCR | `planned` | `scanned-document-ocr` | O OCR local possui testes sintéticos de integração; faltam fixture, executor e expectativa neste pacote híbrido |
| Multicargo, turno e versão | `supported` | `multi-grid-answer-key` | Cargo, manhã ou tarde e tipo selecionam uma única grade |
| Associação ambígua bloqueada | `supported` | `ambiguous-association-blocked` | Dois candidatos sem evidência retornam `blocked` |
| Documento não relacionado | `planned` | `unrelated-document-rejection` | A triagem desktop possui testes sintéticos; faltam fixture, executor e expectativa neste pacote híbrido |

O manifesto é a fonte executável desta tabela. Uma linha muda para `supported` somente quando
recebe fixture, executor, expectativa literal e teste que falha diante da quebra do
comportamento.

## Identidade semântica: matriz executável

| Cenário | Teste executável |
|---|---|
| prova repetida | `test_same_pdf_twice_creates_one_document_job_and_observation` |
| gabarito repetido | `test_same_answer_key_twice_creates_no_second_job` |
| coleta e importação com mesmo SHA | `test_collection_and_direct_import_with_same_sha_converge` |
| bytes diferentes, conteúdo equivalente | `test_equivalent_text_with_different_bytes_is_republication` |
| republicação com nova origem | `test_republication_adds_origin_without_new_questions` |
| questão alterada | `test_same_identity_with_changed_content_creates_successor` |
| questão adicionada ou removida | `test_changed_content_with_question_added_creates_successor`; `test_changed_content_with_question_removed_creates_successor` |
| preliminar seguido do definitivo | `test_definitive_key_supersedes_preliminary_and_reapplies_answers` |
| definitivo repetido | `test_repeated_definitive_key_does_not_reapply_or_duplicate_events` |
| questão anulada | `test_definitive_annulment_is_applied_and_audited_without_erasing_absent_answers` |
| decisão preservada | `test_human_decision_is_carried_to_identical_successor_question` |
| decisão invalidada | `test_changed_statement_does_not_carry_decision` |
| correção manual | `test_manual_identity_correction_is_audited_and_preserves_question_decision` |
| campo desconhecido | `test_weak_title_does_not_invent_minimum_identity` |
| ano conflitante | `test_declared_year_conflicting_with_pdf_is_not_resolved` |
| título fraco | `test_title_only_candidate_is_insufficient` |
| empate de gabaritos | `test_equal_candidates_are_ambiguous` |
| conflito de escopo | `test_known_scope_conflicts_block_candidate` |
| gabarito multicargo | `test_one_key_can_cover_multiple_roles` |
| tipos 1 a 4 | `test_types_one_to_four_do_not_mix_answers` |
| corrida do mesmo SHA | `test_concurrent_claims_have_one_winner` |
| corrida de republicação | `test_concurrent_republications_share_one_version` |
| retomada após falha | `test_reprocessing_resumes_failed_resolution_without_duplicate_event` |
| migração legada | `test_legacy_database_adds_semantic_schema_without_touching_rows` |
| interface e relatório | `test_bootstrap_exposes_semantic_counts` |

Os dois casos de adição e remoção usam nomes mais específicos do que o nome previsto no
planejamento. O caso de anulação também inclui a garantia de não apagar respostas ausentes;
ambos são os nomes executáveis usados pela suíte.
