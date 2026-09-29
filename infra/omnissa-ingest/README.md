# Operator root handoff — Gmail credential isolation

This directory is the complete, reviewed handoff for isolating the
Gmail OAuth credential from `georjero` (the identity Claude/Orca run
as). **Run these yourself, with your own sudo — no agent session can
or should run them.**

## Run

```sh
sudo bash /home/georjero/omnissa-agent/infra/omnissa-ingest/deploy-root.sh
```

It's idempotent — safe to re-run if interrupted or after a code update
(re-running re-syncs `/opt/omnissa-agent` from this repo's current
`src/`). It ends with a PASS/FAIL validation report and does **not**
enable the timers itself — read the output, then run the two
`systemctl enable --now` lines it prints if everything passed.

## What it changes

- New system user `omnissa-ingest` (no login, no password) owns the
  Gmail OAuth credential, moved from
  `~/.config/omnissa-agent-google/` to `/var/lib/omnissa-ingest/google/`
  (mode 700/600, not in any group `georjero` belongs to).
- New group `omnissa-readers` — `georjero` is added to it, giving
  **read-only** access to `/var/lib/omnissa-agent/drop/` (sanitized
  scan/brief results only — ids, subjects, snippets, senders, dates;
  never a token, never a raw Gmail API response).
- A private, root-owned copy of this repo's `src/` deployed to
  `/opt/omnissa-agent` — `georjero` cannot write to it, so cannot tamper
  with the code the privileged service executes.
- Two systemd services (`omnissa-ingest-scan`, `omnissa-ingest-brief`),
  running only as `omnissa-ingest`, each calling
  `python3 -m omnissa_agent.cli ingest ...` — the ONLY command in this
  codebase that touches an OAuth token or the Gmail API. Two timers
  (hourly; daily 07:50 America/Chicago) trigger them — `georjero` has no
  systemctl rights over any of it.

## Why this boundary, not just `chmod 600`

`chmod 600` restricts access by *Linux user*, not by *process*.
Claude/Orca and any Gmail-ingesting code both currently run as
`georjero` — same uid, so file permissions grant them identical access
regardless of the mode bits. A genuinely different OS identity is the
only thing the kernel actually enforces a boundary on.

## What it deliberately does NOT close

`georjero` is in the `sudo` and `docker` groups. Docker group membership
is root-equivalent on its own (`docker run -v /:/host ... chroot /host`)
— independent of the sudo password. This means `georjero` (and any
agent running as it) can already reach real root through Docker,
bypassing every permission this script sets up. This script defends
against **accidental/casual** cross-user access — a buggy or over-eager
agent trying to open the token file gets a genuine permission error, not
a warning it could ignore. It does **not** defend against a **deliberate**
privilege-escalation attempt by that same identity. Closing that fully
means removing `georjero` from `docker` and/or `sudo` — a machine-wide
decision affecting things outside this project, not made here.

## Rollback

```sh
sudo bash /home/georjero/omnissa-agent/infra/omnissa-ingest/rollback-root.sh
```

Stops/removes the timers and units, moves the OAuth credential files
back to `~/.config/omnissa-agent-google/` (not deleted — the refresh
token stays valid), copies the ingest-side checkpoint next to them so
dedup history isn't lost, removes the deployed code copy and the
`omnissa-ingest`/`omnissa-readers` identities. After rollback,
`cli.py run` from georjero's own account works immediately, no new
Google consent needed.
