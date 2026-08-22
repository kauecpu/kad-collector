# PR3 — Import readiness and assisted review implementation plan

**Branch:** `codex/import-readiness-review`  
**Stack:** Python 3.11+, Pydantic, SQLite, local HTML/CSS/JS, `unittest`.

## Task 1 — Structured import diagnosis

1. Add failing tests for missing classification, missing answer, invalid alternatives,
   ambiguous association, duplicate, unproved origin and version conflict.
2. Run the focused tests and confirm RED.
3. Add the structured diagnosis module and make the desktop view derive importability
   from it while preserving compatibility validation.
4. Run focused tests and existing classification tests.

## Task 2 — Filters, facets and summary

1. Add failing store tests for importable/unclassified/blocked filters, blocker
   reason facets and grouped summary counts.
2. Confirm RED, then extend `DesktopFilterSet`, `_matches`, `_facets` and `_summary`.
3. Add the new facets and individual diagnosis to the desktop UI.
4. Run focused desktop tests.

## Task 3 — Assisted batch review

1. Add failing tests for equal suggestion/evidence preview, rejection of mixed or
   stale batches, explicit token confirmation, audit, human-decision preservation and
   safe reversion.
2. Confirm RED, then add batch ledger tables and transactional store operations.
3. Expose preview/apply/revert routes and a confirmation dialog in the desktop UI.
4. Run focused store/server tests and regression tests for human decisions.

## Task 4 — Read-only export preview and final validation

1. Add failing tests proving preview and export use the same inclusion rules and that
   preview has no filesystem/database side effects.
2. Confirm RED, extract a shared export evaluation and add the preview endpoint/UI.
3. Run the full `unittest` suite with the worktree `PYTHONPATH`, Ruff, mypy,
   compileall and `git diff --check`.
4. Copy the live SQLite database to a temporary directory, collect initial metrics,
   exercise diagnosis/review safely on the copy, repeat for idempotence and compare
   protected answers, links, statuses and human decisions.
5. Review the diff, commit, push and open a PR to `main` without merging or publishing
   an executable.

