# Eval results

This directory holds one JSON file per eval run, named `<timestamp>.json`.

Four runs are committed, each added by explicit path so the numbers the
documents quote are reproducible and dated: the two the top-level README quotes
for the baseline figures (`2026-10-11T15-39-42.json`) and for the
tool-selection repeat runs (`2026-10-13T18-24-25.json`,
`2026-10-13T18-40-10.json`), plus `2026-10-08T05-24-41.json`, which
`docs/risks.md` and `docs/progress.md` cite.

`.gitignore` already excludes everything else here (`evals/results/*` with a
negation only for `.gitkeep` and this file), because a directory of every run is
noise that grows without bound.

A file in here is raw output, not a result: it is the record the metric table was
reduced from, and it is only meaningful alongside the provider and model that
produced it. The fake provider's runs appear here too and are carriage, not
evidence.
