# omnissa-agent

Scoped Gmail + revenue-research agent for Omnissa partnership work
as `consult@gjh-inc.com`. One Orca worktree, **two main agents max**
(per Tonbi/Orca workflow in `ORCA-WORKFLOW.md`).

## Scope (day one)

- **Read-only**, Gmail **label `Omnissa` only**. No send, no other labels.
- Understand GJH Inc business, research Omnissa partner / grants program
  (already a partner), surface revenue angles.
- Coursework prep as `consult@gjh-inc.com` (research + checklists only,
  no credential submission without explicit approval).

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

Day one = on-demand only. Continuous polling (Orca automation / systemd)
is designed in `infra/24x7/` but **not enabled** until Gmail OAuth
read-only + label scope are approved. See `.agent/TASK.md`.
