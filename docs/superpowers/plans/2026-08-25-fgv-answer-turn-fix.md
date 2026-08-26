# FGV Answer Turn Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Associate FGV exams with answer keys when the official PDF, rather than the collection manifest, carries the application shift.

**Architecture:** Add one pure FGV structural-turn extractor and consume it from both the FGV section parser and semantic-profile extraction. Rebuild effective runtime profiles from stored immutable evidence during association so an existing database can be evaluated without rewriting identities; keep unresolved decisions reviewable and idempotent.

**Tech Stack:** Python 3.11, Pydantic, SQLite, unittest/pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-25-fgv-answer-turn-fix-design.md`

## Global Constraints

- Do not access the live FGV site in tests.
- Do not mutate the original SQLite database or Supabase.
- Do not infer `turno único` when shift evidence is absent.
- Preserve mandatory role, stage, turn, variant, and interval checks.
- Version only code, tests, and documentation.

---

### Task 1: Shared structural shift extraction

**Files:**
- Create: `src/kad_collector/fgv_turn.py`
- Modify: `src/kad_collector/fgv_parser.py`
- Modify: `src/kad_collector/semantic_identity.py`
- Test: `tests/test_fgv_turn.py`
- Test: `tests/test_fgv_parser.py`
- Test: `tests/test_semantic_identity.py`

**Interfaces:**
- Produces: `extract_fgv_turn_evidence(pages, *, document_role)` returning normalized, located evidence.
- Consumes: numbered page text and the semantic document role (`exam` or `answer_key`).

- [x] **Step 1: Write the failing structural extraction tests**

```python
assert extract_fgv_turn_evidence([(1, "MANHÃ\nPROVA OBJETIVA")], document_role="exam")[0].normalized == "manhã"
assert extract_fgv_turn_evidence([(1, "Questão 1\nAmanhã será...")], document_role="exam") == ()
```

- [x] **Step 2: Run the focused tests and verify they fail because the shared extractor does not exist**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_fgv_turn.py tests/test_fgv_parser.py tests/test_semantic_identity.py -q`

- [x] **Step 3: Implement the bounded extractor and wire both consumers**

```python
turn_evidence = extract_fgv_turn_evidence(pages, document_role="exam")
shift = turn_evidence[0].normalized if len(turn_evidence) == 1 else None
```

- [x] **Step 4: Run the focused tests and verify they pass**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_fgv_turn.py tests/test_fgv_parser.py tests/test_semantic_identity.py -q`

### Task 2: Runtime association with the exam shift

**Files:**
- Modify: `src/kad_collector/answer_association.py`
- Test: `tests/test_answer_association_v2.py`

**Interfaces:**
- Consumes: stored normalized documents and pages for exam/key versions.
- Produces: an effective in-memory profile and an interval parsed from the block selected by the exam shift.

- [x] **Step 1: Write failing integration tests for morning, afternoon, types 1-4, 80/60/70 intervals, definitive precedence, and scope conflicts**

```python
context, decision = decide_runtime_association(connection, exam_version_id)
assert decision.selected_version_id == definitive_version_id
assert context.candidates[0].question_interval == QuestionInterval(first=1, last=80)
```

- [x] **Step 2: Run the association tests and verify the missing-turn reproduction fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_answer_association_v2.py -q`

- [x] **Step 3: Re-extract effective profiles in memory and parse each key with the exam's single shift**

```python
exam_profile = effective_profile_for_version(connection, exam_version_id, stored_profile)
entries = parse_answer_key(text, role=role, variant=variant, turn=turn)
```

- [x] **Step 4: Run the association tests and verify every scope guard remains green**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_answer_association_v2.py -q`

### Task 3: Review queue and idempotent reconciliation

**Files:**
- Modify: `src/kad_collector/answer_association.py`
- Test: `tests/test_answer_association_v2.py`

**Interfaces:**
- Consumes: `DocumentAssociationDecision.reason` plus detailed incomplete/conflict comparisons.
- Produces: one pending `association_review_queue` row per unresolved exam on apply; dry-run remains read-only.

- [x] **Step 1: Write failing tests for a no-turn case, a specific queue reason, and repeated execution**

```python
assert decision.outcome == "incomplete"
assert "turn" in review["reason"].casefold()
assert review_count == 1
```

- [x] **Step 2: Run the tests and verify the missing initial-review behavior fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_answer_association_v2.py -q`

- [x] **Step 3: Add idempotent review upsert without overwriting human decisions**

```sql
INSERT INTO association_review_queue
  (exam_version_id, run_id, status, reason, candidates_json, created_at, updated_at)
VALUES (?, ?, 'pending', ?, ?, ?, ?)
ON CONFLICT(exam_version_id) DO UPDATE SET status = 'pending', reason = excluded.reason
WHERE association_review_queue.status IN ('pending', 'obsolete')
```

- [x] **Step 4: Run association and desktop tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_answer_association_v2.py tests/test_desktop_collection.py -q`

### Task 4: Existing-data dry-run and active documentation

**Files:**
- Modify: `docs/answer-key-revalidation-v2.md`
- Modify: `README.md`
- Local only: `data/benchmarks/local/fgv-answer-turn-fix/`

**Interfaces:**
- Consumes: copied SQLite/WAL/SHM and `revalidate-answer-keys` dry-run.
- Produces: aggregate counts only; no questions, answers, PDFs, or sensitive paths.

- [x] **Step 1: Copy the database sidecars into the ignored local validation directory**

Run: PowerShell `Copy-Item` with exact validated source and destination paths.

- [x] **Step 2: Run the read-only baseline and corrected dry-run**

Run: `.venv\\Scripts\\kad-collector.exe revalidate-answer-keys --database data\\benchmarks\\local\\fgv-answer-turn-fix-final\\collector.sqlite3` without `--apply`.

- [x] **Step 3: Record aggregate outcomes for 23 exams, 1.120 processed questions, and 420 exception questions**

Expected safety rule: exception documents are reported separately and are not declared ready.

- [x] **Step 4: Document structural turn extraction, multi-turn key coverage, and the safe reconciliation command**

### Task 5: Full verification and PR

**Files:**
- Review all modified files.

**Interfaces:**
- Consumes: completed implementation and local aggregate validation.
- Produces: one commit and PR against `main`; no merge.

- [x] **Step 1: Run focused, regression, and full test suites**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_semantic_identity.py tests/test_fgv_turn.py tests/test_fgv_parser.py tests/test_answer_association_v2.py tests/test_desktop_collection.py tests/test_official_regression.py -q`

Run: `.venv\\Scripts\\python.exe -m pytest -q`

- [x] **Step 2: Run lint, types, and whitespace verification**

Run: `.venv\\Scripts\\ruff.exe check .`

Run: `.venv\\Scripts\\mypy.exe src`

Run: `git diff --check`

- [x] **Step 3: Review the diff against every requirement and exclude local artifacts**

Run: `git status --short` and `git diff --stat origin/main...HEAD` after committing.

- [ ] **Step 4: Commit, push, and open the requested PR**

Run: `git commit -m "fix: associar gabaritos FGV pelo turno do PDF"`, push `codex/fgv-answer-turn-fix`, and create a PR to `main` without merging.
