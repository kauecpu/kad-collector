# Semantic Classification Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> and superpowers:test-driven-development. Steps use checkbox syntax.

**Goal:** Add conservative local semantic evidence, strict same-block context,
an optional closed-list AI fallback and automatic internal desktop auditing.

**Architecture:** PR1 remains the authoritative deterministic base. The
taxonomy exposes immutable candidate paths; local scoring and neighbor
propagation fill only unresolved fields; the optional provider selects opaque
candidate IDs and is post-validated. Desktop routes supply a fixed internal
actor while preserving stored history and decision justifications.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLite, vanilla JavaScript/HTML,
`unittest`, Ruff and mypy.

**Spec:** `docs/superpowers/specs/2026-08-22-semantic-classification-fallback-design.md`

## Task 1: Conservative local semantics and block boundaries

**Files:** `tests/test_editorial_classification.py`,
`src/kad_collector/editorial_taxonomy.py`,
`src/kad_collector/desktop_classifier.py`

- [x] Add failing tests for customs content not becoming Mathematics, a
  unique high-evidence match, low evidence remaining unresolved, propagation
  inside one explicit block and prevention across/missing blocks.
- [x] Run the focused tests and confirm the expected failures.
- [x] Implement boundary-aware phrase scoring, confidence floors and explicit
  same-block propagation without contest-specific conditions.
- [x] Run focused tests, Ruff and mypy; refactor only while green.

## Task 2: Closed-list optional AI fallback

**Files:** `tests/test_semantic_classification_fallback.py`,
`src/kad_collector/editorial_taxonomy.py`,
`src/kad_collector/desktop_models.py`,
`src/kad_collector/desktop_classifier.py`

- [x] Add failing tests for no API key, closed option IDs, invalid/partial
  responses, low confidence, local-evidence precedence and idempotency.
- [x] Run the focused tests and confirm the expected failures.
- [x] Expose deterministic taxonomy candidates and implement an injectable,
  offline-safe provider that only fills unresolved fields from validated IDs.
- [x] Record model, rule and evidence for accepted AI choices.
- [x] Run focused tests, Ruff and mypy; refactor only while green.

## Task 3: Remove reviewer/observation inputs but retain audit

**Files:** `tests/test_desktop_app.py`, `src/kad_collector/desktop_ui.html`,
`src/kad_collector/desktop_app.js`, `src/kad_collector/desktop_server.py`

- [x] Add failing route/UI behavior tests: saves and approvals work without
  actor input, the audit uses `operador_local`, existing notes survive edits,
  and exception/rejection still require justification.
- [x] Run focused tests and confirm the expected failures.
- [x] Remove the visible fields, default desktop mutations to the internal
  actor and retain only the decision justification input.
- [x] Run focused tests and static checks.

## Task 4: Offline comparison and PR completion

**Files:** `README.md` and generated terminal-only reports (not committed)

- [x] Reclassify a fresh temporary copy of the local database twice and prove
  preservation/idempotency; compare PR2 counts with PR1 and report gains,
  losses and changed classifications.
- [x] Document optional AI behavior and automatic local audit briefly.
- [x] Run full `unittest`, Ruff, mypy, compileall and `git diff --check`.
- [x] Review the diff for scope, secrets and regressions; stop on regression.
- [ ] Commit, push and open one Pull Request to `main` without merging.
