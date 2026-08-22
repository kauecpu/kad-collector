# Semantic Document Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar identidade semântica determinística, deduplicação binária, republicações, versões sucessoras e associação auditável de provas e gabaritos ao fluxo neutro criado pelo PR 17.

**Architecture:** O contrato normalizado continua sendo a fronteira de entrada. Funções puras extraem um perfil semântico e uma impressão digital do texto; um registro SQLite aditivo resolve observações e versões em transações curtas; o processador só estrutura questões de versões lógicas novas. A associação usa o mesmo avaliador conservador no desktop e no fluxo offline, e decisões humanas são transportadas apenas quando o conteúdo editorial relevante permanece idêntico.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLite, pypdf, `unittest`, JavaScript e HTML/CSS locais, Ruff e mypy.

**Spec:** `docs/superpowers/specs/2026-08-20-semantic-document-identity-design.md`

## Global Constraints

- Trabalhar somente no repositório `kad-collector`, no worktree isolado `C:\Users\unluc\.codex\worktrees\semantic-identity\kad-collector`, branch `codex/semantic-document-identity`.
- Preservar a separação do PR 17: aquisição localiza e baixa; o motor neutro identifica, versiona, associa e interpreta.
- Não adicionar condição por banca, URL, `source_id` ou `provider` ao motor semântico.
- Ausência de evidência é `unknown`; conflito ou evidência insuficiente nunca produz associação automática.
- Duplicata binária não cria linha em `documents`, tarefa ou questão; republicação não cria versão lógica nem questões.
- Migrações SQLite são aditivas, idempotentes e não inferem identidade de registros legados.
- O mesmo contrato, conteúdo e versão de algoritmo devem produzir a mesma chave e a mesma decisão.
- Decisões humanas históricas nunca são apagadas; uma sucessora só as recebe após igualdade do conteúdo editorial relevante.
- PDF sem texto permanece como exceção de OCR.
- Não incluir `.env`, segredo, token, cookie, chave privada ou `service_role` em arquivo, log, teste ou commit.
- Escrever o teste que falha antes de cada mudança, executar o teste focal e commitar somente quando ele estiver verde.

## File Map

- Create `src/kad_collector/semantic_identity.py`: contratos imutáveis, JSON canônico, normalização, impressão de conteúdo e extração genérica de perfil.
- Create `src/kad_collector/semantic_resolution.py`: resolução pura de versão e avaliação conservadora de candidatos a gabarito.
- Create `src/kad_collector/semantic_registry.py`: schema SQLite aditivo, operações transacionais, eventos, vínculos e leituras resumidas.
- Modify `src/kad_collector/desktop_store.py`: integrar o registro semântico ao ciclo de tarefas, documentos, questões e correções humanas.
- Modify `src/kad_collector/document_pipeline.py`: ignorar duplicatas exatas antes de iniciar o processador e preservar reprocessamento explícito.
- Modify `src/kad_collector/desktop_processor.py`: resolver versões após extração, ignorar republicações, associar gabaritos e reconciliar sucessores.
- Modify `src/kad_collector/review_queue.py`: substituir a seleção heurística pelo avaliador semântico puro.
- Modify `src/kad_collector/desktop_server.py`: expor recibos de importação e informações semânticas somente leitura.
- Modify `src/kad_collector/desktop_app.js`: informar duplicatas e mostrar identidade, versão, evidências e vínculos.
- Modify `src/kad_collector/desktop_ui.html`: adicionar contêineres mínimos para o histórico semântico.
- Modify `src/kad_collector/desktop_styles.css`: estilizar selos e evidências sem reformular a interface.
- Modify `README.md`: documentar regras operacionais, migração, exceções e diagnóstico.
- Create `tests/semantic_helpers.py`: fábricas determinísticas compartilhadas pelos testes semânticos.
- Create `tests/test_semantic_identity.py`: contratos, normalização, extração e impressão de conteúdo.
- Create `tests/test_semantic_registry.py`: migração, idempotência, concorrência, eventos e versões.
- Create `tests/test_semantic_workflow.py`: submissão, republicação, sucessão, associação, linhagem e correção manual.
- Modify `tests/test_document_pipeline.py`: convergência entre coleta, importação, repetição e reprocessamento.
- Modify `tests/test_desktop_app.py`: API, migração, interface, resumo e auditoria.
- Modify `tests/test_desktop_collection.py`: repetição de coleta e gabarito armazenado.
- Modify `tests/test_review_automation.py`: associação semântica offline, empate e conflitos.
- Modify `tests/regression/COVERAGE.md`: mapear os 25 cenários obrigatórios para testes executáveis.

## Test Harness Contract

`tests/semantic_helpers.py` também deve fornecer `write_text_pdf`,
`legacy_store_with_one_document` e `count_semantic_tables`. O primeiro usa ReportLab e permite
variar apenas o autor do PDF para produzir bytes diferentes com o mesmo texto. O segundo cria
o schema legado mínimo de `jobs` e `documents`, insere uma linha conhecida, fecha a conexão e
retorna `DesktopStore(path)`. O terceiro consulta `sqlite_master` e conta somente as sete
tabelas semânticas nomeadas na Task 3.

`SemanticWorkflowTests.setUp` usa `TemporaryDirectory`, `DesktopStore`, um runner síncrono que
chama `DesktopProcessor.run(job_id)` e metadados fixos `Banca Oficial`, `Concurso Nacional
2026`, ano `2026`, cargo `Analista`. Os helpers usados nos snippets têm estes contratos:

- `count(table) -> int` e `origin_count(sha256) -> int` executam `SELECT COUNT(*)` somente para
  nomes de tabela constantes definidos no teste;
- `process_pdf`, `process_text`, `process_exam` e `process_key` criam PDFs com
  `write_text_pdf`, submetem pelo pipeline síncrono e retornam um `WorkflowResult` imutável com
  `document_id`, `document_version_id`, `predecessor_version_id`, `version_number`,
  `resolution`, `question_count`, `warnings` e `path`;
- `exam(...)` e `key(...)` constroem `DocumentSemanticProfile` e `AssociationCandidate` com
  valores padrão compatíveis e substituem somente os argumentos informados;
- `question`, `answer`, `link`, `lineage`, `document_actions`, `audit_actions`, `event_count` e
  `last_identity_event` consultam o SQLite, sem alterar estado;
- `approve` chama `DesktopStore.decide_question` com status `approved`;
- `metadata(...)` retorna `DesktopImportMetadata` com os padrões do teste;
- `get_json(path)` usa o servidor local autenticado já adotado por `tests/test_desktop_app.py`.

Implemente esses helpers no primeiro teste que os consumir; não adicione métodos de inspeção
somente para testes à API de produção.

---

### Task 1: Contratos imutáveis, chaves e impressão do conteúdo

**Files:**
- Create: `src/kad_collector/semantic_identity.py`
- Create: `tests/semantic_helpers.py`
- Create: `tests/test_semantic_identity.py`

**Interfaces:**
- Consumes: `NormalizedDocument` de `document_contract.py` e páginas no formato `Sequence[tuple[int, str]]`.
- Produces: `SemanticEvidence`, `SemanticField`, `ExamSemanticIdentity`, `AnswerKeyCoverage`, `ContentFingerprint`, `DocumentSemanticProfile`, `KnownDocumentVersion`, `IdentityResolution`, `AssociationCandidate`, `CandidateAssessment`, `DocumentAssociationDecision`, `canonical_json`, `stable_sha256`, `semantic_identity_key` e `build_content_fingerprint`.

- [ ] **Step 1: Escrever testes que fixem invariantes e hashes**

Use fábricas sem relógio ou UUID aleatório:

```python
# tests/semantic_helpers.py
from pathlib import Path

from reportlab.pdfgen import canvas

from kad_collector.document_contract import DeclaredDocumentType, NormalizedDocument
from kad_collector.semantic_identity import (
    ExamSemanticIdentity,
    SemanticEvidence,
    SemanticField,
)


def normalized_document(
    path: Path,
    *,
    sha256: str = "a" * 64,
    declared_type: DeclaredDocumentType = "exam",
    metadata: dict[str, str | int] | None = None,
    title: str = "prova.pdf",
) -> NormalizedDocument:
    return NormalizedDocument(
        local_path=str(path),
        sha256=sha256,
        size_bytes=100,
        declared_type=declared_type,
        title=title,
        entry_method="direct_import",
        metadata=metadata or {},
    )


def identity(*, board: str | None, concurso: str | None, year: int | None) -> ExamSemanticIdentity:
    def field(name: str, value: str | int | None) -> SemanticField:
        if value is None:
            return SemanticField.unknown(f"{name} ausente no fixture")
        return SemanticField.from_evidence(
            name, (SemanticEvidence.metadata(name, value),)
        )

    def unknown(name: str) -> SemanticField:
        return SemanticField.unknown(f"{name} ausente no fixture")

    return ExamSemanticIdentity(
        board=field("board", board),
        concurso=field("concurso", concurso),
        organization=unknown("organization"),
        year=field("year", year),
        roles=unknown("roles"),
        stage=unknown("stage"),
        turns=unknown("turns"),
        variants=unknown("variants"),
    )


def write_text_pdf(path: Path, lines: list[str], *, author: str = "fixture") -> None:
    document = canvas.Canvas(str(path))
    document.setAuthor(author)
    y = 800
    for line in lines:
        document.drawString(54, y, line)
        y -= 22
    document.showPage()
    document.save()
```

```python
# tests/test_semantic_identity.py
class SemanticContractTests(unittest.TestCase):
    def test_unknown_field_has_no_value_or_confidence(self) -> None:
        field = SemanticField.unknown("ano não localizado")
        self.assertEqual(field.status, "unknown")
        self.assertEqual(field.normalized_values, ())
        self.assertIsNone(field.confidence)

    def test_conflicting_field_preserves_both_evidences(self) -> None:
        field = SemanticField.from_evidence(
            "year",
            (
                SemanticEvidence.metadata("year", 2025),
                SemanticEvidence.pdf_text("page:1", 2026),
            ),
        )
        self.assertEqual(field.status, "conflict")
        self.assertEqual(field.normalized_values, (2025, 2026))

    def test_identity_key_ignores_path_dates_and_evidence_order(self) -> None:
        first = identity(board="FGV", concurso="Receita Federal", year=2026)
        second = identity(board="fgv", concurso="  Receita   Federal ", year=2026)
        self.assertEqual(semantic_identity_key(first), semantic_identity_key(second))

    def test_identity_key_requires_board_contest_and_year(self) -> None:
        self.assertIsNone(semantic_identity_key(identity(board=None, concurso="RF", year=2026)))

    def test_content_fingerprint_tolerates_only_layout_whitespace(self) -> None:
        first = build_content_fingerprint([(1, "Questão 1\nA) azul  B) verde")])
        second = build_content_fingerprint([(1, "Questão 1\r\nA) azul   B) verde  ")])
        changed = build_content_fingerprint([(1, "Questão 1\nA) azul B) vermelho")])
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotEqual(first.sha256, changed.sha256)
```

- [ ] **Step 2: Executar os testes e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_semantic_identity -v`

Expected: falha de importação para `kad_collector.semantic_identity`.

- [ ] **Step 3: Implementar os modelos e a serialização canônica**

Use `ConfigDict(extra="forbid", frozen=True)`, tuplas ordenadas e estas constantes:

```python
SEMANTIC_SCHEMA_VERSION = 1
IDENTITY_ALGORITHM_VERSION = "semantic-identity-v1"
CONTENT_NORMALIZER_VERSION = "pdf-text-nfkc-v1"

SemanticStatus = Literal["known", "unknown", "conflict"]
EvidenceSource = Literal["declared_metadata", "pdf_text", "document_title", "human_review"]
EvidenceStrength = Literal["strong", "medium", "weak"]
DocumentRole = Literal["exam", "answer_key", "other", "unknown"]
AnswerKeyState = Literal["preliminary", "definitive", "unknown"]
ResolutionOutcome = Literal[
    "exact_duplicate", "republication", "new_version", "new_identity", "uncertain"
]
```

Fixe os campos públicos dos contratos antes de implementar os construtores:

```python
SemanticValue = str | int
AssociationOutcome = Literal[
    "selected", "missing", "conflict", "insufficient_evidence", "ambiguous"
]


class SemanticEvidence(FrozenSemanticModel):
    source: EvidenceSource
    locator: str
    raw_value: SemanticValue
    normalized_value: SemanticValue
    strength: EvidenceStrength


class SemanticField(FrozenSemanticModel):
    status: SemanticStatus
    raw_values: tuple[SemanticValue, ...] = ()
    normalized_values: tuple[SemanticValue, ...] = ()
    evidence: tuple[SemanticEvidence, ...] = ()
    method: str
    confidence: float | None = None
    reason: str
    algorithm_version: str = IDENTITY_ALGORITHM_VERSION


class ExamSemanticIdentity(FrozenSemanticModel):
    board: SemanticField
    concurso: SemanticField
    organization: SemanticField
    year: SemanticField
    roles: SemanticField
    stage: SemanticField
    turns: SemanticField
    variants: SemanticField


class AnswerKeyCoverage(FrozenSemanticModel):
    roles: SemanticField
    stage: SemanticField
    turns: SemanticField
    variants: SemanticField


class ContentFingerprint(FrozenSemanticModel):
    sha256: str
    page_sha256s: tuple[str, ...]
    page_count: int
    character_count: int
    normalizer_version: str = CONTENT_NORMALIZER_VERSION


class DocumentSemanticProfile(FrozenSemanticModel):
    identity: ExamSemanticIdentity
    identity_key: str | None
    document_role: DocumentRole
    answer_key_state: AnswerKeyState = "unknown"
    coverage: AnswerKeyCoverage
    content_fingerprint: ContentFingerprint
    has_conflict: bool
    algorithm_version: str = IDENTITY_ALGORITHM_VERSION


class KnownDocumentVersion(FrozenSemanticModel):
    version_id: str
    identity_key: str
    document_role: DocumentRole
    content_sha256: str
    version_number: int
    predecessor_version_id: str | None = None


class IdentityResolution(FrozenSemanticModel):
    outcome: ResolutionOutcome
    profile: DocumentSemanticProfile | None = None
    document_version_id: str | None = None
    predecessor_version_id: str | None = None
    version_number: int | None = None
    reason: str
    algorithm_version: str = IDENTITY_ALGORITHM_VERSION


class AssociationCandidate(FrozenSemanticModel):
    version_id: str
    profile: DocumentSemanticProfile
    predecessor_version_id: str | None = None


class CandidateAssessment(FrozenSemanticModel):
    version_id: str
    compatible: bool
    score: int
    matched_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class DocumentAssociationDecision(FrozenSemanticModel):
    outcome: AssociationOutcome
    selected_version_id: str | None
    assessments: tuple[CandidateAssessment, ...]
    minimum_score: int
    minimum_margin: int
    achieved_margin: int | None
    reason: str
    algorithm_version: str
```

`FrozenSemanticModel` herda `StrictModel` e define `ConfigDict(extra="forbid", frozen=True)`.
`SemanticField.unknown(reason)`, `SemanticEvidence.metadata`,
`SemanticEvidence.pdf_text` e `SemanticField.from_evidence` devem ter as assinaturas usadas
pelos testes e validar que `unknown` não carregue valor ou confiança.

`semantic_identity_key` deve retornar `None` se `board`, `concurso` ou `year` não tiver status
`known` com um único valor. Nos demais casos, calcule SHA-256 do JSON canônico contendo versão
do schema e todos os valores conhecidos de banca, concurso, órgão, ano, cargos, etapa, turnos
e variantes. Evidência, confiança, caminho, fonte e data não entram no payload.

Implemente `build_content_fingerprint` com NFKC, `\r\n` para `\n`, espaços horizontais
comprimidos, linhas finais aparadas, linhas vazias repetidas removidas e marcador explícito de
página. Retorne hash total, hashes por página, quantidade de páginas, caracteres e versão do
normalizador.

- [ ] **Step 4: Executar testes focais e ferramentas estáticas**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_identity -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_identity.py tests/semantic_helpers.py tests/test_semantic_identity.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_identity.py
```

Expected: todos verdes, sem lint ou erro de tipo.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_identity.py tests/semantic_helpers.py tests/test_semantic_identity.py
git commit -m "feat: define semantic identity contracts"
```

---

### Task 2: Extração semântica genérica e conservadora

**Files:**
- Modify: `src/kad_collector/semantic_identity.py`
- Modify: `tests/test_semantic_identity.py`

**Interfaces:**
- Consumes: `NormalizedDocument`, páginas extraídas e `human_overrides: Mapping[str, str | int | Sequence[str]] | None`.
- Produces: `extract_semantic_profile(document, pages, human_overrides=None) -> DocumentSemanticProfile` e `profile_from_document_record(record, pages) -> DocumentSemanticProfile`.

- [ ] **Step 1: Escrever testes para metadados, texto, desconhecido e conflito**

Adicione casos com os rótulos genéricos `Banca`, `Concurso`, `Órgão`, `Cargo`, `Ano`, `Fase`,
`Turno` e `Tipo`:

```python
def test_extracts_labeled_pdf_fields_without_source_rules(self) -> None:
    profile = extract_semantic_profile(
        normalized_document(Path("prova.pdf"), metadata={}),
        [(1, "Banca: Instituto Exemplo\nConcurso: Auditoria 2026\nAno: 2026\nCargo: Auditor")],
    )
    self.assertEqual(profile.identity.board.normalized_values, ("instituto exemplo",))
    self.assertEqual(profile.identity.year.normalized_values, (2026,))
    self.assertIsNotNone(profile.identity_key)

def test_declared_year_conflicting_with_pdf_is_not_resolved(self) -> None:
    profile = extract_semantic_profile(
        normalized_document(Path("prova.pdf"), metadata={"board": "X", "concurso": "Y", "year": 2025}),
        [(1, "Banca: X\nConcurso: Y\nAno: 2026")],
    )
    self.assertEqual(profile.identity.year.status, "conflict")
    self.assertIsNone(profile.identity_key)

def test_weak_title_does_not_invent_minimum_identity(self) -> None:
    profile = extract_semantic_profile(
        normalized_document(Path("prova.pdf"), title="prova-fiscal-2026.pdf"),
        [(1, "Assinale a alternativa correta.")],
    )
    self.assertEqual(profile.identity.board.status, "unknown")
    self.assertIsNone(profile.identity_key)

def test_answer_key_coverage_supports_multiple_roles_and_types(self) -> None:
    profile = extract_semantic_profile(
        normalized_document(Path("key.pdf"), declared_type="answer_key"),
        [(1, "Banca: X\nConcurso: Y\nAno: 2026\nCargos: Auditor; Analista\nTipos: 1 a 4\nGabarito definitivo")],
    )
    self.assertEqual(profile.answer_key_state, "definitive")
    self.assertEqual(profile.coverage.roles.normalized_values, ("analista", "auditor"))
    self.assertEqual(profile.coverage.variants.normalized_values, ("tipo 1", "tipo 2", "tipo 3", "tipo 4"))
```

- [ ] **Step 2: Executar e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_semantic_identity -v`

Expected: falhas porque os extratores e adaptadores ainda não existem.

- [ ] **Step 3: Implementar precedência e evidência por campo**

Use somente aliases gerais de metadados:

```python
METADATA_ALIASES = {
    "board": ("board", "banca"),
    "concurso": ("concurso",),
    "organization": ("organization", "orgao"),
    "year": ("year", "ano"),
    "roles": ("role", "cargo"),
    "stage": ("stage", "etapa", "fase"),
    "turns": ("turn", "turno"),
    "variants": ("variant", "tipo"),
}
```

Regras exatas:

1. revisão humana é evidência forte e explícita, mas divergência com outra revisão humana vira conflito;
2. metadado declarado e rótulo no PDF têm força forte e são comparados independentemente;
3. título só fornece evidência fraca para ano, turno, tipo e estado do gabarito;
4. ano solto no corpo só é aceito quando existe um único ano de quatro dígitos no documento;
5. listas usam vírgula, ponto e vírgula, barra ou intervalo numérico escrito após rótulo;
6. campo sem valor recebe `SemanticField.unknown` com motivo estável;
7. dois valores normalizados fortes diferentes produzem `conflict`;
8. `source_id`, `source_name`, URL e caminho nunca fornecem valor de identidade.

Detecte papel pelo tipo declarado primeiro. Quando ele for `auto`, use apenas marcadores
genéricos de prova ou gabarito no título e nas primeiras 20 mil letras do conteúdo. Se não
houver sinal único, use `unknown`. Detecte `preliminary` e `definitive`; se ambos aparecerem
sem contexto conclusivo, mantenha estado `unknown` e evidência conflitante.

- [ ] **Step 4: Executar os testes**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_identity -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_identity.py tests/test_semantic_identity.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_identity.py
```

Expected: extração reproduzível, desconhecidos preservados e nenhum caso específico de fonte.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_identity.py tests/test_semantic_identity.py
git commit -m "feat: extract generic document identity"
```

---

### Task 3: Schema SQLite aditivo e registro auditável

**Files:**
- Create: `src/kad_collector/semantic_registry.py`
- Modify: `src/kad_collector/desktop_store.py`
- Create: `tests/test_semantic_registry.py`
- Modify: `tests/test_document_pipeline.py`

**Interfaces:**
- Consumes: uma `sqlite3.Connection` configurada pelo `DesktopStore` e os contratos da Task 1.
- Produces: `initialize_semantic_schema`, `semantic_document_view`, `semantic_summary` e `identity_events`. Claims e resolução usam esse schema nas Tasks 4 e 5.

- [ ] **Step 1: Escrever testes de migração e leitura legada**

Crie um banco anterior ao recurso, abra com `DesktopStore` e verifique:

```python
def test_legacy_database_adds_semantic_schema_without_touching_rows(self) -> None:
    store = legacy_store_with_one_document(self.root / "collector.sqlite3")
    with store._connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        self.assertTrue({"semantic_identities", "document_versions", "document_observations", "document_observation_origins", "document_links", "question_lineage", "document_identity_events"} <= tables)
        self.assertTrue({"document_version_id", "observation_id", "semantic_resolution"} <= columns)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
    self.assertEqual(store.semantic_document_view(legacy_document_id)["identityStatus"], "unknown")

def test_initialization_is_idempotent(self) -> None:
    DesktopStore(self.database_path)
    DesktopStore(self.database_path)
    self.assertEqual(count_semantic_tables(self.database_path), 7)
```

- [ ] **Step 2: Executar e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_semantic_registry tests.test_document_pipeline -v`

Expected: tabelas, colunas e métodos semânticos ausentes.

- [ ] **Step 3: Implementar schema completo e adaptadores de leitura**

`initialize_semantic_schema(connection)` deve criar:

```sql
CREATE TABLE IF NOT EXISTS semantic_identities (
    identity_key TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    algorithm_version TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL REFERENCES semantic_identities(identity_key),
    document_role TEXT NOT NULL,
    answer_key_state TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_normalizer_version TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    predecessor_version_id TEXT REFERENCES document_versions(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(identity_key, document_role, content_sha256),
    UNIQUE(identity_key, document_role, version_number)
);
CREATE TABLE IF NOT EXISTS document_observations (
    id TEXT PRIMARY KEY,
    binary_sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    document_id TEXT REFERENCES documents(id),
    document_version_id TEXT REFERENCES document_versions(id),
    resolution_status TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_observation_origins (
    observation_id TEXT NOT NULL REFERENCES document_observations(id) ON DELETE CASCADE,
    origin_key TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(observation_id, origin_key)
);
CREATE TABLE IF NOT EXISTS document_links (
    id TEXT PRIMARY KEY,
    exam_version_id TEXT NOT NULL REFERENCES document_versions(id),
    answer_key_version_id TEXT NOT NULL REFERENCES document_versions(id),
    status TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    predecessor_link_id TEXT REFERENCES document_links(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS question_lineage (
    id TEXT PRIMARY KEY,
    predecessor_version_id TEXT REFERENCES document_versions(id),
    successor_version_id TEXT REFERENCES document_versions(id),
    question_number INTEGER NOT NULL,
    predecessor_question_id TEXT REFERENCES questions(id),
    successor_question_id TEXT REFERENCES questions(id),
    comparison TEXT NOT NULL,
    content_equal INTEGER NOT NULL,
    answer_equal INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_identity_events (
    event_key TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(id),
    document_version_id TEXT REFERENCES document_versions(id),
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Adicione `document_version_id`, `observation_id` e `semantic_resolution` como colunas anuláveis
em `documents`, e `decision_fingerprint` anulável em `questions`. Crie índice parcial que
permita somente um vínculo `active` por `exam_version_id`. `semantic_document_view` deve
retornar campos opcionais para linhas legadas, nunca fabricar uma identidade.

Crie índices únicos parciais para `question_lineage.successor_question_id` e para
`predecessor_version_id + successor_version_id + question_number`. O primeiro permite no
máximo uma origem por questão sucessora; o segundo torna idempotentes inclusive registros
`removed`, cujo `successor_question_id` é nulo.

- [ ] **Step 4: Executar migração, testes existentes e análise estática**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_registry tests.test_document_pipeline -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py tests/test_semantic_registry.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py
```

Expected: migração repetível, registro legado intacto e leituras com `identityStatus=unknown`.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py tests/test_semantic_registry.py tests/test_document_pipeline.py
git commit -m "feat: add semantic document registry"
```

---

### Task 4: Barreira transacional para duplicatas binárias

**Files:**
- Modify: `src/kad_collector/semantic_registry.py`
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/document_pipeline.py`
- Modify: `src/kad_collector/desktop_server.py`
- Modify: `src/kad_collector/desktop_app.js`
- Modify: `tests/test_semantic_registry.py`
- Modify: `tests/test_document_pipeline.py`
- Modify: `tests/test_desktop_collection.py`
- Modify: `tests/test_desktop_app.py`

**Interfaces:**
- Consumes: `NormalizedDocument` validado antes da submissão.
- Produces: `claim_document_observation(connection, document, observed_at) -> ObservationClaim`; `DesktopStore.create_interpretation_job(...) -> str | None`; `DocumentPipeline.submit(...) -> list[str]` sem identificadores vazios.

- [ ] **Step 1: Escrever testes de repetição e corrida**

Cubra estes resultados:

```python
def test_same_pdf_twice_creates_one_document_job_and_observation(self) -> None:
    first = self.pipeline.import_paths([self.exam], self.metadata, "local")
    second = self.pipeline.import_paths([self.exam], self.metadata, "local")
    self.assertEqual(len(first), 1)
    self.assertEqual(second, [])
    self.assertEqual(self.count("jobs"), 1)
    self.assertEqual(self.count("documents"), 1)
    self.assertEqual(self.count("document_observations"), 1)

def test_collection_and_direct_import_with_same_sha_converge(self) -> None:
    collected = self.pipeline.submit([normalize_collected_document(self.record)], "local")
    imported = self.pipeline.import_paths([Path(self.record.local_path)], self.metadata, "local")
    self.assertEqual(len(collected), 1)
    self.assertEqual(imported, [])
    self.assertEqual(self.origin_count(self.record.sha256), 2)

def test_concurrent_claims_have_one_winner(self) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: self.submit_once(), range(2)))
    self.assertEqual(sorted(len(result) for result in results), [0, 1])
    self.assertEqual(self.count("documents"), 1)
```

Repita o primeiro caso com `declared_type="answer_key"`, nomeie-o
`test_same_answer_key_twice_creates_no_second_job` e verifique zero questões adicionais.

- [ ] **Step 2: Executar e confirmar RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_registry tests.test_document_pipeline tests.test_desktop_collection -v
```

Expected: a segunda submissão ainda cria tarefa ou a corrida viola unicidade sem convergir.

- [ ] **Step 3: Integrar claim e criação da tarefa na mesma transação**

Em `DesktopStore.create_interpretation_job`:

1. abra conexão e execute `BEGIN IMMEDIATE`;
2. para cada documento, grave/atualize origem e receba `ObservationClaim`;
3. ignore claims `exact_duplicate`; o evento deve conter SHA, versão existente e nova origem;
4. se todos forem duplicatas, faça `commit` e retorne `None` antes de inserir `jobs`;
5. crie tarefa e linhas de `documents` somente para claims novos;
6. ligue cada observação à linha operacional criada;
7. em `IntegrityError`, reverta, reabra a transação, recarregue o vencedor e retorne o mesmo resultado lógico.

`origin_key` deve ser SHA-256 do JSON canônico formado por método de entrada, URLs, título,
`external_id`, `source_id` e metadados. A mesma origem só atualiza `last_seen_at`.

`DocumentPipeline.submit` só chama `runner.start(job_id)` quando o retorno não for `None`.
Reprocessamento explícito usa `force_reprocess=True`: ele reutiliza a observação e a versão,
mas pode criar uma tarefa operacional para validar novamente o documento selecionado.

Altere `/api/import` para responder `{"jobIds": [...], "exactDuplicate": true|false}`. No JS,
mostre `Arquivo já conhecido; nenhuma nova tarefa foi criada.` quando `jobIds` estiver vazio.

- [ ] **Step 4: Executar testes focais e regressões de submissão**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_registry tests.test_document_pipeline tests.test_desktop_collection tests.test_desktop_app -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/document_pipeline.py src/kad_collector/desktop_server.py tests/test_semantic_registry.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/document_pipeline.py
```

Expected: duplicata exata é idempotente em importação, coleta e concorrência.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/document_pipeline.py src/kad_collector/desktop_server.py src/kad_collector/desktop_app.js tests/test_semantic_registry.py tests/test_document_pipeline.py tests/test_desktop_collection.py tests/test_desktop_app.py
git commit -m "feat: skip exact document duplicates"
```

---

### Task 5: Resolução de republicação e versão sucessora

**Files:**
- Create: `src/kad_collector/semantic_resolution.py`
- Modify: `src/kad_collector/semantic_registry.py`
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/desktop_processor.py`
- Create: `tests/test_semantic_workflow.py`
- Modify: `tests/test_semantic_registry.py`
- Modify: `tests/test_desktop_app.py`

**Interfaces:**
- Consumes: `DocumentSemanticProfile` e versões conhecidas da mesma chave semântica.
- Produces: `decide_document_version(profile, known_versions) -> IdentityResolution`; `resolve_document_version(connection, document_id, profile, resolved_at) -> IdentityResolution`; `DesktopStore.resolve_extracted_document(document_id) -> IdentityResolution`.

- [ ] **Step 1: Escrever testes para os cinco resultados**

```python
def test_equivalent_text_with_different_bytes_is_republication(self) -> None:
    first = self.process_pdf(bytes_variant=1, text="Questão 1\nA) Azul B) Verde")
    second = self.process_pdf(bytes_variant=2, text="Questão 1\r\nA) Azul   B) Verde")
    self.assertEqual(second.resolution, "republication")
    self.assertEqual(second.document_version_id, first.document_version_id)
    self.assertEqual(self.count("questions"), first.question_count)

def test_same_identity_with_changed_content_creates_successor(self) -> None:
    first = self.process_text("Questão 1\nA) Azul B) Verde")
    second = self.process_text("Questão 1\nA) Azul B) Vermelho")
    self.assertEqual(second.resolution, "new_version")
    self.assertEqual(second.predecessor_version_id, first.document_version_id)
    self.assertEqual(second.version_number, 2)

def test_insufficient_identity_is_uncertain_and_not_structured(self) -> None:
    result = self.process_text("Questão 1\nA) Azul B) Verde", metadata={})
    self.assertEqual(result.resolution, "uncertain")
    self.assertEqual(result.question_count, 0)
    self.assertIn("identidade semântica insuficiente", result.warnings)
```

Adicione casos separados para `new_identity`, conteúdo alterado com questão adicionada e
conteúdo alterado com questão removida.

Adicione também estes casos com os nomes exatos usados pela matriz final:

- `test_republication_adds_origin_without_new_questions`: segundo SHA e segunda origem,
  uma versão lógica e a quantidade original de questões;
- `test_concurrent_republications_share_one_version`: dois SHAs equivalentes resolvidos em
  paralelo, duas observações, uma versão lógica e um único conjunto de questões;
- `test_reprocessing_resumes_failed_resolution_without_duplicate_event`: falha injetada após
  a observação, reprocessamento do mesmo `document_id`, uma resolução e um evento final.

- [ ] **Step 2: Executar e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_semantic_registry tests.test_semantic_workflow -v`

Expected: resolução pura e integração pós-extração ausentes.

- [ ] **Step 3: Implementar decisão pura e persistência transacional**

`decide_document_version` segue esta ordem:

```python
if profile.identity_key is None or profile.has_conflict:
    return IdentityResolution.uncertain(profile, "identidade semântica insuficiente ou conflitante")
same_identity_and_role = [
    item for item in known_versions
    if item.identity_key == profile.identity_key and item.document_role == profile.document_role
]
same_content = [
    item for item in same_identity_and_role
    if item.content_sha256 == profile.content_fingerprint.sha256
]
if same_content:
    return IdentityResolution.republication(profile, same_content[-1])
if same_identity_and_role:
    return IdentityResolution.new_version(profile, same_identity_and_role[-1])
return IdentityResolution.new_identity(profile)
```

`resolve_document_version` grava identidade, versão, predecessor, observação e evento dentro
de `BEGIN IMMEDIATE`. IDs de versão são UUIDv5 de
`identity_key:document_role:content_sha256`. Em colisão, recarregue a versão vencedora.

No `DesktopProcessor`, depois de extrair todas as páginas e antes de `_structure_job`, chame
`resolve_extracted_document` para cada documento textual. Marque `uncertain` como exceção.
Marque `republication` como processado, adicione aviso com a versão reutilizada e não envie o
documento para `parse_question_pages`. Somente `new_identity` e `new_version` são estruturados.

- [ ] **Step 4: Executar testes focais e desktop**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_identity tests.test_semantic_registry tests.test_semantic_workflow tests.test_desktop_app -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_resolution.py src/kad_collector/semantic_registry.py src/kad_collector/desktop_processor.py tests/test_semantic_workflow.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_resolution.py src/kad_collector/semantic_registry.py src/kad_collector/desktop_processor.py
```

Expected: republicação não duplica questões; alteração real cria sucessora auditável.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_resolution.py src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/desktop_processor.py tests/test_semantic_workflow.py tests/test_semantic_registry.py tests/test_desktop_app.py
git commit -m "feat: resolve document republications and versions"
```

---

### Task 6: Associação semântica conservadora e cobertura múltipla

**Files:**
- Modify: `src/kad_collector/semantic_resolution.py`
- Modify: `src/kad_collector/desktop_processor.py`
- Modify: `src/kad_collector/review_queue.py`
- Modify: `tests/test_semantic_workflow.py`
- Modify: `tests/test_review_automation.py`

**Interfaces:**
- Consumes: perfil da prova e `Sequence[AssociationCandidate]`.
- Produces: `select_answer_key(exam_profile, candidates) -> DocumentAssociationDecision`; adaptadores desktop e offline que retornam candidato somente quando `decision.selected_version_id` existe.

- [ ] **Step 1: Escrever testes de compatibilidade, empate e tipos**

```python
def test_known_conflict_blocks_candidate(self) -> None:
    decision = select_answer_key(self.exam(year=2026), [self.key(year=2025)])
    self.assertIsNone(decision.selected_version_id)
    self.assertEqual(decision.outcome, "conflict")

def test_unknown_is_not_positive_evidence(self) -> None:
    decision = select_answer_key(self.exam(role="Auditor"), [self.key(role=None)])
    self.assertIsNone(decision.selected_version_id)
    self.assertEqual(decision.outcome, "insufficient_evidence")

def test_equal_candidates_are_ambiguous(self) -> None:
    decision = select_answer_key(self.exam(), [self.key(version_id="a"), self.key(version_id="b")])
    self.assertIsNone(decision.selected_version_id)
    self.assertEqual(decision.outcome, "ambiguous")

def test_one_key_can_cover_multiple_roles(self) -> None:
    decision = select_answer_key(
        self.exam(role="Analista"),
        [self.key(coverage_roles=("Auditor", "Analista"))],
    )
    self.assertEqual(decision.selected_version_id, "key-1")

def test_types_one_to_four_do_not_mix_answers(self) -> None:
    for number in range(1, 5):
        decision = select_answer_key(
            self.exam(variant=f"Tipo {number}"),
            [self.key(coverage_variants=(f"Tipo {number}",), version_id=f"key-{number}")],
        )
        self.assertEqual(decision.selected_version_id, f"key-{number}")
```

Inclua turno, etapa, cargo e organização conflitantes, título fraco isolado e gabarito
definitivo preferido ao preliminar somente quando ambos têm compatibilidade semântica igual.
Nomeie os testes agregados `test_known_scope_conflicts_block_candidate` e
`test_title_only_candidate_is_insufficient` para a matriz final.

- [ ] **Step 2: Executar e confirmar RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_workflow tests.test_review_automation -v
```

Expected: o selecionador atual ainda aceita atalhos do lote ou não explica candidatos.

- [ ] **Step 3: Implementar avaliação explicável e substituir os dois seletores**

Use pesos versionados:

```python
ASSOCIATION_ALGORITHM_VERSION = "semantic-association-v1"
MATCH_WEIGHTS = {
    "board": 12,
    "concurso": 12,
    "year": 12,
    "organization": 8,
    "role": 10,
    "stage": 8,
    "turn": 8,
    "variant": 8,
}
MINIMUM_SCORE = 36
MINIMUM_MARGIN = 8
```

Campos conhecidos incompatíveis eliminam o candidato. Campo desconhecido vale zero. Banca,
concurso e ano devem concordar como três evidências fortes para seleção automática. Cobertura
de cargo, fase, turno ou tipo deve conter o valor conhecido da prova. Um título pode acrescentar
no máximo dois pontos e nunca satisfaz campo forte.

Ordene candidatos por pontuação e ID estável. Se dois tiverem a mesma evidência semântica,
prefira o definitivo ao preliminar; dois definitivos equivalentes continuam ambíguos. Exija
`MINIMUM_MARGIN` quando a segunda opção não for uma predecessora preliminar do definitivo.
Preencha `CandidateAssessment` para selecionados, eliminados e recusados.

Remova o atalho `len(in_batch) == 1` do desktop. Em `review_queue.py`, construa perfis com
`profile_from_document_record` e preserve as mensagens de usuário a partir do `outcome` e das
razões da decisão.

- [ ] **Step 4: Executar testes de associação e regressões existentes**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_workflow tests.test_review_automation tests.test_desktop_collection -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_resolution.py src/kad_collector/desktop_processor.py src/kad_collector/review_queue.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_resolution.py src/kad_collector/desktop_processor.py src/kad_collector/review_queue.py
```

Expected: nenhum empate ou conflito aplica gabarito; cobertura múltipla e tipos funcionam.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_resolution.py src/kad_collector/desktop_processor.py src/kad_collector/review_queue.py tests/test_semantic_workflow.py tests/test_review_automation.py
git commit -m "feat: select answer keys by semantic evidence"
```

---

### Task 7: Vínculos persistentes e gabarito definitivo

**Files:**
- Modify: `src/kad_collector/semantic_registry.py`
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/desktop_processor.py`
- Modify: `tests/test_semantic_registry.py`
- Modify: `tests/test_semantic_workflow.py`
- Modify: `tests/test_desktop_collection.py`

**Interfaces:**
- Consumes: `DocumentAssociationDecision` e páginas persistidas do gabarito selecionado.
- Produces: `active_answer_key_candidates`, `record_document_link`, `exam_documents_affected_by_answer_key`, `DesktopProcessor._apply_answer_key_to_exam` e `DesktopProcessor._reconcile_answer_key`.

- [ ] **Step 1: Escrever testes de preliminar, definitivo e anulação**

```python
def test_definitive_key_supersedes_preliminary_and_reapplies_answers(self) -> None:
    exam = self.process_exam()
    preliminary = self.process_key("1-A\n2-B", state="preliminary")
    self.assertEqual(self.answer(exam, 2), "B")
    definitive = self.process_key("1-A\n2-C", state="definitive")
    self.assertEqual(self.answer(exam, 2), "C")
    self.assertEqual(self.link(preliminary).status, "superseded")
    self.assertEqual(self.link(definitive).status, "active")

def test_definitive_annulment_is_applied_and_audited(self) -> None:
    exam = self.process_exam()
    self.process_key("1-A")
    self.process_key("1-ANULADA", state="definitive")
    question = self.question(exam, 1)
    self.assertEqual(question.answer_status, "annulled")
    self.assertIn("association_superseded", self.document_actions(exam))

def test_repeated_definitive_key_does_not_reapply_or_duplicate_events(self) -> None:
    self.process_exam()
    key = self.process_key("1-A", state="definitive")
    before = self.event_count()
    self.import_same_pdf(key.path)
    self.assertEqual(self.event_count(), before)
```

- [ ] **Step 2: Executar e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_semantic_registry tests.test_semantic_workflow tests.test_desktop_collection -v`

Expected: um gabarito importado sozinho ainda não reconcilia provas anteriores.

- [ ] **Step 3: Persistir decisões e reconciliar sucessores**

`record_document_link` grava a decisão completa. Dentro de `BEGIN IMMEDIATE`, marque o vínculo
ativo anterior como `superseded`, insira o novo vínculo e eventos idempotentes. O índice parcial
de vínculo ativo deve ser a última defesa contra corrida.

Extraia de `_structure_job` uma função `_apply_answer_key_to_exam(exam, answer_key, decision)`
que analisa o gabarito com variante, cargo e turno da prova, atualiza somente respostas
reconhecidas, preserva perguntas ausentes e registra o vínculo.

Quando uma versão de gabarito for resolvida, `_reconcile_answer_key` consulta todas as versões
de prova compatíveis, executa novamente o selecionador usando todos os gabaritos ativos e
reaplica apenas quando a decisão ativa mudar. O preliminar é preservado; o definitivo vira
sucessor. Uma repetição binária ou republicação não chama reconciliação.

- [ ] **Step 4: Executar testes e verificar idempotência**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_registry tests.test_semantic_workflow tests.test_desktop_collection -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/desktop_processor.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/desktop_processor.py
```

Expected: definitivo substitui vínculo e resposta sem apagar preliminar; repetição é inerte.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/desktop_processor.py tests/test_semantic_registry.py tests/test_semantic_workflow.py tests/test_desktop_collection.py
git commit -m "feat: reconcile definitive answer keys"
```

---

### Task 8: Linhagem de questões e preservação seletiva de decisões

**Files:**
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/desktop_processor.py`
- Modify: `src/kad_collector/semantic_registry.py`
- Modify: `tests/test_semantic_workflow.py`
- Modify: `tests/test_desktop_app.py`

**Interfaces:**
- Consumes: versão predecessora, número da questão, `question_fingerprint` e resposta oficial.
- Produces: `question_decision_fingerprint`, `record_question_lineage`, `carry_forward_question_decision` e `invalidate_changed_official_answer`.

- [ ] **Step 1: Escrever testes para igualdade, alteração e histórico**

```python
def test_human_decision_is_carried_to_identical_successor_question(self) -> None:
    first = self.process_exam("Q1\nA) Azul B) Verde")
    self.approve(first, 1, actor="revisor")
    second = self.process_exam("Q1\nA) Azul B) Verde\nQ2\nA) Um B) Dois")
    successor = self.question(second, 1)
    self.assertEqual(successor.status, "approved")
    self.assertEqual(successor.reviewer, "revisor")
    self.assertIn("decision_carried_forward", self.audit_actions(successor.id))

def test_changed_statement_does_not_carry_decision(self) -> None:
    first = self.process_exam("Q1\nA) Azul B) Verde")
    self.approve(first, 1, actor="revisor")
    second = self.process_exam("Q1 alterada\nA) Azul B) Verde")
    self.assertEqual(self.question(second, 1).status, "pending")
    self.assertEqual(self.lineage(second, 1).comparison, "changed")

def test_changed_official_answer_invalidates_decision(self) -> None:
    exam = self.process_exam_with_key("1-A")
    self.approve(exam, 1, actor="revisor")
    self.process_key("1-B", state="definitive")
    updated = self.question(exam, 1)
    self.assertNotEqual(updated.status, "approved")
    self.assertIn("decision_invalidated", self.audit_actions(updated.id))
```

Adicione testes para questão adicionada, removida, resposta anulada e registro histórico da
versão anterior sem alteração.
O caso combinado de adição e remoção deve se chamar
`test_added_and_removed_questions_have_lineage`.

- [ ] **Step 2: Executar e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_semantic_workflow tests.test_desktop_app -v`

Expected: sucessora idêntica nasce pendente e mudança só de resposta não invalida aprovação.

- [ ] **Step 3: Implementar duas impressões e linhagem explícita**

Mantenha `question_fingerprint` restrito ao enunciado e alternativas para deduplicação de
conteúdo. Adicione:

```python
def question_decision_fingerprint(question: QuestionRecord) -> str:
    payload = {
        "content": question_fingerprint(question),
        "answer_status": question.answer_status,
        "correct_answer": question.correct_answer,
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
```

`save_question` grava ambos. Para versão sucessora, compare a questão de mesmo número na
predecessora:

- conteúdo e resposta iguais: `unchanged`, copiar `approved` ou `rejected`, revisor e notas;
- conteúdo igual e resposta diferente: `changed`, limpar decisão e registrar invalidação;
- conteúdo diferente: `changed`, não copiar decisão;
- sem predecessora: `added`;
- números ausentes na sucessora: inserir linhagem `removed` com sucessor nulo, usando a chave
  e os índices definidos na Task 3.

Um estado `exported` da predecessora é transportado como `approved`, pois a nova versão ainda
não foi exportada. Toda cópia ou invalidação gera `audit_log` e `document_identity_events`.

- [ ] **Step 4: Executar testes de decisões e regressão editorial**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_workflow tests.test_desktop_app tests.test_editorial_export -v
.venv\Scripts\ruff.exe check src/kad_collector/desktop_store.py src/kad_collector/desktop_processor.py src/kad_collector/semantic_registry.py
.venv\Scripts\mypy.exe src/kad_collector/desktop_store.py src/kad_collector/desktop_processor.py src/kad_collector/semantic_registry.py
```

Expected: apenas conteúdo editorial idêntico preserva decisão; histórico permanece consultável.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/desktop_store.py src/kad_collector/desktop_processor.py src/kad_collector/semantic_registry.py tests/test_semantic_workflow.py tests/test_desktop_app.py
git commit -m "feat: preserve decisions across document versions"
```

---

### Task 9: Correção humana da identidade e reavaliação de vínculos

**Files:**
- Modify: `src/kad_collector/semantic_identity.py`
- Modify: `src/kad_collector/semantic_registry.py`
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/desktop_server.py`
- Modify: `tests/test_semantic_workflow.py`
- Modify: `tests/test_desktop_app.py`

**Interfaces:**
- Consumes: metadados editados e ator obrigatório no endpoint `PUT /api/documents/{id}`.
- Produces: `DesktopStore.correct_document_identity(document_id, metadata, actor) -> IdentityResolution` e reavaliação dos vínculos afetados.

- [ ] **Step 1: Escrever testes de correção, auditoria e atomicidade**

```python
def test_manual_identity_correction_is_audited_and_preserves_question_decision(self) -> None:
    document = self.process_exam_with_unknown_role()
    self.approve(document, 1, actor="revisor")
    result = self.store.correct_document_identity(
        document.id,
        self.metadata(role="Auditor"),
        actor="coordenador",
    )
    self.assertIsNotNone(result.profile)
    assert result.profile is not None
    self.assertEqual(result.profile.identity.roles.normalized_values, ("auditor",))
    self.assertEqual(self.question(document, 1).status, "approved")
    event = self.last_identity_event(document.id)
    self.assertEqual(event.action, "identity_corrected")
    self.assertEqual(event.actor, "coordenador")

def test_conflicting_manual_merge_rolls_back(self) -> None:
    first, second = self.two_versions_that_would_collide_after_override()
    before = self.store.semantic_document_view(second.id)
    with self.assertRaisesRegex(ValueError, "correção colide com versão existente"):
        self.store.correct_document_identity(second.id, first.metadata, actor="coordenador")
    self.assertEqual(self.store.semantic_document_view(second.id), before)
```

Adicione um caso em que a correção troca o gabarito compatível e invalida somente respostas
oficiais alteradas.

- [ ] **Step 2: Executar e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_semantic_workflow tests.test_desktop_app -v`

Expected: atualização atual muda metadados e questões, mas não reavalia identidade ou vínculo.

- [ ] **Step 3: Implementar correção forte, transacional e conservadora**

Reextraia o perfil das páginas persistidas passando os campos editados como `human_overrides`.
Dentro de `BEGIN IMMEDIATE`:

1. valide ator e perfil sem conflitos;
2. insira ou atualize a identidade alvo;
3. recuse se `identity_key + role + content_sha256` já pertencer a outra versão;
4. atualize a versão e todas as observações ligadas a ela;
5. registre evento com chave antiga, chave nova, valores e evidências;
6. preserve decisões das questões;
7. marque vínculos antigos como rejeitados e execute o selecionador novamente;
8. se a resposta oficial mudar, use a invalidação da Task 8.

`update_document_metadata` passa a chamar essa operação depois de validar `DesktopImportMetadata`.
Falha em qualquer etapa deve reverter metadados, identidade, vínculos e eventos.

- [ ] **Step 4: Executar testes e regressões de edição**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_semantic_workflow tests.test_desktop_app -v
.venv\Scripts\ruff.exe check src/kad_collector/semantic_identity.py src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/desktop_server.py
.venv\Scripts\mypy.exe src/kad_collector/semantic_identity.py src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/desktop_server.py
```

Expected: correção é atômica, auditada e não apaga decisão sem mudança relevante.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/semantic_identity.py src/kad_collector/semantic_registry.py src/kad_collector/desktop_store.py src/kad_collector/desktop_server.py tests/test_semantic_workflow.py tests/test_desktop_app.py
git commit -m "feat: audit manual identity corrections"
```

---

### Task 10: Interface, relatórios, matriz de cobertura e documentação

**Files:**
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/desktop_server.py`
- Modify: `src/kad_collector/desktop_app.js`
- Modify: `src/kad_collector/desktop_ui.html`
- Modify: `src/kad_collector/desktop_styles.css`
- Modify: `tests/test_desktop_app.py`
- Modify: `tests/test_semantic_workflow.py`
- Modify: `tests/regression/COVERAGE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `semantic_document_view`, `semantic_summary`, `identity_events` e vínculo ativo.
- Produces: `bootstrap.semanticSummary`, `question.documentIdentity`, `GET /api/documents/{id}/identity` e apresentação mínima de identidade e versão.

- [ ] **Step 1: Escrever testes de API e recursos empacotados**

```python
def test_bootstrap_exposes_semantic_counts(self) -> None:
    payload = self.application.bootstrap()
    self.assertEqual(
        set(payload["semanticSummary"]),
        {"observations", "logicalVersions", "exactDuplicates", "republications", "activeLinks", "uncertain"},
    )

def test_identity_endpoint_exposes_evidence_without_pdf_text(self) -> None:
    payload = self.get_json(f"/api/documents/{self.document_id}/identity")
    self.assertEqual(payload["resolution"], "new_identity")
    self.assertIn("algorithmVersion", payload)
    self.assertIn("evidence", payload)
    self.assertNotIn("canonicalText", payload)

def test_packaged_ui_contains_semantic_identity_controls(self) -> None:
    html = resources.files("kad_collector").joinpath("desktop_ui.html").read_text("utf-8")
    js = resources.files("kad_collector").joinpath("desktop_app.js").read_text("utf-8")
    self.assertIn("document-identity", html)
    self.assertIn("Identidade desconhecida", js)
    self.assertIn("Republicação", js)
```

- [ ] **Step 2: Executar e confirmar RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_desktop_app tests.test_semantic_workflow -v`

Expected: resumo, endpoint e elementos de interface ausentes.

- [ ] **Step 3: Expor dados mínimos e atualizar a matriz obrigatória**

`semanticSummary` deve contar arquivos observados, versões lógicas, duplicatas exatas,
republicações, vínculos ativos e documentos incertos. Na linha ou modal da questão, mostre:

- banca, concurso, ano, cargo, turno e tipo conhecidos;
- `Identidade desconhecida` quando faltar a chave;
- papel, estado do gabarito e `Versão N`;
- selo `Duplicata exata`, `Republicação`, `Nova versão` ou `Exceção`;
- versão predecessora e gabarito ativo;
- evidências, motivo e versão do algoritmo em `<details>`;
- nenhum percentual de identidade quando status for `unknown` ou `conflict`.

O endpoint aceita apenas o mesmo token local das demais APIs e não devolve texto integral das
páginas. Use `textContent`, nunca `innerHTML`, para valores provenientes do PDF.

Em `tests/regression/COVERAGE.md`, registre esta correspondência executável:

| Cenário | Teste |
|---|---|
| prova repetida | `test_same_pdf_twice_creates_one_document_job_and_observation` |
| gabarito repetido | `test_same_answer_key_twice_creates_no_second_job` |
| coleta e importação com mesmo SHA | `test_collection_and_direct_import_with_same_sha_converge` |
| bytes diferentes, conteúdo equivalente | `test_equivalent_text_with_different_bytes_is_republication` |
| republicação com nova origem | `test_republication_adds_origin_without_new_questions` |
| questão alterada | `test_same_identity_with_changed_content_creates_successor` |
| questão adicionada ou removida | `test_added_and_removed_questions_have_lineage` |
| preliminar seguido do definitivo | `test_definitive_key_supersedes_preliminary_and_reapplies_answers` |
| definitivo repetido | `test_repeated_definitive_key_does_not_reapply_or_duplicate_events` |
| questão anulada | `test_definitive_annulment_is_applied_and_audited` |
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

Documente no README a diferença entre arquivo, observação, versão e vínculo; estados de
exceção; reprocessamento; correção manual; interpretação dos selos; e consultas de diagnóstico.

- [ ] **Step 4: Executar testes da interface e matriz**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_desktop_app tests.test_semantic_workflow tests.test_semantic_registry tests.test_review_automation -v
.venv\Scripts\ruff.exe check src tests
.venv\Scripts\mypy.exe src
git diff --check
```

Expected: recursos empacotados presentes, API protegida, 25 cenários apontando para testes reais.

- [ ] **Step 5: Commit**

```powershell
git add src/kad_collector/desktop_store.py src/kad_collector/desktop_server.py src/kad_collector/desktop_app.js src/kad_collector/desktop_ui.html src/kad_collector/desktop_styles.css tests/test_desktop_app.py tests/test_semantic_workflow.py tests/regression/COVERAGE.md README.md
git commit -m "docs: expose semantic document history"
```

---

### Task 11: Verificação final, revisão do diff e Pull Request

**Files:**
- Modify only if verification finds a scoped defect: files already listed in Tasks 1–10 and their tests.

**Interfaces:**
- Consumes: branch completa e todos os comandos do projeto.
- Produces: branch verificada, commits enviados e um Pull Request para `main`, sem merge.

- [ ] **Step 1: Confirmar escopo e árvore de trabalho**

```powershell
git status --short
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD
git diff --check origin/main...HEAD
```

Expected: somente arquivos do mapa acima; nenhuma alteração no repositório `kad`, `.env`,
Supabase, infraestrutura, novas bancas ou OCR.

- [ ] **Step 2: Executar a verificação completa**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\ruff.exe check .
.venv\Scripts\mypy.exe src
.venv\Scripts\python.exe -m compileall src tests
.venv\Scripts\kad-collector.exe regression
$smokeData = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ("kad-collector-semantic-smoke-" + [guid]::NewGuid()))
.venv\Scripts\kad-collector-desktop.exe --smoke-test --data-dir $smokeData.FullName
```

Expected: zero falhas, zero lint, zero erros de tipo, compileall concluído, regressão offline
verde e smoke desktop encerrado com sucesso.

- [ ] **Step 3: Revisar invariantes com consultas SQLite**

Em um banco temporário criado pelo teste de workflow, valide:

```sql
SELECT binary_sha256, COUNT(*) FROM document_observations GROUP BY binary_sha256 HAVING COUNT(*) > 1;
SELECT identity_key, document_role, content_sha256, COUNT(*) FROM document_versions GROUP BY 1,2,3 HAVING COUNT(*) > 1;
SELECT exam_version_id, COUNT(*) FROM document_links WHERE status = 'active' GROUP BY exam_version_id HAVING COUNT(*) > 1;
SELECT event_key, COUNT(*) FROM document_identity_events GROUP BY event_key HAVING COUNT(*) > 1;
```

Expected: todas as consultas retornam zero linhas. Confirme também que uma republicação tem
duas observações, uma versão e somente o conjunto original de questões.

- [ ] **Step 4: Corrigir somente falhas verificadas e registrar o ajuste**

Para cada falha, primeiro adicione ou refine um teste que a reproduza, execute-o em RED, faça a
menor correção, execute-o em GREEN e depois repita a Step 2. Se houver correção, faça commit com
mensagem específica ao defeito, como `fix: serialize concurrent republication claims`. Se não
houver falha, não crie commit vazio.

- [ ] **Step 5: Push e criação do Pull Request**

```powershell
git status --short
git log --oneline origin/main..HEAD
git push -u origin codex/semantic-document-identity
$prBody = @"
## Resumo
- identidade semântica e versão lógica auditáveis
- deduplicação binária e republicações sem questões duplicadas
- associação conservadora e sucessão de gabarito definitivo
- preservação seletiva de decisões humanas
- interface local com identidade, evidências e histórico

## Verificação
- unittest completo
- Ruff e mypy
- compileall
- regressão offline
- smoke test desktop
"@
gh pr create --base main --head codex/semantic-document-identity --title "feat: add semantic document identity" --body $prBody
```

Substitua as cinco linhas genéricas da seção de verificação pelos números e resultados reais
obtidos na Step 2 antes de executar o comando. Confirme que o PR aponta para `main`, não contém
merge e não inclui nenhum arquivo fora do escopo.
