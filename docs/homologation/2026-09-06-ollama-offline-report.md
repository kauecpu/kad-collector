# Homologação real das fontes do KAD Collector

- Data UTC: 2026-09-06T23:27:33.650322+00:00
- Commit: `16635f4-dirty`
- Ollama: indisponível de propósito
- Precisão: 100.0%
- Cobertura: 100.0%
- Associação prova/gabarito: 100.0%
- Falsos positivos: 0
- Falsos negativos: 0
- Fontes sem intervenção: 100.0%
- Tempo médio por fonte: 6.55s

## inep_enem

- Cenário: Acervo anual em data-url; fallback Qwen e PDF textual
- URL inicial: https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enem/provas-e-gabaritos
- Esperados/encontrados/aceitos: 2/2/2
- Caminho: deterministic
- Páginas visitadas: 3
- Chamadas ao Qwen: 1
- Tempo: 6.55s
- OCR tentado/suficiente: 0/0
- Falhas isoladas: 0
- Arquivos rejeitados por regra: 0

- `answer_key` Gabarito — `583ac1950cd1927c42c971c270f96c0e33816844084fd5866a13bf94d8e08a81` — https://download.inep.gov.br/enem/provas_e_gabaritos/2025_GB_impresso_D1_CD1.pdf
- `exam` Prova — `a0f19f878d4ef3ba94c3e0f5ef9e57c13fb046d4bb362f72651898b473ddb7c8` — https://download.inep.gov.br/enem/provas_e_gabaritos/2025_PV_impresso_D1_CD1.pdf
