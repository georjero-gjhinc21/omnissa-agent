# Operations record — Omnissa two-agent service

Source of truth for what actually runs, as what identity, on what
schedule, and what it is and isn't allowed to do. Written 2026-09-29.
If this ever disagrees with the code or the deployed units, the code
wins — update this file.

## 1. Architecture — who runs what

| Stage | Process / identity | Code | Network it can reach |
|---|---|---|---|
| Ingestion (Agent A's data source) | `omnissa-ingest` system user, via `omnissa-ingest-{scan,brief}.service` (systemd timers) | `python3 -m omnissa_agent.cli ingest` | Real internet: `accounts.google.com`, `gmail.googleapis.com` only (OAuth refresh + Gmail REST) |
| Classification (Agent A) + drafting (Agent B) | `omnissa-analysis` system user, via `omnissa-analysis-{scan,brief}.service` (systemd timers) | `python3 -m omnissa_agent.cli classify` → `agent_a.py` / `agent_b.py` | Loopback only (`IPAddressAllow=localhost`) — reaches the OmniRoute gateway at `127.0.0.1:11435`, nothing else, unless `--llm-policy combo-continuous` is explicitly passed (not the deployed default) |
| OmniRoute gateway (shared infra, not part of this project) | `georjero` (existing `omniroute-gateway.service`, systemd `--user`) | `$HOME/bin/omniroute` / `infra/omniroute` | Whatever the active combo's backends need — `combo-private` (the analysis default) only ever reaches `ollama-local` (also loopback) |
| Interactive work (this conversation, ad-hoc `cli.py run`, manual `authorize`/`draft`/`list-labels`) | `georjero`, via Orca/Claude Code terminals | any `cli.py` subcommand | whatever the interactive session does — not autonomous, a human is driving |

**Orca is not part of the autonomous execution path.** Orca automations
run as `georjero`, and `georjero` carries `docker`/`sudo` group
membership — root-equivalent — which is inappropriate for anything
claiming to be privilege-isolated. The two restricted identities above
are driven entirely by systemd timers, with zero Orca/Claude involvement
once deployed. Georjero (and therefore Orca/Claude, interactively) can
only ever *read* the two identities' output directories — never trigger,
configure, or inspect their internals beyond that.

### Data flow

```
Gmail (george@gjh-inc.com, label Archive_/@omnissa.com)
   |  OAuth (omnissa-ingest only)
   v
omnissa-ingest: verify account+label, fetch metadata, dedup
   |  writes sanitized JSON (ids/subject/snippet/sender/date/labels only)
   v
/var/lib/omnissa-agent/drop/latest-{scan,brief}.json   (mode 2750, group omnissa-readers)
   |  read-only
   v
omnissa-analysis: classify (Agent A) -> draft candidates (Agent B, local text only)
   |  writes brief/report + drafts (DRAFT ONLY -- NOT SENT)
   v
/var/lib/omnissa-analysis/state/reports/*.md   (mode 2750/0640, group omnissa-reports-readers)
   |  read-only
   v
georjero (interactive review, via this project's terminals) -- NOT automated beyond reading
```

## 2. Units, schedule, timezone

System timezone confirmed `America/Chicago` (`timedatectl`), so all
`OnCalendar=` specs below are already local/Central time, no explicit
TZ needed.

| Unit | Fires | Purpose |
|---|---|---|
| `omnissa-ingest-scan.timer` | hourly, `:00` (+random ≤30s) | trigger `omnissa-ingest-scan.service` |
| `omnissa-analysis-scan.timer` | hourly, `:07` (+random ≤30s) | trigger `omnissa-analysis-scan.service` (reads the `:00` drop) |
| `omnissa-ingest-brief.timer` | daily `07:45` | trigger `omnissa-ingest-brief.service` |
| `omnissa-analysis-brief.timer` | daily `07:55` | trigger `omnissa-analysis-brief.service` (finished brief ready before 08:00) |

All four `.timer` units have `Persistent=true` — a missed run (machine
off/asleep) fires once at next boot instead of silently vanishing.

## 3. Health, status, logs

```sh
systemctl list-timers 'omnissa-*'                 # next/last run times for all 4
systemctl status omnissa-ingest-scan.service       # last run's result
systemctl status omnissa-analysis-scan.service
journalctl -u omnissa-ingest-scan.service -n 20    # georjero CAN read these (adm group) --
journalctl -u omnissa-analysis-scan.service -n 20  # safe by design, stdout never has secrets
ls -la /var/lib/omnissa-agent/drop/                # latest sanitized fetch (georjero-readable)
ls -la /var/lib/omnissa-analysis/state/reports/    # latest briefs/drafts (georjero-readable)
cat /var/lib/omnissa-analysis/state/reports/<latest>.md
```

## 4. Exit codes (both `ingest` and `classify`)

| Code | Meaning | Action |
|---|---|---|
| 0 | Complete success | none |
| 2 | REFUSED — account/label/auth check failed | check credentials/label; not a transient issue |
| 3 | LLM summary deferred — all OmniRoute backends failed | classification still completed; investigate OmniRoute/Ollama health |
| 4 | Unexpected error — bad input/config | inspect stderr; nothing partially written |
| 5 | RATE_LIMITED — Gmail is throttling this account | transient; next scheduled run retries |
| 6 | PARTIAL — ingestion deadline reached before all messages were fetched | data written is genuine but incomplete; unprocessed ids are NOT marked seen, next run retries them automatically. The brief text itself also says `VERIFIED (PARTIAL -- ingestion deadline reached)` — this is visible in the report, not just the exit code. |

**No alerting is configured.** `systemctl status`/`journalctl`/exit
codes are pull-only — nothing pages or emails on failure (email would
itself require the mail-sending capability this project deliberately
never has). A `FailureAction=`/`OnFailure=` unit forwarding to some
notification channel is a reasonable future addition; not built here
because it isn't determined what channel would be appropriate.

## 5. Checkpoint / dedup recovery

- `omnissa-ingest` tracks fetched-message ids under its own
  `/var/lib/omnissa-ingest/state/checkpoint.json` (`gmail_ingested_ids`
  key) — independent of analysis's checkpoint.
- `omnissa-analysis` tracks classified-message ids under
  `/var/lib/omnissa-analysis/state/checkpoint.json` (`seen_ids` key).
- Both are atomic-write (`.tmp` + `os.replace`), mode 600, survive a
  crash mid-run without corruption (worst case: the last run's result
  is simply redone, ids aren't double-counted since dedup is id-based
  not count-based).
- A PARTIAL (deadline-hit) run never marks unfetched ids as seen in
  either checkpoint — verified by
  `tests/test_gmail_ingest.py::test_partial_run_does_not_falsely_mark_unfetched_messages_seen`.
- An older message newly labeled is still picked up on the next run
  (label listing is never date-filtered) — verified by
  `test_older_message_newly_labeled_is_still_picked_up`.

## 6. Data retention

- Reports/drafts accumulate under `/var/lib/omnissa-analysis/state/reports/`
  with no automatic pruning. This is metadata (subjects/snippets), not
  full email bodies, but still grows unbounded over time. **Not yet
  addressed** — a `tmpfiles.d` rule or a retention flag in `cli.py`
  would be the way to bound this; flagged here rather than silently
  left as a surprise.
- Checkpoints cap at 5000 ids per namespace (`state.MAX_SEEN_IDS`),
  oldest dropped first.

## 7. Failure handling

- Every failure mode below is a distinct, documented exit code (§4) —
  none of them produce a report that looks like a normal success.
- All-backend OmniRoute outage → exit 3, brief still written with
  classifications but no LLM summary section, clearly labeled
  "deferred" in the text.
- Gmail auth/account/label failure → exit 2, **nothing is read or
  written** for that run (fail closed, not fail open).
- Ingestion deadline reached → exit 6, PARTIAL, visible in both the
  exit code and the brief text (§4).

## 8. Autonomous action boundary (unconditional)

Every autonomous run (both systemd-timer identities) is restricted to:
- **Read-only Gmail ingestion** — `get_profile`, `list_labels`,
  `list_message_ids`, `get_message_metadata` (metadata format only, no
  body/attachments). No other Gmail API method exists anywhere in this
  codebase — enforced by a standing static test
  (`tests/test_no_write_capability.py`) that scans all of `src/` for
  write-capable symbol names and fails the suite if one appears.
- **Local analysis** — rule-based classification, confidence capped at
  "Unverified" from message content alone (never auto-elevated).
- **LOCAL DRAFT TEXT ONLY** — `agent_b.py` produces `DraftEmail`
  objects written to a local file, tagged `DRAFT ONLY -- NOT SENT`.
  There is no Gmail draft-create method, no SMTP/send capability, no
  portal-submission code, and no course-enrollment code anywhere in
  this project. Confidence never implies eligibility or an awarded
  benefit — `agent_b`'s draft body always phrases the ask as a
  question ("Could you confirm...") never a claim.
- Nothing here ever asserts eligibility for a grant, benefit, or award
  on GJH INC's behalf. That determination is explicitly left to a human
  checking an official source (see `cli.py draft`'s `--note` field,
  which records what the human actually checked).

## 9. Disable / rollback

```sh
# stop the autonomous service, keep everything else in place
sudo systemctl disable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer \
  omnissa-analysis-scan.timer omnissa-analysis-brief.timer

# full teardown, credentials preserved (not deleted)
sudo bash infra/omnissa-ingest/rollback-root.sh
```

## 10. Deployment checklist (explicit PASS/FAIL gates)

Run in order. Stop at the first FAIL and report it rather than
proceeding.

1. `git status` clean, `git log -1` shows the commit you intend to
   deploy. **Gate: clean tree** (the deploy script also enforces this
   and refuses otherwise).
2. `python3 -m pytest tests/ -q` → expect all passing.
3. `bash infra/omniroute/tests/test-omniroute.sh` (from
   `system-prompts/infra/omniroute/`) → expect all passing.
4. **[Operator, privileged]** `sudo bash infra/omnissa-ingest/deploy-root.sh`
   → expect `ALL FILE-PERMISSION VALIDATION CHECKS PASSED` at the end.
   The docker-group INFO line will still print — that's expected, not a
   failure, and is a separate, standing decision (see
   `docs/gmail-ingestion-security-review.md`).
5. **[Operator, manual, once]** Trigger each service once by hand and
   inspect the result before trusting the timer:
   ```sh
   sudo systemctl start omnissa-ingest-scan.service && sudo systemctl status omnissa-ingest-scan.service
   sudo journalctl -u omnissa-ingest-scan.service -n 20
   sudo systemctl start omnissa-analysis-scan.service && sudo systemctl status omnissa-analysis-scan.service
   sudo journalctl -u omnissa-analysis-scan.service -n 20
   cat /var/lib/omnissa-analysis/state/reports/<latest>.md
   ```
   **Gate: both exit 0 (or 6/PARTIAL with a sane reason), the report
   reads correctly, no error in the journal.**
6. Only after step 5 passes: `sudo systemctl enable --now omnissa-ingest-scan.timer omnissa-analysis-scan.timer omnissa-ingest-brief.timer omnissa-analysis-brief.timer`.
7. `systemctl list-timers 'omnissa-*'` → confirm all four show a future
   `NEXT` time and `enabled`.

**Next command for the operator to run, only after reviewing this
document and the diff**: step 4 above,
`sudo bash infra/omnissa-ingest/deploy-root.sh` (already run once for
the ingest-only shape before this revision — safe/idempotent to re-run
now that it also sets up `omnissa-analysis`).
