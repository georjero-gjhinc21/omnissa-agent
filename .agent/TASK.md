# TASK (single worktree, two agents)

## Milestone 1 — scoped skeleton + partner baseline (no live Gmail yet)

- A1 (Agent-A): `src/omnissa_agent/gmail_scope.py` + `tests/test_scope_guard.py`
  green. No real Gmail calls until user approves OAuth readonly.
- B1 (Agent-B): `docs/omnissa-partner-baseline.md` — program tiers, grants
  entry points, open questions, all with links/dates.
- B2 (Agent-B): review A1, blockers-only note in `docs/`.

## Blocked on user approval (do NOT proceed without it)

1. Google OAuth for `consult@gjh-inc.com`, scope `gmail.readonly`, token
   mode 600 on Spark. Confirm exact label name `Omnissa` exists.
2. `gmail.send` — separate approval, later milestone only.
3. Any 24x7 polling (Orca automation / systemd) — off until 1 is granted.
4. Course / partner-portal credential submission — never without explicit go.

## Done when

`python3 -m pytest tests/ -q` green, no cross-path edits, peer review
loop exercised once over Orca CLI (send → read → fix).
