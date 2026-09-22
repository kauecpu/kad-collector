# Auditoria técnica do primeiro lote Banco do Brasil/Cesgranrio

Esta auditoria reavaliou as 41 candidatas automáticas do PR #109. Não é uma
aprovação editorial humana. Nenhum documento foi publicado ou enviado ao KAD.

## Evidência consultada

| Caderno oficial, identificado pelo conteúdo do PDF | SHA-256 da prova | SHA-256 do gabarito | Candidatas anteriores |
|---|---|---|---:|
| Agente de Tecnologia, gabarito 4, 2021 | `0517d1e62644c0ccc6a9b52908e56d2bd07d8de94e716433cf8009aaa4db4afd` | `e6b4d810b4c71b439a580cd7e9d30479f310f734512b86add595072bbbbc814b` | 27 |
| Agente Comercial, prova C, gabarito 1, 2021 | `120216a86e22ea76a387b2c6cbd9629c88766c448009eb4594cab41833ed6ade` | `88f866a0212a4dfe41a73edaaabeb8ccdd689aa5d8935ed0d34fb1af77f35902` | 14 |

Os nomes e diretórios de download desses PDFs estão invertidos na origem:
arquivos sob `/gabarito/` contêm o caderno de questões, e arquivos sob
`/provas/` contêm tabelas de respostas. Os hashes e o conteúdo, não os nomes,
foram usados para identificar os papéis. As respostas das 41 questões foram
confrontadas com as tabelas oficiais: **41/41 coincidem**. Isso não valida os
enunciados nem a taxonomia.

## Resultado da reavaliação

| Destino das 41 candidatas anteriores | Quantidade | Questões |
|---|---:|---|
| Estrutura bloqueada | 17 | Tecnologia: 4, 7, 10, 13, 25, 28, 30, 35, 54, 60; Comercial: 2, 3, 4, 7, 10, 14, 15 |
| Taxonomia pendente | 3 | Tecnologia: 23, 33; Comercial: 23 |
| Permanecem na amostra, sem aprovação | 21 | Tecnologia: 18, 21, 36, 40, 41, 42, 44, 45, 49, 53, 64, 65, 67, 68, 69; Comercial: 22, 26, 27, 28, 29, 31 |

Nas 21 restantes, os primeiros trechos dos enunciados e das cinco alternativas
foram confrontados com as páginas indicadas dos PDFs: **126/126 trechos
localizados**. As 21 passam pelo contrato local de importação, mas continuam
sem auditoria editorial completa. A Comercial 31 merece atenção especial: o
Qwen classificou uma questão de taxa de câmbio em “Instituições e Mercados”;
a taxonomia atual não oferece um assunto explícito para câmbio. Não a considero
aprovada por essa aproximação.

No lote original de 100, o novo filtro resulta em **21 na amostra obrigatória,
28 pendentes de revisão e 51 em quarentena**. São estados de triagem, não uma
contagem de questões prontas para o app.

## Defeitos reproduzidos e correções

| Defeito | Exemplo no PDF oficial | Correção geral |
|---|---|---|
| Texto de apoio ausente | Tecnologia 4 e 13; Comercial 2–4 e 14–15 referem-se a parágrafos não anexados | Referências a texto externo sem contexto bloqueiam aprovação automática |
| Ênfase perdida | Tecnologia 7 e 10; Comercial 10 perguntam por termo “em destaque”, mas o destaque não sobrevive à extração | Questões dependentes dessa formatação ficam em quarentena |
| Página seguinte anexada à alternativa | Tecnologia 10, 25, 30, 35, 54, 60; Comercial 10 e 15 incluem “RASCUNHO”, cabeçalho ou seção da próxima página | Conteúdo com esses marcadores não passa como estrutura íntegra |
| Glifo ilegível | Tecnologia 28 tem o sinal de menos extraído como caractere privado `U+F02D` | Glifos privados ou de substituição bloqueiam aprovação |
| Assunto inferido de distratores | Tecnologia 23 pergunta pelo Registrato, mas “blockchain”, “bitcoin” e “Pix” nas alternativas puxaram o assunto “Blockchain, Criptoativos e PIX” | Regra local passa a usar o enunciado, não os distratores; classificações antigas sem suporte no enunciado ficam pendentes |

As mudanças são gerais e não dependem do endereço dos PDFs. Não reescrevem
questões nem fazem classificação por aproximação.

## Segurança da importação

- Exportação real para staging foi recusada: `nenhum grupo auditado foi aprovado para staging`.
- Nenhum diretório de staging foi criado; nenhuma escrita no KAD ou Supabase.
- Reprocessar a campanha com a mesma entrada preservou o hash do estado, as
  classificações e os bloqueios.
- A auditoria amostral exigida pelo coletor permanece pendente. Os 21 itens não
  devem ser publicados apenas porque passaram pelo contrato de formato.

## Validação local

| Verificação | Resultado |
|---|---|
| Suíte completa | 988 testes e 119 subtestes passaram |
| Lint | passou |
| Tipos | passou em 83 arquivos |
| Wheel e instalação isolada | `kad_collector-0.4.0` construído e importado da instalação de teste; regra `1.2.0` presente |
| Exportação de staging sem grupos aprovados | recusada, sem criar saída |
| Repetição do cálculo de aprovação | mesmo hash e mesmos estados |

Próximo trabalho: corrigir a extração de textos compartilhados, ênfase e limites
de página na origem; reprocessar o mesmo lote; revisar especialmente o assunto
de câmbio; somente então concluir a auditoria da nova amostra e exportar para
staging.
