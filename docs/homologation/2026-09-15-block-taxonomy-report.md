# Campanha editorial PF, BB e CORE-PI

- Campanha: `pf-bb-corepi-editorial-20260908`
- Versão: `1.5`
- Estado de publicação: `draft`
- Inventário: `data/editorial-campaign/block-taxonomy-v1-offline/review/index.json`
- Hash: `4accd038c5f129d3570ccb11dbecb3e7c924b2d571ac2e71a4cdad07d0061ca3`

## Totais

| Indicador | Total |
|---|---:|
| Ocorrências brutas | 5580 |
| Questões únicas por conteúdo | 5292 |
| Ocorrências duplicadas | 288 |
| Estruturalmente aceitas | 5158 |
| Em quarentena | 422 |
| Classificadas somente por regra | 5580 |
| Com participação do Qwen | 0 |
| Taxonomia completa | 1191 |
| Cobertura taxonômica | 21.3% |
| Taxonomia não resolvida | 4389 |
| Decisões humanas | 0 |
| Aguardando revisão humana | 5580 |
| Aptas para exportação | 0 |

## Cobertura por campo

| Campo | Cobertura |
|---|---:|
| discipline | 21.3% |
| matter | 21.3% |
| subject | 21.3% |
| level | 100.0% |
| difficulty | 0.0% |

## Antes e depois do lote

| Indicador | Antes | Depois | Diferença |
|---|---:|---:|---:|
| Classificação completa | 0 | 1191 | +1191 |
| Pendentes | 5580 | 4389 | -1191 |

## Comparação auditada com a rodada anterior

A linha “Antes” acima mede apenas o estado limpo desta execução. Para avaliar a correção,
comparamos as sessões reais com a última rodada offline, usando a taxonomia 3.2.0 para validar
cada combinação completa de disciplina, matéria e assunto.

| Corpus | Válidas antes | Válidas agora | Ganho |
|---|---:|---:|---:|
| Polícia Federal / Cebraspe | 393 | 683 | +290 |
| Banco do Brasil / Cesgranrio | 271 | 409 | +138 |
| CORE-PI / Quadrix | 99 | 99 | 0 |
| **Total** | **763** | **1191** | **+428 (56,1%)** |

A rodada anterior dizia ter 1.042 classificações completas, mas 279 usavam rótulos estruturais
como `Bloco I`, `Bloco II`, `Bloco III`, `2` ou `4` em matéria/assunto. Esses casos não são
mais contados como classificação. Na nova rodada, as 1.191 combinações completas pertencem a
um caminho fechado da taxonomia; combinações completas inválidas: **0**.

O ganho veio sem Qwen: foram **0 chamadas**. A leitura da tabela oficial da capa forneceu a
disciplina por intervalo e o classificador fechou matéria/assunto somente quando encontrou
evidência controlada no texto ou em um bloco explícito. Sete classificações usaram propagação
de bloco verificada; a inspeção dos sete casos não encontrou conflito temático.

A campanha foi executada novamente no mesmo diretório. O segundo processamento manteve
5.580 ocorrências, 5.292 questões únicas, 1.191 caminhos completos e zero IDs estáveis
duplicados; nenhuma decisão ou questão adicional foi criada.

## Qwen local

| Indicador | Resultado |
|---|---:|
| Habilitado | não |
| Disponível | não |
| Modelo | qwen3:8b |
| Digest | indisponível |
| Chamadas | 0 |
| Questões enviadas | 0 |
| Sugestões aceitas para revisão | 0 |
| Sem sugestão segura | 0 |
| Falhas | 0 |
| Reuso do cache | 0 |
| Itens novos no cache | 0 |
| Sugestões sem apoio no enunciado | 0 |

O Qwen não aprovou nem publicou questões. Todas as sugestões continuam pendentes de decisão humana.

## Amostra estratificada

- Itens: 300
- Duplicatas semânticas na amostra: 0
- Estruturalmente aceitas: 276
- Em quarentena estrutural: 24
- Dependentes de elemento visual: 6
- Decisões humanas registradas: 0
- Precisão estrutural: não medida sem revisão humana.
- Precisão do gabarito: não medida sem revisão humana.
- Precisão da classificação: não medida sem revisão humana.

## Distribuição para revisão

### Banca

| Valor | Questões |
|---|---:|
| CEBRASPE | 4108 |
| CESGRANRIO | 1312 |
| QUADRIX | 160 |

### Órgão

| Valor | Questões |
|---|---:|
| Policia Federal | 4108 |
| Banco do Brasil | 1312 |
| Conselho Regional dos Representantes Comerciais do Piaui | 160 |

### Concurso

| Valor | Questões |
|---|---:|
| POLÍCIA FEDERAL | 1710 |
| bb0121 | 1312 |
| PF 25 | 1248 |
| PF 25 ADM | 1150 |
| CORE-PI 2026 | 160 |

### Ano

| Valor | Questões |
|---|---:|
| 2025 | 2398 |
| 2021 | 1792 |
| 2018 | 1230 |
| 2026 | 160 |

### Cargo

| Valor | Questões |
|---|---:|
| CONHECIMENTOS ESPECÍFICOS – CARGO 1 | 190 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 10 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 11 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 12 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 13 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 14 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 2 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 3 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 4 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 5 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 6 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 7 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 8 | 140 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 9 | 140 |
| CARGO 1: DELEGADO DE POLÍCIA FEDERAL | 120 |
| CARGO 2: AGENTE DE POLÍCIA FEDERAL | 120 |
| CARGO 3: ESCRIVÃO DE POLÍCIA FEDERAL | 120 |
| CARGO 4: PAPILOSCOPISTA DE POLÍCIA FEDERAL | 120 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 1 | 120 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 12 | 120 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 13 | 120 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 14 | 120 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 15 | 94 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 10/ÁREA 12 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 11/ÁREA 14 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 2/ÁREA 1 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 3/ÁREA 2 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 4 - ÁREA 3 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 5/ÁREA 4 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 6/ÁREA 5 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 7/ÁREA 6 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 8/ÁREA 7 | 70 |
| CONHECIMENTOS ESPECÍFICOS - CARGO 9/ÁREA 9 | 70 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 1 | 70 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 2 | 70 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 3 | 70 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 4 | 70 |
| PROVA C – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 5 | 70 |
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 3 | 69 |
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 4 | 69 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 2 | 69 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 3 | 69 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 4 | 69 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 1 | 69 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 2 | 69 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 3 | 69 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 4 | 69 |
| PROVA B – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 5 | 69 |
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 1 | 68 |
| AGENTE DE TECNOLOGIA – MICRORREGIÃO 16 DF-TI – GABARITO 2 | 68 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 1 | 68 |
| PROVA A – ESCRITURÁRIO – AGENTE COMERCIAL – GABARITO 5 | 68 |
| CONHECIMENTOS BÁSICOS – BLOCO I – PARA OS CARGOS 15, 16 E 17 | 60 |
| CONHECIMENTOS BÁSICOS PARA O CARGO 15 | 50 |
| CONHECIMENTOS BÁSICOS PARA O CARGO 2 - TODAS AS ÁREAS | 50 |
| CONHECIMENTOS BÁSICOS PARA OS CARGOS DE 1 A 14 | 50 |
| CONHECIMENTOS BÁSICOS PARA TODOS OS CARGOS DE PERITO CRIMINAL FEDERAL | 50 |
| Provas aplicadas 22/06/2026 - 200 Assistente Administrativo QUADRIX Concurso-2026 CORE-PI Prova | 40 |
| Provas aplicadas 22/06/2026 - 201 Fiscal QUADRIX Concurso-2026 CORE-PI Prova | 40 |
| Provas aplicadas 22/06/2026 - 400 Assistente Jurídico QUADRIX Concurso-2026 CORE-PI Prova | 40 |
| Provas aplicadas 22/06/2026 - 401 Contador QUADRIX Concurso-2026 CORE-PI Prova | 40 |
| CONHECIMENTOS BÁSICOS – BLOCO II – PARA OS CARGOS 15, 16 E 17 | 36 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 16 | 24 |
| CONHECIMENTOS ESPECÍFICOS – CARGO 17 | 24 |

### Disciplina

| Valor | Questões |
|---|---:|
| (não informado) | 4389 |
| Tecnologia da Informação | 156 |
| Conhecimentos Bancários | 136 |
| Língua Portuguesa | 123 |
| Informática | 101 |
| Direito Administrativo | 92 |
| Direito Processual Penal | 87 |
| Matemática Financeira | 68 |
| Direito Constitucional | 61 |
| Estatística | 59 |
| Administração Geral | 51 |
| Atualidades do Mercado Financeiro | 47 |
| Legislação do Sistema CONFERE/COREs | 41 |
| Contabilidade | 40 |
| Vendas e Negociação | 35 |
| Ética e Legislação Pública | 21 |
| Arquivologia | 20 |
| Língua Inglesa | 16 |
| Matemática | 16 |
| Direito Penal | 13 |
| Raciocínio Lógico-Matemático | 8 |

### Matéria

| Valor | Questões |
|---|---:|
| (não informado) | 4389 |
| Gramática | 108 |
| Sistema Financeiro Nacional | 90 |
| Banco de Dados | 72 |
| Ciência de Dados | 55 |
| Juros e Descontos | 50 |
| Estatística Descritiva | 45 |
| Organização do Estado | 44 |
| Agentes Públicos | 43 |
| Representação Comercial | 41 |
| Investigação Criminal | 39 |
| Internet e Redes | 38 |
| Segurança da Informação | 37 |
| Prova Penal | 34 |
| Compliance Bancário | 30 |
| Contabilidade Geral | 30 |
| Sistemas Operacionais e Escritório | 30 |
| Moedas e Pagamentos Digitais | 28 |
| Gestão de Pessoas | 26 |
| Infraestrutura de TI | 25 |
| Marketing | 25 |
| Gestão de Documentos | 20 |
| Transformação do Sistema Financeiro | 19 |
| Organização Administrativa | 18 |
| Sistemas de Amortização | 18 |
| Compreensão de Textos em Inglês | 16 |
| Produtos e Serviços Bancários | 16 |
| Interpretação de Textos | 15 |
| Licitações e Contratos | 15 |
| Ética Pública | 15 |
| Inferência Estatística | 14 |
| Medidas Cautelares | 14 |
| Atos Administrativos | 11 |
| Aritmética | 10 |
| Atendimento e Vendas | 10 |
| Contabilidade Pública | 10 |
| Administração de Materiais | 9 |
| Controle de Constitucionalidade | 9 |
| Penas | 9 |
| Processo Administrativo | 9 |
| Direitos Fundamentais | 8 |
| Lógica Proposicional | 8 |
| Atendimento ao Público | 7 |
| Geometria | 6 |
| Transparência e Dados | 6 |
| Poderes Administrativos | 5 |
| Crimes em Espécie | 4 |

### Assunto

| Valor | Questões |
|---|---:|
| (não informado) | 4389 |
| Instituições e Mercados | 90 |
| Modelagem e Consulta de Dados | 72 |
| Análise e Aprendizado de Máquina | 55 |
| Capitalização e Taxas | 50 |
| Medidas e Distribuições | 45 |
| Administração e Poderes | 44 |
| Regime e Responsabilidade | 43 |
| Coesão e Reescrita | 42 |
| Registro, Fiscalização e Ética | 41 |
| Inquérito Policial | 39 |
| Navegação, Correio e Redes | 38 |
| Pontuação e Concordância | 38 |
| Meios de Prova e Cadeia de Custódia | 34 |
| Ameaças, Proteção e Backup | 33 |
| PLD, Ética e Proteção de Dados | 30 |
| Patrimônio e Demonstrações Contábeis | 30 |
| Windows e Microsoft Office | 30 |
| Blockchain, Criptoativos e PIX | 28 |
| Liderança, Motivação e Equipes | 26 |
| Marketing Digital e Serviços | 25 |
| Redes de Computadores | 25 |
| Ciclo, Avaliação e Protocolo | 20 |
| Inovação e Novos Modelos | 19 |
| Administração Direta e Indireta | 18 |
| Prestações e Saldos | 18 |
| Crédito, Investimentos e Meios de Pagamento | 16 |
| Interpretação Textual | 16 |
| Compreensão e Interpretação | 15 |
| Licitação Pública | 15 |
| Ética, Improbidade e Processo Administrativo | 15 |
| Estimação e Testes | 14 |
| Morfossintaxe | 14 |
| Prisões e Liberdade Provisória | 14 |
| Regência e Crase | 14 |
| Elementos, Atributos e Extinção | 11 |
| Razões, Proporções e Porcentagem | 10 |
| Receita, Despesa e Orçamento | 10 |
| Relacionamento com o Cliente | 10 |
| Aplicação e Extinção da Pena | 9 |
| Controle Concentrado e Difuso | 9 |
| Estoques e Patrimônio | 9 |
| Planejamento, Organização, Direção e Controle | 9 |
| Direitos e Garantias Fundamentais | 8 |
| Proposições e Conectivos | 8 |
| Qualidade e Relações Interpessoais | 7 |
| Geometria Plana e Espacial | 6 |
| LAI e LGPD | 6 |
| Poder de Polícia e Abuso de Poder | 5 |
| Crimes contra a Administração e a Pessoa | 4 |
| Gestão e Controles de Segurança | 4 |

### Nível

| Valor | Questões |
|---|---:|
| Superior | 4188 |
| Médio | 1392 |

### Dificuldade

| Valor | Questões |
|---|---:|
| (não informado) | 5580 |

### Estado

| Valor | Questões |
|---|---:|
| pending | 5580 |

## Maiores lacunas taxonômicas

| Banca | Concurso | Ano | Cargo/bloco | Questões |
|---|---|---:|---|---:|
| CEBRASPE | POLÍCIA FEDERAL | 2021 | CARGO 4: PAPILOSCOPISTA DE POLÍCIA FEDERAL | 88 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 12 | 86 |
| CEBRASPE | POLÍCIA FEDERAL | 2021 | CARGO 2: AGENTE DE POLÍCIA FEDERAL | 85 |
| CEBRASPE | POLÍCIA FEDERAL | 2021 | CARGO 1: DELEGADO DE POLÍCIA FEDERAL | 84 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 1 | 84 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 14 | 80 |
| CEBRASPE | POLÍCIA FEDERAL | 2021 | CARGO 3: ESCRIVÃO DE POLÍCIA FEDERAL | 79 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 13 | 72 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 3 | 70 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 6/ÁREA 5 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 13 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 8 | 70 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 11/ÁREA 14 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 6 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 7 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 4 | 70 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 10 | 70 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 14 | 70 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 5 | 70 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 9 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 2 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 10 | 70 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 5/ÁREA 4 | 70 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 3/ÁREA 2 | 70 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 7 | 70 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 11 | 69 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 1 | 69 |
| CEBRASPE | PF 25 | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 12 | 69 |
| CEBRASPE | PF 25 ADM | 2025 | CONHECIMENTOS ESPECÍFICOS – CARGO 9 | 69 |
| CEBRASPE | POLÍCIA FEDERAL | 2018 | CONHECIMENTOS ESPECÍFICOS - CARGO 10/ÁREA 12 | 68 |

## Limitações

- A amostra ainda não recebeu revisão humana; as metas de precisão não podem ser alegadas.
- Nenhuma questão tem aprovação humana válida; a exportação real permanece bloqueada.
- 4389 questões não receberam classificação completa na taxonomia atual.
- A cobertura taxonômica ficou abaixo da meta de 90%; o acervo não pode ser declarado pronto para importação.

Nenhum dado foi enviado ao KAD ou ao Supabase.
