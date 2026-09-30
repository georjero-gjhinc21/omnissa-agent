# Future cutover: omnissa-* identities/paths -> partner-*

**Status: planning only. No script in this repo creates the users,
paths, or units described below.** This document exists so the future
cutover is deliberate and sequenced, not because any part of it is
approved to run yet.

## Why this is separate from the `partner_agent` package alias

The `partner_agent` Python package (added alongside this doc) is a
zero-risk source-level rename: a re-export alias, same implementation,
same deployed units, same `User=`s, same paths. It changes nothing
about what's running. This document is the OTHER, much riskier half of
a full rebrand — actually moving the live systemd identities and
on-disk paths — and it is explicitly **not** part of that change.

## What would need to exist (not created by this doc)

| Today (live) | Future (not yet created) |
|---|---|
| `User=omnissa-ingest` | `User=partner-ingest` |
| `User=omnissa-analysis` | `User=partner-analysis` |
| `/opt/omnissa-agent` | `/opt/partner-agent` |
| `/var/lib/omnissa-agent/{drop,reports}` | `/var/lib/partner-agent/{drop,reports}` |
| `/var/lib/omnissa-ingest/`, `/var/lib/omnissa-analysis/` | `/var/lib/partner-ingest/`, `/var/lib/partner-analysis/` |
| `omnissa-ingest-scan.timer`/`.service` | `partner-ingest-scan.timer`/`.service` |
| `omnissa-ingest-brief.timer`/`.service` | `partner-ingest-brief.timer`/`.service` |
| `omnissa-analysis-scan.service` | `partner-analysis-scan.service` |
| `omnissa-analysis-brief.service` | `partner-analysis-brief.service` |

## The OAuth token — copy, never share

`partner-ingest` needs its own private credential storage
(`/var/lib/partner-ingest/google/`, mode 700 dir / 600 files, owned by
`partner-ingest`), seeded by **copying** the existing
`client_secret.json`/`token.json` from `omnissa-ingest`'s storage —
never a symlink, never a shared path, never moved out from under the
still-running `omnissa-ingest` identity while it might still be active.
Two independent copies of the same underlying OAuth grant is fine
(same Google-side credential, two local files); two identities
*sharing one file* is not — a refresh-token write race between two
processes holding the same file open is exactly the kind of bug this
whole project has spent real incidents fixing (see
`docs/operations-record.md` §0).

## Sequencing (the part that actually matters)

**Never run both ingest loops at once.** The core risk isn't
corruption (both would be read-only against Gmail) — it's confusing,
duplicated processing against two independent checkpoint stores, and
needless simultaneous Gmail API traffic under the same underlying
credential. The sequence below exists specifically to avoid any window
where that's possible for longer than a single manual verification:

1. Deploy the new `partner-*` identities, paths, and units —
   timers **disabled**. `omnissa-*` timers remain the only ones
   actually scheduled and running.
2. Manually trigger exactly one `partner-ingest-scan.service` run
   (`systemctl start --wait`, same pattern as every guarded cutover in
   `docs/operations-record.md`). Verify it produces a clean report,
   real partner sections, no errors — while `omnissa-*` is still the
   live, scheduled path.
3. Only after that one verified-green manual run: enable the
   `partner-ingest-*.timer` units.
4. Wait for **one real scheduled (not manual) green hour** on the
   `partner-*` timers — confirmed via the same read-only status checks
   as today (`infra/24x7/omnissa-status.sh`'s `partner-*` equivalent).
5. Only after that: disable `omnissa-ingest-scan.timer` and
   `omnissa-ingest-brief.timer`. Do not delete `omnissa-*`'s units,
   identities, or `/opt`/`/var/lib` paths at this step — decommissioning
   those is a separate, later, even-lower-urgency cleanup once
   `partner-*` has a real track record, not part of this cutover.

If step 2 or 4 shows anything wrong: stop, leave `omnissa-*` exactly as
it is (never touched, never disabled until step 5), fix the issue, and
re-verify from step 2 again. `omnissa-*` is the fallback for the entire
sequence, not just step 1.

## What this cutover does NOT change

Gmail scope, the account (`george@gjh-inc.com`), the label allowlist
(`config/partners.yaml`), and the OAuth consent itself (the token is
copied, not re-issued — no new consent flow, no scope widening). This
is a local identity/path rename, not a re-authorization or a
capability change.

## Explicitly out of scope, even later

Nothing in this document implies `gmail.send`/`gmail.compose`,
autonomous action, or any capability beyond what's already running —
see `docs/autonomous-vision-and-open-decisions.md` for those, unrelated
decisions.
