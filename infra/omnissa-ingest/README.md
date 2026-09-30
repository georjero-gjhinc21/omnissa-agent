# Operator root handoff — Gmail credential + analysis isolation

This directory (plus `infra/omnissa-analysis/`) is the complete,
reviewed handoff for isolating both the Gmail OAuth credential AND the
classification/drafting step from `georjero` (the identity Claude/Orca
run as). **Run these yourself, with your own sudo — no agent session
can or should run them.** Full operational detail:
`docs/operations-record.md`. Security reasoning:
`docs/gmail-ingestion-security-review.md`.

## Run

```sh
sudo bash /home/georjero/omnissa-agent/infra/omnissa-ingest/deploy-root.sh
```

Idempotent — safe to re-run after a code update or to pick up a new
piece (e.g. this revision adds the `omnissa-analysis` identity to an
already-deployed `omnissa-ingest`). It refuses to run from an
uncommitted/dirty source tree and prints exactly which git commit it's
about to deploy. Ends with two clearly separated verdicts —
`SCHEDULED-AGENT ISOLATION: PASS/FAIL` (what this script actually
builds and verifies) and a standing `OPERATOR ACCOUNT RISK` disclosure
about `georjero`'s own Docker access (a separate, pre-existing fact,
never merged into the isolation verdict) — and does
**not** enable the timers itself.

## What it changes

- **`omnissa-ingest`** (system user, no login) owns the Gmail OAuth
  credential — moved from `~/.config/omnissa-agent-google/` to
  `/var/lib/omnissa-ingest/google/` (0700/0600). Runs only
  `cli.py ingest` — the ONLY command in this codebase that touches a
  token or the Gmail API.
- **`omnissa-analysis`** (system user, no login) runs `cli.py classify`
  (Agent A classification, Agent B local drafting, OmniRoute calls).
  Never touches Gmail or a credential. Verified by the script to carry
  NONE of `docker`/`lxd`/`sudo`/`adm`. Network restricted to loopback
  at the systemd level.
- **`omnissa-readers`** group — `georjero` and `omnissa-analysis` both
  join it, read-only, for `/var/lib/omnissa-agent/drop/` (sanitized
  ingest output: ids/subjects/snippets/senders/dates, never a token).
- **`omnissa-reports-readers`** group — `georjero` joins it, read-only,
  for `/var/lib/omnissa-analysis/state/reports/` (finished briefs and
  local drafts).
- A private, root-owned copy of this repo's `src/` at
  `/opt/omnissa-agent` — `georjero` cannot write to it.
- 4 systemd services + **2 timers** (hourly scan; daily 07:45 America/
  Chicago brief fetch). `omnissa-analysis` has no timer of its own — its
  two services are activated exclusively by the matching ingest
  service's `OnSuccess=` the instant that run exits cleanly, so a
  partial/failed ingest (exit 6/PARTIAL or worse) never chains into a
  success-looking brief, and there's no fixed-offset window where
  analysis could read stale or in-progress data. `georjero` has no
  systemctl rights over any of the 4 services.

## Why this boundary, not just `chmod 600`

`chmod 600` restricts access by *Linux user*, not by *process*.
Claude/Orca and any Gmail-ingesting or analysis code all currently run
as `georjero` — same uid, so file permissions alone grant identical
access regardless of mode bits. A genuinely different OS identity is
the only thing the kernel actually enforces a boundary on. Two such
identities exist now, not one — Orca is not part of the autonomous
path at all (see `docs/operations-record.md` §1 for why: it only ever
runs as georjero).

## What it deliberately does NOT close

`georjero` is in the `sudo` and `docker` groups. Docker group membership
is root-equivalent on its own (`docker run -v /:/host ... chroot /host`)
— independent of the sudo password. This means `georjero` (and any
agent running as it) can already reach real root through Docker,
bypassing every permission this script sets up. **The script's own
validation output says this explicitly — it never reports the privilege
boundary as a clean PASS while this remains true.** It defends against
**accidental/casual** cross-identity access (a buggy or over-eager
agent trying to open the token file gets a genuine permission error) —
not a **deliberate** escalation by georjero itself. Three options, none
applied here (your call — see `docs/gmail-ingestion-security-review.md`
for the trade-off table):
1. Remove `georjero` from `docker`/`sudo` — closes it fully, costs
   passwordless `docker` CLI access.
2. Migrate to rootless Docker — closes it fully, real migration effort.
3. Accept the residual risk, documented (current state).

## Rollback

```sh
sudo bash /home/georjero/omnissa-agent/infra/omnissa-ingest/rollback-root.sh
```

Stops/removes both timers and all 4 units, moves the OAuth credential
files back to `~/.config/omnissa-agent-google/` (not deleted), and
preserves — never silently deletes — everything with state: both
identities' checkpoints, analysis's reports, and any drop file ingest
had written but analysis hadn't yet consumed, all copied under
`~/.local/state/omnissa-agent/`. Removes the deployed code copy and all
4 new identities/groups. After rollback, `cli.py run` from georjero's
own account works immediately, no new Google consent needed.
