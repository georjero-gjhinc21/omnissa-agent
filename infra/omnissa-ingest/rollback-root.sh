#!/usr/bin/env bash
# OPERATOR-RUN ONLY. Requires root. Reverses deploy-root.sh.
# Preserves the OAuth authorization (moves credentials back rather than
# deleting them) and preserves the ingest-side checkpoint (copies it
# next to the credentials so dedup history isn't silently lost).
# Does NOT touch anything on the Google side -- the refresh token stays
# valid; you can resume with `cli.py run`/`authorize` from georjero's
# own account immediately after this.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo bash $0" >&2
  exit 1
fi

ING_USER="omnissa-ingest"
ING_HOME="/var/lib/omnissa-ingest"
DROP_DIR="/var/lib/omnissa-agent/drop"
READ_GROUP="omnissa-readers"
CALLER_USER="georjero"
RESTORE_DIR="/home/georjero/.config/omnissa-agent-google"
CALLER_STATE_DIR="/home/georjero/.local/state/omnissa-agent"

echo "== stop and remove scheduling =="
systemctl disable --now omnissa-ingest-scan.timer omnissa-ingest-brief.timer 2>/dev/null || true
rm -f /etc/systemd/system/omnissa-ingest-scan.service \
      /etc/systemd/system/omnissa-ingest-brief.service \
      /etc/systemd/system/omnissa-ingest-scan.timer \
      /etc/systemd/system/omnissa-ingest-brief.timer
systemctl daemon-reload

echo "== restore credentials to georjero's own storage (not deleted) =="
mkdir -p "$RESTORE_DIR"
[[ -f "$ING_HOME/google/client_secret.json" ]] && mv "$ING_HOME/google/client_secret.json" "$RESTORE_DIR/client_secret.json"
[[ -f "$ING_HOME/google/token.json" ]] && mv "$ING_HOME/google/token.json" "$RESTORE_DIR/token.json"
chown georjero:georjero "$RESTORE_DIR"/*.json 2>/dev/null || true
chmod 600 "$RESTORE_DIR"/*.json 2>/dev/null || true

echo "== preserve the ingest-side checkpoint (merge manually if you want combined dedup history) =="
if [[ -f "$ING_HOME/state/checkpoint.json" ]]; then
  mkdir -p "$CALLER_STATE_DIR"
  cp "$ING_HOME/state/checkpoint.json" "$CALLER_STATE_DIR/checkpoint.from-ingest-identity.json"
  chown georjero:georjero "$CALLER_STATE_DIR/checkpoint.from-ingest-identity.json"
fi

echo "== remove the deployed code copy and drop directory (contains no secrets) =="
rm -rf /opt/omnissa-agent
rm -rf "$DROP_DIR"

echo "== remove the service identity =="
userdel "$ING_USER" 2>/dev/null || true
groupdel "$ING_USER" 2>/dev/null || true
gpasswd -d "$CALLER_USER" "$READ_GROUP" 2>/dev/null || true
groupdel "$READ_GROUP" 2>/dev/null || true

echo "Rollback complete. Credentials restored to $RESTORE_DIR."
echo "Resume manual use with: python3 -m omnissa_agent.cli run --kind scan --client-secret $RESTORE_DIR/client_secret.json --token $RESTORE_DIR/token.json"
