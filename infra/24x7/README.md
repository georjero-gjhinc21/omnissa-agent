# 24x7 — LIVE via systemd, Orca is read-only

**What's actually running (since 2026-09-29 22:19 CDT):** two systemd
timers on this host, `omnissa-ingest-scan.timer` (hourly) and
`omnissa-ingest-brief.timer` (daily 07:45 CDT), driving two
privilege-separated identities (`omnissa-ingest`, `omnissa-analysis`) —
never `georjero`, never Orca, never a coding agent process. See
`../omnissa-ingest/deploy-root.sh` and `docs/operations-record.md` for
the full architecture and every incident found along the way.

**Orca's role here is deliberately read-only.** Orca terminals run as
`georjero`, the same OS account that holds `docker`/`sudo` — root-
equivalent — so it is architecturally excluded from ever being the
execution identity for Gmail ingestion or classification. No Orca
automation exists for this project (`orca automations list` returns
empty) and none should be created: it would either duplicate the
systemd timers above or, worse, run as an identity that was
specifically designed out of this path.

What Orca *can* do, using nothing beyond what `georjero`'s own account
already has (no Gmail credential, no `omnissa-ingest`/`omnissa-analysis`
private state, no privileged-service control):

- Open/read a report file (`orca file open <path>`, or any editor/`cat`
  in an Orca terminal) — `georjero` is already a member of the
  purpose-built `omnissa-reports-readers` group (granted by
  `deploy-root.sh` itself, not something added for Orca), which is the
  intended human-review surface for `/var/lib/omnissa-agent/reports/`.
- Run `./omnissa-status.sh` (same directory) in any terminal for a
  compact, read-only view: timer schedule, each unit's last exit
  status, and the most recent reports. No sudo.
- Run plain, unprivileged `systemctl status`/`list-timers` on the
  `omnissa-*` units directly.

What Orca (or `georjero`, or anyone without sudo) correctly **cannot**
see, by design:

- The Gmail OAuth token or client secret.
- Either identity's private checkpoint state, and therefore the
  fetched-vs-classified backlog count (`reconcile` needs root — see
  `docs/operations-record.md` §5).
- Anything that would let it start, stop, or reconfigure the privileged
  units (`org.freedesktop.systemd1.manage-units` always requires
  interactive `auth_admin`, unconditionally, for every identity).

**One gotcha, not a bug:** group grants made by `deploy-root.sh`
(`usermod -aG ...`) only apply to *new* sessions. A terminal (Orca or
otherwise) left open from before a deploy needs to be closed and
reopened before it can read the reports directory.

**On the system journal:** `georjero` happens to already be in the
`adm` group, so `journalctl -u omnissa-*` technically works without
sudo — but a running scan/brief currently logs real subject lines
there, which is a broader audience than the reports directory's
dedicated reader group. Until that's fixed (tracked in
`docs/operations-record.md` §0), treat the journal as a diagnostic tool
for `sudo`-holding operators, not a routine viewing path — use the
reports directory / `omnissa-status.sh` instead.
