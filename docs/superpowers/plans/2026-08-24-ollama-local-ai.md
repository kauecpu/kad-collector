# Ollama Local AI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a loopback-only Ollama provider and a gated benchmark for three local models without downloading models or running inference in this pull request.

**Architecture:** A native HTTP adapter implements `CanonicalAIProvider` and exposes typed Ollama usage metrics. A preflight module owns host, disk, model, GPU, and schema checks. A separate local benchmark runner reuses the frozen canonical sample, checkpoints every measured call, and emits aggregate-only reports.

**Tech Stack:** Python 3.11, `httpx`, Pydantic 2, SQLite, `unittest`, Ollama native HTTP API.

**Spec:** `docs/superpowers/specs/2026-08-24-ollama-local-ai-design.md`

## Global Constraints

- Keep Ollama on `http://127.0.0.1:11434` or another loopback address.
- Use only `qwen3.5:9b-q4_K_M`, `qwen3:14b-q4_K_M`, and `gemma3:12b-it-qat` in the local benchmark.
- Keep concurrency at one, context at 4096, temperature at zero, and thinking disabled.
- Do not download models, run local inference, call cloud APIs, or install n8n.
- Keep raw prompts, responses, logs, and checkpoints under `data/benchmarks/local/`.
- Preserve scraping, parsing, OCR, answer association, identity, equivalence, taxonomy, and export behavior.

---

### Task 1: Native Ollama provider contract

**Files:**
- Create: `src/kad_collector/ollama_ai_provider.py`
- Modify: `src/kad_collector/canonical_classification.py`
- Modify: `src/kad_collector/canonical_ai_providers.py`
- Test: `tests/test_ollama_ai_provider.py`
- Test: `tests/test_canonical_ai_providers.py`

**Interfaces:**
- Produces: `OllamaCanonicalEnrichmentProvider`, `OllamaUnavailableError`, `OllamaUsage`, and `validate_ollama_base_url(value: str) -> str`.
- Consumes: `CanonicalAIRequest`, `CanonicalAIResult`, `canonical_ai_messages`, and `canonical_ai_response_schema`.

- [ ] **Step 1: Write failing provider boundary tests**

```python
def test_ollama_posts_native_schema_request_to_loopback() -> None:
    transport = FakeOllamaTransport.success()
    provider = OllamaCanonicalEnrichmentProvider(
        "qwen3:14b-q4_K_M", transport=transport
    )
    result = provider.enrich(canonical_request())
    assert transport.last_json["format"]["additionalProperties"] is False
    assert transport.last_json["think"] is False
    assert transport.last_json["options"] == {
        "temperature": 0,
        "num_ctx": 4096,
        "num_predict": 512,
        "seed": 0,
    }
    assert result.input_tokens == 321
    assert result.output_tokens == 24

def test_ollama_rejects_non_loopback_and_url_credentials() -> None:
    for value in ("http://192.168.0.10:11434", "https://example.com", "http://u:p@localhost:11434"):
        with self.assertRaises(CanonicalClassificationError):
            validate_ollama_base_url(value)
```

- [ ] **Step 2: Run the new tests and confirm missing symbols fail**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_ollama_ai_provider -v`

Expected: import failure for `kad_collector.ollama_ai_provider`.

- [ ] **Step 3: Add typed telemetry and the minimal native adapter**

```python
@dataclass(frozen=True)
class OllamaUsage:
    total_duration_ns: int | None = None
    load_duration_ns: int | None = None
    prompt_eval_duration_ns: int | None = None
    eval_duration_ns: int | None = None

@dataclass(frozen=True)
class CanonicalAIResult:
    response: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    provider_metrics: dict[str, int | float | str | bool | None] = field(default_factory=dict)
```

Implement `POST /api/chat` with `stream=False`, the request-specific schema, `think=False`, loopback validation, timeout handling, and Pydantic validation. Add `ollama` to the provider factory and supported provider set.

- [ ] **Step 4: Run provider and existing canonical provider tests**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_ollama_ai_provider tests.test_canonical_ai_providers -v`

Expected: PASS with no live HTTP call.

- [ ] **Step 5: Commit the provider**

```powershell
git add src/kad_collector/ollama_ai_provider.py src/kad_collector/canonical_classification.py src/kad_collector/canonical_ai_providers.py tests/test_ollama_ai_provider.py tests/test_canonical_ai_providers.py
git commit -m "feat: add local Ollama provider"
```

### Task 2: Durable pause and resume for local availability

**Files:**
- Modify: `src/kad_collector/canonical_classification.py`
- Test: `tests/test_canonical_classification.py`

**Interfaces:**
- Consumes: `OllamaUnavailableError` through the canonical `AIProviderUnavailableError` base error.
- Produces: per-question commits in apply mode and a paused report that leaves the interrupted question eligible.

- [ ] **Step 1: Write failing transaction tests**

```python
def test_unavailable_provider_commits_prior_item_and_leaves_current_item_pending(self) -> None:
    provider = SucceedsOnceThenUnavailableProvider()
    first = run_canonical_classification(
        self.connection, apply=True, enable_ai=True, provider=provider,
        run_id="local-resume", limit=2,
    )
    self.assertEqual(first.status, "paused")
    self.assertEqual(self.completed_item_ids("local-resume"), [self.first_id])
    resumed = run_canonical_classification(
        self.connection, apply=True, enable_ai=True,
        provider=AlwaysSucceedsProvider(), run_id="local-resume", limit=2,
    )
    self.assertEqual(resumed.processed, 1)
```

- [ ] **Step 2: Run the targeted test and confirm the current code completes or reviews the failed item**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_canonical_classification.CanonicalClassificationTests.test_unavailable_provider_commits_prior_item_and_leaves_current_item_pending -v`

Expected: FAIL because provider failures currently create review items and complete run items.

- [ ] **Step 3: Implement per-item commits and availability pause**

Catch the dedicated availability error outside `_process_row`, roll back only the current row, update the run status to `paused`, and preserve prior committed items. Keep dry-run rollback unchanged. Do not create a review item for service unavailability.

- [ ] **Step 4: Run all canonical classification tests**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_canonical_classification -v`

Expected: PASS, including resume and dry-run behavior.

- [ ] **Step 5: Commit transaction behavior**

```powershell
git add src/kad_collector/canonical_classification.py tests/test_canonical_classification.py
git commit -m "feat: pause local classification safely"
```

### Task 3: Ollama preflight and explicit probes

**Files:**
- Create: `src/kad_collector/ollama_preflight.py`
- Create: `tests/test_ollama_preflight.py`
- Modify: `src/kad_collector/cli.py`

**Interfaces:**
- Produces: `inspect_ollama_environment(...) -> dict[str, Any]`, `probe_ollama_models(...) -> dict[str, Any]`, `OLLAMA_BENCHMARK_TARGETS`, and CLI `preflight-ollama-ai`.
- Consumes: an injected `OllamaAdminClient`, `CommandRunner`, disk-usage reader, and Windows log reader.

- [ ] **Step 1: Write failing inspection tests**

```python
def test_inspection_never_pulls_or_generates() -> None:
    client = FakeAdminClient(tags=[installed_qwen35()])
    report = inspect_ollama_environment(client=client, free_bytes=40 * GIB)
    self.assertEqual(client.posted_chat_requests, [])
    self.assertEqual(report["missingModels"], [
        "qwen3:14b-q4_K_M", "gemma3:12b-it-qat"
    ])
    self.assertEqual(report["networkScope"], "loopback")

def test_probe_requires_matching_approval_and_full_gpu(self) -> None:
    with self.assertRaisesRegex(CanonicalClassificationError, "aprovação"):
        probe_ollama_models(preflight=ready_preflight(), approved_probe_id="wrong")
```

- [ ] **Step 2: Run tests and verify imports fail**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_ollama_preflight -v`

Expected: import failure for `kad_collector.ollama_preflight`.

- [ ] **Step 3: Implement offline inspection and gated probe**

Use `/api/version`, `/api/tags`, `/api/ps`, one schema-constrained probe per model, `ollama ps`, `shutil.disk_usage`, and optional Windows log parsing. Require 35 GiB free when models are missing. Return pull commands as data; never execute them. Reject CPU or partial GPU placement.

- [ ] **Step 4: Add CLI parsing and fake-client command tests**

Add `preflight-ollama-ai --report PATH [--probe-models --approved-probe-id ID]`. Verify the no-probe form makes no chat call and the probe form requires the matching ID.

- [ ] **Step 5: Run preflight and CLI tests**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_ollama_preflight tests.test_canonical_ai_providers -v`

Expected: PASS without Ollama installed.

- [ ] **Step 6: Commit preflight**

```powershell
git add src/kad_collector/ollama_preflight.py src/kad_collector/cli.py tests/test_ollama_preflight.py
git commit -m "feat: add gated Ollama preflight"
```

### Task 4: Checkpointed local benchmark

**Files:**
- Create: `src/kad_collector/ollama_ai_benchmark.py`
- Create: `tests/test_ollama_ai_benchmark.py`
- Modify: `src/kad_collector/cli.py`

**Interfaces:**
- Produces: `prepare_ollama_ai_benchmark(...)`, `execute_ollama_ai_benchmark(...)`, and `summarize_ollama_ai_benchmark(...)`.
- Consumes: the existing local canonical bundle, approved preflight report, exact targets, `CanonicalAIProvider`, and deterministic canonical response validation.

- [ ] **Step 1: Write failing smoke and resume tests**

```python
def test_smoke_uses_same_ten_items_for_each_exact_model(self) -> None:
    result = execute_ollama_ai_benchmark(
        self.bundle, manifest_path=self.manifest, checkpoint_path=self.checkpoint,
        preflight_path=self.preflight, phase="smoke",
        approved_benchmark_id=self.benchmark_id, max_new_calls=30,
        provider_factory=self.factory,
    )
    self.assertEqual(result["newCalls"], 30)
    self.assertEqual(self.ids_by_model(), {model: self.first_ten for model in MODELS})

def test_resume_skips_every_checkpointed_model_question_pair(self) -> None:
    self.run_smoke()
    resumed = self.run_smoke()
    self.assertEqual(resumed["newCalls"], 0)
```

- [ ] **Step 2: Run tests and confirm the local runner is absent**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_ollama_ai_benchmark -v`

Expected: import failure for `kad_collector.ollama_ai_benchmark`.

- [ ] **Step 3: Implement manifest preparation and phase gates**

Freeze the sample fingerprint, taxonomy, exact target tags and digests, Ollama version, parameters, and concurrency. Smoke requires exactly 30 available new-call slots. Full requires 30 successful smoke records and exactly 570 available new-call slots.

- [ ] **Step 4: Implement sequential execution and local telemetry**

Warm each model, write one checkpoint record after every measured call, stop on availability failure, unload before the next target, and calculate tokens per second from `eval_count / eval_duration`. Store raw output only in the ignored checkpoint.

- [ ] **Step 5: Implement aggregate-only reporting**

Emit field accuracy, all-fields accuracy, schema failures, prohibited fields, coverage, review rate, median and p95 latency, tokens, tokens per second, load duration, peak VRAM, failures, interruptions, and paired comparisons. Exclude `statement`, `alternatives`, `rawResponse`, local paths, and error bodies that contain prompt text.

- [ ] **Step 6: Add CLI commands and run tests**

Add `prepare-ollama-ai-benchmark`, `run-ollama-ai-benchmark`, and `report-ollama-ai-benchmark`. Run:

`$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_ollama_ai_benchmark tests.test_canonical_ai_benchmark -v`

Expected: PASS; the cloud benchmark remains unchanged.

- [ ] **Step 7: Commit the local runner**

```powershell
git add src/kad_collector/ollama_ai_benchmark.py src/kad_collector/cli.py tests/test_ollama_ai_benchmark.py
git commit -m "feat: add checkpointed Ollama benchmark"
```

### Task 5: Documentation and safe defaults

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/canonical-classification-v1.md`
- Modify: `docs/canonical-ai-providers.md`
- Modify: `docs/canonical-ai-benchmark.md`
- Create: `docs/ollama-local-ai.md`
- Test: `tests/test_ollama_preflight.py`

**Interfaces:**
- Documents the CLI and operational gates implemented by Tasks 1 through 4.

- [ ] **Step 1: Add behavior tests for default environment values**

Test that the provider defaults to loopback and remains dormant without `--enable-ai`. Do not grep prose in tests.

- [ ] **Step 2: Document installation, GPU verification, model sizes, checkpoints, and approvals**

Include the exact three tags, about 25 GB combined download size, the 35 GB free-space gate, `ollama ps`, Windows log location, smoke versus full phases, Ctrl+C/resume behavior, and the absence of n8n and paid providers.

- [ ] **Step 3: Add empty Ollama variables**

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=
```

- [ ] **Step 4: Run documentation-adjacent tests and lint**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest tests.test_ollama_ai_provider tests.test_ollama_preflight tests.test_ollama_ai_benchmark -v`

Run: `C:\Users\unluc\kad-collector\.venv\Scripts\ruff.exe check .`

Expected: PASS.

- [ ] **Step 5: Commit documentation**

```powershell
git add .env.example README.md docs/canonical-classification-v1.md docs/canonical-ai-providers.md docs/canonical-ai-benchmark.md docs/ollama-local-ai.md tests/test_ollama_preflight.py
git commit -m "docs: describe local Ollama workflow"
```

### Task 6: Full verification and pull request

**Files:**
- Verify all files changed in Tasks 1 through 5.

**Interfaces:**
- Produces a clean branch and a pull request to `main`.

- [ ] **Step 1: Run the complete suite**

Run: `$env:PYTHONPATH='src'; C:\Users\unluc\kad-collector\.venv\Scripts\python.exe -m unittest discover -s tests`

Expected: all tests pass; the baseline was 477 tests.

- [ ] **Step 2: Run static checks**

Run: `C:\Users\unluc\kad-collector\.venv\Scripts\ruff.exe check .`

Run: `C:\Users\unluc\kad-collector\.venv\Scripts\mypy.exe src`

Expected: both pass with no diagnostics.

- [ ] **Step 3: Scan tracked changes for secrets and raw benchmark content**

Inspect `git diff --cached` and search changed files for API-key patterns, full question statements, `rawResponse`, `.env` values, and non-loopback endpoints. Keep runtime artifacts untracked and ignored.

- [ ] **Step 4: Review the final diff and commit remaining changes**

```powershell
git status --short
git diff --check
git log --oneline origin/main..HEAD
```

- [ ] **Step 5: Push and open the pull request**

Push `codex/ollama-local-benchmark` and open a PR titled `feat: adicionar benchmark local com Ollama`. State that no model was downloaded, no local inference ran, no cloud API ran, and full mode remains approval-gated. Do not merge.
