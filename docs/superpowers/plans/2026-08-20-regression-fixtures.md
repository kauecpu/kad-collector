# Hybrid Regression Fixtures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline, manifest-driven regression package with local official PDFs, versioned synthetic fixtures, honest coverage states, one command, and a machine-readable report.

**Architecture:** A focused `kad_collector.regression` module loads and validates a TOML manifest, verifies every fixture, executes supported cases twice with network calls blocked, and writes an atomic JSON report. A separate preparation script is the only component allowed to download official PDFs; normal tests use temporary files and versioned synthetic text.

**Tech Stack:** Python 3.11+, `tomllib`, `unittest`, existing `pypdf`/Pydantic pipeline APIs, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-20-regression-fixtures-design.md`

## Global Constraints

- Work only in `kad-collector`; do not copy code from or modify the private frontend repository.
- Do not change collection, extraction, association, or classification logic to satisfy fixtures.
- Normal tests must run without network, official PDFs, AI, Supabase, or an operational database.
- Official PDFs remain under ignored `tests/regression/official/`; Git stores their HTTPS origin, byte size, SHA-256, and expected summaries.
- A `supported` case fails the command on missing fixtures, integrity mismatch, nondeterminism, or behavioral mismatch.
- A `planned` case must include a concrete gap and must never appear as supported or passed.
- Do not invent or edit official answers. Synthetic answers must be labeled fictional.
- Do not generate an EXE or merge the pull request.

---

### Task 1: Manifest contract and structural validation

**Files:**
- Create: `src/kad_collector/regression.py`
- Create: `tests/test_regression.py`

**Interfaces:**
- Consumes: TOML bytes from a caller-supplied path.
- Produces: `load_regression_manifest(path: Path) -> RegressionManifest` and `RegressionError`.

- [ ] **Step 1: Write failing manifest tests**

Add table-driven `unittest` cases that build manifests in `TemporaryDirectory` and assert:

```python
manifest = load_regression_manifest(root / "manifest.toml")
self.assertEqual(manifest.schema_version, 1)
self.assertEqual(manifest.cases[0].status, "supported")
```

Mutations must reject duplicate case IDs, duplicate fixture IDs and paths, malformed SHA-256,
non-HTTPS official origins, a planned case without `gap`, a supported case without an executor,
unknown fixture references, and a coverage topic absent from all cases.

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_regression -v`

Expected: import failure for `kad_collector.regression`.

- [ ] **Step 3: Implement the minimum typed manifest model**

Create frozen dataclasses `FixtureSpec`, `CaseSpec`, and `RegressionManifest`. Parse with
`tomllib`, reject unknown statuses and formats, and collect all validation messages into one
`RegressionError`. Keep path resolution relative to the manifest directory.

```python
def load_regression_manifest(path: Path) -> RegressionManifest:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    manifest = _parse_manifest(payload)
    _validate_manifest(manifest)
    return manifest
```

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_regression -v`

Expected: all manifest tests pass.

- [ ] **Step 5: Commit the manifest contract**

Stage only `src/kad_collector/regression.py` and `tests/test_regression.py`; commit with
`test: define regression manifest contract`.

### Task 2: Integrity, offline execution, determinism, and reporting

**Files:**
- Modify: `src/kad_collector/regression.py`
- Modify: `tests/test_regression.py`

**Interfaces:**
- Consumes: `RegressionManifest`, local fixture files, executor registry.
- Produces: `validate_fixture(spec, root) -> Path`,
  `run_regression(manifest_path: Path, report_path: Path) -> dict[str, object]`.

- [ ] **Step 1: Write failing behavior tests**

Add tests that prove a missing fixture, wrong byte size, wrong SHA-256, and invalid PDF
signature each raise `RegressionError`. Register a test executor that attempts
`socket.create_connection(("example.com", 443))` and assert the runner blocks it. Register a
counter executor that returns a different result on its second call and assert the runner
reports nondeterminism. Run the same valid manifest twice and compare reports after removing
`generated_at`; they must match.

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_regression -v`

Expected: failures because integrity checks and `run_regression` do not exist.

- [ ] **Step 3: Implement checks and atomic report writing**

Hash files in 1 MiB chunks, verify `%PDF-` for PDF fixtures, patch connection entry points
inside a context manager, and execute each supported case twice. Build coverage rows with
`supported`, `planned`, `passed`, or `failed` state. Write JSON to a sibling temporary file,
then use `Path.replace`.

```python
first = executor(case, fixtures)
second = executor(case, fixtures)
if first != second:
    raise RegressionError(f"caso não determinístico: {case.id}")
```

Use a caller-injected executor registry in tests; production uses a fixed internal registry.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `.venv\Scripts\python.exe -m unittest tests.test_regression -v`

Expected: integrity, offline, absence, report, and determinism tests pass.

- [ ] **Step 5: Commit the runner core**

Stage the two modified files and commit with `feat: add offline regression runner`.

### Task 3: Executable cases, synthetic fixtures, and coverage manifest

**Files:**
- Modify: `src/kad_collector/regression.py`
- Modify: `tests/test_regression.py`
- Create: `tests/regression/manifest.toml`
- Create: `tests/regression/synthetic/comvest-inline-answer.txt`
- Create: `tests/regression/synthetic/multi-grid-answer-key.txt`
- Create: `tests/regression/synthetic/answer-key-selection.json`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `parse_question_pages`, `parse_answer_key`, `_select_answer_key`, `PdfReader`,
  `FuvestStaticExtractor`, and verified fixture paths.
- Produces executors `fuvest_official`, `inline_answer`, `answer_grid`,
  `definitive_selection`, and `ambiguous_selection`.

- [ ] **Step 1: Add failing executor tests with literal expected results**

Use small files copied to temporary directories. Assert the inline case returns question 1
with answer `C`; the grid case selects types 1 through 4, preserves an annulled item, and
selects the requested cargo and turn; the definitive selector returns the definitive fixture;
the ambiguous selector returns no match. Assert planned cases never call an executor.

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_regression -v`

Expected: unknown executor failures.

- [ ] **Step 3: Implement the executors without modifying production logic**

Each executor returns a small dictionary with counts and SHA-256 summaries. The FUVEST
executor extracts the verified V1 PDF and parses the verified answer-key PDF for `V1` through
`V4`. Lock these reviewed facts:

```text
exam: 38 pages, 90 questions, numbers 1..90
exam summary: e5f4c85527dd252c4a55e32b6e0caf35183d63b7a4dfe17a9b8b595fd181e81e
V1: 90 entries, digest d3d1bebe77d523c8e62f04c9c70664c84a3adf6c98e4f75a527e8c06748873a7
V2: 90 entries, digest 30cf550188076510a07f3bce5cb05b1b254365e608b462ae8a5ceffdd13930b1
V3: 90 entries, digest c7881f6d8e549359653815fe5fbd29a81ad3b40c0caaaec19fb41a07ba619efc
V4: 90 entries, digest dce9f558a698e5d52ed62a66a04f3948ae1ecf33976651c91b1f330f5e18c490
```

- [ ] **Step 4: Add fixtures and the complete matrix**

Declare the official files with the audited metadata:

```text
fuvest2026-fase1-prova-v1.pdf: 8,019,288 bytes, 93b417ad6ea7e81a3b6adc46337920fd71c213306849846723f13583074f9025
fuvest2026-fase1-gabarito.pdf: 218,795 bytes, a9e1084a125fa35bcefb875610328c0a8cb9b1a298880f2f0c75b928bd860e8e
```

Cover all ten required topics. Mark republication, real OCR, and unrelated-document rejection
as planned with the exact product gaps from the spec. Add `tests/regression/official/` and the
generated JSON report to `.gitignore`.

- [ ] **Step 5: Run focused tests and the real package**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_regression -v
.venv\Scripts\kad-collector.exe regression
```

Expected: supported cases pass; three gap rows remain `planned`; the report contains all ten
topics and does not contain official answer text.

- [ ] **Step 6: Commit cases and fixtures**

Stage only the module, test, manifest, synthetic fixtures, and `.gitignore`; commit with
`test: add hybrid regression cases`.

### Task 4: CLI, preparation script, and maintenance documentation

**Files:**
- Modify: `src/kad_collector/cli.py`
- Create: `scripts/prepare-regression-fixtures.py`
- Create: `tests/regression/README.md`
- Create: `tests/regression/COVERAGE.md`
- Modify: `README.md`
- Modify: `tests/test_regression.py`

**Interfaces:**
- Consumes: `kad-collector regression [--manifest PATH] [--report PATH]` and the official
  fixture declarations.
- Produces: exit code `0` on all supported cases passing, `2` on validation/regression error;
  preparation script exit `0` only after every official fixture passes integrity checks.

- [ ] **Step 1: Write failing CLI and preparation tests**

Patch `run_regression` at the CLI boundary and assert argument forwarding and exit codes. For
the preparation script, test the downloader function with an injected byte stream, including
hash mismatch and atomic replacement. Do not make a live request in tests.

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_regression -v`

Expected: CLI parser lacks `regression` and preparation module cannot be imported.

- [ ] **Step 3: Implement CLI and preparation flow**

Add the subcommand without changing other routes. The preparation script must identify itself
with a fixed user agent, download only HTTPS origins declared as `official`, stream to a
temporary file, call the same integrity validator, then replace the destination.

- [ ] **Step 4: Document operation and maintenance**

Document the one-command run, local preparation, offline guarantee, report fields, update
procedure, source origin, fields collected, two-file limit, and execution mode. Add a final
matrix that labels the three gaps `planned` and never uses pass-like language for them.

- [ ] **Step 5: Run focused tests and style checks**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_regression -v
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
```

Expected: all commands exit `0`.

- [ ] **Step 6: Commit the command and documentation**

Stage only the CLI, preparation script, docs, and regression tests; commit with
`docs: document regression maintenance`.

### Task 5: Full verification and pull request

**Files:**
- Verify all scoped files from Tasks 1 through 4.

**Interfaces:**
- Consumes: clean branch diff against `origin/main`.
- Produces: pushed `codex/regression-fixtures` and one draft pull request targeting `main`.

- [ ] **Step 1: Run all required verification commands fresh**

Run:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
.venv\Scripts\kad-collector.exe regression
git diff --check origin/main...HEAD
```

Expected: 0 failures, 0 lint errors, 0 type errors, all supported regressions pass, and no
whitespace errors.

- [ ] **Step 2: Audit scope and secrets**

Inspect `git status`, `git diff --stat origin/main...HEAD`, and the list of tracked files.
Confirm no PDF, `.env`, database, token, cookie, generated report, EXE, or unrelated change is
tracked.

- [ ] **Step 3: Push and create one draft PR**

Push `codex/regression-fixtures`, check for an existing PR with the same head, then create one
draft PR to `main`. Include the official FUVEST acervo and PDF URLs, the supported/planned
matrix, and exact verification commands. Do not merge.
