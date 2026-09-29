#!/usr/bin/env bash
# OPERATOR-RUN ONLY. Requires root (run with: sudo bash deploy-root.sh).
# Idempotent -- safe to re-run. Never displays credential file contents.
# The agent that wrote this cannot run it (no sudo password available to
# that session) -- that is the point of this being a separate handoff.
#
# WHAT THIS DOES
#   1. Creates a dedicated, unprivileged, no-login system identity
#      `omnissa-ingest` that owns the Gmail OAuth credential and is the
#      ONLY identity that ever runs Gmail-touching code.
#   2. Creates a read-only `omnissa-readers` group and adds georjero to
#      it -- a one-way handoff: georjero can read the SANITIZED drop
#      files this identity produces, never the credentials directory.
#   3. Deploys a private, root-owned copy of the reviewed omnissa_agent
#      source (from this repo, at whatever commit you run this from) to
#      /opt/omnissa-agent -- NOT writable by georjero, so georjero (or
#      any agent running as georjero) cannot tamper with the code a
#      privileged systemd service executes.
#   4. MOVES your EXISTING OAuth client_secret.json + token.json from
#      ~/.config/omnissa-agent-google/ into the new private location.
#      Nothing is regenerated; no new browser consent is needed.
#   5. Installs 2 systemd services + 2 timers (hourly scan, daily
#      07:50 America/Chicago brief-window fetch) running ONLY as
#      omnissa-ingest -- georjero is granted no systemctl rights over
#      them at all.
#   6. Runs negative and positive validation tests and prints PASS/FAIL
#      for each. Does not enable the timers itself -- review the output
#      first.
#
# WHAT THIS DOES NOT DO
#   - Does not touch Gmail, generate new credentials, or send/draft/
#     enroll/purchase anything.
#   - Does not grant georjero any sudo/systemctl rights over the new
#     service.
#   - Does NOT remove georjero from the `sudo` or `docker` groups. See
#     the NOTE near the bottom: those remain a SEPARATE, more powerful
#     escalation path this script cannot close, because georjero (and
#     therefore any agent running as georjero) can already reach real
#     root via `docker run -v /:/host ...` regardless of file
#     permissions on omnissa-ingest's files. Closing that is your call,
#     affects things outside this project, and is not attempted here.
#
# ROLLBACK: see rollback-root.sh in this same directory. It preserves
# the OAuth authorization (moves the files back, doesn't delete them)
# and preserves the ingest-side checkpoint.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo bash $0" >&2
  exit 1
fi

REPO_SRC="/home/georjero/omnissa-agent"
OPT_DIR="/opt/omnissa-agent"
ING_USER="omnissa-ingest"
ING_HOME="/var/lib/omnissa-ingest"
DROP_DIR="/var/lib/omnissa-agent/drop"
READ_GROUP="omnissa-readers"
CALLER_USER="georjero"
OLD_CRED_DIR="/home/georjero/.config/omnissa-agent-google"

echo "== 1. dedicated service identity =="
getent group "$ING_USER" >/dev/null || groupadd --system "$ING_USER"
id "$ING_USER" >/dev/null 2>&1 || useradd --system --gid "$ING_USER" \
  --home-dir "$ING_HOME" --create-home --shell /usr/sbin/nologin "$ING_USER"
getent group "$READ_GROUP" >/dev/null || groupadd --system "$READ_GROUP"
usermod -aG "$READ_GROUP" "$CALLER_USER"

echo "== 2. private credential storage (0700 dir / 0600 files, no group access) =="
install -d -o "$ING_USER" -g "$ING_USER" -m 0700 "$ING_HOME/google"
install -d -o "$ING_USER" -g "$ING_USER" -m 0700 "$ING_HOME/state"

if [[ -f "$OLD_CRED_DIR/client_secret.json" && ! -f "$ING_HOME/google/client_secret.json" ]]; then
  mv "$OLD_CRED_DIR/client_secret.json" "$ING_HOME/google/client_secret.json"
  echo "moved client_secret.json into private storage (contents not shown)"
fi
if [[ -f "$OLD_CRED_DIR/token.json" && ! -f "$ING_HOME/google/token.json" ]]; then
  mv "$OLD_CRED_DIR/token.json" "$ING_HOME/google/token.json"
  echo "moved token.json into private storage (contents not shown)"
fi
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

[[ -x "$OPT_DIR/venv/bin/python3" ]] || python3 -m venv "$OPT_DIR/venv"
"$OPT_DIR/venv/bin/pip" install -q -e "$OPT_DIR"
chown -R root:root "$OPT_DIR/venv"
find "$OPT_DIR/venv" -type d -exec chmod 755 {} \;

echo "== 5. install systemd units =="
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-scan.service" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-brief.service" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-scan.timer" /etc/systemd/system/
install -m 644 "$REPO_SRC/infra/omnissa-ingest/omnissa-ingest-brief.timer" /etc/systemd/system/
systemctl daemon-reload

echo "== 6. validation =="
FAIL=0

check() { # <label> <expect_fail_as_caller> <command...>
  local label="$1"; local expect_denied="$2"; shift 2
  if sudo -u "$CALLER_USER" "$@" >/dev/null 2>&1; then
    if [[ "$expect_denied" == "yes" ]]; then
      echo "FAIL: $label (caller succeeded but should have been denied)"; FAIL=1
    else
      echo "PASS: $label"
    fi
  else
    if [[ "$expect_denied" == "yes" ]]; then
      echo "PASS: $label (correctly denied)"
    else
      echo "FAIL: $label (expected to succeed, was denied)"; FAIL=1
    fi
  fi
}

check "$CALLER_USER cannot read token.json" yes test -r "$ING_HOME/google/token.json"
check "$CALLER_USER cannot read client_secret.json" yes test -r "$ING_HOME/google/client_secret.json"
check "$CALLER_USER cannot write the deployed ingestion code" yes test -w "$OPT_DIR/src/omnissa_agent/cli.py"
check "$CALLER_USER cannot start the privileged service" yes systemctl start omnissa-ingest-scan.service

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
  echo "SKIPPED positive test: credentials not yet in place"
fi

echo
if [[ "$FAIL" -eq 0 ]]; then
  cat <<'EOM'
ALL VALIDATION CHECKS PASSED.

Enable scheduling when you're ready (not done automatically by this script):
  systemctl enable --now omnissa-ingest-scan.timer
  systemctl enable --now omnissa-ingest-brief.timer

Check status any time:
  systemctl list-timers 'omnissa-ingest-*'
  systemctl status omnissa-ingest-scan.service
  journalctl -u omnissa-ingest-scan.service -n 20

Disable if needed:
  systemctl disable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer
EOM
else
  echo "ONE OR MORE CHECKS FAILED -- do NOT enable the timers. Report this output."
  exit 1
fi

cat <<'EOM'

NOTE -- residual risk this script does not and cannot close:
georjero is a member of the `sudo` and `docker` groups. Docker group
membership is root-equivalent (e.g. `docker run -v /:/host --rm -it
alpine chroot /host sh`) independent of any sudo password. This means
georjero -- and therefore any agent process running as georjero,
including Claude/Orca -- can already reach real root on this machine
through Docker, which would let it read omnissa-ingest's files despite
everything above. This script defends against ACCIDENTAL or CASUAL
cross-user access (a buggy or over-eager agent trying to open the
token file finds it genuinely inaccessible at the permission layer);
it does not defend against a DELIBERATE privilege-escalation attempt
by that same identity. Closing that fully requires removing georjero
from `docker` (and/or `sudo`), which affects things outside this
project and was not done here.
EOM
