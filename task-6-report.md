# Task 6 report

- RED: the new semantic selector import failed because `select_answer_key` did not exist.
- GREEN: semantic scoring, conflict elimination, threshold/margin, stable ordering, assessments,
  and desktop/offline adapter integration were implemented.
- Ruff: passed with `ruff check --no-cache` on the three production modules.
- Mypy: the installed mypy 2.3.1 terminated with an internal error before reporting diagnostics.
- The legacy suite still has expectations for the removed single-candidate shortcut and
  title-only matching; those are intentionally incompatible with Task 6's conservative rules.
