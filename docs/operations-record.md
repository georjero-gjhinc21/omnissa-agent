# Operations record — Omnissa two-agent service

Source of truth for what actually runs, as what identity, on what
schedule, and what it is and isn't allowed to do. Written 2026-09-29.
If this ever disagrees with the code or the deployed units, the code
wins — update this file.

## 0. Known incidents

**2026-09-29 — deploy-time check started services for real; a real
drop-overwrite data loss occurred as a result.** `deploy-root.sh`'s
"negative" permission checks literally called `systemctl start
omnissa-ingest-scan.service`/`omnissa-analysis-scan.service` as
`georjero`. `georjero`'s `sudo` membership makes it a polkit admin
identity, so this triggered a real interactive authentication prompt;
the operator answered it (reasonably, not realizing a "check" would do
this), and both services actually ran. Fixed: those checks are removed
entirely, replaced by a non-mutating read of polkit's declared policy
(`pkaction --verbose`, confirms `auth_admin` is always required — never
invoked). See `tests/test_deploy_script_safety.py`'s
`test_no_negative_probe_ever_invokes_a_mutating_systemctl_verb` and
`test_check_helper_negative_probes_are_all_read_only_commands`.

As a direct consequence, an earlier ingest run's 50 real messages were
overwritten by a later empty drop before analysis ever consumed them —
marked "ingested" (so never re-fetched) but never classified. Fixed
going forward by the merge behavior below; **Recovery**: targeted, not
a blanket checkpoint wipe — see §5 for the `reconcile`/`requeue`
commands that identify exactly which ids were fetched-but-never-
classified and requeue only those, backing up the checkpoint first.

**2026-09-30 — a reports directory nested under a private identity home
is unreachable by its reader group, no matter its own permissions.**
`omnissa-analysis`'s reports were originally written to
`/var/lib/omnissa-analysis/state/reports/` (mode 2750, group
`omnissa-reports-readers`). Confirmed live: `georjero` still got
Permission denied reaching it, because `/var/lib/omnissa-analysis`
itself is `750 omnissa-analysis:omnissa-analysis` — every ancestor
directory needs its own traversal (+x) rights, and permissions on a
child never override a blocking parent. Fixed by moving reports to
`/var/lib/omnissa-agent/reports/`, a sibling of the drop directory under
the already-world-traversable `/var/lib/omnissa-agent/` (`755
root:root`) — the same pattern that already worked correctly for
`drop/`. `cli.py classify` gained an explicit `--reports-dir` so the
output path is never implicitly derived from (and therefore trapped
inside) `--state-dir` again.

**2026-09-30, same day — two more real bugs found via live read-only
inspection before enabling anything:**

1. **Delete-on-consume silently failed in production.** The deployed
   brief run logged `WARNING: ... could not remove
   /var/lib/omnissa-agent/drop/latest-brief.json: [Errno 30] Read-only
   file system`. The drop directory was `ReadOnlyPaths=` for the
   analysis systemd units, and its own DAC mode only gave the shared
   reader group `r-x`. Consumed drop files were never actually deleted,
   so every subsequent ingest merged into an ever-growing file and
   every classify re-processed everything (`duplicates_skipped=83` and
   climbing). Fixed with a per-user ACL
   (`setfacl -m u:omnissa-analysis:rwx`) on the drop directory — NOT a
   group-mode change, which would have also handed `georjero` write
   access since it shares that same reader group — plus moving the
   drop path from `ReadOnlyPaths=` to `ReadWritePaths=` in both analysis
   units (`ProtectSystem=strict` blocks writes to a path regardless of
   DAC/ACL permissions unless it's explicitly listed).
2. **No locking around either shared checkpoint, and a lock-skip could
   have deleted real data.** `ingest` and `classify` never called into
   the existing, already-tested `lock.py` after the ingest/classify
   split — scan and brief share one checkpoint file per identity with
   no protection against a concurrent run. Added `SingleInstanceLock`
   to `_ingest`. Found live, while testing this, that
   `pipeline.run_pilot` (called by `classify`) already had its OWN
   internal lock on the same file — a second lock on top of it
   self-blocked on `flock` (per-open-file-description, not per-process)
   and, worse, `classify`'s delete-on-consume didn't check whether
   `run_pilot` had actually run or been lock-skipped, so a skipped run
   would have deleted a real, never-processed drop file. Fixed: no
   second lock in `classify` (relies on `run_pilot`'s existing one), and
   `_pipeline_from_ingest_result` now detects a lock-skip explicitly and
   returns a distinct `LOCKED` (exit 7) that `classify` checks before
   ever unlinking its input.

**2026-09-30 — a root-run checkpoint rewrite silently changed the
checkpoint's file ownership.** `requeue --verify-unclassified-against`
must run as root (it needs to read a second identity's private
checkpoint to cross-check against). `state.save_state`'s atomic
tmp-file-then-replace rewrite didn't preserve the original file's
owner, so a root-run save left `/var/lib/omnissa-ingest/state/
checkpoint.json` root-owned — the `omnissa-ingest` systemd service then
got `PermissionError: [Errno 13] Permission denied` trying to read its
own checkpoint on the very next scheduled run. Confirmed live from the
operator's own terminal output. Fixed in `state.save_state`: capture
the existing file's owner before replacing it, and `chown` the
replacement back to that owner whenever the process is running as root
and the file already existed (first-ever writes and non-root saves are
untouched — see `tests/test_state_and_lock.py`'s four
`test_save_state_*` regression tests). The cutover script's own first
step now also self-heals any already-corrupted checkpoint from before
this fix landed, since remediating the live file and deploying the fix
are two separate steps.

**2026-09-30 — classify's own `--max-messages` truncated a merged drop
before checking what was already classified, and the file was deleted
anyway.** During the first real post-cutover scan chain, `merge_pending`
correctly combined a leftover unconsumed drop (33 messages, from
earlier in this same day's incidents) with a fresh fetch (50 new
messages) into an 83-message drop file. `classify`'s own
`--max-messages` (defaulting to 50, same as ingest's separate per-run
fetch cap — coincidentally similar numbers, unrelated caps) truncated
`run_pilot`'s view of that list to the first 50 *before* Agent A ever
compared it against `seen_ids`. The remaining 33 messages — genuinely
real, never-before-classified — were never looked at, yet
`ingest_result.status == OK` was enough for `classify` to delete the
drop file, per the existing (insufficient) `rc != LOCKED` guard. The log
line reporting `messages_seen=83` also actively hid this: it printed
the drop's raw size, not what was actually processed, which is what
made this look like a clean, complete run. Caught only because the
cutover script's own `reconcile` gate (fetched vs. classified) refused
to proceed with a nonzero pending count after the chain "succeeded."
Nothing was lost from Gmail (read-only; the 33 ids were still safely
recorded in `gmail_ingested_ids`, exactly what `reconcile`/`requeue`
exist to recover), but the drop file that would have let a normal
retry happen automatically was already gone.

Fixed in `cli.py`: (1) `_pipeline_from_ingest_result` now compares
`report.messages_seen` (what was actually processed) against
`len(ingest_result.messages)` (the drop's raw size) and treats a
mismatch as PARTIAL exactly like a deadline hit, with an honest log
line (`drop_size=`/`messages_processed=`/`message_list_truncated=`
instead of the misleading `messages_seen=<raw size>`); (2) `_classify`'s
delete-on-consume now independently re-checks
`len(ingest_result.messages) > args.max_messages` and refuses to delete
when true, regardless of `rc` — a pure deadline-hit PARTIAL with no
truncation still deletes safely, since in that case everything the
drop *did* contain was fully processed. (3) `classify`'s own
`--max-messages` default raised from 50 to 500 so a routinely-merged
backlog doesn't hit this path in normal operation — the truncation
guard is now a safety net, not something steady-state runs should ever
exercise. See `tests/test_drop_file_merge_and_consume.py`'s
`test_classify_does_not_delete_the_drop_file_when_max_messages_truncates_it`
and the two adjacent tests distinguishing the truncation case from a
safe, fully-processed deadline-hit PARTIAL.

Known residual limitation, not yet fixed (tracked, not urgent given the
raised cap): truncation always keeps the *first* N messages of the
list and, since the file is no longer deleted, a still-oversized backlog
would keep reprocessing the same already-seen head of the list on every
run rather than making progress toward the untouched tail. This would
only bite under a backlog larger than 500 merged messages, which normal
hourly operation should never reach; if it ever does, fixing it means
having `run_pilot` dedupe against `seen_ids` before truncating, not
after, rather than raising the cap further.

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
/var/lib/omnissa-agent/reports/*.md   (mode 2750/0640, group omnissa-reports-readers)
   |  read-only
   v
georjero (interactive review, via this project's terminals) -- NOT automated beyond reading
```

## 2. Units, schedule, timezone

**Exactly 6 unit files, not 8.** `omnissa-analysis` has no independent
timer — it is activated exclusively by the matching ingest service's
`OnSuccess=` the moment that run exits 0. This is a deliberate fix, not
an oversight: an independently-scheduled analysis timer (fixed minute
offset after ingest) could race, or process a drop file from an ingest
run that failed or hit its deadline (PARTIAL, exit 6) as if it were
fresh, successful data. Since a oneshot service's nonzero exit is
"failed" in systemd's own accounting by default, `OnSuccess=` correctly
never fires for a PARTIAL or failed ingest — the next scheduled ingest
run simply retries (unprocessed message ids were never marked seen).

System timezone confirmed `America/Chicago` (`timedatectl`), so the
`OnCalendar=` specs below are already local/Central time, no explicit
TZ needed.

| Unit | Identity | Fires / triggered by | Purpose |
|---|---|---|---|
| `omnissa-ingest-scan.timer` | n/a (timer) | hourly, `:00` (+random ≤30s) | trigger `omnissa-ingest-scan.service` |
| `omnissa-ingest-scan.service` | `omnissa-ingest` | by the timer above | fetch+verify, write `latest-scan.json`; on clean exit, `OnSuccess=` fires `omnissa-analysis-scan.service` |
| `omnissa-analysis-scan.service` | `omnissa-analysis` | `OnSuccess=` from `omnissa-ingest-scan.service` only (no timer) | classify + draft from `latest-scan.json`, write the hourly report |
| `omnissa-ingest-brief.timer` | n/a (timer) | daily `07:45` | trigger `omnissa-ingest-brief.service` |
| `omnissa-ingest-brief.service` | `omnissa-ingest` | by the timer above | fetch+verify, write `latest-brief.json`; on clean exit, `OnSuccess=` fires `omnissa-analysis-brief.service` |
| `omnissa-analysis-brief.service` | `omnissa-analysis` | `OnSuccess=` from `omnissa-ingest-brief.service` only (no timer) | classify + draft from `latest-brief.json` (LLM summary), write the daily brief — ready before 08:00 |

Both `.timer` units have `Persistent=true` — a missed run (machine
off/asleep) fires once at next boot instead of silently vanishing.

### Atomic publish / stale-data behavior

- `ingest` writes to a `.tmp` path and atomically `replace()`s the real
  drop file — `classify` can only ever observe the complete old file or
  the complete new one, never a partial write, independent of any
  systemd ordering.
- `classify --max-age-s` (1800s for both pipelines) refuses to process
  a drop file older than that, in case it's ever triggered out-of-band
  against stale data (manual testing, a restarted OnSuccess chain, etc.)
  — the normal OnSuccess-chained path is always seconds-fresh.
- **`ingest` MERGES with, never overwrites, an unconsumed prior drop.**
  A real incident (2026-09-29) confirmed why this matters: one ingest
  run fetched 50 real messages and wrote them; the next run found 0 new
  messages (correctly — already recorded in `gmail_ingested_ids`) and
  overwrote the drop file with an empty one before analysis ever read
  it, permanently losing those 50 from classification even though they
  were never altered on the Gmail side. `gmail_ingest.merge_pending`
  now combines an existing pending file's messages with a fresh run's
  instead of replacing it (safe to concatenate without re-dedup — ids
  across two ingest runs are disjoint by construction). `classify`
  deletes the drop file only once its data has actually been used (a
  report was written); a refused/unreadable file is left in place for
  inspection. See `tests/test_drop_file_merge_and_consume.py`, which
  replays the exact incident sequence as a regression test.

## 3. Health, status, logs

```sh
systemctl list-timers 'omnissa-*'                 # next/last run times for the 2 real timers
systemctl status omnissa-ingest-scan.service       # last run's result
systemctl status omnissa-analysis-scan.service
journalctl -u omnissa-ingest-scan.service -n 20    # georjero CAN read these (adm group) --
journalctl -u omnissa-analysis-scan.service -n 20  # safe by design, stdout never has secrets
ls -la /var/lib/omnissa-agent/drop/                # latest sanitized fetch (georjero-readable)
ls -la /var/lib/omnissa-agent/reports/    # latest briefs/drafts (georjero-readable)
cat /var/lib/omnissa-agent/reports/<latest>.md
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
| 7 | LOCKED — another instance already held this identity's checkpoint lock (scan and brief share one per identity) | not a failure; this run was skipped cleanly rather than racing it, input is left untouched, the next scheduled run tries again normally |

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

### Targeted backlog recovery (`reconcile` + `requeue`)

Built for the incident in §0: never wipe a whole checkpoint to recover
from a gap. Both commands are read-only or self-backing-up; neither
touches Gmail.

**`reconcile` must run as root, not `sudo -u <identity>`.** It reads
BOTH checkpoints at once, and `omnissa-ingest`/`omnissa-analysis` are
deliberately unable to read each other's private state (0700, no shared
group) — that mutual isolation is intentional and is not weakened for
this tool. Only root can read both files without granting either
identity a new cross-read permission. `requeue` only ever touches ONE
checkpoint (the one named by `--state-dir`), so it's fine to run it as
that checkpoint's own owning identity via `sudo -u`.

```sh
# 1. READ-ONLY: which ids were fetched but never classified? (root -- reads both checkpoints)
sudo /opt/omnissa-agent/venv/bin/python3 -m omnissa_agent.cli reconcile \
  --ingest-state-dir /var/lib/omnissa-ingest/state \
  --analysis-state-dir /var/lib/omnissa-analysis/state
# -> fetched=N classified=M pending=K, plus the K pending ids (ids only, no content)

# 2. Requeue exactly those ids (backs up the checkpoint first, automatically;
#    --verify-unclassified-against double-checks against the analysis
#    checkpoint and refuses if any requested id turns out to already be
#    classified -- also needs root, since it too reads the other identity's
#    state)
sudo /opt/omnissa-agent/venv/bin/python3 -m omnissa_agent.cli requeue \
  --state-dir /var/lib/omnissa-ingest/state \
  --ids <comma-separated pending ids from step 1, e.g. 1a0abc111,1a0abc222> \
  --backup-dir /var/lib/omnissa-ingest/recovery-backups \
  --verify-unclassified-against /var/lib/omnissa-analysis/state

# 3. Re-fetch them for real (higher limits than the default hourly run,
#    to guarantee one pass covers the whole backlog even if new mail
#    has since pushed them further back in Gmail's listing order)
sudo -u omnissa-ingest /opt/omnissa-agent/venv/bin/python3 -m omnissa_agent.cli ingest \
  --client-secret /var/lib/omnissa-ingest/google/client_secret.json \
  --token /var/lib/omnissa-ingest/google/token.json \
  --out /var/lib/omnissa-agent/drop/latest-scan.json \
  --max-messages 200 --max-pages 10 \
  --state-dir /var/lib/omnissa-ingest/state

# 4. Classify the recovered batch
sudo -u omnissa-analysis /opt/omnissa-agent/venv/bin/python3 -m omnissa_agent.cli classify \
  --kind scan --ingest-result /var/lib/omnissa-agent/drop/latest-scan.json \
  --state-dir /var/lib/omnissa-analysis/state \
  --reports-dir /var/lib/omnissa-agent/reports --report-group-readable

# 5. Confirm: pending should now be 0 (root again)
sudo /opt/omnissa-agent/venv/bin/python3 -m omnissa_agent.cli reconcile \
  --ingest-state-dir /var/lib/omnissa-ingest/state \
  --analysis-state-dir /var/lib/omnissa-analysis/state
```

A blanket checkpoint delete is a last resort only, and only if a
targeted replay is impossible (e.g. the checkpoint file itself is
corrupted beyond parsing) — in that case document exactly why targeted
recovery couldn't be used before falling back to it.

## 6. Data retention

- Reports/drafts accumulate under `/var/lib/omnissa-agent/reports/`
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
# stop the autonomous service, keep everything else in place --
# disabling the 2 real timers is sufficient: with no ingest run, there
# is nothing for OnSuccess= to ever chain into
sudo systemctl disable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer

# full teardown, credentials + checkpoints + any unconsumed drop data preserved (not deleted)
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
   → expect `ALL FILE-PERMISSION VALIDATION CHECKS PASSED (SCHEDULED-AGENT
   ISOLATION: PASS)`. A separate `OPERATOR ACCOUNT RISK` block always
   prints too — that's expected, not a failure, and describes a
   standing decision about `georjero`'s own account, not this script's
   isolation boundary (see `docs/gmail-ingestion-security-review.md`).
5. **[Operator, manual, once]** Trigger the ingest service once by hand
   — its `OnSuccess=` fires the matching analysis service automatically
   on a clean exit, so triggering `omnissa-analysis-*` directly isn't
   the normal path (only useful for isolated debugging):
   ```sh
   sudo systemctl start omnissa-ingest-scan.service
   sudo systemctl status omnissa-ingest-scan.service omnissa-analysis-scan.service
   sudo journalctl -u omnissa-ingest-scan.service -u omnissa-analysis-scan.service -n 40
   cat /var/lib/omnissa-agent/reports/<latest>.md
   ```
   **Gate: ingest exits 0 (or 6/PARTIAL with a sane reason — in which
   case analysis correctly does NOT run this cycle, by design), analysis
   then runs and exits 0, the report reads correctly, no error in the
   journal.**
6. Only after step 5 passes: `sudo systemctl enable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer`.
7. `systemctl list-timers 'omnissa-*'` → confirm both show a future
   `NEXT` time and `enabled`. `omnissa-analysis-*.service` will not
   appear in `list-timers` (correct — they have no timer of their own).

**Next command for the operator to run, only after reviewing this
document and the diff**: step 4 above,
`sudo bash infra/omnissa-ingest/deploy-root.sh` (already run once for
the ingest-only shape before this revision — safe/idempotent to re-run
now that it also sets up `omnissa-analysis`).
