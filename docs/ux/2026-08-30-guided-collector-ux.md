# UX guiada do KAD Collector

## Diagnóstico

O Collector atende quatro trabalhos diferentes na mesma página: acompanhar coletas,
validar documentos, classificar questões e fazer revisão editorial. A navegação lateral e
a régua horizontal repetem as mesmas etapas, mas somente **Coletar** abre uma tela própria.
As demais opções rolam para blocos de uma página longa. O cabeçalho ainda oferece ações de
Qwen, reclassificação, exportação e novo lote antes de indicar o trabalho atual.

O modelo da interface também acompanha as tabelas internas. Termos como questão canônica,
ocorrência, grupo equivalente e escopo aparecem antes da ação que explicam. Isso transforma
uma decisão sobre uma prova em dezenas de pendências visuais sobre questões.

### Problemas prioritários

1. Duas navegações representam o mesmo fluxo.
2. Cinco etapas aparecem juntas e disputam atenção.
3. Ações por prova e por questão compartilham o mesmo espaço.
4. Filtros e diagnósticos ocupam o caminho principal.
5. Textos entre 9 px e 11 px reduzem legibilidade.
6. Cores de ação, alerta e estado não seguem uma hierarquia única.

## Público e trabalho principal

O público é a pessoa responsável por preparar um acervo de questões de concurso. Em cada
tela, ela precisa concluir um único trabalho: coletar, validar provas, classificar, revisar
ou exportar. A interface mostra primeiro a decisão humana e mantém identificadores, versões
de algoritmo e evidências em **Ver detalhes técnicos**.

## Arquitetura proposta

Uma única navegação lateral controla seis telas exclusivas:

```text
┌──────────────────┬──────────────────────────────────────────────────────────┐
│ KAD Collector    │ Tela atual                                               │
│                  │                                                          │
│ Início           │ Título + explicação curta                    Ação primária│
│ 1 Coletar        │                                                          │
│ 2 Validar provas │ Conteúdo necessário para concluir esta etapa             │
│ 3 Classificar    │                                                          │
│ 4 Revisar        │ Detalhes técnicos e operações em massa recolhidos        │
│ 5 Exportar       │                                                          │
│                  │                                                          │
│ 100% local       │                                                          │
└──────────────────┴──────────────────────────────────────────────────────────┘
```

### Início

```text
Próxima ação recomendada                                  [Continuar]
Motivo, quantidade afetada e etapa atual

Acervo: total | por validar | para revisar | pronto para exportar

Processos em andamento / último resultado

▸ Diagnósticos do acervo
▸ Banco e detalhes técnicos
```

### Validar provas

```text
Validar provas                              [Validar provas deste conjunto]
Uma confirmação por prova ou conjunto prova + gabarito

70 questões encontradas | 70 prontas | 0 provas com pendência

┌ Banco do Brasil · CESGRANRIO · 2023 ─────────────────────────────┐
│ Escriturário · Agente Comercial       70 questões                │
│ Prova identificada · gabarito relacionado                        │
│                                                    [Revisar prova]│
└──────────────────────────────────────────────────────────────────┘

▸ Auditoria e detalhes técnicos
▸ Operações em massa
```

### Classificar

```text
Classificar questões                         [Classificar com Qwen]
70 podem ser classificadas

Estado | progresso | tempo | pausa/retomada

▸ O que o Qwen pode alterar
▸ Reclassificação e configurações
```

### Revisar

```text
Revisar pendências
[Precisa de revisão] [Exceções] [Prontas]

▸ Filtros avançados

Lista de questões com seleção e ações em lote
```

### Exportar

```text
Exportar questões                                 [Revisar exportação]
70 prontas | 0 bloqueadas

Incluídas e excluídas por motivo
Última exportação
```

## Sistema visual

O conceito visual é uma **mesa editorial de provas**. A navegação funciona como lombada do
acervo; cartões de prova lembram fichas de identificação, com metadados alinhados e estado
legível. Essa é a assinatura visual. O restante usa superfícies planas e poucos efeitos.

### Tokens

| Papel | Token | Valor |
| --- | --- | --- |
| Fundo | `--canvas` | `#F3F5F7` |
| Superfície | `--surface` | `#FFFFFF` |
| Texto | `--ink` | `#172033` |
| Texto secundário | `--muted` | `#667085` |
| Ação | `--indigo` | `#4F46E5` |
| Sucesso | `--teal` | `#0F766E` |
| Atenção | `--amber` | `#B45309` |
| Bloqueio | `--red` | `#B4233F` |

- Títulos: Bahnschrift SemiCondensed, com Aptos como alternativa.
- Corpo e controles: Aptos/Segoe UI, mínimo de 13 px.
- Dados técnicos: Cascadia Mono/Consolas.
- Espaçamento: escala de 4, 8, 12, 16, 24 e 32 px.
- Raios: 8 px para controles, 12 px para cartões e 16 px para painéis principais.
- Movimento: transição única de entrada de 160 ms, desativada com movimento reduzido.

## Linguagem

| Interno | Fluxo principal |
| --- | --- |
| Preparação canônica | Validar provas |
| Questão canônica | Questão principal, somente nos detalhes |
| Ocorrências equivalentes | Questões repetidas |
| Escopo do filtro | Questões deste filtro |
| Exceção editorial | Precisa de revisão |
| Executar preparação | Validar prova |

## Autocrítica do desenho

A primeira proposta usava outra régua horizontal para mostrar as cinco etapas. Ela repetia a
navegação e recriava o problema atual. A versão escolhida incorpora estado e quantidade na
própria navegação lateral. Cada tela preserva um único botão primário e usa cartões somente
quando representam provas, processos ou filas reais.

## Cenário de aceitação

No conjunto PCI Concursos, Banco do Brasil/CESGRANRIO 2023, Escriturário - Agente
Comercial, o operador encontra uma ficha de prova com 70 questões. A prévia mostra uma
unidade de confirmação e inclui as 70 questões. Depois da validação, a tela de classificação
oferece o Qwen; revisão mostra somente decisões humanas pendentes; exportação informa
incluídas, excluídas e motivos.

## Capturas de validação

- [Interface anterior](screenshots/before-overview.png)
- [Nova central de trabalho](screenshots/after-overview.png)
- [Validação organizada por prova](screenshots/after-validation.png)
- [Prévia PCI com uma confirmação para 70 questões](screenshots/after-pci-validation-preview.png)
- [Exportação em janela compacta](screenshots/after-export-responsive.png)
