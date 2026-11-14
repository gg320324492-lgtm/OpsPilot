# Baselines

A baseline is a **reference result** a later run can be compared against:
the committed summary of one eval run, with the facts a comparison needs to
say "same code, same data, same model" before it compares numbers.

- `phase1-baseline.json` — the Phase 1/2 baseline: the committed run
  `evals/results/2026-10-11T15-39-42.json` (live OpenAI-protocol endpoint,
  `MiniMax-M3.1-Flash-Preview`, all four datasets), reduced to the metric
  values and pinned to the commit, dataset hashes and configuration that
  produced it. This is the number "did the tool-catalogue prompt change move
  anything?" is asked against.

`.gitignore` does **not** exclude this directory: a baseline that lives only
on one machine is not a baseline, it is a note. Everything here is
desanitised already — a baseline carries metric aggregates, dataset hashes
and configuration, never ticket text or provider payloads (the raw results
file it summarises stays under `evals/results/` under that directory's own
commit rules).

## Recomputing a baseline

The summary is derived, not authoritative. To recompute it from the raw run:

```bash
opspilot-eval compare --baseline evals/baselines/phase1-baseline.json \
    --results evals/results/2026-10-11T15-39-42.json
```

`--results` is optional; omitted, the comparison target is the newest file in
`evals/results/`. Exit code is `0` when every compared metric agrees within
tolerance, `1` when one moved beyond it, `2` on a usage error. See
`docs/evals.md` §7 for what the command prints and what "moved" means.
