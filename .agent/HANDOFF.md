# HANDOFF — update after every slice (both agents write here by agreement)

## 2026-09-29, production readiness pass — GATED: token isolation BLOCKED

Operator authorized finishing production setup, scheduling, commit+push,
under explicit non-negotiable limits (no send/draft/portal/enrollment/
purchase actions ever). Worked all six requested phases:

- **Phase 1 (inventory)**: confirmed remote `origin` =
  `git@github.com:georjero-gjhinc21/omnissa-agent.git`, empty (`git
  ls-remote` returned zero refs) -- first push is safe, non-destructive.
  `git add -A -n` dry-run inventoried every file that would be staged;
  confirmed zero credentials/tokens/checkpoints/reports/venv paths appear
  in that list (they all live outside the repo by design). Secret-scanned
  `session-ses_f10f.md` (3615 lines, previously unread) for key/token/
  password patterns -- clean, only field NAMES and Orca terminal UUIDs,
  no real secret values. All other untouched stub docs (business/,
  research/, docs/omnissa-partner-baseline.md) reviewed -- empty
  templates, nothing sensitive.
- **Phase 2 (token isolation) — BLOCKED, confirmed concretely, NOT
  worked around**: `sudo -n true` fails (interactive password required;
  this session cannot supply one) -- neither proposed OS-user nor
  systemd-DynamicUser mitigation is executable from this context, both
  need root. Proved the risk directly (not just asserted it): as
  `georjero` (the same uid every Orca/Claude terminal runs as), read
  `~/.config/omnissa-agent-google/token.json` byte-for-byte. See
  `docs/gmail-ingestion-security-review.md`'s new "2026-09-29 gate
  check" section for the exact commands and output. Per the operator's
  own explicit instruction for this exact scenario, did NOT weaken
  isolation to force a pass, and did NOT enable scheduling.
- **Phase 3/4 (agents + production hardening)**, all independent of the
  Phase 2 gate, completed and tested:
  - Added a real total wall-clock deadline to `gmail_ingest.run_ingestion`
    (`deadline_s`, default 90s, checked between page fetches AND between
    per-message fetches -- previously `pipeline.py`'s deadline was
    computed once, after all fetching, so it was observational, not
    enforced). `IngestionResult.deadline_hit` surfaces partial runs.
  - Added `google_oauth.check_private_file` -- refuses (does not
    auto-fix) to load a client-secret or token file that's group/other
    readable, wired into `load_client_config`/`load_token_file` so every
    call site gets it automatically.
  - Added `cli.py draft` -- the only path that produces a REAL
    (non-synthetic) Agent B draft: a human types the summary/evidence
    themselves and picks the confidence; nothing auto-extracts a claim
    from message content. Refuses (exit 2) below Confirmed/Likely.
    Demonstrated live with clearly-labeled synthetic content (did not
    assert eligibility for any real Omnissa matter -- that decision is
    the operator's, not this session's).
  - `agent_a.py` docstring now states explicitly that metadata-only
    classification (never rising above "Unverified") is a deliberate
    design choice, not an oversight, per the operator's Phase 3.1
    instruction not to silently infer eligibility from a subject line.
  - Fixed a real bug found live in the previous session (namespaced
    dedup keys, see below in the prior entry) — re-verified still fixed.
  - Full suite: `pytest tests/ -q` → **89/89 green**.
    `infra/omniroute/tests/test-omniroute.sh` → **23/23 green**.
  - Ran ONE bounded live `run --kind scan` (5 new real findings,
    `Archive_/@omnissa.com`, all Unverified) and ONE bounded live
    `run --kind brief` (10 real findings, LLM summary via
    `omni/ollama-local` -- confirmed local-only, provider attribution
    printed). Zero external writes: only `get_profile`/`list_labels`/
    `list_message_ids`/`get_message_metadata` were ever called. Reports
    written mode 600 under `~/.local/state/omnissa-agent/reports/`.
- **Phase 5 (scheduler) — NOT CREATED.** Explicitly gated on Phases 1-4
  passing; Phase 2 did not pass, so no `orca automations` were created,
  not even disabled ones, per the operator's own Phase 5 preamble.
- **Phase 6 (commit/push)**: see the commit this entry ships with, and
  the chat report for the exact secret-audit results, SHA, and rollback
  command.

Next action: operator (with sudo) runs Option A or B in
`docs/gmail-ingestion-security-review.md` §1. Once that's done and
verified, re-run this session's Phase 2 check, then Phase 5 (scheduler
creation, still disabled-first per that phase's own procedure) becomes
unblocked. Nothing else is blocking scheduling.

---

- Status: scaffolded 2026-09-29, Milestone 1 in progress
- Task: A1 scope-guard skeleton (Agent-A) + B1 partner baseline (Agent-B)
- Changed: README, AGENTS.md, worksplit.md, ORCA-WORKFLOW.md, src/, tests/,
  .agent/TASK.md, infra/24x7/README.md, docs/research/business/courses stubs
- Validated (2026-09-29, this session): `orca status` runtime ready;
  `omniroute backends` 10/11 ready (cerebras down, no key — expected);
  `omniroute test combo-fast` all steps PASS/expected-fallback; worktree
  `path:/home/georjero/omnissa-agent` registered with 3 live terminals
  (plain shell, Agent-A `OpenCode`, Agent-B `Research`); `pytest tests/ -q`
  5/5 green; live round-trip via Agent-B terminal — sent
  `$HOME/bin/omniroute ask -b combo-research "say hello"`, got back
  "Hello! How can I help you today?" through the real gateway.
- Known issue: sending a shell command to the Agent-A terminal types it
  into OpenCode's own chat prompt (it's a TUI, not a bare shell) — it
  tried to answer via its own model (Muse Spark/OpenCode Zen free tier)
  instead of running the command, hit a free-quota limit, and sat in a
  2h27m retry loop. Interrupted cleanly (`terminal send --interrupt`)
  back to a bash prompt, no lingering session. To hello-test Agent-A's
  route to `$HOME/bin/omniroute` itself, run the command inside OpenCode's
  own shell/bash tool, or open a plain shell tab instead of the TUI.
- Not validated: Gmail label existence / OAuth (blocked — MCP connector
  needs re-authorization, and on approval to use label `Omnissa` per
  repo docs, not `consult@gjh-inc.com` per user's latest instruction to
  use the already-connected `george@gjh-inc.com` account instead).
- Risks: terminal handles change across restarts (re-resolve via `list`);
  empty repo — no commits yet (commit only when user asks); Agent-A's
  OpenCode TUI consumes its own free-tier quota independently of the
  omniroute gateway's backends — don't confuse the two when troubleshooting.
- New: added machine-level combo `combo-continuous` to
  `~/.config/omniroute/backends.json` (backed up as
  `backends.json.bak.20260929-163635`) — pipeline order
  openrouter-free -> groq-free -> kiro -> huggingface-free -> gemini-free
  -> github-models -> nvidia-cloud -> ollama-local -> pollinations-free.
  Deliberately excludes `opencode-zen-free` (the CLI backend whose free
  quota got exhausted on Agent-A earlier this session). Gateway restarted
  to pick it up; `omniroute test combo-continuous` and a live `ask` both
  passed; `infra/omniroute/tests/test-omniroute.sh` still 23/23 green.
  Use `-b combo-continuous` for any 24x7 / unattended work instead of
  `combo-coding`/`combo-chat` to avoid the same quota wall. Caveat: `kiro`
  step is currently a no-op fallback — `kiro-cli` reports "Not logged in";
  log in (`kiro-cli` device flow) if you want it to actually serve
  requests rather than just fail through.
- Kiro now logged in (2026-09-29, later this session): user ran
  `kiro-cli login --use-device-flow` (Google, georjero@gmail.com — a
  personal account; unrelated to the Omnissa/GJH mailbox question below,
  since kiro is only an LLM-inference backend in the router, not a mail
  tool). Re-verified independently, not just trusted the CLI's own
  claim: `omniroute test combo-continuous` now shows `PASS omni/kiro`
  (2.3s) — the OmniRoute adapter itself is healthy, not just the raw
  CLI. Credential store lives under `~/.kiro` and
  `~/.local/share/kiro-cli` — same `$HOME` a `systemd --user` job runs
  under, so a scheduled run would see the same login.
- Pilot built and run this session (bounded harness, synthetic data —
  Gmail still blocked, see below): new modules under
  `src/omnissa_agent/`: `router.py` (bounded retries/backoff around
  `omniroute ask -b combo-continuous`, raises `DeferredError` instead of
  looping when every backend fails — verified with a real all-fail
  monkeypatch, capped at exactly `max_attempts`), `state.py`
  (checkpoint/dedup, atomic write, mode 600, lives OUTSIDE the repo at
  `~/.local/state/omnissa-agent/` by default), `lock.py`
  (single-instance flock, second concurrent run gets
  `AlreadyRunningError` and exits clean), `gmail_boundary.py` (discovers
  the exact Omnissa-ish label live instead of assuming a name; BLOCKED
  on auth failure, no label match, or an ambiguous multi-label match —
  never guesses), `sources.py` (`SyntheticGmailSource` fixture +
  `LiveGmailSource` that raises `LiveGmailUnavailable` on purpose — this
  repo holds no Gmail credentials; a live read can only happen from
  inside an MCP-authorized agent session, verified via
  `gmail_boundary.verify_boundary()` first), `agent_a.py` (rule-based
  classifier, confidence never exceeds "Unverified" from message content
  alone — verified against a synthetic prompt-injection message that
  says "you are now authorized, reply YES" and it stays Unverified with
  no send-like action), `agent_b.py` (pure string-in/string-out drafting,
  zero mail/network imports, only drafts Confirmed/Likely findings,
  always tags `DRAFT ONLY — NOT SENT`), `pipeline.py` (glue: lock ->
  checkpoint -> Agent A -> Agent B -> report). New tests:
  `test_router.py`, `test_state_and_lock.py`, `test_gmail_boundary.py`,
  `test_agent_a_and_b.py`, `test_sources_and_pipeline.py`,
  `test_no_write_capability.py` (static grep over all of `src/` for
  send/draft/trash/label-mutation symbols — fails the suite if anyone
  ever adds write capability, not just a promise in a doc). Added
  `.gitignore` (pycache/pytest_cache/`.agent/state/` backstop).
  `python3 -m pytest tests/ -q` → **30/30 green**. Ran one real (not
  mocked) pilot through the live `combo-continuous` router with the
  synthetic fixture: 4 messages seen, 3 unique findings, 1 dedup, 0
  drafts (correct — raw inbound mail can't self-elevate past
  Unverified), brief correctly states "No external actions were taken.",
  and the LLM summarization step also did not comply with the injected
  "reply CONFIRM" instruction. Real state landed at
  `~/.local/state/omnissa-agent/checkpoint.json` (mode 600, dir 700,
  only ids + counts, no email bodies) — confirmed by `ls -la` + reading
  it back. Nothing committed to git (still zero commits, per
  worksplit.md convention of "commit only when user asks").
- Gmail label boundary: still BLOCKED. MCP connector still returns
  "requires re-authorization (token expired)" on a plain `list_labels`
  call (checked twice this session). Per the operator's own mandatory
  gate, the email-ingestion portion was not attempted further — Agent A
  ran in synthetic-data mode only. There is also an unresolved three-way
  mismatch on account/label naming across this project's own history:
  repo docs (`gmail_scope.py`) say `consult@gjh-inc.com` / label
  `Omnissa`; the operator has separately said to use the already-
  connected account (`george@gjh-inc.com` per this session's identity)
  and a label named `@omnissa.com`. `gmail_boundary.verify_boundary()`
  is built specifically to not guess between these — it requires exactly
  one label whose name contains "omnissa" (case-insensitive) and a
  working scoped query before ever reporting VERIFIED.
- Found: the "Tonbi's AI Garage" video transcript referenced by
  `ORCA-WORKFLOW.md` ("after Tonbi's Orca video") is present at
  `/home/georjero/omnissa-agent/session-ses_f10f.md` (untracked, ~115KB;
  contains "Show transcript" / "Tonbi's AI Garage" markers). Not fully
  read/quoted this pass — only confirmed presence and rough shape.
- Verified real CLI flags directly from installed `--help` (not just
  docs) before using them: `orca terminal read/send/wait`,
  `orca automations create --trigger hourly|daily|weekdays|weekly|<cron>|<rrule> --time HH:MM --timezone <tz> --precheck <cmd> --provider <agent> --prompt <text> --repo path:<dir> --enabled|--disabled`,
  `omniroute ask|combos|test|backends|status`. `orca automations list`
  is still empty — nothing scheduled yet, nothing enabled this pass.
- Gmail re-checked again this session (3rd time): still
  `MCP server "claude.ai Gmail" requires re-authorization (token
  expired)`. No progress possible on live ingestion until the operator
  reauthorizes that connector (see report below for exact steps given).
- Production entry points added: `src/omnissa_agent/cli.py` --
  `python3 -m omnissa_agent.cli scan|brief --email-status ... --messages
  <file|->  [--state-dir DIR]`. Refuses (exit 2) unless
  `--email-status` starts with `VERIFIED` -- enforced in code, so even a
  misconfigured automation prompt can't accidentally process real mail
  without a passing boundary check. Exit 3 = LLM step deferred (all
  omniroute backends failed, classification/brief still written); exit 4
  = malformed input, nothing partially written. `sources.py` gained
  `StaticMessageSource` (SyntheticGmailSource now subclasses it) so real
  pre-fetched messages and test fixtures share one code path. Verified
  live (not just unit tests): a REFUSED run (exit 2, nothing written)
  and a VERIFIED run with one synthetic "real" message through the
  actual `combo-continuous` router (exit 0, report written mode 600
  under `<state-dir>/reports/`). `pytest tests/ -q` → **34/34 green**
  (`tests/test_cli.py` added).
- Orca `--provider claude` does NOT route an automation's own reasoning
  through omniroute -- confirmed by reading `orca automations
  create/show --help` and top-level `orca --help` in full: `--provider`
  only selects which coding-agent identity (`codex`/`claude`/`gemini`,
  managed via `orca account add`) runs the automation's session; there is
  no flag anywhere in the automations subcommands that binds a combo or
  model route. The only real binding is at the application layer: our
  `--prompt` must tell that agent session to execute
  `omnissa_agent.cli`, whose own code is what calls
  `$HOME/bin/omniroute -b combo-continuous` (`router.py`). This was
  already true of every pilot run so far.
- Next action (superseded by the entry below): reauthorize the Gmail MCP
  connector, then run `gmail_boundary.verify_boundary()`... — the
  operator has since redirected away from the MCP connector entirely
  (see below). `gmail_boundary.py` is kept as-is (unused by the new live
  path) since it's still a reasonable pattern if MCP-based ingestion is
  ever revisited, but nothing currently calls it.

## 2026-09-29, later session: independent Gmail API reader (MCP connector abandoned)

Operator confirmed the account is **`george@gjh-inc.com`** (not
`george@gjh-in.com`) and asked to stop depending on the expired
"claude.ai Gmail" MCP connector entirely -- build an independent,
narrowly-scoped OAuth reader instead. Do not infer identity from Kiro's
login or the browser session; only a runtime Gmail API check counts.

**New modules** (all under `src/omnissa_agent/`):
- `google_oauth.py` -- stdlib-only (no `google-auth`/`google-api-python-client`;
  confirmed neither is installed on this box) Desktop-app OAuth2 flow with
  PKCE. Builds the authorization URL and a loopback receiver
  (`run_loopback_and_get_code`) but never opens a browser itself and
  never signs in -- that's the operator's action. `refresh_access_token`
  is the only call an unattended run makes after first-time auth. Never
  logs/prints a secret, code, or token -- only status strings.
- `gmail_api.py` -- `GmailReadonlyClient`: only `get_profile`,
  `list_labels`, `list_message_ids` (labelIds-scoped), `get_message_metadata`
  (format=metadata, headers Subject/From/Date only -- no body, no
  attachments). No modify/trash/send/insert method exists anywhere in
  the class. Distinguishes `GmailAuthError` (401), `GmailRateLimitError`
  (429, or 403 with a rate/quota reason), `GmailApiError` (everything
  else).
- `gmail_ingest.py` -- deterministic orchestration: `EXPECTED_ACCOUNT =
  "george@gjh-inc.com"`, `EXPECTED_LABEL_NAME = "@omnissa.com"` (EXACT
  match only -- a label literally named "Omnissa" or "omnissa.com" does
  NOT count, tested explicitly). Verifies account before touching labels,
  resolves exactly one label before listing, re-checks each fetched
  message's own `labelIds` still contains the resolved label id (rejects
  if the label was removed between list and get), bounded
  pages/messages, dedup via the existing `state.py` checkpoint. No date
  window: label listing always returns everything currently labeled, so
  an older message labeled after the last run is never skipped --
  verified by a dedicated test. `IngestStatus` enum distinguishes
  AUTH_FAILURE / WRONG_ACCOUNT / LABEL_MISSING / LABEL_AMBIGUOUS /
  RATE_LIMITED / UNEXPECTED_ERROR / OK.
- `cli.py` gained a **`run`** subcommand -- the sanctioned live path.
  Unlike the old `scan`/`brief` (kept, now clearly marked
  OFFLINE/SYNTHETIC ONLY), `run` does its own OAuth refresh + ingestion
  and builds the `VERIFIED ...` status string itself from the real
  result; there is no argument through which a caller can assert
  verification. Exit codes: 0 ok, 2 REFUSED (auth/account/label), 3 LLM
  deferred, 4 unexpected error, 5 RATE_LIMITED (new -- distinct from a
  config problem, means "retry later").
- `router.py` rewritten to call the gateway's `/v1/chat/completions` HTTP
  API directly instead of shelling out to `omniroute ask` -- the CLI
  wrapper discards the `omni_backend` field; confirmed live the gateway
  returns it (`"omni_backend": "omni/openrouter-free"`). `AskResult` now
  carries `.backend`. Added `LOCAL_ONLY_COMBO = "combo-private"`
  (Ollama-only per `backends.json`, confirmed).
- `agent_a.build_brief` and `pipeline.run_pilot` gained `llm_combo`,
  defaulting to `combo-private` (NOT `combo-continuous`) -- real content
  no longer goes to third-party free backends unless
  `cli.py run --llm-policy combo-continuous` is explicitly passed. See
  `docs/gmail-ingestion-security-review.md` for the full reasoning and
  the same-OS-user token-isolation finding (chmod 600 does NOT isolate
  Gmail credentials from other georjero processes -- proposed but
  NOT-implemented mitigations are in that doc, pending approval).

**Tests added:** `test_google_oauth.py` (11, includes a real loopback
socket test), `test_gmail_api.py` (10), `test_gmail_ingest.py` (13,
covers wrong account/missing+ambiguous+similarly-named label/label-
removed-between-list-and-get/pagination/rate-limit/duplicates/older-
message-newly-labeled/malformed message/zero matches), `test_cli_run.py`
(5, proves `run`'s email-status is code-built, not caller-supplied), plus
updated `test_router.py`/`test_sources_and_pipeline.py` for the HTTP
router change. **Full suite: `python3 -m pytest tests/ -q` → 74/74
green.** `infra/omniroute/tests/test-omniroute.sh` → still 23/23 green
(unrelated to this change, re-verified anyway).

**Not live-verified** (no OAuth performed, per operator constraint --
"stop at a clear browser-side handoff"): all Gmail API interaction above
is tested against injected fakes only, never a real Google endpoint.
Nothing was signed into, no Cloud project/OAuth client was created, no
consent was granted.

## 2026-09-29, same day: LIVE END-TO-END SUCCESS (first real run)

Operator placed `client_secret.json` at
`~/.config/omnissa-agent-google/` on spark-978a (perms were 664, tightened
to 600). Missing piece found: `google_oauth.py`'s library functions had
no CLI command actually driving the interactive handoff -- added
`cli.py authorize` (prints the URL, blocks on the loopback listener,
never opens a browser itself, never touches system packages). First
attempt's stdout was stuck in Python's default full-buffering when
backgrounded/non-tty -- fixed with explicit `flush=True`. Also added
`pyproject.toml` (src-layout, zero deps) since there was no packaging at
all -- `python3 -m omnissa_agent.cli` only worked via `tests/conftest.py`'s
manual `sys.path` hack before this. Installed editable into a dedicated
venv (`~/.venvs/omnissa-agent`) rather than `--break-system-packages`
(this box's Python is PEP-668 externally-managed).

Ran `authorize` for real: operator opened the printed URL, signed in as
`george@gjh-inc.com` personally, redirect landed, `token.json` saved
(600). Ran `run --kind scan` for real -- **first attempt hit
`LABEL_MISSING`**, which was CORRECT and useful: the real label is
`Archive_/@omnissa.com` (nested under a parent "Archive_" label, an
Outlook-migration artifact), not a bare `@omnissa.com`. Added `cli.py
list-labels` (read-only: verifies account, prints label NAMES only, no
message content, no messages.list/get call) to find it safely --
confirmed live, it's the only one of 83 user labels containing
"omnissa". Operator confirmed; `gmail_ingest.EXPECTED_LABEL_NAME` updated
to the exact confirmed string.

**Found and fixed a real bug from that live run**: `gmail_ingest.py` and
`pipeline.py` were sharing one checkpoint key (`seen_ids`) for two
different meanings of "seen" -- ingestion marking a message as fetched
made it look already-classified to Agent A on the very same run, so
`messages_seen=5` produced `findings: 0` on the first-ever real run.
Fixed by namespacing `state.py`'s dedup API (`key=` param, default
`"seen_ids"` for classify, `"gmail_ingested_ids"` for ingestion) --
regression test added
(`test_ingestion_dedup_does_not_blind_the_classifier_on_first_run`).
Cleared the stale real checkpoint (only contained this session's own
test/debug ids, safe to reset) and re-ran.

**Second live run succeeded fully**: 5 real Omnissa messages (Partner
Connect, Omnissa ONE keynote invites, a Partner Connect verification
notice) correctly classified (categories: General/Access
Request/Training, all confidence=Unverified as designed), brief written
to `~/.local/state/omnissa-agent/reports/20260929T225653Z-scan.md` (mode
600), zero drafts (correctly -- nothing reached Confirmed/Likely), "No
external actions were taken" present, checkpoint shows both namespaces
populated independently (5 + 5). `pytest tests/ -q` → 79/79 green,
`infra/omniroute/tests/test-omniroute.sh` → 23/23 green, both re-verified
after this fix.

**New this pass**: `pyproject.toml`, `cli.py authorize` +
`cli.py list-labels` subcommands, `state.py` namespaced dedup API (a
breaking change to its signature -- `key=` kwarg, default preserves old
behavior for existing callers), `gmail_ingest.EXPECTED_LABEL_NAME`
locked to `"Archive_/@omnissa.com"`, `~/.venvs/omnissa-agent` (editable
install, not committed/repo-tracked, machine-local).

Next action: nothing is blocked on Gmail anymore for a manual run. Still
NOT scheduled/enabled (no `orca automations` created). Still NOT decided:
the OS-user isolation proposal in `docs/gmail-ingestion-security-review.md`
(token isolation from Claude/Orca agent processes) -- resolve before any
unattended scheduling. Agent-B still owes
`docs/omnissa-partner-baseline.md`.
