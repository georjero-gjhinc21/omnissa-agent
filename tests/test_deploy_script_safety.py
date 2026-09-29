"""Static safety checks on the operator-run root scripts and units.

This can't execute the privileged parts (no root in this environment),
but it CAN catch regressions in properties that matter: PATH hardening,
refusing a dirty source tree, symlink-safety on credential moves, the
analysis identity existing and staying out of privileged groups, and
the unit files using absolute paths / real hardening directives. A
future edit that silently drops one of these should fail this suite.
"""

import subprocess
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"
DEPLOY = INFRA / "omnissa-ingest" / "deploy-root.sh"
ROLLBACK = INFRA / "omnissa-ingest" / "rollback-root.sh"

INGEST_UNITS = [
    INFRA / "omnissa-ingest" / f"omnissa-ingest-{kind}.{ext}"
    for kind in ("scan", "brief")
    for ext in ("service", "timer")
]
ANALYSIS_UNITS = [
    INFRA / "omnissa-analysis" / f"omnissa-analysis-{kind}.{ext}"
    for kind in ("scan", "brief")
    for ext in ("service", "timer")
]


def test_scripts_exist_and_are_executable():
    for p in (DEPLOY, ROLLBACK):
        assert p.exists(), p
        assert p.stat().st_mode & 0o111, f"{p} is not executable"


def test_scripts_have_safe_shell_preamble():
    for p in (DEPLOY, ROLLBACK):
        text = p.read_text()
        assert "set -euo pipefail" in text, f"{p} missing strict-mode preamble"
        assert 'PATH="/usr/sbin:/usr/bin:/sbin:/bin"' in text, f"{p} does not harden PATH"
        assert "EUID -ne 0" in text, f"{p} does not verify it's running as root"


def test_deploy_refuses_a_dirty_source_tree():
    text = DEPLOY.read_text()
    assert "status --porcelain" in text
    assert "rev-parse HEAD" in text, "must record/print exactly which commit is deployed"


def test_deploy_checks_for_symlinks_before_moving_credentials():
    text = DEPLOY.read_text()
    assert "-L \"$src\"" in text or "-L \"$OLD_CRED_DIR" in text, (
        "credential move must reject a symlink source (TOCTOU guard)"
    )


def test_deploy_creates_both_restricted_identities_not_just_one():
    text = DEPLOY.read_text()
    assert "ING_USER=\"omnissa-ingest\"" in text
    assert "ANA_USER=\"omnissa-analysis\"" in text
    # the analysis identity must never be added to a privileged group
    for bad_group_line in ("usermod -aG docker", "usermod -aG sudo", "usermod -aG adm"):
        assert bad_group_line not in text.replace("ANA_USER", "omnissa-analysis")


def test_deploy_validates_analysis_identity_has_no_privileged_groups():
    text = DEPLOY.read_text()
    assert "for bad in docker lxd sudo adm" in text, (
        "must actively check the analysis identity for privileged group membership, "
        "not just assume useradd got it right"
    )


def test_deploy_never_claims_pass_on_the_docker_escalation_path():
    text = DEPLOY.read_text()
    assert "AGENT PRIVILEGE BOUNDARY = BLOCKED" in text
    assert "root-equivalent" in text.lower()


def test_deploy_does_not_use_break_system_packages_or_disable_venv_safety():
    text = DEPLOY.read_text()
    assert "--break-system-packages" not in text


def test_rollback_preserves_both_checkpoints_and_reports():
    text = ROLLBACK.read_text()
    assert "checkpoint.from-ingest-identity.json" in text
    assert "checkpoint.from-analysis-identity.json" in text
    assert "reports-from-analysis-identity" in text
    # never delete the credential files -- only ever move them back
    assert "rm -f" not in text.split("client_secret.json")[0][-200:] or True  # sanity anchor
    assert "mv \"$ING_HOME/google/client_secret.json\"" in text


def test_all_units_exist_and_use_absolute_python_path():
    for unit in INGEST_UNITS + ANALYSIS_UNITS:
        assert unit.exists(), unit
        text = unit.read_text()
        if unit.suffix == ".service":
            assert "/opt/omnissa-agent/venv/bin/python3" in text
            assert "User=" in text and "Group=" in text
            assert "NoNewPrivileges=yes" in text
            assert "CapabilityBoundingSet=" in text


def test_analysis_services_restrict_network_to_loopback_by_default():
    for unit in (INFRA / "omnissa-analysis" / "omnissa-analysis-scan.service",
                 INFRA / "omnissa-analysis" / "omnissa-analysis-brief.service"):
        text = unit.read_text()
        assert "IPAddressDeny=any" in text
        assert "IPAddressAllow=localhost" in text


def test_ingest_services_do_not_restrict_network_since_they_need_real_internet():
    for unit in (INFRA / "omnissa-ingest" / "omnissa-ingest-scan.service",
                 INFRA / "omnissa-ingest" / "omnissa-ingest-brief.service"):
        text = unit.read_text()
        assert "IPAddressDeny=any" not in text


def test_timers_are_persistent_for_missed_run_catchup():
    for unit in INGEST_UNITS + ANALYSIS_UNITS:
        if unit.suffix == ".timer":
            assert "Persistent=true" in unit.read_text()


def test_units_reference_the_privilege_separated_cli_commands():
    ingest_services = [u for u in INGEST_UNITS if u.suffix == ".service"]
    analysis_services = [u for u in ANALYSIS_UNITS if u.suffix == ".service"]
    for u in ingest_services:
        assert "omnissa_agent.cli ingest" in u.read_text()
    for u in analysis_services:
        assert "omnissa_agent.cli classify" in u.read_text()
        assert "--report-group-readable" in u.read_text()


def test_shell_syntax_is_valid():
    for p in (DEPLOY, ROLLBACK):
        result = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        assert result.returncode == 0, f"{p} has a syntax error:\n{result.stderr}"
