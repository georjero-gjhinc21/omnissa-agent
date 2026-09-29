# ORCA-WORKFLOW.md — two-agent protocol (after Tonbi's Orca video)

One project, one worktree, two main agents, Orca terminal CLI as the
only cross-agent channel. No DMs, no hidden backchannel.

## Setup used here

- Worktree: `/home/georjero/omnissa-agent` (Orca `main`, branch `main`).
- Agent-A Builder: this terminal (OpenCode / Muse Spark), title `Agent-A Builder`.
- Agent-B Researcher: peer terminal in the same worktree, title `Agent-B Research`.
- Router: `$HOME/bin/omniroute` (`combo-coding` builder, `combo-research`
  researcher, `combo-judge` reviews, `combo-private` secrets).

## Commands (all scoped to this worktree)

```bash
# handles change after every restart — always re-resolve, never hardcode
orca terminal list --worktree path:/home/georjero/omnissa-agent
orca terminal list --worktree path:/home/georjero/omnissa-agent --json | python3 -c \
  "import json,sys; [print(t['handle'], '|', t.get('title')) for t in json.load(sys.stdin)['result']['terminals']]"

# inspect peer without disturbing it
orca terminal read --terminal <peer-handle> --limit 80

# assign / reply (short brief + exact file paths, ask for blockers only)
orca terminal send --terminal <peer-handle> --text "Please review src/omnissa_agent/gmail_scope.py — reply blockers only here." --enter

# wait for peer to go idle (review done)
orca terminal wait --terminal <peer-handle> --for tui-idle --timeout-ms 300000
```

## Live loop (what the video shows)

1. Implementer finishes a slice in their owned paths, runs tests.
2. Implementer `send`s the peer a review request with file paths.
3. Reviewer `read`s the files, writes e.g. `docs/slice-01_review.md`
   (blockers only), replies in their own terminal.
4. Implementer `read`s the reply / review file, fixes blockers, re-runs tests.
5. Both update `.agent/HANDOFF.md`. Repeat. User only answers questions.

## Verified live handles (2026-09-29 — re-resolve via `list` after restart)

- Agent-A Builder (this terminal): `term_41fd6c3a-3369-4e49-86f4-3bcad6ec13c8`
- Agent-B Research (peer): `term_3a0f86f6-5c34-46a3-96e1-fba9d6f71534`
- Round-trip proven: `send echo AGENT-B-READY` → `read` returned
  `AGENT-B-READY 2026-09-29T21:15:00Z`. Correct read flag is `--limit`,
  wait is `--for tui-idle --timeout-ms` (see Commands above).

## Recovery (terminal ID changed / day two)

The video's exact case: first `send` fails or hits the wrong handle.
Fix: `orca terminal list` again, pick the live handle by title + worktree
path, re-`send`. Never persist a handle in a file.

## When NOT to add agents

Max two mains. Need parallelism? Each main spawns subagents under itself
(or uses `orchestration run-*` for DAG tracking) instead of opening a
third main terminal. Additional worktrees only for truly parallel,
multi-member work — not for this repo.

## 24x7 note

`orca automations list` is currently empty. Scheduled polling
(`automations create`, systemd timer) stays **off** until Gmail OAuth
read-only + `Omnissa`-label scope are explicitly approved by the user.
