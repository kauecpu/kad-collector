# Homologação real das fontes do KAD Collector

- Data UTC: 2026-09-06T23:27:14.878228+00:00
- Commit: `16635f4-dirty`
- Ollama: qwen3:8b disponível
- Precisão: 100.0%
- Cobertura: 77.8%
- Associação prova/gabarito: 75.0%
- Falsos positivos: 0
- Falsos negativos: 4
- Fontes sem intervenção: 77.8%
- Tempo médio por fonte: 8.79s

## Decisão de homologação

O coletor **não deve ser declarado pronto para operação autônoma em todas as fontes**.
A precisão superou a meta, mas a cobertura ficou abaixo dos 85% exigidos porque duas
fontes não puderam ser concluídas sem intervenção externa.

| Critério | Resultado | Estado |
| --- | ---: | --- |
| Precisão mínima de 95% | 100,0% | aprovado |
| Cobertura mínima de 85% | 77,8% | reprovado |
| Nenhum edital, resultado ou comunicado aceito | 0 aceitos; 36 rejeitados por regra | aprovado |
| Nenhum host fora da allowlist | 0 | aprovado |
| Nenhum PDF inválido aceito | 0 | aprovado |
| Falha do Ollama não derruba o determinístico | ENEM 2/2 sem Ollama | aprovado |
| Falha de uma fonte não interrompe as demais | 7/9 fontes concluídas | aprovado |
| Decisões do Qwen na telemetria | todas registradas | aprovado |
| Reexecução sem duplicação lógica | 2 documentos, hashes idênticos | aprovado |
| PDF integralmente digitalizado com OCR suficiente | amostra oficial indisponível | não comprovado |

O ensaio separado com Ollama indisponível está em
`docs/homologation/2026-09-06-ollama-offline-report.md`. O corpus congelado de 23 PDFs
está em `docs/homologation/2026-09-06-corpus-report.md`: 23/23 processados, 100% de
triagem correta e 56,25% de recuperação das páginas que pediram OCR. Na amostra ao
vivo, duas provas tiveram páginas pontuais recuperadas por OCR e continuaram legíveis,
mas nenhuma era um PDF integralmente digitalizado; portanto esse requisito permanece
pendente, sem ser mascarado como sucesso.

## Defeitos encontrados e corrigidos

- Páginas anuais do INEP guardavam a navegação em `data-url` fora de links `<a>`.
  O parser agora lê esses contêineres públicos e mantém os limites de navegação.
- Padrões ancorados no início da URL eram testados contra `título + URL`, descartando
  todas as referências da OBMEP. A URL agora é avaliada separadamente.
- O transporte HTTP usava apenas a cadeia do Certifi no Windows e rejeitava a cadeia
  válida do INEP. Agora usa o contexto seguro do sistema operacional, mantendo
  verificação de certificado e hostname.
- O diretório `/provas_e_gabaritos/` fazia provas do INEP serem classificadas como
  gabaritos. A classificação agora considera título, nome do arquivo e consulta, não o
  nome das pastas do acervo.
- O Qwen recebia primeiro links de menu e podia não ver o acervo relevante. Os
  candidatos agora são deduplicados, priorizados por evidência e limitados depois da
  ordenação.
- Um PDF de nome opaco podia ser aceito apenas pela decisão do modelo. A decisão agora
  precisa passar também por evidência determinística compatível com prova ou gabarito.
- Referências da OBMEP perdiam o título visível e viravam apenas `view`; o título da
  página agora é preservado para classificação e auditoria.

## Limitações externas restantes

- A UERJ apresentou certificado HTTPS expirado também no cliente do sistema. A
  validação TLS não foi desativada e a fonte foi isolada como falha.
- O PCI Concursos apresentou CAPTCHA. Nenhum login, navegador assistido ou contorno de
  desafio foi automatizado; a fonte foi isolada conforme a política do projeto.

## fuvest_vestibular

- Cenário: PDF direto; prova e gabarito separados
- URL inicial: https://www.fuvest.br/acervo-vestibular-2026/
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Tempo: 5.55s
- OCR tentado/suficiente: 1/1
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 0

- `answer_key` Gabaritos de Provas da 1ª fase — `a9e1084a125fa35bcefb875610328c0a8cb9b1a298880f2f0c75b928bd860e8e` — https://www.fuvest.br/wp-content/uploads/fuvest2026-fase1-gabarito.pdf
- `exam` Prova 2026 V1 — `93b417ad6ea7e81a3b6adc46337920fd71c213306849846723f13583074f9025` — https://www.fuvest.br/wp-content/uploads/fuvest2026-fase1-prova-V1.pdf

## coperve_ufsc_2026

- Cenário: Página oficial com prova e gabarito
- URL inicial: https://vestibularunificado2026.ufsc.br/provas-e-gabaritos-definitivos/
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Tempo: 1.61s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 2

- `answer_key` Gabarito — `81ea40e1aac6ffb27e0cdebb2f8a8d96f1c5e5075993958ef7d3626e9be82fba` — https://vestibularunificado2026.ufsc.br/files/2025/12/novo_gabarito_p1_amarela.pdf
- `exam` Prova — `386e482c93ed499eb1f5419714e5143873fdb5aaf825e705164c279f5a9b19c0` — https://vestibularunificado2026.ufsc.br/files/2014/12/p1_amarela.pdf
- Rejeitado: Edital do Proc. Sel. por Histórico Escolar — exclude_pattern: (?i)(edital|comunicado|resultado|lista) — http://vestibularunificado2026.paginas.ufsc.br/files/2026/01/Histórico-Escolar-2026-1.pdf
- Rejeitado: Comunicado Oficial – Alterações de Gabarito — exclude_pattern: (?i)(edital|comunicado|resultado|lista) — https://vestibularunificado2026.ufsc.br/files/2025/12/Comunicado-Oficial-Vest-2026.pdf

## fgv_conhecimento

- Cenário: Página com muitos editais e resultados
- URL inicial: https://conhecimento.fgv.br/concursos/rfb22
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Tempo: 1.13s
- OCR tentado/suficiente: 1/1
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 27

- `exam` Auditor-Fiscal da Receita Federal do Brasil — `6271191f43c7d650f99c1df1607165dd2fadb403c53cb987f324d2b5bb6a637b` — https://conhecimento.fgv.br/sites/default/files/concursos/auditor-fiscal-frb100-tipo-1.pdf
- `answer_key` Gabarito Oficial Definitivo da Prova Objetiva - Curso de Formação Profissional (Sub Judice) — `2eb2f10fb746ab05faac890d4fdca498301bec0b1c85ea3785fa67436341a13d` — https://conhecimento.fgv.br/sites/default/files/concursos/analista-tributario-e-auditor-fiscal.pdf
- Rejeitado: Resultado Final de Aprovados (específico para candidatos negros) (retificado em 29/05/2026) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-final-de-aprovados-nova-class-negro-2026-05-27.pdf
- Rejeitado: Resultado Final de Aprovados (específico para candidatos com deficiência) (retificado em 29/05/2026) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-final-de-aprovados-nova-class-pcd-2026-05-27.pdf
- Rejeitado: Resultado Final de Aprovados (retificado em 01/06/2026) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-final-de-aprovados-nova-class-2026-06-01.pdf
- Rejeitado: COMUNICADO - Prova Objetiva Final do Curso de Formação Profissional Sub judice — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/rfb-locais-prova-do-curso-de-fomacao-subjudice-2025.pdf
- Rejeitado: COMUNICADO - Prova Objetiva Final do Curso de Formação Profissional Sub judice — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/rfb-prova-do-curso-de-fomacao-subjudice-2025.pdf
- Rejeitado: Homologação da Relação de Candidatos Aprovados — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/rfb_concurso_homologacao_edital_6_2025.pdf
- Rejeitado: Resultado Final de Aprovados (específico para candidatos negros) (retificado em 03/09/2025) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388-receita-federal-resultado-final-de-aprovados-negro-2025-08-26.pdf
- Rejeitado: Resultado Final de Aprovados (específico para candidatos com deficiência) (retificado em 03/09/2025) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388-receita-federal-resultado-final-de-aprovados-pcd-2025-08-26.pdf
- Rejeitado: Resultado Final de Aprovados (retificado em 03/09/2025) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388-receita-federal-resultado-final-de-aprovados-2025-08-26.pdf
- Rejeitado: COMUNICADO - Prova Objetiva Final do Curso de Formação Profissional — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/comunicado-prova-objetiva-receita.pdf
- Rejeitado: Homologação da Relação de Candidatos Aprovados — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/edital-no-1_2023-rfb-de-18-de-dezembro-de-2023-edital-no-1_2023-rfb-de-18-de-dezembro-de-2023-dou-imprensa-nacional.pdf
- Rejeitado: Resultado Final de Aprovados (específico para candidatos negros) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/resultado-final-de-aprovados-negros.pdf
- Rejeitado: Resultado Final de Aprovados (específico para candidatos com deficiência) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/resultado-final-de-aprovados-pcd.pdf
- Rejeitado: Resultado Final de Aprovados — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/resultado-final-de-aprovados-ampla.pdf
- Rejeitado: COMUNICADO - Prova Objetiva Final do Curso de Formação Profissional (atualizado em 09/11/2023) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/comunicado-atualizado-prova-cfp.pdf
- Rejeitado: Resultado Final de Aprovados - 1ª Etapa (específico para candidatos negros) (retificado em 27/09/2023) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-final-de-aprovados-negros-2023-09-27.pdf
- Rejeitado: Resultado Final de Aprovados - 1ª Etapa (específico para candidatos com deficiência) (retificado em 27/09/2023) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-final-de-aprovados-pcd-2023-09-27.pdf
- Rejeitado: Resultado Final de Aprovados - 1ª Etapa (retificado em 27/09/2023) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-final-de-aprovados-2023-09-27.pdf
- Rejeitado: Resultado Definitivo da Prova Discursiva — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-definitivo-prova-discursiva-2023-08-02.pdf
- Rejeitado: COMUNICADO — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/comunicado-rfb-prova-discursiva.pdf
- Rejeitado: Espelho de correção - Prova Discursiva — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/rfb2022_espelhodiscursiva.pdf
- Rejeitado: Resultado Preliminar da Prova Discursiva (retificado em 06/07/2023) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-preliminar-prova-discursiva-2023-07-06.pdf
- Rejeitado: Relação de candidatos - correção da Prova Discursiva — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/relacao-de-candidatos-correcao-da-prova-discursiva_at.pdf
- Rejeitado: Resultado Definitivo da Prova Objetiva (Auditor-Fiscal) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-definitivo-prova-objetiva-auditor-2023-05-22.pdf
- Rejeitado: Resultado Definitivo da Prova Objetiva (Analista-Tributário) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-definitivo-prova-objetiva-2023-05-16_0.pdf
- Rejeitado: Resultado Preliminar da Prova Objetiva (Auditor-Fiscal) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-preliminar-prova-objetiva-2023-05-12.pdf
- Rejeitado: Resultado Preliminar da Prova Objetiva (Analista-Tributário) — exclude_pattern: (?i)(termos?.?de.?uso|privacidade|cookies?|edital|comunicado|convocacao|resultado|classificacao|isencao|inscricao|cronograma|orientacao|homologacao|retificacao|recurso|vagas?|locais?.?de.?prova|espelho|rela..o.?(?:de.?)?candidatos|correcao.?da.?prova) — https://conhecimento.fgv.br/sites/default/files/concursos/388_receita-federal-resultado-preliminar-prova-objetiva-2023-04-20_analista.pdf

## inep_enem

- Cenário: Acervo anual em data-url; fallback Qwen e PDF textual
- URL inicial: https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enem/provas-e-gabaritos
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic, qwen_fallback
- Páginas visitadas: 3
- Chamadas ao Qwen: 1
- Tempo: 19.05s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 0

- `answer_key` Gabarito — `583ac1950cd1927c42c971c270f96c0e33816844084fd5866a13bf94d8e08a81` — https://download.inep.gov.br/enem/provas_e_gabaritos/2025_GB_impresso_D1_CD1.pdf
- `exam` Prova — `a0f19f878d4ef3ba94c3e0f5ef9e57c13fb046d4bb362f72651898b473ddb7c8` — https://download.inep.gov.br/enem/provas_e_gabaritos/2025_PV_impresso_D1_CD1.pdf

## inep_enade

- Cenário: Acervo anual e paginação lógica
- URL inicial: https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enade/provas-e-gabaritos
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic, qwen_fallback
- Páginas visitadas: 3
- Chamadas ao Qwen: 1
- Tempo: 19.11s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 0

- `answer_key` Gabarito 1 — `9f94afae064f2384dd4b2a5b5019e0adff398a895ae7722bd226a0c7e4d8e791` — https://download.inep.gov.br/enade/provas_e_gabaritos/2025_administracao_GB_1.pdf
- `exam` Prova 1 — `5c5d5f577c5585497b51a12e1caca4b98b045eb7af6eac31fed50c8b6b9b0b1c` — https://download.inep.gov.br/enade/provas_e_gabaritos/2025_administracao_PV_1.pdf

## comvest_unicamp

- Cenário: Acervo com subpáginas e fallback Qwen
- URL inicial: https://www.comvest.unicamp.br/vestibulares-anteriores/
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic, qwen_fallback
- Páginas visitadas: 3
- Chamadas ao Qwen: 2
- Tempo: 17.50s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 7

- `exam` Questões – múltipla escolha — `2d1d0d375fb8ee7ea84ebb11350ca30851a5c928db80ed6e2479aeeb1ba23284` — https://www.comvest.unicamp.br/wp-content/uploads/2026/06/F1_2026_Prova-Q.pdf
- `exam` Prova comum aos candidatos de cursos de todas as áreas — `5407a2c93fcddc08e2598f7595d04822b5dba9201a0b6cb9e567a19161b8a4a1` — https://www.comvest.unicamp.br/wp-content/uploads/2026/06/F2_1o-dia_todos.pdf
- Rejeitado: programa — exclude_pattern: (?i)(edital|manual|estatistica|relatorio|perfil|demanda|nota.?de.?corte|programa|anuario|desempenho|lista|chamada|convocad|aprovad|classificacao|resultado|declaracao|isencao|vagas?|normas?) — https://www.comvest.unicamp.br/vest_anteriores/2003/download/comentadas/musica/Introd_Progr_Objetivo.pdf
- Rejeitado: Desempenho dos Candidatos — exclude_pattern: (?i)(edital|manual|estatistica|relatorio|perfil|demanda|nota.?de.?corte|programa|anuario|desempenho|lista|chamada|convocad|aprovad|classificacao|resultado|declaracao|isencao|vagas?|normas?) — https://www.comvest.unicamp.br/vest_anteriores/2003/download/comentadas/desempenho.pdf
- Rejeitado: Desempenho dos Candidatos — exclude_pattern: (?i)(edital|manual|estatistica|relatorio|perfil|demanda|nota.?de.?corte|programa|anuario|desempenho|lista|chamada|convocad|aprovad|classificacao|resultado|declaracao|isencao|vagas?|normas?) — https://www.comvest.unicamp.br/vest_anteriores/2002/download/comentadas/Desempenho.pdf
- Rejeitado: Desempenho dos Candidatos — exclude_pattern: (?i)(edital|manual|estatistica|relatorio|perfil|demanda|nota.?de.?corte|programa|anuario|desempenho|lista|chamada|convocad|aprovad|classificacao|resultado|declaracao|isencao|vagas?|normas?) — https://www.comvest.unicamp.br/vest_anteriores/2001/download/comentadas/Desempenho.pdf
- Rejeitado: Desempenho dos Candidatos — exclude_pattern: (?i)(edital|manual|estatistica|relatorio|perfil|demanda|nota.?de.?corte|programa|anuario|desempenho|lista|chamada|convocad|aprovad|classificacao|resultado|declaracao|isencao|vagas?|normas?) — https://www.comvest.unicamp.br/vest_anteriores/2000/download/comentadas/Desempenho.pdf
- Rejeitado: Desempenho dos Candidatos — exclude_pattern: (?i)(edital|manual|estatistica|relatorio|perfil|demanda|nota.?de.?corte|programa|anuario|desempenho|lista|chamada|convocad|aprovad|classificacao|resultado|declaracao|isencao|vagas?|normas?) — https://www.comvest.unicamp.br/vest_anteriores/1999/download/comentadas/Desempenho.pdf
- Rejeitado: Desempenho dos Candidatos — exclude_pattern: (?i)(edital|manual|estatistica|relatorio|perfil|demanda|nota.?de.?corte|programa|anuario|desempenho|lista|chamada|convocad|aprovad|classificacao|resultado|declaracao|isencao|vagas?|normas?) — https://www.comvest.unicamp.br/vest_anteriores/1998/download/comentadas/Desempenho.pdf

## obmep_referencias

- Cenário: Referências públicas do Google Drive; download proibido
- URL inicial: https://www.obmep.org.br/provas-2025.htm
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Tempo: 0.29s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 0

- `exam` PROVA — `reference-only` — https://drive.google.com/file/d/10amPdrx4OVcfmGoQwQ0RHxvj5J_RN9ui/view
- `answer_key` SOLUÇÃO — `reference-only` — https://drive.google.com/file/d/1mZznC5F4aMvFzTvKHh1mX2ehhhyni6HX/view

## uerj_vestibular

- Cenário: Falha TLS da fonte deve ficar isolada
- URL inicial: https://sistema.vestibular.uerj.br/portal_vestibular_uerj/busca_rapida/provas_e_gabaritos.html
- Esperados/encontrados/aceitos: 2/0/0
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Tempo: 1.26s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 1
- Arquivos rejeitados por regra: 0

- Falha tratada (discovery): uerj_vestibular: falha ao ler https://sistema.vestibular.uerj.br/portal_vestibular_uerj/busca_rapida/provas_e_gabaritos.html: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1006)

## pci_concursos

- Cenário: Página de acervo, detalhe e data-url; desafio sem contorno
- URL inicial: https://www.pciconcursos.com.br/provas/banco-do-brasil
- Esperados/encontrados/aceitos: 2/0/0
- Caminho: deterministic
- Páginas visitadas: 1
- Chamadas ao Qwen: 0
- Tempo: 13.55s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 1
- Arquivos rejeitados por regra: 0

- Falha tratada (discovery): pci_concursos: acao manual necessaria: captcha em https://www.pciconcursos.com.br/provas/banco-do-brasil; nenhum contorno automatico foi tentado
