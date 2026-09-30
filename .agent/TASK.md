# TASK (single worktree, two agents)

## Milestone 1 — scoped skeleton + partner baseline (superseded, kept for history)

- A1 (Agent-A): `src/omnissa_agent/gmail_scope.py` + `tests/test_scope_guard.py`
  green. **Done, and since realigned (2026-09-30)** to the real
  production account/label (`george@gjh-inc.com` /
  `Archive_/@omnissa.com`), not the day-one placeholder
  (`consult@gjh-inc.com` / label `Omnissa`) originally listed below.
- B1 (Agent-B): `docs/omnissa-partner-baseline.md` — **done**, filled
  with sourced partner-status facts (Partner ID, renewal, OSP/OTSP
  enablement gap, preferred-distributor gap), each cited to a Gmail
  thread + date. Read at runtime by `classify --baseline-file` for the
  brief's always-present Scoreboard section.
- B2 (Agent-B): review A1, blockers-only note in `docs/`.

## Current state (2026-09-30) — NOT blocked, already live

Gmail OAuth (readonly), the real label, and 24x7 scheduling are **all
already approved and running** — this is no longer "day one." Two
systemd timers (`omnissa-ingest-scan.timer` hourly,
`omnissa-ingest-brief.timer` daily 07:45 Central) drive a
privilege-separated ingest+classify pipeline; see
`docs/operations-record.md` for the architecture and incident history,
and `infra/24x7/README.md` for what's viewable read-only and how.

What's still genuinely gated, unchanged from the original list below:

1. `gmail.send` / `gmail.compose` — not granted, not requested. See
   `docs/autonomous-vision-and-open-decisions.md`.
2. Course / partner-portal credential submission, any real-world action
   on Omnissa's systems (portal submit, deal registration, enrollment,
   purchase) — never without explicit operator execution. No code path
   in this repo can do any of these.
3. Orca or any coding-agent automation reading/acting on Gmail — never;
   Orca stays a read-only viewer of already-written report files.

## Done when

`python3 -m pytest tests/ -q` green, no cross-path edits, peer review
loop exercised once over Orca CLI (send → read → fix).
