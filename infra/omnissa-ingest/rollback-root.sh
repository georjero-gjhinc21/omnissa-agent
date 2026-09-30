#!/usr/bin/env bash
# OPERATOR-RUN ONLY. Requires root. Reverses deploy-root.sh (both the
# omnissa-ingest and omnissa-analysis identities).
# Preserves the OAuth authorization (moves credentials back rather than
# deleting them) and preserves both identities' checkpoints. Does NOT
# touch anything on the Google side -- the refresh token stays valid;
# you can resume with `cli.py run`/`authorize` from georjero's own
# account immediately after this.

set -euo pipefail
export PATH="/usr/sbin:/usr/bin:/sbin:/bin"

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo bash $0" >&2
  exit 1
fi

ING_USER="omnissa-ingest"
ING_HOME="/var/lib/omnissa-ingest"
ANA_USER="omnissa-analysis"
ANA_HOME="/var/lib/omnissa-analysis"
DROP_DIR="/var/lib/omnissa-agent/drop"
READ_GROUP="omnissa-readers"
REPORTS_READ_GROUP="omnissa-reports-readers"
CALLER_USER="georjero"
RESTORE_DIR="/home/georjero/.config/omnissa-agent-google"
CALLER_STATE_DIR="/home/georjero/.local/state/omnissa-agent"

echo "== stop and remove scheduling =="
# Only omnissa-ingest has timers (2) -- omnissa-analysis is activated
# exclusively via OnSuccess=, no independent timer to disable. The
# *.timer entries for omnissa-analysis are removed anyway in case an
# older revision of deploy-root.sh installed them.
systemctl disable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer 2>/dev/null || true
rm -f /etc/systemd/system/omnissa-ingest-scan.service \
      /etc/systemd/system/omnissa-ingest-brief.service \
      /etc/systemd/system/omnissa-ingest-scan.timer \
      /etc/systemd/system/omnissa-ingest-brief.timer \
      /etc/systemd/system/omnissa-analysis-scan.service \
      /etc/systemd/system/omnissa-analysis-brief.service \
      /etc/systemd/system/omnissa-analysis-scan.timer \
      /etc/systemd/system/omnissa-analysis-brief.timer
systemctl daemon-reload

echo "== restore credentials to georjero's own storage (not deleted) =="
mkdir -p "$RESTORE_DIR"
[[ -f "$ING_HOME/google/client_secret.json" ]] && mv "$ING_HOME/google/client_secret.json" "$RESTORE_DIR/client_secret.json"
[[ -f "$ING_HOME/google/token.json" ]] && mv "$ING_HOME/google/token.json" "$RESTORE_DIR/token.json"
chown georjero:georjero "$RESTORE_DIR"/*.json 2>/dev/null || true
chmod 600 "$RESTORE_DIR"/*.json 2>/dev/null || true

echo "== preserve both identities' checkpoints/reports/unconsumed drop data =="
mkdir -p "$CALLER_STATE_DIR"
[[ -f "$ING_HOME/state/checkpoint.json" ]] && \
  cp "$ING_HOME/state/checkpoint.json" "$CALLER_STATE_DIR/checkpoint.from-ingest-identity.json"
[[ -f "$ANA_HOME/state/checkpoint.json" ]] && \
  cp "$ANA_HOME/state/checkpoint.json" "$CALLER_STATE_DIR/checkpoint.from-analysis-identity.json"
if [[ -d "$ANA_HOME/state/reports" ]]; then
  mkdir -p "$CALLER_STATE_DIR/reports-from-analysis-identity"
  cp -r "$ANA_HOME/state/reports/." "$CALLER_STATE_DIR/reports-from-analysis-identity/" 2>/dev/null || true
fi
# Do NOT delete the drop directory's only copy sight-unseen: a drop file
# that ingest wrote but analysis hasn't yet consumed (e.g. rollback runs
# in the narrow window between an ingest success and its OnSuccess=
# analysis run finishing) would otherwise be lost. Preserve every file
# found there, whether or not it looks consumed -- cheap, and there is
# no reliable local signal for "already read" to filter on.
if [[ -d "$DROP_DIR" ]] && [[ -n "$(ls -A "$DROP_DIR" 2>/dev/null)" ]]; then
  mkdir -p "$CALLER_STATE_DIR/drop-from-ingest-identity"
  cp -r "$DROP_DIR/." "$CALLER_STATE_DIR/drop-from-ingest-identity/" 2>/dev/null || true
  echo "preserved $(ls "$DROP_DIR" | wc -l) drop file(s) to $CALLER_STATE_DIR/drop-from-ingest-identity/"
fi
chown -R georjero:georjero "$CALLER_STATE_DIR" 2>/dev/null || true

echo "== remove the deployed code copy and drop directory (contents already preserved above; contain no secrets either way) =="
rm -rf /opt/omnissa-agent
rm -rf "$DROP_DIR"

echo "== remove the service identities =="
userdel "$ING_USER" 2>/dev/null || true
groupdel "$ING_USER" 2>/dev/null || true
userdel "$ANA_USER" 2>/dev/null || true
groupdel "$ANA_USER" 2>/dev/null || true
gpasswd -d "$CALLER_USER" "$READ_GROUP" 2>/dev/null || true
gpasswd -d "$CALLER_USER" "$REPORTS_READ_GROUP" 2>/dev/null || true
groupdel "$READ_GROUP" 2>/dev/null || true
groupdel "$REPORTS_READ_GROUP" 2>/dev/null || true

echo "Rollback complete. Credentials restored to $RESTORE_DIR."
echo "Preserved checkpoints/reports (if any) copied under $CALLER_STATE_DIR."
echo "Resume manual use with: python3 -m omnissa_agent.cli run --kind scan --client-secret $RESTORE_DIR/client_secret.json --token $RESTORE_DIR/token.json"
