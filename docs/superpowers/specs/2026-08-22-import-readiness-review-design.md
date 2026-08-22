# PR3 — Import readiness and assisted review

## Goal

Make every import block understandable and actionable, without weakening existing
quality gates or mixing app importability with editorial publication readiness.

## Boundaries

- App importability does not require explanation or difficulty.
- Missing official answers, invalid alternatives, ambiguous answer-key links,
  unresolved duplicates, unproved origin and version conflicts remain blocking.
- The feature does not collect PDFs, run OCR, call AI or modify the KAD App.
- Existing human decisions and official-answer data are immutable unless the operator
  explicitly edits the corresponding question through an existing flow.

## Architecture

### 1. Canonical diagnosis

`import_readiness.py` owns structured issues. Each issue has a stable code and the
four operator-facing answers: what happened, why it blocks, how to resolve it and
which source document produced the record. The desktop view derives `importable`
from this diagnosis; legacy string errors remain available through the validation
module for compatibility.

Structural question checks and store context are combined in one diagnosis:
question fields/answers/alternatives/pages, document provenance, duplicate flags and
semantic association/version state. Explanation and difficulty are intentionally
absent from this gate.

### 2. Readiness filters and summary

The desktop filter contract gains two independent facets:

- readiness: `importable`, `unclassified`, `blocked`;
- blocking reason: stable issue code.

`unclassified` means at least one of discipline, matter or subject is absent. A
question can therefore be both unclassified and blocked. Summary counters group
all blocking reasons without hiding overlaps.

### 3. Assisted classification review

The operator selects pending questions. A preview accepts the selection only when
all questions contain the same discipline/matter/subject suggestion and the same
classification evidence/provenance. A server-issued confirmation token binds the
question IDs, current classification snapshots and evidence. Applying the preview
requires that exact token, preventing stale or altered selections.

Confirmation keeps the proposed values but records them as a human-confirmed
classification. It does not alter answers, alternatives, answer-key relationships,
editorial status, reviewer or notes. A small batch ledger stores before/after
classification snapshots. Reversion is permitted only while current classification
still equals the batch result; otherwise it stops rather than overwriting a later
human decision. Every item receives an audit event.

### 4. Export preview

The preview is read-only and reuses the same candidate selection and quality rules
as the real desktop export. It returns the questions that would enter the JSONL and
grouped exclusions, but creates no directory, copies no PDF and marks nothing as
exported.

## Data safety

The schema adds only review-batch ledger tables. Existing question rows are updated
transactionally and only in `classification_json` during assisted confirmation or
reversion. Before and after snapshots are preserved. Final validation runs on a
temporary copy of the application database and compares protected columns before
and after.

