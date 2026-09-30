#!/usr/bin/env bash
# Read-only status view for the omnissa-agent 24x7 service, safe to run
# from any georjero shell -- including an Orca terminal, which is just
# a normal shell running as georjero. No sudo, no privileged reads, no
# Gmail credential or private-identity-state access -- everything here
# uses permissions georjero already has as part of the deployed design
# (infra/omnissa-ingest/deploy-root.sh's own `usermod -aG
# omnissa-readers,omnissa-reports-readers georjero`), not anything
# granted for Orca specifically.
#
# What this deliberately does NOT show, and why:
#   - Gmail message ids/subjects from `journalctl` -- georjero's `adm`
#     group membership CAN read the journal, but a running scan/brief
#     currently logs real subject lines there (a known, tracked gap --
#     see docs/operations-record.md). Until that's fixed, the sanitized
#     report files below are the correct human-facing surface, not the
#     journal. Use `sudo journalctl -u omnissa-ingest-scan.service` only
#     if you deliberately need raw diagnostic detail.
#   - Backlog/pending count (`reconcile`) -- reading it requires reading
#     BOTH omnissa-ingest's and omnissa-analysis's private checkpoints,
#     neither of which georjero can read. That's correct isolation, not
#     a bug. Run `sudo python3 -m omnissa_agent.cli reconcile ...` (see
#     docs/operations-record.md §5) if you need that number.
#
# NOTE: if this was just deployed/regranted, group membership only
# takes effect in a NEW shell session (new login, new Orca terminal,
# `newgrp <group>`) -- not in one already open from before the grant.

set -uo pipefail

echo "=== Timers ==="
systemctl list-timers 'omnissa-*' --no-pager 2>&1 || echo "(could not read timer list)"

echo
echo "=== Last run per unit ==="
for unit in omnissa-ingest-scan.service omnissa-analysis-scan.service \
            omnissa-ingest-brief.service omnissa-analysis-brief.service; do
  line=$(systemctl show "$unit" --no-pager \
    -p ActiveState -p SubState -p Result -p ExecMainStatus -p ExecMainStartTimestamp 2>&1)
  if [[ -z "$line" ]]; then
    echo "$unit: (could not read unit status)"
  else
    echo "$unit:"
    echo "$line" | sed 's/^/  /'
  fi
done

echo
echo "=== Reports (sanitized brief output, newest first) ==="
REPORTS_DIR="/var/lib/omnissa-agent/reports"
if [[ ! -r "$REPORTS_DIR" || ! -x "$REPORTS_DIR" ]]; then
  echo "Cannot read $REPORTS_DIR."
  echo "If deploy-root.sh ran recently, start a NEW shell/terminal --"
  echo "group membership grants don't apply to already-open sessions."
else
  reports=$(ls -t "$REPORTS_DIR"/*.md 2>/dev/null)
  if [[ -z "$reports" ]]; then
    echo "(no reports yet)"
  else
    echo "$reports" | head -10
    latest=$(echo "$reports" | head -1)
    echo
    echo "--- latest report: $latest ---"
    cat "$latest"
  fi
fi
