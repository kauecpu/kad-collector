# Canonical Qwen Recovery Design

## Goal

Make the representative question the single editorial source for each confirmed equivalence group without losing valid human or local-Qwen classifications.

## Rules

- The representative is selected only from provenance and extraction quality. Editorial classification never affects that choice.
- Human classification has the highest priority, followed by accepted Qwen classification, then deterministic local classification.
- Reclassification may fill an empty field or replace an older deterministic result with a new deterministic result. It may not erase a non-empty value and may not replace human or Qwen values.
- Before reclassification, protected values still present on equivalent copies are recovered to the representative. A unique highest-priority value is restored; conflicting protected values send only that group to review.
- After any representative change, Disciplina, Matéria, Assunto and Nível are synchronized to every occurrence in the group. Answers remain occurrence-specific and are related by alternative text.
- Recovery and synchronization are idempotent and append an audit/equivalence event only when data changes.
- Normal lists, Qwen work, review and export use representatives only. Raw occurrences and copies remain visible in a separate operational summary and in “Ver cópias e origens”.

## Safety

The migration is in-place and preserves PDFs, answer keys, occurrences and audit history. It performs no inference and calls no external service.
