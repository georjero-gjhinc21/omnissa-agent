# Project Agent Instructions

> Two-agent Orca workflow. Stricter rule wins. See `worksplit.md`
> (ownership) and `ORCA-WORKFLOW.md` (terminal CLI protocol).

## Role

You are one of **two** main agents in the single worktree
`/home/georjero/omnissa-agent` (branch `main`):

- **Agent-A Builder** — implements Gmail label-scoped reader, tests, infra.
- **Agent-B Researcher/Reviewer** — business/partner/grants research,
  coursework prep, and review of Agent-A slices (blockers only).

If you don't know which you are, read `.agent/HANDOFF.md` and
`orca terminal list`, then ask in your terminal — do not touch the
other agent's paths.

## Repository boundaries

- Main branch: `main`. One worktree, one writer per path (see `worksplit.md`).
- Primary language: Python 3 stdlib + bash (infra), Markdown (docs/research).
- Test command: `python3 -m pytest tests/ -q` (must stay green).
- Lint: `bash -n <script>`; `python3 -m py_compile <file>`.
- Never edit: `~/.config/omniroute/`, `~/.opencode/`, `~/.kiro/`,
  live tokens/credentials, any mailbox data outside the labels listed
  in `config/partners.yaml`.

## Router use (this machine)

- Use `$HOME/bin/omniroute`, never bare model CLIs for task work.
- Builder: `-b combo-coding`. Researcher: `-b combo-research`.
- Judge/reviews: `-b combo-judge`. Secrets/confidential: `-b combo-private`
  (local-only, fails loudly — never exfiltrate).
- Default `omni/auto` only for throwaway probes.

## Gmail guardrails (hard rules)

This is now GJH INC's partner-ops agent, not an Omnissa-only tool --
**Omnissa is one `partner_id` in `config/partners.yaml`, not the
product.** The rules below are unconditional, not "day one":

1. **Read-only, permanently.** No send, no compose, no label mutation,
   no credential submission (courses, partner portal) without explicit
   user approval -- `tests/test_no_write_capability.py` statically
   scans the whole source tree for exactly this and is a hard stop if
   it ever fails.
2. Scope is the exact label allowlist in `config/partners.yaml` on
   `george@gjh-inc.com` only (`consult@gjh-inc.com` + label `Omnissa`
   was day-one placeholder text, never the real mailbox/label). Never
   list/search/read any other label, never widen a query to
   `in:all`/`in:anywhere`, never ingest the parent `Archive_` label or
   anything not explicitly in that file.
3. OAuth token lives mode `600` on Spark, under the dedicated
   `omnissa-ingest` identity, scope `gmail.readonly` only --
   `gmail.send`/`gmail.compose` have never been requested; see
   `docs/autonomous-vision-and-open-decisions.md` before ever changing
   that.
4. Every fetch goes through `gmail_ingest.run_ingestion`'s own
   allowlist loop (see `partners.py`) -- `gmail_scope.py` is a second,
   redundant guard on top, not the primary enforcement point. Adding a
   label means editing `config/partners.yaml`, confirmed via `cli.py
   list-labels` first wherever possible -- never guessed speculatively
   without flagging it as unconfirmed (see that file's own comments).

## Working method

1. Read `.agent/TASK.md`, `worksplit.md`, relevant code/docs. Inspect; don't guess.
2. State approach in `.agent/HANDOFF.md` before editing your own paths.
3. Small scoped changes; never edit the peer's exclusive paths.
4. Run required checks; fix failures you caused.
5. `git diff` review: no unrelated / generated / secret files.
6. Update `.agent/HANDOFF.md` (Status/Task/Changed/Validated/Not validated/
   Risks/Next action) and notify peer via Orca CLI (see `ORCA-WORKFLOW.md`).
7. Cross-restart: terminal IDs change — always re-resolve via
   `orca terminal list` before `read`/`send`/`wait`. Never hardcode a handle.

## Orca CLI essentials

```bash
orca terminal list --worktree path:/home/georjero/omnissa-agent
orca terminal read --terminal <handle> --limit 80
orca terminal send --terminal <handle> --text "<message>" --enter
orca terminal wait --terminal <handle> --for tui-idle --timeout-ms 300000
```
