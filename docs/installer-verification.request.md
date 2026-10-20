# Could you install OpsPilot on your Windows machine and walk through it?

Hi — and thank you. This note has everything you need to decide whether it is
worth your time, and it is the only thing you need to read before you start.
The instructions themselves are the other file I am sending you: the
walk-through, `installer-verification.md` from the project's `docs` folder.

## What this is

**OpsPilot** is an AI operations agent for support and billing. It takes a
customer ticket, looks up the policy that applies, proposes an action — and
when that action moves money it stops and waits for a human to approve it. It
runs on your own machine, on Docker. The source is public.

One thing up front, so the walk-through does not alarm you: **the billing in
this project is simulated.** There is no payment processor behind it. The
refund the walk-through asks you to approve is a row written to a test file,
and no money moves.

## Why I am asking you

Installing OpsPilot on a clean machine and walking through it is **the one
thing this project cannot do for itself**, and the people who wrote it cannot
do it for themselves either — someone who built it knows where the rough edges
are. A release is even being deliberately held back until this passes. So I
need somebody who has never used it.

**You are not the one being tested — the instructions are.** The walk-through
is written down, but nobody outside this project has followed it yet, and I do
not know whether the writing is good enough. If a step does not make sense,
or you cannot get past something, that is the finding I am asking you for.

## What you need

- **A Windows machine** — version 10 or 11, 64-bit. An ordinary laptop is fine.
- **Docker Desktop**, installed and running. It comes from
  <https://www.docker.com/products/docker-desktop/>. If you have never used
  it, install it first, start it, and give it a minute to settle before you
  begin.
- **About 6 GB of free disk.**
- **About 15 minutes.** The first launch is the slow one: the app builds
  three container images from source on your machine, so it scrolls a lot of
  output for a while. That is normal, and it is how it is designed to work —
  the walk-through allows 5 to 15 minutes. Later launches take seconds.
- **A screen recorder** — anything that writes MP4 and lets you cap the
  bitrate. You do not need a particular program.

That is the whole list. You do **not** need this project's source code, Node, a
build tool, or an account with anybody.

## What you will do

1. **Get the installer** — one file, `OpsPilot-Setup-<version>-x64.exe`.
   - **If you have a GitHub account**, you can download it yourself from the
     project's CI runs; the walk-through's *Where to get the installer* section
     has the exact path.
   - **If you do not**, that is the ordinary situation and it is fine: **I am
     sending you the file** along with this note. Downloading a build from CI
     requires a signed-in GitHub account, so the file reaches you through me
     instead. It is the same file either way — built by the project's CI from
     one specific commit — and I can tell you which run it came from.
2. **Follow the walk-through.** It is a step-by-step script: every step says
   what you should see, and what to do when you do not see it.
3. **Record your screen while you do it.** Start recording before you open the
   installer, and leave it running for as long as the walk-through's recording
   section says — it tells you exactly where to stop. This recording is the
   piece of evidence the project is actually missing: the repository has
   screenshots of the app, but nothing of the app *arriving* on a machine that
   has never seen it. That section gives the exact file name, format and size
   to use — please follow it, because the name is checked. You do not have
   this repository and you are not expected to have one: leave the recording
   where you made it and hand it over with your report, and I will put it in.
4. **Send back what the walk-through's last section asks for.**

## If you get stuck

**Stop, and tell me. Please do not improvise a way around it.** Every step in
the walk-through is written so that somebody else can check the answer, so a
step you worked around produces a result nobody can use — and whatever stopped
you is probably exactly what I need to hear about.

The walk-through lists the failures it knows about, and for each one it says
what to do; if you hit one of those, do what it says. If you hit something it
does not list, or a step that does not do what it says, stop there, write down
what happened, and send it to me.

A walk-through that stops at the first problem and comes back with a clear
description of it is **a good outcome for me.** A report that says everything
worked, when it did not, is not.

## What to send back

- What you saw, **word for word** — the whole window log, not a summary of it.
- The output of the two commands the last section asks for.
- Your Windows version and your Docker Desktop version.
- Whether that launch was the app's first launch on this machine, or a repeat.
- Which installer you ran, and where it came from — if it came from me, just
  say so.
- Whether you made the recording, and if so **its file name and its size in
  bytes**.

## One thing to leave out

This project is public, and you are welcome to keep the installer and to write
about what you found. **Please do not paste the operator token, or the
contents of any `.env` file, into your report.** The app generates its own
token on your machine the first time it starts, and it is a secret even though
nothing valuable sits behind it. If a line you are sending happens to contain
one, replace it with `[redacted]` and mention that you did.

Thank you — genuinely. This is the one thing on this project that only a
stranger can do, and your report is what closes it.

— [your name]
