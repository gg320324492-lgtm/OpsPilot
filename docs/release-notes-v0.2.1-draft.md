# v0.2.1

Patch release: two upgrade-safety fixes found by the interactive walks, plus
the upgrade path verified live on a machine that still had v0.2.0 installed.

## Fixed

- **Upgrades no longer reuse a previous build's images.** Every built service
  now names its image with a versioned tag
  (`opspilot-api:0.2.1`, …), and the desktop shell passes its own version as
  the tag. A fixed name made `docker compose up` reuse whatever image it
  found — on the walkthrough machine that meant a reinstall silently ran an
  older API that was missing the export route. Verified live: after the
  0.2.0 → 0.2.1 override install, every container ran `opspilot-*:0.2.1` and
  the export route answered `200`.
- **Crashed containers restart; a killed app no longer pins them up
  indefinitely.** The four long-running services use `restart: on-failure`
  instead of `unless-stopped`. The normal quit path is unchanged (the shell
  still runs `compose down` itself).

## Verified in the upgrade walk

- Override install over v0.2.0: registry reports 0.2.1, the packaged compose
  carries both fixes, cold start 70 s to `/ready`.
- Golden path: ticket → run → approval → approved → completed; a second
  approve is refused with `409 approval_already_decided`; an unauthenticated
  write is refused with `401`.
- Data survives: tickets, runs and volumes persist across the restart.

## Notes

- The packaged run still uses the scripted fake provider by default; the
  dashboard carries the disclosure.
- A killed app process's healthy containers keep running until the operator
  runs `docker compose -p opspilot down` — restart policies act on container
  exit, not on an external process's death. Automatic reclamation on abnormal
  termination is future work.

**Install:** `OpsPilot-Setup-0.2.1-x64.exe` — 130,567,926 bytes, SHA-256
`567bec190d0a05d27c9aa29f99db68b3e1b96b32be0ef4a2ef924ee665f676c5`.
