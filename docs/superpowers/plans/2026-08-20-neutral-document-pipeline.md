# Neutral Document Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make automatic collection, direct import, and local reprocessing submit the same neutral document contract to one source-independent interpretation engine.

**Architecture:** Add an immutable `NormalizedDocument` contract and a small desktop orchestration service. Persist the contract beside existing desktop document columns, keep legacy rows readable, and remove source identifiers from interpretation and answer-key association decisions. The CLI extraction boundary will normalize collected records before reading local PDFs while retaining its current output schemas.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLite, pypdf, unittest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-20-neutral-document-pipeline-design.md`

## Global Constraints

- Work only in `kad-collector`; do not modify or copy code from the private frontend.
- Keep acquisition rules for hosts, discovery, crawl, MIME, size, and download inside acquisition modules.
- Do not change official-answer rules, OCR behavior, editorial schemas, UI behavior, or export eligibility.
- Do not invent URLs, external IDs, source identifiers, or metadata for direct imports.
- Preserve existing SQLite tables, rows, audit events, questions, and human decisions.
- Use only local fixtures in tests; do not call live sites, OpenAI, Supabase, or the operational database.
- Do not generate an EXE and do not merge the pull request.

---

### Task 1: Neutral document contract

**Files:**
- Create: `src/kad_collector/document_contract.py`
- Create: `tests/test_document_pipeline.py`

**Interfaces:**
- Consumes: local PDF paths and existing `DocumentRecord` values.
- Produces: `NormalizedDocument`, `normalize_local_document`,
  `normalize_collected_document`, and `as_reprocessing_document`.

- [ ] **Step 1: Write contract tests with literal expectations**

Create a temporary `%PDF-` file with known bytes. Add tests that prove:

```python
document = normalize_local_document(pdf_path)
self.assertEqual(document.entry_method, "direct_import")
self.assertEqual(document.declared_type, "auto")
self.assertEqual(document.title, "sample.pdf")
self.assertEqual(document.local_path, str(pdf_path.resolve()))
self.assertEqual(document.size_bytes, len(payload))
self.assertEqual(document.sha256, hashlib.sha256(payload).hexdigest())
self.assertEqual(document.metadata, {})
self.assertIsNone(document.original_url)
self.assertIsNone(document.external_id)
```

Build a `DocumentRecord` with a fictitious source and assert that
`normalize_collected_document(record, source_page_url=...)` preserves path, hash, size,
declared type, title, original/resolved URLs, source identifiers, acquisition timestamp,
content type, source page, metadata and terms evidence. Assert that missing optional values
remain `None` or empty.

Assert that `as_reprocessing_document(document)` changes only `entry_method`. Reject a missing
file, directory, empty file, wrong size, or wrong SHA-256 when local validation runs.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v`

Expected: import failure for `kad_collector.document_contract`.

- [ ] **Step 3: Implement the immutable contract**

Use a Pydantic model with exact literals:

```python
DocumentEntryMethod = Literal["automated_collection", "direct_import", "reprocessing"]
DeclaredDocumentType = Literal["auto", "exam", "answer_key", "other"]

class NormalizedDocument(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    local_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1)
    declared_type: DeclaredDocumentType = "auto"
    title: str = Field(min_length=1)
    original_url: str | None = None
    resolved_url: str | None = None
    source_page_url: str | None = None
    entry_method: DocumentEntryMethod
    metadata: dict[str, str | int] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    external_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    content_type: str | None = None
    acquired_at: datetime | None = None
```

Hash in 1 MiB chunks. Resolve the path without copying or moving the file. Copy only values
present in `DocumentRecord`; add the authorization basis and terms URL to evidence only when
they contain text.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v`

Expected: all contract tests pass.

- [ ] **Step 5: Commit the contract**

Stage the contract and its tests. Commit with `feat: add neutral document contract`.

### Task 2: Additive persistence and shared orchestration

**Files:**
- Create: `src/kad_collector/document_pipeline.py`
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/desktop_server.py`
- Modify: `src/kad_collector/desktop_collection.py`
- Modify: `tests/test_document_pipeline.py`
- Modify: `tests/test_desktop_collection.py`

**Interfaces:**
- Consumes: `list[NormalizedDocument]` and a classifier provider.
- Produces: `DocumentPipeline.submit(documents, classifier_provider) -> list[str]`,
  `DocumentPipeline.import_paths(paths, metadata, classifier_provider) -> list[str]`, and
  `DesktopStore.create_interpretation_job(documents, classifier_provider) -> str`.

- [ ] **Step 1: Write migration and submission tests**

Create a legacy SQLite database by instantiating the current schema, remove or omit
`normalized_json`, then reopen it with the changed store. Assert existing rows remain readable
and the column appears without changing row counts.

Submit two local documents through `DocumentPipeline.import_paths` and assert each stored row
contains a validated `normalized_document` with `direct_import`. Submit collected documents
through `DocumentPipeline.submit` and assert `automated_collection` survives persistence.

Use a recording runner whose `start(job_id)` stores IDs. Assert both entry paths call the same
`DocumentPipeline.submit` path and runner interface. Do not assert on a mocked PDF parser.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_collection -v
```

Expected: missing pipeline APIs and `normalized_json` assertions fail.

- [ ] **Step 3: Add the compatible store migration**

Create `normalized_json TEXT` in new schemas. In `_initialize`, inspect
`PRAGMA table_info(documents)` and execute:

```sql
ALTER TABLE documents ADD COLUMN normalized_json TEXT
```

only when the column is absent. Do not update existing rows.

Add `create_interpretation_job`. It writes the contract JSON, mirrors path/hash/size into the
existing columns, and writes known editorial metadata into `metadata_json`. Keep `create_job`
as a compatibility adapter that calls `normalize_local_document` and then
`create_interpretation_job`.

`_document_row` should return `normalized_document=None` for a legacy row and a validated
payload for a new row.

- [ ] **Step 4: Implement neutral batch planning and submission**

Move `_processing_batches` out of `desktop_collection`. Implement neutral planning in
`document_pipeline.py`: keep candidate answer keys available to each exam group, enforce the
20-PDF limit, and make no answer-key selection. The service should call
`create_interpretation_job` and then `runner.start` for each returned ID.

`DesktopApplication.import_pdfs` must expand one PDF, multiple PDFs, or a directory, then call
`pipeline.import_paths`. `DesktopCollectionManager` must normalize successful
`DocumentRecord` values and call the same `pipeline.submit` method. Preserve public return
types used by the desktop server.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_collection -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_app -v
```

Expected: all submission, migration, import, collection and desktop tests pass.

- [ ] **Step 6: Commit persistence and orchestration**

Stage only the files in this task. Commit with `refactor: route documents through shared pipeline`.

### Task 3: Source-independent interpretation and association

**Files:**
- Modify: `src/kad_collector/desktop_processor.py`
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/review_queue.py`
- Modify: `src/kad_collector/pdf_extractor.py`
- Modify: `tests/test_document_pipeline.py`
- Modify: `tests/test_desktop_collection.py`
- Modify: `tests/test_review_automation.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: normalized local documents and extracted content.
- Produces: the existing `QuestionRecord`, `QuestionBatch`, review queue and desktop rows.

- [ ] **Step 1: Write behavioral tests that fail on origin-based logic**

Create V1 and V2 exam fixtures with `provider="fictitious_new_source"` and common semantic
metadata. Assert the interpreter chooses V1 as canonical and preserves V2 as evidence. This
test must fail while `_canonical_exam_documents` checks `fuvest_vestibular`.

Create an exam and two answer keys with different `source_id` values but unique matching year,
variant and title tokens. Assert the compatible key is selected. Create equal candidates and
assert no answer is applied and the queue reports ambiguity.

Add an AST-based dependency test that resolves imports in `desktop_processor.py`,
`desktop_parser.py`, `desktop_classifier.py`, `answer_key.py` and `review_queue.py`. Fail if
they import `collector`, `collection_transport`, `collection_state`, `config`, `discovery`,
`security` or `desktop_collection`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_collection -v
.venv\Scripts\python.exe -m unittest tests.test_review_automation -v
```

Expected: the fictitious-source V1 test and cross-source semantic association test fail.

- [ ] **Step 3: Replace source checks with structural evidence**

Change canonical selection to group documents by known concurso, year, role and organization.
Only collapse a group when at least two variants match `V[1-9]\d*`; select the lowest number.
Keep `Tipo N` documents distinct.

Remove `provider` from `_document_group` and cached-answer-key filtering. Rank candidates with
known semantic metadata, year/period, role words, variant, definitive marker, title tokens and
available text. Select a sole in-batch candidate for backward compatibility. Block a tie or
multiple zero-evidence candidates.

Apply the same neutral scoring behavior in `review_queue`. Do not use source IDs, domains,
URLs as host identities or crawl configuration.

- [ ] **Step 4: Normalize the CLI extraction boundary**

In `pdf_extractor`, convert each collected `DocumentRecord` to `NormalizedDocument` before
validating and reading bytes. Keep `ExtractionManifest` and `ExtractedDocument.document`
unchanged so reviewed batches and exports remain compatible. File verification must consume
only `local_path`, `size_bytes`, and `sha256` from the neutral contract.

- [ ] **Step 5: Run focused and source regressions and confirm GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_collection -v
.venv\Scripts\python.exe -m unittest tests.test_review_automation -v
.venv\Scripts\python.exe -m unittest tests.test_collector -v
.venv\Scripts\python.exe -m unittest tests.test_pipeline -v
```

Expected: fictitious source, FUVEST, FGV, COMVEST and CLI pipeline tests pass.

- [ ] **Step 6: Commit neutral interpretation**

Stage only the files in this task. Commit with `refactor: remove source rules from interpretation`.

### Task 4: Convergence, failures, and local reprocessing

**Files:**
- Modify: `src/kad_collector/document_pipeline.py`
- Modify: `src/kad_collector/desktop_store.py`
- Modify: `src/kad_collector/desktop_server.py`
- Modify: `tests/test_document_pipeline.py`
- Modify: `tests/test_desktop_app.py`
- Modify: `tests/test_desktop_collection.py`

**Interfaces:**
- Consumes: a stored document ID or a normalized PDF from either entry method.
- Produces: `DocumentPipeline.reprocess(document_ids, classifier_provider) -> list[str]` and
  identical question payloads for identical bytes and metadata.

- [ ] **Step 1: Write the real-engine convergence test**

Generate one local text PDF with two questions. Create one `direct_import` contract and one
`automated_collection` contract that point to the same bytes and carry the same known
editorial metadata. Submit both to separate temporary stores and run the real
`DesktopProcessor.run` synchronously. Compare the stored `QuestionRecord` payloads after
excluding origin/audit fields; they must match exactly.

Assert each stored document retains its own entry method, origin URL and source page while
path, hash and size remain available.

- [ ] **Step 2: Write failure and reprocessing tests**

Use an acquisition callable that raises before returning documents and assert the recording
runner receives no job. Use a runner that marks interpretation failed, then call
`DocumentPipeline.reprocess` with stored IDs. Assert the pipeline creates a new job from local
contracts without invoking the acquisition callable.

Assert reprocessing does not update or delete the original questions, audit log or document
row. Missing local files must fail before a new job starts while keeping the old evidence.

- [ ] **Step 3: Run focused tests and confirm RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_app -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_collection -v
```

Expected: missing reprocessing API and convergence assertions fail.

- [ ] **Step 4: Implement reprocessing without acquisition**

Load stored contracts by document ID, validate local files against persisted hash and size,
copy them with `entry_method="reprocessing"`, and submit through the existing method. A legacy
row should produce a contract from proven columns and metadata, omit unknown origin fields,
and add a compatibility warning.

Expose reprocessing only through the application service needed by tests and future callers;
do not add UI controls in this task.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run the three commands from Step 3. Expected: convergence, failure isolation,
reprocessing and existing desktop behavior pass.

- [ ] **Step 6: Commit failure-safe reprocessing**

Stage only the files in this task. Commit with `feat: reprocess stored documents locally`.

### Task 5: Documentation, architecture guard, and verification

**Files:**
- Modify: `README.md`
- Modify: `tests/test_document_pipeline.py`
- Review: all scoped files from Tasks 1 through 4.

**Interfaces:**
- Consumes: the completed implementation and all documented commands.
- Produces: operator guidance, rollback instructions, verification evidence and a pull request.

- [ ] **Step 1: Document the implemented boundaries**

Add the required diagram and document:

- acquisition versus interpretation;
- every neutral contract field and absence semantics;
- automatic collection, direct import and folder import;
- new-source procedure limited to acquisition configuration;
- the structural condition that warrants a new document strategy;
- local reprocessing, evidence retention, known limitations and rollback.

Keep the existing source descriptions, limits and official policy records intact.

- [ ] **Step 2: Run the architecture and feature tests**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_document_pipeline -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_collection -v
.venv\Scripts\python.exe -m unittest tests.test_desktop_app -v
.venv\Scripts\python.exe -m unittest tests.test_review_automation -v
.venv\Scripts\python.exe -m unittest tests.test_regression -v
```

Expected: all tests pass without network.

- [ ] **Step 3: Run full verification fresh**

Run:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
.venv\Scripts\kad-collector.exe regression
$smokeData = New-Item -ItemType Directory -Path (
    Join-Path ([IO.Path]::GetTempPath()) ("kad-collector-smoke-" + [guid]::NewGuid())
)
.venv\Scripts\kad-collector-desktop.exe --smoke-test --data-dir $smokeData.FullName
git diff --check origin/main...HEAD
```

If the ignored official PDFs are absent, run
`.venv\Scripts\python.exe scripts\prepare_regression_fixtures.py` once, verify its two-file
limit and hashes, then rerun `.venv\Scripts\kad-collector.exe regression`. The implementation
tests must stay offline.

- [ ] **Step 4: Audit requirements, scope and secrets**

Compare the implementation with every acceptance test in the spec. Inspect `git status`,
`git diff --stat origin/main...HEAD`, tracked paths and staged content. Confirm no PDF,
database, `.env`, key, token, cookie, report, EXE or unrelated file is tracked.

- [ ] **Step 5: Request code review and fix findings**

Dispatch a read-only reviewer for `origin/main..HEAD` with this plan and spec. Fix Critical
and Important findings through a failing test when behavior changes. Repeat the relevant
verification after each fix.

- [ ] **Step 6: Commit documentation and verified fixes**

Commit documentation with `docs: explain document pipeline boundaries`. Keep review fixes in
a separate scoped commit when needed.

- [ ] **Step 7: Push and open the pull request**

Push `codex/separate-acquisition-interpretation`. Check for an existing PR for the branch,
then open one PR to `main`. Include old/new flow, changed files, source-specific acquisition
rules, migration/rollback, limitations and exact verification results. Do not merge.
