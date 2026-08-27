# Canonical Qwen Recovery Implementation Plan

**Goal:** Recover accepted Qwen classifications from equivalent copies and make representatives the only operational records.

**Architecture:** Add a transactional recovery pass to the equivalence layer, apply protected-value precedence during local reclassification, and synchronize representative editorial fields across the confirmed group. Change desktop queries to return representatives by default while exposing raw occurrence totals separately.

**Tech Stack:** Python, SQLite, vanilla JavaScript, unittest/pytest, PyInstaller.

### Task 1: Protect classifications during reclassification

- Add regression tests for accepted Qwen fields followed by local reclassification.
- Preserve human/Qwen values and any non-empty value when the rule result is empty.
- Verify idempotence and audit behavior.

### Task 2: Recover classifications from copies

- Add tests for recovery, alternative reordering, stable representative and repeated execution.
- Recover the unique highest-priority protected field to the representative.
- Synchronize all occurrences and record recovery history.
- Route true protected-value conflicts to equivalence review.

### Task 3: Use representatives in operational flows

- Add API tests proving the dashboard/query excludes copies.
- Keep raw appearances in the operational summary.
- Confirm Qwen, normal review and export receive one representative per group.

### Task 4: Validate and deliver

- Run targeted tests, full suite, lint and type checks documented by the repository.
- Apply recovery to the active local database without inference.
- Build and smoke-test the Windows executable.
- Commit, push and open a pull request to `main` without merging.
