"""partner_agent is the new public package name -- a thin re-export
alias over omnissa_agent (see docs/rename-to-partner-agent.md for the
not-yet-executed plan to rename the underlying implementation and
deployed systemd units too). Both module paths must keep working."""

import importlib
import subprocess
import sys

from omnissa_agent.agent_a import AgentAResult, build_partner_ops_brief


def test_both_module_paths_import_cleanly():
    omnissa_cli = importlib.import_module("omnissa_agent.cli")
    partner_cli = importlib.import_module("partner_agent.cli")
    assert omnissa_cli.main is partner_cli.main  # same function object, not a copy


def test_partner_agent_re_exports_are_the_same_objects_not_copies():
    """A re-export, not a fork -- importing via partner_agent must
    never give a caller a second, independently-drifting copy of a
    class/function/constant."""
    from omnissa_agent.baseline import Baseline as OldBaseline
    from omnissa_agent.gmail_ingest import EXPECTED_ACCOUNT as OLD_ACCOUNT
    from partner_agent.baseline import Baseline as NewBaseline
    from partner_agent.gmail_ingest import EXPECTED_ACCOUNT as NEW_ACCOUNT

    assert NewBaseline is OldBaseline
    assert NEW_ACCOUNT == OLD_ACCOUNT == "george@gjh-inc.com"


def test_partner_agent_loads_the_same_real_partner_config():
    from partner_agent.partners import load_partner_allowlist

    ids = {p.id for p in load_partner_allowlist()}
    assert "omnissa" in ids and len(ids) == 15


def test_brief_header_says_partner_ops_not_omnissa_daily_brief():
    result = AgentAResult(findings=[])
    brief = build_partner_ops_brief(result, email_status="VERIFIED", partner_ids=["omnissa"])
    assert "Partner Ops" in brief.splitlines()[0]
    assert "Omnissa Daily Brief" not in brief


def test_cli_help_works_via_both_module_paths():
    """python3 -m omnissa_agent.cli --help and python3 -m
    partner_agent.cli --help must both actually run, exit 0, and print
    usage -- not just import without error."""
    for module in ("omnissa_agent.cli", "partner_agent.cli"):
        proc = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            capture_output=True, text=True, cwd="src",
        )
        assert proc.returncode == 0, f"{module} --help failed: {proc.stderr}"
        assert "usage:" in proc.stdout
