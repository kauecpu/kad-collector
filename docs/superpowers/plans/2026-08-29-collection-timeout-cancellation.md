# Collection Timeout and Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure desktop collections cannot remain indefinitely in `Baixando` or `Cancelando` when the first transport operation stalls.

**Architecture:** Keep HTTP and Scrapling transports intact, but execute each collection in a bounded worker boundary with explicit cancellation and timeout reporting. Push progress events from the collection manager into the in-memory desktop job so the UI reflects requests before the final manifest is written. Preserve checkpoints and final manifests for normal completion and bounded failures.

**Tech Stack:** Python 3.11, `threading`, existing `httpx`/Scrapling transport, `unittest`, PyInstaller desktop app.

**Spec:** User-provided timeout/cancellation prompt in the conversation.

## Global Constraints

- Do not access PCI Concursos or COMVEST live.
- Do not change `solve_cloudflare` or any bypass setting.
- Do not alter collected questions or operational databases.
- Automated tests use local fixtures and injected transports only.
- Do not commit PDFs, databases, logs, secrets, or tokens.
- Preserve existing parsing, metadata, checkpoints, cache, deduplication, and manifest formats.

---

### Task 1: Reproduce the stuck collection with a failing test

**Files:**
- Modify: `tests/test_desktop_collection.py`
- Test: `tests/test_desktop_collection.py`

**Interfaces:**
- Consumes the existing `DesktopCollectionManager` and injected `collect_documents` seam.
- Produces assertions for bounded cancellation, timeout state, and incremental telemetry.

- [ ] **Step 1: Write the failing tests**

Add tests that inject a blocking collector and assert that a cancellation request completes within the configured grace period, changes the job to `cancelled`, and records a failure/timeout event. Add a second test that emits one telemetry event before blocking and asserts the desktop job exposes that request count while still running.

- [ ] **Step 2: Run the focused tests and verify they fail for the expected reason**

Run: `python -m unittest tests.test_desktop_collection -v`

Expected: the new tests fail because the manager has no bounded worker cancellation or progress callback.

- [ ] **Step 3: Commit the failing tests**

```powershell
git add tests/test_desktop_collection.py
git commit -m "test: reproduce collection startup cancellation hang"
```

### Task 2: Add bounded execution and cancellation propagation

**Files:**
- Modify: `src/kad_collector/desktop_collection.py`
- Modify: `src/kad_collector/collector.py`
- Modify: `src/kad_collector/collection_transport.py`
- Test: `tests/test_desktop_collection.py`

**Interfaces:**
- `collect_documents(..., progress_callback: Callable[[CollectionTelemetryEvent], None] | None = None)` remains backward compatible.
- `DesktopCollectionManager` owns a per-job deadline and cancellation event.

- [ ] **Step 1: Implement the smallest transport progress callback**

Invoke the callback immediately after each sanitized telemetry event is persisted. Leave the event schema unchanged and pass `None` from existing callers.

- [ ] **Step 2: Implement deadline-aware collection execution**

Run the blocking collection operation behind a per-job worker boundary. On cancellation or deadline expiry, close the client/session, record a sanitized failure, preserve the existing checkpoint, and update the job to `cancelled` or `failed` with a clear message. Do not kill unrelated jobs.

- [ ] **Step 3: Run the focused tests and verify they pass**

Run: `python -m unittest tests.test_desktop_collection -v`

Expected: cancellation and timeout tests pass, including cleanup assertions.

- [ ] **Step 4: Commit the transport/manager change**

```powershell
git add src/kad_collector/desktop_collection.py src/kad_collector/collector.py src/kad_collector/collection_transport.py tests/test_desktop_collection.py
git commit -m "fix: bound collection startup and cancellation"
```

### Task 3: Surface live state and final diagnostics in the desktop UI

**Files:**
- Modify: `src/kad_collector/desktop_app.js`
- Modify: `src/kad_collector/desktop_collection.py`
- Test: `tests/test_desktop_collection.py`

**Interfaces:**
- Existing `/api/bootstrap` payload remains compatible; `collectionJobs[*].telemetry` is updated incrementally.
- Terminal jobs expose `error`, `warnings`, `failureDetails`, and `manifestPath` when available.

- [ ] **Step 1: Add a failing UI-state assertion**

Assert that a running job with one emitted telemetry event reports `1 req` and that a timed-out job renders a terminal error instead of the generic “A página e os PDFs estão sendo verificados.” message.

- [ ] **Step 2: Implement incremental state updates and terminal labels**

Merge progress updates into the job without replacing existing counters. Add explicit labels for waiting, timeout, cancelled, transport failure, and completed-without-documents. Keep the existing details disclosure for warnings and output paths.

- [ ] **Step 3: Run focused tests**

Run: `python -m unittest tests.test_desktop_collection -v`

Expected: all live-state and terminal-label assertions pass.

### Task 4: Add local fixture coverage for transport outcomes and idempotency

**Files:**
- Modify: `tests/test_collection_engine.py`
- Modify: `tests/test_collector.py`
- Create: `tests/fixtures/transport/blocked.html`
- Create: `tests/fixtures/transport/ok.html`

**Interfaces:**
- Uses existing fixture clients and fake Scrapling session factories; no network access.

- [ ] **Step 1: Add tests for 200, redirects, 403, 429, 5xx, network error, retry exhaustion, challenge, checkpoint resume, and repeated execution**

Each test asserts sanitized telemetry, bounded retry count, no duplicate documents, and preserved original/canonical URLs.

- [ ] **Step 2: Run the fixture suite**

Run: `python -m unittest tests.test_collection_engine tests.test_collector -v`

Expected: all existing and new fixture tests pass without external requests.

### Task 5: Verify packaging and repository hygiene

**Files:**
- Modify: `README.md`
- Modify: `KADCollector.spec` only if the verification shows a missing runtime resource.
- Test: existing project test suites.

**Interfaces:**
- Documentation describes timeout, cancellation, state labels, and local test commands.

- [ ] **Step 1: Update documentation**

Document the default deadline, cancellation behavior, where terminal manifests are written, and how a blocked transport is reported. State that no bypass setting is changed.

- [ ] **Step 2: Run all verification commands**

Run: `python -m unittest discover -s tests -v`, `ruff check .`, `mypy`, and `npm run check` only if a package script exists in the repository.

- [ ] **Step 3: Build the executable outside Git**

Run the existing PyInstaller command using `KADCollector.spec`, place the output under `dist/`, and confirm no artifact is tracked.

- [ ] **Step 4: Review the diff and create the PR**

Run `git status --short`, `git diff --check`, commit the implementation, push the `codex/collection-timeout-cancellation` branch, and open a Pull Request to `main` without merging. Report exact tests, build result, and remaining limitations.
