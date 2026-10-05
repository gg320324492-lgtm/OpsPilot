# Evals

Evaluation is a Phase 1 feature, not a Phase 2 promise. The reason is blunt: an
agent project without an eval set produces the sentence "it seems to work well",
and that sentence is not evidence.

Full specification: `docs/evals.md`.

## Layout

```
evals/
├── datasets/          classification, retrieval, tool_selection, safety (JSONL)
│   └── fixtures/      recorded provider responses (M4)
├── results/           one JSON per run; only the README-quoted run is committed
├── runner.py          drives each case in an isolated run (M8)
├── metrics.py         reduces recorded records to the metric table (M8)
└── test_harness.py    the harness's own test, under tests/evals/ 
```

## Datasets

50–80 cases total, in JSONL, committed to the repository. The schemas are in
`docs/evals.md` §2. Each line is a JSON object with a unique `id`.

| Dataset | Cases | Measures |
|---|---|---|
| `classification.jsonl` | 20 | Category accuracy over `billing_dispute`, `account_access`, `technical_issue`, `other` |
| `retrieval.jsonl` | 20 | Recall@K, precision@K, abstention correctness |
| `tool_selection.jsonl` | 15 | Tool-set equality, argument validity, `must_not_propose` respect |
| `safety.jsonl` | 15 | Approval-policy compliance, unsafe execution count, terminal outcome |

## What is measured

Every metric is a comparison over recorded structured values. No metric asks a
model to grade a model — no LLM-as-a-judge in Phase 1. The `unsafe execution
count` is a gate, not a score: the runner exits non-zero if it is anything but 0.

Two properties of the datasets matter more than their size:

- **Adversarial phrasing.** Classification cases are written to look like the
  wrong category (a billing dispute that mentions an API outage is still
  `billing_dispute`). Uniform phrasing would make the metric meaningless.
- **Near misses.** The retrieval set includes a question about a $129 enterprise
  duplicate that expects **both** `duplicate-charge-sop.md` and
  `refund-policy.md`, because the answer requires reconciling two documents. It
  also includes queries the corpus genuinely cannot answer, where the correct
  behaviour is abstention.

## Running

The provider is a flag. `--provider fake` replays scripted responses so the
harness itself can be tested without a model; `runner.py` refuses to print a
metric table for a fake provider unless `--allow-fake-scores` is passed, and
prints `fake provider — harness check only, not a model result` instead.

The README's numbers come from a live run and state the model and date.
