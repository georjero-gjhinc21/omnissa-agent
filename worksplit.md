# worksplit.md — agreed by Agent-A + Agent-B, 2026-09-29

Single worktree `/home/georjero/omnissa-agent` (branch `main`).
Two main agents max; subagents allowed underneath each. No two agents
edit the same exclusive path.

## Exclusive ownership

| Path | Owner | Notes |
|---|---|---|
| `src/` | Agent-A Builder | Gmail scoped reader, parsing |
| `tests/` | Agent-A Builder | scope-guard + unit tests |
| `infra/` | Agent-A Builder | 24x7 wiring (disabled until approved) |
| `.agent/` | Agent-A Builder | TASK/HANDOFF bookkeeping |
| `docs/` | Agent-B Researcher | partner/grants research notes |
| `research/` | Agent-B Researcher | raw findings, links, quotes |
| `business/` | Agent-B Researcher | GJH Inc offerings, revenue angles |
| `courses/` | Agent-B Researcher | coursework prep (no credential submit) |

Shared (write only by explicit agreement in terminal): `AGENTS.md`,
`worksplit.md` itself, `README.md`, `.agent/HANDOFF.md`.

## Milestone 1 (current)

1. **A1 — label-scoped reader skeleton** (Agent-A): `gmail_scope.py`
   enforces `label_ids=["Omnissa"]`, read-only; `test_scope_guard.py` green.
2. **B1 — partner/grants baseline** (Agent-B): `docs/omnissa-partner-baseline.md`
   with program tiers, grants entry points, open questions.
3. **B2 — review A1**: Agent-B reviews each Agent-A slice, replies with
   **blockers only** (no style nits), Agent-A fixes.

## Acceptance criteria (every slice)

- [ ] No file outside owner's paths touched (except agreed shared files).
- [ ] `python3 -m pytest tests/ -q` green.
- [ ] No Gmail access outside label `Omnissa`; no send; no secrets committed
      (`git status` clean of tokens/credentials).
- [ ] Peer notified via `orca terminal send`, reply read via
      `orca terminal read`, terminal handles re-resolved via `list`
      (never hardcoded).

## Communication protocol (Orca CLI, no backchannel)

1. Sender: `orca terminal list --worktree path:/home/georjero/omnissa-agent`
   to resolve the peer handle (IDs change across restarts).
2. Send review/task: `orca terminal send --terminal <peer> --text "<short brief + file paths>" --enter`.
3. Wait: `orca terminal wait --terminal <peer> --idle-timeout 300`
   (or poll `orca terminal read --terminal <peer> --lines 60`).
4. Reviewer replies **in their own terminal** with blockers-only note and/or
   a `*_review.md` file in their owned path; implementer reads it and fixes.
