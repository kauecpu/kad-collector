# Semantic Classification Fallback Design

## Goal

Improve the PR1 deterministic classifier without weakening its closed,
versioned taxonomy or its audit trail. Classification must remain useful
offline, use neighboring questions only inside an explicit section/block, and
optionally ask an AI provider to choose one of the taxonomy paths after local
evidence is exhausted.

The same PR removes the visible reviewer and observation fields from the
desktop workflow. The application keeps an internal audit actor
(`operador_local`) and still requires a justification for exception or
rejection.

## Classification pipeline

1. Preserve human classifications and existing official answer data.
2. Apply PR1 evidence in order: document metadata, section heading, official
   ranges and deterministic local semantic evidence.
3. Score exact token/phrase evidence against the closed taxonomy. A unique
   candidate must meet the configured confidence floor; otherwise it remains
   unresolved.
4. Propagate a complete classification only between adjacent questions that
   have the same explicit, non-empty block identifier. Missing block identity
   never permits propagation.
5. If explicitly configured, ask AI only for still-unresolved questions. The
   request contains opaque IDs for the permitted taxonomy paths; responses
   with free names, unknown IDs, missing evidence or low confidence are
   ignored.
6. AI fills only empty fields. It cannot replace human, metadata, range,
   heading or local-semantic evidence.

Every accepted value records source, confidence, short evidence, rule/model
and taxonomy version. Repeating classification with the same inputs must
produce the same stored result.

## Desktop approval

The form no longer asks for reviewer or generic observations. Saving,
approving or deferring uses the internal actor `operador_local`. Existing
review notes remain stored and are not erased by an edit. Exception and
rejection keep the visible justification field and the existing validation.
Batch approval also uses the automatic actor and no generic notes.

## Safety and acceptance

- No rule is specific to Receita Federal, FGV or a particular contest.
- The classifier works without network access or an AI key.
- Unknown taxonomy names are rejected.
- Local context never crosses section/block boundaries.
- Official answers, annulments, answer-key associations and human decisions
  are preserved during reclassification.
- Focused tests, the complete suite, Ruff, mypy and an offline database-copy
  comparison must pass before the PR is opened.
