# Target vision: closed-loop autonomous Omnissa agent

Captured 2026-09-30, verbatim intent from the operator: an agent that (1)
receives input/instructions, (2) starts working on it as soon as
received, (3) reports a summary back to `george@gjh-inc.com` by email
when done, (4) receives its next focus/instruction from an email, (5)
self-heals and self-corrects, and (6) operates with the autonomy of "an
employee of GJH INC" whose job is to bring business from Omnissa.

This is a design capture and risk assessment, not an implementation.
**Nothing in this document has been built.** Everything currently
running (`docs/operations-record.md`) is unchanged: read-only ingest,
classification, local-file-only drafting, zero send/portal/enrollment
capability anywhere in the codebase.

## 1. What each piece of the vision requires, mechanically

| Piece | What it needs |
|---|---|
| Receive input/instructions | A channel the agent polls or is triggered by, and a way to tell "this is an instruction" apart from ordinary Omnissa mail |
| Start working immediately | Either shorten the timer interval, or move from timer-poll to event-triggered (e.g. Gmail push notifications via Pub/Sub, or a webhook) |
| Report a summary back **by email** | A new OAuth scope (`gmail.send` or at minimum `gmail.compose`) on the credential-holding identity — `gmail.readonly` (what's granted today) cannot send or draft anything |
| Receive next focus from an email | The agent must parse arbitrary inbound email content as a command and act on it |
| Self-heal / self-correct | Automated detection of a bad run + automated remediation, with no human in the loop for the remediation step |
| "Bring business from Omnissa" | The agent would need to actually act in the world: submit to a partner portal, register a deal, enroll in a course, or similar |

## 2. Where this conflicts with decisions already made this session

Every one of these was said explicitly, more than once, over the course
of building what's currently running:

> "Agent B — LOCAL DRAFT TEXT ONLY — never sends email, creates Gmail
> drafts, modifies messages, submits to Omnissa portals, enrolls in
> courses, makes purchases, or sends external notifications."

> "Do NOT create an Orca automation that reads Gmail, runs another
> scan, classifies mail, drafts emails... Do not claim Orca is
> scheduling these agents."

The current architecture's entire privilege-separation design — two
restricted identities, no send scope, no portal access, every
classification capped at `confidence=Unverified` until a human
confirms — exists *because* of that boundary. Three parts of tonight's
request sit directly on top of it:

- **"Report back by email"** — needs a send/compose scope that has
  never existed, on the one identity that already holds the Gmail
  credential. That identity's whole threat model until now assumed
  read-only access; adding send changes what a bug or a bad
  classification can actually do to the real world (a wrong or
  garbled email leaving the system unattended, addressed to a real
  person).
- **"Receive instruction from an email"** — this makes an inbox a
  command channel. Any message landing in whatever's monitored — a
  spoofed sender, a compromised account, or even a legitimately
  received but adversarially-worded email from Omnissa itself — is a
  candidate to be *interpreted as an instruction* by the same pipeline
  that already reads it. This is the textbook shape of a prompt
  injection vector, and it's a fundamentally different risk than
  "read this label and classify it," which is all the running system
  does today.
- **"Bring business from Omnissa," autonomously** — this is the one
  most directly opposite to what's built. Submitting to a portal,
  registering a deal, or enrolling in something means the agent takes
  an action with real business/legal/financial consequences, with no
  human confirming it first. A single bad classification (there's no
  guarantee Agent A's keyword-based categorizer is always right — it's
  deliberately capped at "Unverified" for exactly this reason) could
  turn into a real, wrong action taken on GJH INC's behalf.

None of this means the vision is wrong — it means it's a different
risk tier, and the decision to move into it belongs to you, not to me
choosing to build it because it was asked.

## 3. A staged path, safest to riskiest

**Stage 1 — buildable today, no new OAuth scope, no new risk tier.**
Instructions arrive via a dedicated Gmail *label* (not "any email"),
one only `george@gjh-inc.com` can apply to a message — e.g. an
`Agent-Focus` label you put on an email you send/label yourself. The
existing read-only `ingest` identity already has everything it needs
to read a labeled message the same way it reads `Archive_/@omnissa.com`
today. "Working on it" means updating a config value (e.g. which
category to prioritize, or a keyword filter) and writing the outcome to
the existing sanitized report file. No email is ever sent by the agent.
This keeps the *entire* existing safety model intact — it just adds a
second read-only input label alongside the existing one.

**Stage 2 — needs one new scope, needs your explicit sign-off, still
human-gated.** Instead of *sending* email, the agent creates a Gmail
**draft** addressed to yourself summarizing the run — you still click
send. This needs `gmail.compose` (materially smaller than `gmail.send`)
and a fresh OAuth consent flow, since the current token was authorized
read-only. A draft-to-self is a different risk than the "never creates
Gmail drafts" line above, which was written with drafts *to Omnissa or
a partner* in mind — worth confirming explicitly whether a self-only
status-summary draft is what you intend to permit, since it's not
automatically covered by relaxing that rule.

**Stage 3 — real autonomous action (portal submission, deal
registration, enrollment, purchase). Not recommended without a
separate, much heavier design pass** covering: per-action dollar/scope
caps, mandatory human approval gates, full audit logging of every
attempted action (not just classifications), and very likely legal
review given this is GJH INC committing to something with a partner.
This is the piece I'd push hardest to keep human-executed regardless of
how the rest evolves — everything else in this document is a channel
or a scope change; this one is the agent actually acting as an
employee, with all the accountability that implies.

## 4. Self-healing / self-correction — the part that's genuinely
buildable now, independent of the above

This doesn't require any new scope or new risk tier, and is a natural
extension of what already exists:
- The six real incidents this session already found (ownership
  corruption, truncation-then-delete, lock races, etc.) all had a
  common shape: a check *would have* caught them if one had existed
  before enabling timers. A standing health check (Phase D item,
  already tracked) that runs `reconcile` on a schedule and pages/alerts
  if `pending > 0` for more than N cycles is real self-*detection*.
- "Self-correction" for the failure modes already seen (a stuck lock,
  a PARTIAL that never resolves) can be automated safely because
  they're idempotent and reversible — re-running `ingest`/`classify` is
  safe by design. That's a good, low-risk place to start.
- Self-correction for a *bad classification* is a different claim —
  there's no automated way to know a classification was wrong without
  either a human or a much stronger verification step, so this stays
  human-reviewed regardless.

## 5. Decisions (2026-09-30)

Operator confirmed, explicitly:
- **Report channel: Gmail draft-to-self** (Stage 2) — new
  `gmail.compose` scope, agent drafts, operator clicks send. No
  autonomous send, ever.
- **Input channel: dedicated label the operator controls** (Stage 1) —
  not "any email." No new OAuth scope; same `gmail.readonly` grant
  already in place.
- **No autonomous real-world action** — Stage 3 (portal submission,
  deal registration, enrollment, purchase) is explicitly declined. The
  agent stays detection/recommendation-only; a human always executes
  the real action. This is the existing boundary, reaffirmed, not
  relaxed.

## 6. Stage 1 concrete design (buildable now, no new scope)

- New label, operator-created and operator-applied only (e.g.
  `Agent-Focus`) — mirrors `EXPECTED_LABEL_NAME` handling already in
  `gmail_ingest.py`, just a second label resolved the same read-only
  way.
- The instruction lives in the **subject line only**, not the body —
  matching the existing, deliberate metadata-only policy in
  `agent_a.py` (no message-body access anywhere in this codebase, by
  design). A body-based instruction format would be a bigger read
  footprint than anything else here reads today.
- Ingest picks up labeled messages same as it does `Archive_/@omnissa.com`
  and writes them to a small, separate focus-config file (not the
  drop file) that `classify` reads on each run.
- Effect of a focus instruction: open question, needs one product
  decision --- see below.

## 7. Stage 2 concrete design (needs operator's own OAuth re-consent)

- The current token was authorized read-only; `gmail.compose` requires
  a fresh interactive consent (`cli.py authorize`, same one-time,
  operator's-own-browser flow already used for the read-only grant --
  I cannot do this step, by design).
- Identity split: only `omnissa-ingest` ever holds a Gmail credential,
  but `omnissa-analysis` is the one that builds the brief text. This
  means draft creation needs the same one-way handoff shape as
  ingestion itself, just reversed -- `omnissa-analysis` writes the
  finished brief to a location `omnissa-ingest` can read, and a new,
  narrowly-scoped step (privileged side) creates the Gmail draft from
  that text. No new capability is added to `omnissa-analysis` itself.

## 8. Status

Stage 1 and Stage 2 are approved in direction; Stage 3 is declined.
Nothing above is implemented yet -- one product decision (§6) is
needed before writing Stage 1, and Stage 2 is blocked on the
operator's own OAuth re-consent step regardless of when the code is
ready.
