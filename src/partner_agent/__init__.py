"""partner_agent -- public package name for GJH INC's partner-ops agent.

Omnissa is one configured ``partner_id`` (see config/partners.yaml),
not the product. This package is a thin re-export alias: every module
here does ``from omnissa_agent.<name> import *``, so implementation
stays in one place (``src/omnissa_agent/``, still what the deployed
systemd units actually run -- see docs/rename-to-partner-agent.md for
the not-yet-executed plan to rename those too) while new code/docs can
import/run ``partner_agent`` today. Both names keep working:

    python3 -m omnissa_agent.cli --help
    python3 -m partner_agent.cli --help
"""
