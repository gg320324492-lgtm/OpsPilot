"""Generate the English subtitles (.srt) for the M2-05 showcase videos.

Timings are the recording's own beat offsets (see record-manifest.json, the
`beats` array record_showcase.mjs writes) shifted by the title card's length,
so each caption lands on the screen state it describes. The three videos share
one narration voice; path A is the main deliverable.

Outputs:
  web/public/showcase/demo-path-a.srt
  web/public/showcase/demo-path-b.srt
  web/public/showcase/demo-path-c.srt
  docs/assets/showcase-raw/showcase-video-script.md is updated separately.

Run:
    .venv/Scripts/python.exe scripts/make_showcase_subtitles.py
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHOWCASE = REPO / "web" / "public" / "showcase"

TITLE = 8.0  # title card seconds, prepended by cut_showcase_video.mjs
END = 8.0

# (start, end, text) in FINAL-VIDEO seconds for path A. The beat offsets from
# the manifest (+TITLE) set the boundaries; captions start a beat after the
# screen settles and end when the next beat begins.
PATH_A = [
    (
        0.0,
        8.0,
        "OpsPilot - an AI operations agent for B2B billing support. "
        "This is a live dashboard over a real api + worker + Postgres stack; "
        "the model is a scripted replay, so no external AI service is called.",
    ),
    (
        TITLE + 0.0,
        TITLE + 8.0,
        "The dashboard on a freshly brought-up demo stack: nothing parked, "
        "nothing failed, knowledge indexed.",
    ),
    (
        TITLE + 8.5,
        TITLE + 16.5,
        "A customer reports a duplicate charge on invoice INV-2026-384. "
        "The ticket form is pre-filled with the README's case.",
    ),
    (
        TITLE + 16.5,
        TITLE + 23.5,
        "Submitting creates the ticket and enqueues a run in the same transaction.",
    ),
    (
        TITLE + 23.5,
        TITLE + 33.0,
        "The run is received. The worker claims it on its next poll and starts "
        "the investigation - every step lands on the timeline as it happens.",
    ),
    (
        TITLE + 33.0,
        TITLE + 46.0,
        "The agent classified the ticket as a duplicate charge (0.93), retrieved "
        "the refund policy, and proposed a $129 refund on transaction TX-88219 - "
        "then PARKED. Nothing has been sent; the money waits for a human.",
    ),
    (
        TITLE + 46.0,
        TITLE + 52.5,
        "The Approvals inbox. The card shows exactly what will execute: the "
        "arguments, the risk level, the deterministic policy explanation - and "
        "the model's own justification, labelled untrusted.",
    ),
    (
        TITLE + 52.5,
        TITLE + 60.5,
        "The human approves. The click releases the run back to the worker - "
        "it does not execute the refund inside the request.",
    ),
    (
        TITLE + 60.5,
        TITLE + 74.0,
        "The worker executes the refund exactly once, idempotency-keyed, and the "
        "run completes with the full audit trail: 21 recorded steps, 5 cited sources.",
    ),
    (TITLE + 74.0, TITLE + 77.4, "Back on the Runs list: one completed run, fully traceable."),
    (
        TITLE + 77.4,
        TITLE + 77.4 + END,
        "Run it yourself: python scripts/demo_paths.py up. "
        "Paths B (reject) and C (model failure) ship with this video.",
    ),
]

PATH_B = [
    (
        0.0,
        6.0,
        "Path B - the human says no. Same live stack, same scripted model, no external AI calls.",
    ),
    (
        6.0 + 4.0,
        6.0 + 13.5,
        "The same duplicate-charge ticket is submitted. The investigation is "
        "identical - the agent proposes the refund and parks.",
    ),
    (6.0 + 13.5, 6.0 + 21.5, "This time the human REJECTS the refund on the Approvals inbox."),
    (
        6.0 + 21.5,
        6.0 + 35.0,
        "A rejection is the workflow working: the run COMPLETES with an "
        "escalation reply - and no money moves. The ledger still holds exactly "
        "one refund.",
    ),
    (6.0 + 35.0, 6.0 + 37.4, "The rejection is recorded as a human decision on the Rejected tab."),
]

PATH_C = [
    (
        0.0,
        6.0,
        "Path C - the model provider fails. A throwaway HTTP container answers "
        "every model call with HTTP 429; the SDK is configured with zero retries.",
    ),
    (
        6.0 + 4.0,
        6.0 + 22.0,
        "A ticket is submitted. The first model call raises a rate-limit error - "
        "and the run fails LOUDLY: model_unavailable, with the structured "
        "diagnosis on the timeline. No silent retry, no swallowed error.",
    ),
    (
        6.0 + 22.0,
        6.0 + 53.0,
        "The 429 server is removed and a real worker is restored. The next ticket is submitted -",
    ),
    (
        6.0 + 53.0,
        6.0 + 67.9,
        "- and completes without needing an approval. The queue survived the "
        "outage; the ledger is unchanged.",
    ),
]


def fmt(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, cues) -> None:
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        text = " ".join(text.split())
        lines.append(f"{i}\n{fmt(start)} --> {fmt(end)}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    write_srt(SHOWCASE / "demo-path-a.srt", PATH_A)
    write_srt(SHOWCASE / "demo-path-b.srt", PATH_B)
    write_srt(SHOWCASE / "demo-path-c.srt", PATH_C)


if __name__ == "__main__":
    main()
