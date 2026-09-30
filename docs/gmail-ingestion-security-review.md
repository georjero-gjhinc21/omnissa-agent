# Gmail ingestion — security review

## 1. Same-OS-user token isolation

**Question:** if Gmail ingestion runs as `georjero`, and Claude Code / Orca
agent terminals also run as `georjero`, can those coding-agent processes
read the OAuth token file?

**Yes — and `chmod 600` alone does NOT prevent it.** POSIX file
permissions gate access by *user identity* (uid/gid), not by *process*.
Every process running as uid=georjero — every Claude Code session, every
Orca terminal, OpenCode, `kiro-cli` — has identical read access to a 600
file owned by georjero as the ingestion code itself. `chmod 600` blocks
*other Linux users* and group/world; it does nothing to separate two
processes running as the same user. Today, with ingestion and agents
both running as georjero, there is no OS-level barrier stopping an agent
process from opening `~/.config/omnissa-agent-google/token.json`
directly — only the fact that no code currently does so. This is a real,
open gap, not a hypothetical.

### Proposed mitigation A — dedicated unprivileged OS user (recommended)

Not implemented this pass; requires root and your explicit approval.

```sh
sudo useradd --system --home-dir /var/lib/omnissa-gmail --create-home \
  --shell /usr/sbin/nologin omnissa-gmail
sudo groupadd omnissa-shared
sudo usermod -aG omnissa-shared georjero

sudo mkdir -p /var/lib/omnissa-gmail/.config/google
sudo chown omnissa-gmail:omnissa-gmail /var/lib/omnissa-gmail/.config/google
sudo chmod 700 /var/lib/omnissa-gmail/.config/google

sudo mkdir -p /var/lib/omnissa-agent/drop
sudo chown omnissa-gmail:omnissa-shared /var/lib/omnissa-agent/drop
sudo chmod 2750 /var/lib/omnissa-agent/drop   # setgid: new files inherit the group
```

`omnissa-gmail` is the only identity that ever runs
`python3 -m omnissa_agent.cli run ...` (via a systemd unit with
`User=omnissa-gmail`), and its credentials directory is 700, owned only
by itself — georjero's agents cannot read it. The one-way handoff is the
`drop` directory: `omnissa-gmail` writes only the already-restricted
report (ids/subject/snippet/sender/date/labels — never a token) there;
georjero (and its agents) can read that directory via the shared group,
but never the credentials directory itself.

### Proposed mitigation B — systemd sandboxing without a new user

`DynamicUser=yes` + `ProtectHome=yes` + `ProtectSystem=strict` +
`StateDirectory=omnissa-gmail` on a `.service` unit gives the ingestion
process its own ephemeral uid and a kernel-enforced private state
directory that no other uid — including georjero — can read, without
`useradd`. More care needed around `ReadWritePaths=` for the drop
directory, but avoids managing a persistent account.

Either option is a genuine OS-level boundary. **File permissions alone,
under the current single-user setup, are not one.** No privileged
changes have been made; both are proposals pending your approval.

### 2026-09-29 gate check: BLOCKED, confirmed concretely

Tested from the actual agent execution identity (this session runs as
`georjero`, uid 1000 -- the same as every Orca/Claude terminal on this
box):

```
$ sudo -n true; echo $?
sudo: a password is required
1
```

Passwordless sudo is not available, and this session has no way to
supply an interactive password. Neither mitigation above can be
implemented from this execution context -- both require root to create
a user/group or a systemd unit with elevated privilege.

### 2026-09-29, later same day: chosen architecture, prepared for operator execution

Full design + idempotent scripts + systemd units are in
`infra/omnissa-ingest/` (`README.md`, `deploy-root.sh`,
`rollback-root.sh`, and the four unit files). Summary of the chosen
boundary:

- Dedicated system user `omnissa-ingest` (no login/password) owns the
  OAuth credential (`/var/lib/omnissa-ingest/google/`, mode 700/600) and
  is the ONLY identity that ever executes Gmail-touching code
  (`cli.py ingest` -- the only command anywhere in this codebase that
  loads a token or calls the Gmail API).
- A private, root-owned copy of `src/` at `/opt/omnissa-agent`, not
  writable by `georjero` -- closes the "agent edits the trusted
  service's code" bypass.
- A one-way, group-mediated drop directory
  (`/var/lib/omnissa-agent/drop/`, mode 2750, group `omnissa-readers`)
  -- `omnissa-ingest` writes sanitized results (ids/subject/snippet/
  sender/date/labels only), `georjero` reads, `georjero` cannot write.
- Two systemd timers drive `cli.py ingest` on schedule (hourly; daily
  07:50 America/Chicago, confirmed the system's actual local timezone).
  `georjero` has no systemctl rights over either unit -- no bridge from
  the agent identity to the credential owner exists at all, not even a
  narrow sudoers rule, since the timers need no external trigger.
- The scheduled coding agent (Orca automation, running as `georjero`)
  only ever calls the new `cli.py classify` entry point against the drop
  file -- it never holds or requests a credential, and there is no
  `--email-status` flag on that command for it to assert; the file's
  own `status` field (written exclusively by the privileged side) is
  what "VERIFIED" comes from.

Indirect-bypass checklist (Phase 2.5 of the operator's brief), what's
closed and what isn't:

| Vector | Status |
|---|---|
| `georjero` sudo (password-gated) | Not usable by this session; separate from the isolation boundary itself |
| `georjero` in `docker` group | **NOT closed** -- root-equivalent, independent of sudo; see below |
| Edit the ingestion service's code/config | Closed -- `/opt/omnissa-agent` and `/etc/systemd/system/*.service` are root-owned, `georjero` has no write access |
| Control the unit (start/stop/enable) | Closed -- no sudoers rule grants `georjero` any systemctl right over these units |
| Read its process environment (`/proc/<pid>/environ`) | Closed -- kernel-enforced, different uid, no ptrace capability |
| Read its logs (journalctl) | `georjero` CAN read them (in the `adm` group, which has journal ACL access) -- acceptable only because `cli.py ingest`'s stdout/stderr never contain secret material by design (verified: only status/account/count fields are ever printed) |
| Its socket | N/A -- this design uses a systemd timer + file handoff, no socket exists |
| World-readable output directory | Closed -- drop dir is mode 2750 (group-restricted to `omnissa-readers`, not world) |
| Command-line argument exposure (`ps`/`/proc/pid/cmdline`) | Closed -- only file *paths* are ever passed as arguments, never secret *values* |

As a concrete (not hypothetical) proof of the risk this leaves open:

```
$ whoami
georjero
$ python3 -c "
with open('/home/georjero/.config/omnissa-agent-google/token.json','rb') as f:
    data = f.read()
print('bytes read:', len(data))
"
bytes read: 535
```

The agent process reading this message IS the same process class that
would read the OAuth token if it were used to run ingestion -- there is
no barrier today. **This gate does not pass.** Scheduling was not
enabled as a result (see HANDOFF.md). Resolving it requires an operator
with sudo to run the commands in Option A or B above; nothing in this
codebase can substitute for that.

### 2026-09-29, third pass: the analysis step ALSO isolated, docker path re-affirmed BLOCKED

The operator flagged that the deployed design still let the *analysis*
step (Agent A/B classification, OmniRoute calls) be presented as an
Orca automation -- which runs as `georjero`, carrying `docker`/`sudo`.
"Do not present an Orca automation as isolated if it runs as georjero
with Docker access" -- correct, and now changed:

- Orca is **removed from the autonomous execution path entirely**.
  There is no Orca automation in this design. A second dedicated system
  identity, `omnissa-analysis`, runs `cli.py classify` via its own
  systemd timers -- it is NOT in `docker`, `lxd`, `sudo`, or `adm`
  (verified by `deploy-root.sh`'s own validation step, which actively
  checks group membership rather than assuming `useradd` got it right).
- `omnissa-analysis` reads `omnissa-ingest`'s sanitized drop files
  (group `omnissa-readers`) and never touches a Gmail credential.
  Network is restricted at the systemd unit level
  (`IPAddressDeny=any` / `IPAddressAllow=localhost`) so even a code bug
  that tried `combo-continuous` would be blocked by the OS, not just by
  a default argument.
- `georjero` gets a THIRD group, `omnissa-reports-readers`, read-only
  access to the finished briefs/drafts `omnissa-analysis` produces --
  the only thing georjero (and thus Orca/Claude, interactively) can see
  of the autonomous pipeline's output.

### 2026-09-29, fourth pass: precise split, corrected from a prior over-merged verdict

An earlier version of this doc (and of `deploy-root.sh`'s own output)
reported a single merged `AGENT PRIVILEGE BOUNDARY: BLOCKED` verdict.
The operator correctly pushed back: that conflated two unrelated
findings, and risked reading as "the scheduled-agent design has a flaw"
when it doesn't. Corrected to two separate findings, verified live:

**1. SCHEDULED-AGENT ISOLATION: PASS.** `omnissa-analysis` has no path
to `omnissa-ingest`'s credential or to root:
- `id omnissa-ingest` (live): `groups=982(omnissa-ingest)` -- no
  supplementary groups at all.
- `omnissa-analysis` (per `useradd`/`usermod` calls in `deploy-root.sh`,
  re-verified live the moment it's created): primary group
  `omnissa-analysis` plus supplementary `omnissa-readers` only -- never
  `docker`, `lxd`, `sudo`, or `adm`. `deploy-root.sh` actively checks
  this (`for bad in docker lxd sudo adm`) rather than assuming
  `useradd` got it right.
- The credential directory (`/var/lib/omnissa-ingest/google/`, mode
  `700`, owner `omnissa-ingest`) has zero group/other bits -- kernel-
  enforced, no group `omnissa-analysis` is in can reach it.
- `omnissa-analysis` cannot write `/opt/omnissa-agent` (root-owned),
  cannot reach the Docker socket (not in `docker`, socket is
  `root:docker 660`), cannot `sudo`, cannot control `omnissa-ingest`'s
  systemd unit (no sudoers/polkit rule grants it anything).
- Live evidence for `georjero` specifically (same tests apply): reading
  `token.json`/`client_secret.json` → Permission denied; writing the
  deployed `cli.py` → Permission denied; `systemctl start
  omnissa-ingest-scan.service` → interactive authentication required.

**2. OPERATOR ACCOUNT RISK: `georjero` retains root-equivalent Docker
access.** `docker version` succeeds with zero authentication beyond the
existing session (confirmed live: real server response, no password).
This is a fact about the `georjero` account's own group memberships --
it exists independent of whether `omnissa-ingest`/`omnissa-analysis`
are deployed at all, and there is no path FROM either scheduled
identity TO `georjero`'s privileges (the arrows only ever point the
other way: `georjero` reads their sanitized output; neither one can be
influenced by `georjero` beyond that read). **Removing `georjero` from
`docker` is therefore not required for the scheduled-agent boundary to
be correctly isolated** -- it only matters if the operator separately
wants to close `georjero`'s own pre-existing path to root.
`deploy-root.sh`'s validation output now prints these as two clearly
separated blocks, never merged into one verdict.

**Operator decision, not made here** (three real options, trade-offs
only -- Docker config was not touched):

| Option | Closes the path? | Cost |
|---|---|---|
| Remove `georjero` from `docker` (and/or `sudo`) | Yes, fully | Loses passwordless `docker` CLI for georjero's own workflows; `sudo docker ...` still works with the password |
| Migrate to rootless Docker (`dockerd-rootless`) | Yes, fully -- container root is namespace-remapped, not real root | Real migration: existing containers/volumes/port-bindings need re-creating; out of scope for this project, mentioned for completeness only |
| Accept the residual risk, documented | No | Isolation still fully defeats accidental/casual cross-identity access (a buggy agent genuinely cannot open the token file); does not defend against a deliberate escalation by georjero itself |

None of these were applied. `deploy-root.sh` does not touch group
membership or Docker configuration, per explicit instruction.

## 2. Third-party provider exposure via combo-continuous

`combo-continuous`'s fallback chain mixes fully local `ollama-local`
with third-party free-tier services (`openrouter-free`, `groq-free`,
`huggingface-free`, `gemini-free`, `github-models`, `kiro`). This
project's own ops guide already flags the risk: "Free != private:
OpenRouter free logs prompts; pollinations is public; contributor-tier
Zen models need opt-in."

Before this pass, `agent_a.build_brief`'s LLM summarization step
defaulted to `combo-continuous` — real finding summaries (built from
real email subject lines) could be sent to whichever third party won
that run's fallback race.

**Fixed this pass:**
- `agent_a.build_brief(..., llm_combo=router.LOCAL_ONLY_COMBO)` now
  defaults to `combo-private` — verified in
  `~/.config/omniroute/backends.json`: `{"strategy": "pipeline",
  "steps": ["ollama-local"]}`, i.e. Ollama only, nothing leaves the box.
- `pipeline.run_pilot(..., llm_combo=...)` and
  `cli.py run --llm-policy {local-only,combo-continuous}` thread this
  through. `run`'s default is `local-only`; sending real content through
  `combo-continuous` requires explicitly passing
  `--llm-policy combo-continuous` — never the default.
- `router.ask()` now calls the gateway's HTTP API directly instead of
  the `omniroute` CLI wrapper, which silently discarded the
  `omni_backend` field. Confirmed live: a raw gateway call returned
  `"omni_backend": "omni/openrouter-free"`. This is now recorded on
  `AgentAResult.llm_backend` / `PilotReport.llm_backend` and printed by
  `cli.py run` (`LLM backend used: omni/...`), so every real
  classification's provider is visible, not silent.

**Residual tradeoff:** `combo-private`'s only step is `ollama-local`; if
Ollama is down, the LLM summary step defers (exit code 3) rather than
falling back to a third-party provider. Live runs will show "no LLM
summary" more often than `combo-continuous` would. That is the correct
default given the sensitivity of real mailbox content — change it only
with explicit approval via `--llm-policy combo-continuous`.
