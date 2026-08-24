# Ollama Local AI Design

## Objective

Add Ollama as an opt-in provider for canonical classification and prepare a reproducible local benchmark for these exact models:

- `qwen3.5:9b-q4_K_M`
- `qwen3:14b-q4_K_M`
- `gemma3:12b-it-qat`

The change must not download a model, call a cloud provider, expose Ollama beyond loopback, or run the smoke or full benchmark without an explicit approval identifier.

## Boundaries

The existing deterministic classifier remains the first stage. Ollama receives one request per canonical question and only the missing fields among `discipline`, `matter`, `subject`, and `level`. The existing taxonomy validator remains authoritative. The provider cannot write answers, official identity, provenance, difficulty, or explanation.

This pull request does not change scraping, parsers, OCR, answer keys, identity, equivalence, taxonomy, exports, or production data. It does not install n8n or add a cloud fallback.

## Provider architecture

Create a native Ollama adapter around `POST /api/chat` with `stream: false`. The request includes the canonical JSON Schema in `format`, the same schema in the prompt, `think: false`, `temperature: 0`, `num_ctx: 4096`, a short output limit, and one model loaded at a time. The native endpoint supplies `load_duration`, prompt and generation token counts, prompt and generation durations, and total duration. The existing OpenAI-compatible providers keep their current implementation.

The adapter accepts only `http://127.0.0.1`, `http://localhost`, or `http://[::1]`. `OLLAMA_BASE_URL` defaults to `http://127.0.0.1:11434`; `OLLAMA_MODEL` supplies the model when the CLI does not. The adapter uses the existing `httpx` dependency, validates the response with Pydantic, and maps transport failures to a dedicated availability error.

Extend `CanonicalAIResult` with typed optional provider telemetry. Cloud providers may leave it empty. This keeps classification independent from benchmark-only measurements.

## Classification availability and checkpoints

The classifier distinguishes invalid model output from an unavailable local service. Invalid output enters review under the existing rules. An unavailable Ollama instance pauses the run without completing the current question or placing it in review.

In apply mode, the classifier commits each completed question and updates the run cursor before starting the next question. A restart with the same run ID skips committed items. Dry-run keeps its all-or-nothing rollback. Ctrl+C rolls back only the current question; committed items remain available for the next run.

## Preflight

Add a two-stage `preflight-ollama-ai` command.

The inspection stage performs no inference. It checks the Ollama version, loopback endpoint, free space in the model volume, installed tags and digests, expected quantizations, and the presence of at least 35 GB of free space when any model is missing. It reports pull commands but never runs them. The report contains a fingerprinted probe ID.

The probe stage requires `--probe-models` and a matching `--approved-probe-id`. It sends one schema-constrained warm-up to each installed target, queries `/api/ps`, and requires full GPU placement. The public API reports `size` and `size_vram`; `ollama ps` reports the authoritative `100% GPU` status. On Windows, the probe also reads `%LOCALAPPDATA%\Ollama\server.log` and records the latest `offloaded X/Y layers` message when present. Missing layer detail remains `null` with a reason; it does not get fabricated. CPU-only or split CPU/GPU placement fails the gate.

The preflight records model size, digest, quantization, context, structured-output result, load time, VRAM, processor placement, Ollama version, and probe timestamp. Tests inject HTTP and command clients and never contact a real service.

## Local benchmark

Reuse the existing 200-item canonical benchmark bundle as the only source of prompts and gold labels. Create a local manifest that fixes the sample fingerprint, taxonomy version, Ollama version, target IDs, exact model tags and digests, generation parameters, concurrency one, and benchmark algorithm version. The versioned manifest contains no statements, alternatives, or raw responses.

The local runner processes targets sequentially. It performs one unmeasured warm-up, then ten measured questions per target in smoke mode. It writes a checkpoint after every measured call and unloads the model before moving to the next target. It disables retries. Re-running the same phase skips existing checkpoint keys.

Smoke mode requires a matching benchmark ID and `--max-new-calls 30`. Full mode requires all 30 smoke records, no provider-level smoke failure, a new explicit approval, and `--max-new-calls 570`. This pull request prepares full mode but does not execute it.

The aggregate report contains per-field accuracy, all-requested-fields accuracy, invalid and prohibited responses, coverage, review rate, median and p95 latency, input and output tokens, tokens per second, model load time, peak VRAM, failures, interruptions, and paired comparisons. It contains no question text, raw response, local path, or secret. A deterministic taxonomy comparison provides the score; no LLM judges another model.

## Error handling

- Missing Ollama or a refused connection pauses classification and stops a benchmark phase.
- A missing model blocks the probe and benchmark and prints the exact pull command.
- A schema failure records an invalid response; it does not relax the schema or retry.
- A fingerprint mismatch blocks checkpoint reuse.
- CPU or partial GPU placement blocks measured calls.
- A failed smoke leaves the benchmark incomplete and prevents full mode.
- Ctrl+C preserves records written before the interrupted call.

## Security and local data

Ollama stays on loopback. The adapter rejects credentials in URLs and non-loopback hosts. Raw benchmark data, checkpoints, detailed logs, and responses stay under `data/benchmarks/local/`, which Git already ignores. Git may contain the safe manifest, aggregate report, documentation, and tests.

## Verification

All new behavior follows red-green-refactor. Unit tests use fake HTTP transports, fake command runners, temporary SQLite databases, and temporary checkpoints. The final verification runs the complete 477-test baseline plus new tests, Ruff, mypy, and a secret-pattern scan. No automated test uses the network or starts Ollama.

