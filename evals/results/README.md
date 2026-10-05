# Eval results

This directory holds one JSON file per eval run, named `<timestamp>.json`.

Only the single run the top-level README quotes is committed, and it is added by
explicit path so the README's numbers are reproducible and dated.

`.gitignore` already excludes everything else here (`evals/results/*` with a
negation only for `.gitkeep` and this file), because a directory of every run is
noise that grows without bound.

A file in here is raw output, not a result: it is the record the metric table was
reduced from, and it is only meaningful alongside the provider and model that
produced it. The fake provider's runs appear here too and are carriage, not
evidence.
