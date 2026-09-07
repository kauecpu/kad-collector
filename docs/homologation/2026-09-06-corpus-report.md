# Homologação real de PDFs

- Execução: `2026-09-06T23:36:37.234895+00:00`
- Commit: `16635f4-dirty`
- Sistema: `Windows-10-10.0.26200-SP0`
- Documentos: 23
- Bancas: {"Cebraspe": 2, "FCC": 2, "FGV": 19}
- Taxa de download: não medida; arquivos já presentes no cache
- Integridade do cache: 100.0%
- Triagem automática correta: 100.0%
- Documentos concluídos: 100.0%
- Recuperação OCR: 56.2%
- Retomada: 100.0%
- Tempo mediano: 27.906 s
- Pico aproximado de memória Python (bytes; None = não medido): 195959302

## Resultado por documento

| Documento | Banca | Tipo | Triagem | Páginas | OCR | Questões | Respostas | Tempo | Resultado |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| rfb22-main-2023-answer-key-preliminary | FGV | answer_key | answer_key | 4 | 0 | 0 | 0 | 9.484s | success |
| rfb22-main-2023-answer-key-auditor-definitive | FGV | answer_key | answer_key | 2 | 0 | 0 | 0 | 3.250s | success |
| rfb22-main-2023-answer-key-analyst-definitive | FGV | answer_key | answer_key | 2 | 0 | 0 | 0 | 3.531s | success |
| rfb22-main-2023-auditor-morning-type-1 | FGV | exam | exam | 20 | 1 | 80 | 78 | 32.125s | success |
| rfb22-main-2023-auditor-morning-type-2 | FGV | exam | exam | 20 | 1 | 80 | 78 | 27.906s | success |
| rfb22-main-2023-auditor-morning-type-3 | FGV | exam | exam | 20 | 1 | 80 | 78 | 27.968s | success |
| rfb22-main-2023-auditor-morning-type-4 | FGV | exam | exam | 20 | 1 | 80 | 78 | 28.203s | success |
| rfb22-main-2023-auditor-afternoon-type-1 | FGV | exam | exam | 20 | 1 | 60 | 58 | 31.704s | success |
| rfb22-main-2023-auditor-afternoon-type-2 | FGV | exam | exam | 20 | 1 | 60 | 58 | 32.828s | success |
| rfb22-main-2023-auditor-afternoon-type-3 | FGV | exam | exam | 20 | 1 | 60 | 58 | 31.906s | success |
| rfb22-main-2023-auditor-afternoon-type-4 | FGV | exam | exam | 20 | 1 | 60 | 58 | 31.750s | success |
| rfb22-main-2023-analyst-morning-type-1 | FGV | exam | exam | 16 | 1 | 70 | 69 | 24.094s | success |
| rfb22-main-2023-analyst-morning-type-2 | FGV | exam | exam | 16 | 1 | 70 | 69 | 24.328s | success |
| rfb22-main-2023-analyst-morning-type-3 | FGV | exam | exam | 16 | 1 | 70 | 69 | 23.468s | success |
| rfb22-main-2023-analyst-morning-type-4 | FGV | exam | exam | 16 | 1 | 70 | 69 | 23.500s | success |
| rfb22-main-2023-analyst-afternoon-type-1 | FGV | exam | exam | 20 | 1 | 70 | 68 | 33.141s | success |
| rfb22-main-2023-analyst-afternoon-type-2 | FGV | exam | exam | 20 | 1 | 70 | 68 | 32.844s | success |
| rfb22-main-2023-analyst-afternoon-type-3 | FGV | exam | exam | 20 | 1 | 70 | 68 | 33.422s | success |
| rfb22-main-2023-analyst-afternoon-type-4 | FGV | exam | exam | 20 | 1 | 70 | 68 | 32.891s | success |
| cebraspe-pcdf13-agent-exam | Cebraspe | exam | exam | 16 | 2 | 120 | 118 | 16.047s | success |
| cebraspe-pcdf13-agent-answer-key | Cebraspe | answer_key | answer_key | 1 | 0 | 0 | 0 | 0.265s | success |
| fcc-dpeba125-answer-key-notice | FCC | answer_key | answer_key | 3 | 0 | 0 | 0 | 2.625s | success |
| fcc-dpeba125-discursive-result | FCC | other | other | 9 | 0 | 0 | 0 | 5.407s | success |

## Retomada e duplicação

```json
{
  "document_id": "rfb22-main-2023-auditor-morning-type-1",
  "paused_status": "paused",
  "resumed_status": "completed",
  "duplicate_blocked": true,
  "checkpoint_pages": 3,
  "resumed_pages": 20,
  "checkpoint_text_preserved": true,
  "new_store_and_processor": true,
  "passed": true
}
```

## Limites observados

- `peak_python_bytes` mede alocações do Python e não inclui toda a memória nativa do OCR.
- Prova e gabarito no mesmo PDF e detecção completa de republicações permanecem fora deste trabalho.
- PDFs do corpus ficam no cache local e não entram no Git.
- Taxa OCR inclui páginas visualmente vazias; recuperação não mede fidelidade textual.
- Tempo e memória incluem o gabarito pareado quando houver.
- Triagem automática é medida separadamente do tipo declarado pelo operador.
