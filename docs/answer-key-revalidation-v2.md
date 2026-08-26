# Revalidação de gabaritos v2

## O que foi substituído

O mecanismo anterior (`semantic-association-v1`) atribuía pontos às evidências
semânticas e podia aceitar uma associação com parte do escopo desconhecida. A
seleção também permitia desempates editoriais entre candidatos equivalentes.

`semantic-association-v2` mantém a comparação de banca, concurso e ano, mas só
ativa um vínculo quando cargo, etapa, turno, caderno/tipo e intervalo objetivo
estão presentes e são compatíveis. Os valores são normalizados antes da
comparação. Um empate na melhor classificação válida não escolhe vencedor: o
vínculo anterior é desativado, as respostas derivadas são invalidadas e o caso
entra na fila de revisão.

Para documentos FGV, o turno também pode vir de uma região estrutural do PDF.
`MANHÃ`, `MANHA` e `TARDE` são normalizados para `manhã` e `tarde`. Uma prova
precisa ter exatamente um turno; um gabarito pode cobrir os dois. O turno da
prova seleciona primeiro a grade correspondente e só então o intervalo é
comparado. Menções a manhã ou tarde depois do início das questões não contam
como evidência. Cabeçalhos isolados de turno no gabarito valem para as grades
seguintes; se houver grades identificadas por turno e nenhuma corresponder à
prova, nenhuma resposta é aproveitada.

Chamadas legadas que ainda não fornecem intervalos continuam legíveis durante a
transição. O processamento do banco local e a rotina de revalidação sempre
fornecem intervalos e, portanto, executam as regras estritas da v2.

## Migração do banco local

A abertura do `DesktopStore` cria, sem apagar registros antigos:

- `association_revalidation_runs`, com estado e cursor da execução;
- `association_revalidation_audit`, histórico append-only da decisão anterior e
  da v2;
- `association_review_queue`, fila operacional para toda prova que continue sem
  associação, com os campos incompletos ou conflitantes no motivo;
- proveniência e motivo de invalidação nas questões.

Os vínculos antigos continuam disponíveis no histórico. Uma associação mantida
ganha um novo vínculo v2, ligado ao anterior como sucessor. Exportações aceitam
respostas de documentos semânticos somente quando a questão aponta para um
vínculo v2 ativo.

## Execução

Simule primeiro. O modo padrão abre um banco existente em modo imutável e
somente leitura: não cria banco, tabelas, WAL ou SHM e não altera os arquivos
existentes. Use uma cópia consistente e fechada do SQLite antes da simulação.

```powershell
kad-collector revalidate-answer-keys `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --report data/reports/answer-key-revalidation-dry-run.json
```

Depois de revisar o relatório, aplique com um identificador que possa ser usado
para retomada:

```powershell
kad-collector revalidate-answer-keys `
  --database "$env:LOCALAPPDATA\KAD Collector\collector.sqlite3" `
  --apply `
  --run-id 2026-08-association-v2 `
  --report data/reports/answer-key-revalidation.json
```

Se a execução for interrompida, repita o segundo comando com o mesmo `run-id`.
Cada prova é confirmada em uma transação separada. Registros já auditados não são
reprocessados. Provas FGV que nunca tiveram vínculo também participam da
primeira reconciliação; documentos sem vínculo inicial de outras bancas ficam
fora desta migração específica. Vínculos legados de qualquer banca continuam
elegíveis para migração. Depois que todos os casos forem associados, auditados
ou enviados à revisão, uma nova execução informa zero associações pendentes e
não cria histórico adicional. Uma revisão humana concluída não é reaberta
automaticamente.

`--limit N` permite executar lotes controlados. Um relatório `paused` indica que
ainda existem vínculos antigos pendentes.

## Exemplo de relatório

```json
{
  "runId": "2026-08-association-v2",
  "algorithmVersion": "semantic-association-v2",
  "mode": "apply",
  "status": "completed",
  "associationsExamined": 3,
  "maintained": 1,
  "changed": 1,
  "invalidated": 0,
  "ambiguous": 1,
  "incomplete": 0,
  "answersInvalidated": 140,
  "sentToReview": 1,
  "byContest": {
    "concurso exemplo": 3
  },
  "byDocument": {
    "document-id-1": 1,
    "document-id-2": 1,
    "document-id-3": 1
  }
}
```

Cada item de `cases` contém apenas identificadores locais, resultado e motivo.
O histórico no SQLite acrescenta os perfis comparados, intervalos, candidatos,
pontuações, evidências, vínculo anterior e vínculo novo.

## Riscos e limites restantes

- Metadados ausentes deixam a associação incompleta; não há inferência para
  forçar compatibilidade.
- Um gabarito parcial não substitui outro quando seu intervalo não fecha com a
  prova objetiva.
- A v2 não cria a identidade canônica planejada para a PR 4.
- Duplicidade canônica, IA, OCR e enriquecimento editorial permanecem fora desta
  mudança.
- Os PDFs oficiais do RFB22 continuam fora do Git. A regressão versionada usa o
  manifesto oficial para validar todos os 16 cadernos suportados e seus escopos;
  a execução completa com PDFs exige a preparação local já documentada.

## Validação local do RFB22

O dry-run de 25 de agosto de 2026, executado em uma cópia ignorada do banco,
examinou 23 provas sem vínculos anteriores:

- 16 provas processadas obtiveram associação segura, cobrindo 1.120 questões;
- seis provas excepcionadas permaneceram em conflito, cobrindo 360 questões;
- uma prova excepcionada permaneceu incompleta, cobrindo 60 questões;
- as 420 questões dos sete documentos de curso de formação continuam
  bloqueadas e não foram consideradas prontas.

O relatório local e a cópia do SQLite ficam fora do Git. Nenhuma reconciliação
foi aplicada ao banco original ou enviada ao Supabase.

A aplicação controlada somente nessa cópia criou 16 vínculos com gabaritos
definitivos, resolveu 1.120 respostas e deixou 420 em `missing`. Os sete casos
restantes entraram uma única vez na fila de revisão; uma segunda execução
examinou zero provas. Nenhum vínculo selecionado divergiu em cargo, etapa,
turno, tipo ou intervalo.
