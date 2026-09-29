#!/usr/bin/env bash
# OPERATOR-RUN ONLY. Requires root (run with: sudo bash deploy-root.sh).
# Idempotent -- safe to re-run (e.g. after a code update, or to add the
# analysis identity to an already-deployed ingest identity). Never
# displays credential file contents. The agent that wrote this cannot
# run it (no sudo password available to that session) -- that is the
# point of this being a separate handoff.
#
# TWO restricted identities, not one:
#   omnissa-ingest    owns the Gmail OAuth credential; runs ONLY
#                      `cli.py ingest` (the sole command that ever
#                      touches a token or the Gmail API).
#   omnissa-analysis  runs Agent A/B classification + OmniRoute calls
#                      (`cli.py classify`); NEVER touches Gmail or a
#                      token; NOT a member of docker/sudo/adm or any
#                      other privileged group.
# Neither is `georjero`, and `georjero` has no systemctl/sudo rights
# over either. See docs/gmail-ingestion-security-review.md for why: the
# scheduled coding agent (Orca, which only ever runs as georjero) must
# never be the identity that touches Gmail credentials OR runs the
# analysis step, precisely because georjero carries `docker` (and
# `sudo`) group membership that these two new identities deliberately
# do not.
#
# WHAT THIS DOES
#   1. Creates the two identities above, plus two narrow one-way reader
#      groups: `omnissa-readers` (georjero can read ingest's sanitized
#      drop files) and `omnissa-reports-readers` (georjero can read
#      analysis's finished briefs/drafts). Georjero never gets write
#      access to anything either identity owns.
#   2. Deploys a private, root-owned copy of this repo's reviewed `src/`
#      to /opt/omnissa-agent -- NOT writable by georjero, refusing to
#      run at all from an uncommitted/dirty working tree so what gets
#      deployed is always a specific, inspectable git commit.
#   3. MOVES your EXISTING OAuth client_secret.json + token.json from
#      ~/.config/omnissa-agent-google/ into omnissa-ingest's private
#      storage. Nothing is regenerated; no new browser consent needed.
#   4. Installs 4 systemd services + 4 timers (hourly scan, daily
#      07:45/07:55 America/Chicago brief pipeline) -- georjero has no
#      systemctl rights over any of them.
#   5. Runs negative and positive validation tests and prints
#      PASS/FAIL/INFO for each. Does not enable the timers itself.
#
# WHAT THIS DOES NOT DO
#   - Does not touch Gmail, generate new credentials, or send/draft/
#     enroll/purchase anything.
#   - Does not grant georjero any sudo/systemctl rights over either
#     new service.
#   - Does NOT remove georjero from `sudo`/`docker`, and does not
#     change Docker configuration. See the INFO line in the validation
#     output and docs/gmail-ingestion-security-review.md: that remains
#     a SEPARATE, more powerful escalation path this script cannot
#     close, is outside this project's scope, and is your call.
#
# ROLLBACK: infra/omnissa-ingest/rollback-root.sh. Preserves the OAuth
# authorization (moves files back, doesn't delete) and both
# identities' checkpoints.

set -euo pipefail

# Hardened PATH: never trust an inherited PATH that could have been
# influenced by the invoking (georjero) shell environment, even though
# sudo's default secure_path already does this on most systems --
# belt and suspenders for a root-run script.
export PATH="/usr/sbin:/usr/bin:/sbin:/bin"

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo bash $0" >&2
  exit 1
fi

REPO_SRC="/home/georjero/omnissa-agent"
OPT_DIR="/opt/omnissa-agent"
ING_USER="omnissa-ingest"
ING_HOME="/var/lib/omnissa-ingest"
ANA_USER="omnissa-analysis"
ANA_HOME="/var/lib/omnissa-analysis"
DROP_DIR="/var/lib/omnissa-agent/drop"
READ_GROUP="omnissa-readers"
REPORTS_READ_GROUP="omnissa-reports-readers"
CALLER_USER="georjero"
OLD_CRED_DIR="/home/georjero/.config/omnissa-agent-google"

echo "== 0. verify what will actually be deployed =="
if [[ -n "$(git -C "$REPO_SRC" status --porcelain 2>/dev/null)" ]]; then
  echo "REFUSING: $REPO_SRC has uncommitted changes. Commit or stash first --" >&2
  echo "this script deploys whatever is on disk, and an uninspectable, unreviewed" >&2
  echo "diff should never become the code a privileged service runs." >&2
  git -C "$REPO_SRC" status --short >&2
  exit 1
fi
DEPLOY_SHA="$(git -C "$REPO_SRC" rev-parse HEAD)"
echo "Deploying commit: $DEPLOY_SHA"
git -C "$REPO_SRC" log -1 --format='  %h %s' "$DEPLOY_SHA"

echo "== 1. dedicated identities (no supplementary groups -- verified below) =="
getent group "$ING_USER" >/dev/null || groupadd --system "$ING_USER"
id "$ING_USER" >/dev/null 2>&1 || useradd --system --gid "$ING_USER" \
  --home-dir "$ING_HOME" --create-home --shell /usr/sbin/nologin "$ING_USER"

getent group "$ANA_USER" >/dev/null || groupadd --system "$ANA_USER"
id "$ANA_USER" >/dev/null 2>&1 || useradd --system --gid "$ANA_USER" \
  --home-dir "$ANA_HOME" --create-home --shell /usr/sbin/nologin "$ANA_USER"

getent group "$READ_GROUP" >/dev/null || groupadd --system "$READ_GROUP"
getent group "$REPORTS_READ_GROUP" >/dev/null || groupadd --system "$REPORTS_READ_GROUP"
usermod -aG "$READ_GROUP" "$CALLER_USER"                 # georjero reads ingest's drop
usermod -aG "$REPORTS_READ_GROUP" "$CALLER_USER"          # georjero reads analysis's reports
usermod -aG "$READ_GROUP" "$ANA_USER"                     # analysis reads ingest's drop

# explicit, not relying on distro useradd defaults for home-dir modes
chmod 750 "$ING_HOME" "$ANA_HOME"
chown "$ING_USER:$ING_USER" "$ING_HOME"
chown "$ANA_USER:$ANA_USER" "$ANA_HOME"

echo "== 2. private credential storage (0700 dir / 0600 files, no group access) =="
install -d -o "$ING_USER" -g "$ING_USER" -m 0700 "$ING_HOME/google"
install -d -o "$ING_USER" -g "$ING_USER" -m 0700 "$ING_HOME/state"
install -d -o "$ANA_USER" -g "$ANA_USER" -m 0700 "$ANA_HOME/state"
# reports subdir pre-created here (NOT left to cli.py's own mkdir) so it
# gets the shared reader group + setgid from the start
install -d -o "$ANA_USER" -g "$REPORTS_READ_GROUP" -m 2750 "$ANA_HOME/state/reports"

move_credential() { # <src> <dst>
  local src="$1" dst="$2"
  if [[ -f "$dst" ]]; then return 0; fi
  if [[ -L "$src" ]]; then
    echo "REFUSING to move $src: it is a symlink, not a regular file (possible tamper attempt)." >&2
    exit 1
  fi
  if [[ -f "$src" ]]; then
    mv "$src" "$dst"
    echo "moved $(basename "$dst") into private storage (contents not shown)"
  fi
}
move_credential "$OLD_CRED_DIR/client_secret.json" "$ING_HOME/google/client_secret.json"
move_credential "$OLD_CRED_DIR/token.json" "$ING_HOME/google/token.json"
chown "$ING_USER:$ING_USER" "$ING_HOME/google"/*.json 2>/dev/null || true
chmod 600 "$ING_HOME/google"/*.json 2>/dev/null || true

[[ -f "$ING_HOME/google/client_secret.json" ]] || echo "WARNING: no client_secret.json in place yet -- copy it to $ING_HOME/google/client_secret.json (owner $ING_USER, mode 600) before enabling timers." >&2
[[ -f "$ING_HOME/google/token.json" ]] || echo "WARNING: no token.json in place yet." >&2

echo "== 3. drop directory (one-way: $ING_USER writes, $READ_GROUP reads only) =="
install -d -o "$ING_USER" -g "$READ_GROUP" -m 2750 "$DROP_DIR"

echo "== 4. deploy reviewed code to $OPT_DIR (root-owned, not writable by $CALLER_USER) =="
mkdir -p "$OPT_DIR"
if command -v rsync >/dev/null; then
  rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude '.pytest_cache' \
    "$REPO_SRC/src" "$REPO_SRC/pyproject.toml" "$OPT_DIR/"
else
  rm -rf "$OPT_DIR/src"
  cp -r "$REPO_SRC/src" "$OPT_DIR/src"
  cp "$REPO_SRC/pyproject.toml" "$OPT_DIR/pyproject.toml"
fi
chown -R root:root "$OPT_DIR/src" "$OPT_DIR/pyproject.toml"
find "$OPT_DIR/src" -type d -exec chmod 755 {} \;
find "$OPT_DIR/src" -type f -exec chmod 644 {} \;
echo "$DEPLOY_SHA" > "$OPT_DIR/DEPLOYED_SHA"
chmod 644 "$OPT_DIR/DEPLOYED_SHA"

[[ -x "$OPT_DIR/venv/bin/python3" ]] || python3 -m venv "$OPT_DIR/venv"
"$OPT_DIR/venv/bin/pip" install -q -e "$OPT_DIR"
chown -R root:root "$OPT_DIR/venv"
find "$OPT_DIR/venv" -type d -exec chmod 755 {} \;

echo "== 5. install systemd units =="
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-scan.service" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-brief.service" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-scan.timer" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-brief.timer" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-analysis/omnissa-analysis-scan.service" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-analysis/omnissa-analysis-brief.service" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-analysis/omnissa-analysis-scan.timer" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-analysis/omnissa-analysis-brief.timer" /etc/systemd/system/
systemctl daemon-reload

echo "== 6. validation =="
FAIL=0

check() { # <label> <expect_denied yes|no> <command...>
  local label="$1" expect_denied="$2"; shift 2
  if sudo -u "$CALLER_USER" "$@" >/dev/null 2>&1; then
    if [[ "$expect_denied" == "yes" ]]; then echo "FAIL: $label (caller succeeded, should have been denied)"; FAIL=1
    else echo "PASS: $label"; fi
  else
    if [[ "$expect_denied" == "yes" ]]; then echo "PASS: $label (correctly denied)"
    else echo "FAIL: $label (expected to succeed, was denied)"; FAIL=1; fi
  fi
}

echo "-- direct file-permission checks (georjero) --"
check "$CALLER_USER cannot read token.json" yes test -r "$ING_HOME/google/token.json"
check "$CALLER_USER cannot read client_secret.json" yes test -r "$ING_HOME/google/client_secret.json"
check "$CALLER_USER cannot write the deployed ingestion code" yes test -w "$OPT_DIR/src/omnissa_agent/cli.py"
check "$CALLER_USER cannot start the privileged ingest service" yes systemctl start omnissa-ingest-scan.service
check "$CALLER_USER cannot start the analysis service" yes systemctl start omnissa-analysis-scan.service
check "$CALLER_USER cannot read omnissa-analysis's state dir" yes test -r "$ANA_HOME/state"

echo "-- $ANA_USER identity's own privilege surface (must be as restricted as georjero would need to be) --"
ANA_GROUPS="$(id -Gn "$ANA_USER")"
echo "$ANA_USER groups: $ANA_GROUPS"
for bad in docker lxd sudo adm; do
  if echo "$ANA_GROUPS" | tr ' ' '\n' | grep -qx "$bad"; then
    echo "FAIL: $ANA_USER is unexpectedly in the '$bad' group"; FAIL=1
  else
    echo "PASS: $ANA_USER is not in '$bad'"
  fi
done
if sudo -n -l -U "$ANA_USER" 2>&1 | grep -qi "not allowed\|unknown user"; then
  echo "PASS: $ANA_USER has no sudo rules"
else
  echo "FAIL: $ANA_USER may have sudo rules -- inspect manually: sudo -l -U $ANA_USER"; FAIL=1
fi

echo "-- effective privileged-path check (the part a file-permission test alone would miss) --"
echo "INFO: $CALLER_USER (georjero) is a member of 'docker' (and 'sudo'). Docker group"
echo "      membership is root-equivalent on its own (e.g. 'docker run -v /:/host"
echo "      --rm -it alpine chroot /host sh'), independent of any sudo password."
echo "      This means georjero -- and therefore any agent process running as"
echo "      georjero -- could still reach real root and read ANY file on this"
echo "      machine, including everything this script just locked down, THROUGH"
echo "      DOCKER, regardless of every PASS above. This script does not and"
echo "      cannot close that path (removing georjero from docker/sudo is a"
echo "      separate, machine-wide decision -- see the README in this directory"
echo "      for the trade-offs). Treat the boundary above as effective against"
echo "      ACCIDENTAL/CASUAL cross-identity access only, not a deliberate"
echo "      escalation by georjero itself."
echo "STATUS: AGENT PRIVILEGE BOUNDARY = BLOCKED while georjero remains in 'docker'."

if [[ -f "$ING_HOME/google/client_secret.json" && -f "$ING_HOME/google/token.json" ]]; then
  echo "-- positive: $ING_USER can verify account + resolve the exact label --"
  OUT=$(mktemp)
  if sudo -u "$ING_USER" "$OPT_DIR/venv/bin/python3" -m omnissa_agent.cli list-labels \
       --client-secret "$ING_HOME/google/client_secret.json" \
       --token "$ING_HOME/google/token.json" > "$OUT" 2>&1; then
    if grep -q "george@gjh-inc.com" "$OUT" && grep -q "Archive_/@omnissa.com" "$OUT"; then
      echo "PASS: $ING_USER verified george@gjh-inc.com and resolved Archive_/@omnissa.com"
    else
      echo "FAIL: ran but did not confirm account/label -- see $OUT"; FAIL=1
    fi
  else
    echo "FAIL: $ING_USER could not run list-labels -- see $OUT"; FAIL=1
  fi
  [[ "$FAIL" -eq 0 ]] && rm -f "$OUT"
else
  echo "SKIPPED positive ingest test: credentials not yet in place"
fi

echo
if [[ "$FAIL" -eq 0 ]]; then
  cat <<EOM
ALL FILE-PERMISSION VALIDATION CHECKS PASSED. (Docker-group risk remains -- see INFO above; not a PASS/FAIL, a standing note.)

Enable scheduling when you're ready (not done automatically by this script):
  systemctl enable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer
  systemctl enable --now omnissa-analysis-scan.timer omnissa-analysis-brief.timer

Check status any time:
  systemctl list-timers 'omnissa-*'
  systemctl status omnissa-ingest-scan.service omnissa-analysis-scan.service
  journalctl -u omnissa-ingest-scan.service -u omnissa-analysis-scan.service -n 20

Disable if needed:
  systemctl disable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer omnissa-analysis-scan.timer omnissa-analysis-brief.timer
EOM
else
  echo "ONE OR MORE CHECKS FAILED -- do NOT enable the timers. Report this output."
  exit 1
fi
