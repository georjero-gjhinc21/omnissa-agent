# omnissa-agent

Scoped Gmail + revenue-research agent for Omnissa partnership work,
reading `george@gjh-inc.com`'s `Archive_/@omnissa.com` label. One Orca
worktree, **two main agents max** (per Tonbi/Orca workflow in
`ORCA-WORKFLOW.md`) for development; the live 24x7 pipeline itself runs
as two dedicated, restricted systemd identities, never as Orca or
either development agent — see the 24x7 section below.

## Scope (production, as of 2026-09-30)

- **Read-only**, Gmail account `george@gjh-inc.com`, exact label
  `Archive_/@omnissa.com` only. No send, no other labels, no label
  mutation anywhere in the codebase (`tests/test_no_write_capability.py`
  enforces this as a standing check). `consult@gjh-inc.com` + label
  `Omnissa` was day-one placeholder text and was never the real
  mailbox/label — see `docs/operations-record.md` for the actual
  architecture and `gmail_scope.py` for the (now-corrected) scope guard.
- Understand GJH Inc business, research Omnissa partner / grants program
  (already a partner), surface revenue angles.
- Coursework prep (research + checklists only, no credential submission
  without explicit approval).

## Two-agent split

| Agent | Harness / route | Owns |
|---|---|---|
| **Agent-A Builder** (this terminal) | OpenCode + Muse Spark, `~/bin/omniroute -b combo-coding` | `src/`, `tests/`, `infra/`, `.agent/` |
| **Agent-B Researcher/Reviewer** (peer terminal) | any harness, `~/bin/omniroute -b combo-research` | `docs/`, `research/`, `business/`, reviews |

Shared (read both, write by agreement): `AGENTS.md`, `worksplit.md`,
`README.md`, `.agent/HANDOFF.md`. See `worksplit.md` for ownership +
acceptance criteria, `ORCA-WORKFLOW.md` for the terminal CLI protocol.

## Quick start

```bash
# 1. Find peer terminal (IDs change across restarts — never hardcode)
orca terminal list --worktree path:/home/georjero/omnissa-agent

# 2. Ask router (never bare model calls for task work)
~/bin/omniroute ask -b combo-coding "summarise this repo in 3 bullets"
~/bin/omniroute ask -b combo-research "latest Omnissa partner program news 2026"

# 3. Run scope-guard test (must stay green)
python3 -m pytest tests/ -q
```

## 24x7

**Live.** Two systemd timers run this unattended: `omnissa-ingest-scan.timer`
(hourly) and `omnissa-ingest-brief.timer` (daily 07:45 Central), each
driving a privilege-separated ingest+classify pair — never Orca, never
a coding-agent process, never `george`'s/`consult@`'s own account.
See `docs/operations-record.md` for the full architecture,
`infra/24x7/README.md` for what's viewable read-only and how, and
`infra/24x7/omnissa-status.sh` for a quick status check. Disable with:
`sudo systemctl disable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer`.
