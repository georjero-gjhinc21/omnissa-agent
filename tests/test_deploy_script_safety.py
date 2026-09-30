"""Static safety checks on the operator-run root scripts and units.

This can't execute the privileged parts (no root in this environment),
but it CAN catch regressions in properties that matter: PATH hardening,
refusing a dirty source tree, symlink-safety on credential moves, the
analysis identity existing and staying out of privileged groups, real
OnSuccess= chaining (not a decorative After=), split status reporting,
rollback data preservation, and the unit files using absolute paths /
real hardening directives. A future edit that silently drops one of
these should fail this suite.
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
# Analysis has NO independent timer -- it is activated exclusively by
# the matching ingest service's OnSuccess=. Only 2 files, not 4.
ANALYSIS_UNITS = [
    INFRA / "omnissa-analysis" / f"omnissa-analysis-{kind}.service"
    for kind in ("scan", "brief")
]
ALL_UNITS = INGEST_UNITS + ANALYSIS_UNITS


def test_exactly_six_units_ship_not_eight():
    assert len(ALL_UNITS) == 6, (
        "analysis was redesigned to have no independent timer (OnSuccess= "
        "chaining instead) -- if this grows back to 8, the ordering fix "
        "was probably reverted"
    )
    for u in ALL_UNITS:
        assert u.exists(), u
    # and confirm no stray analysis timer files were left behind in the repo
    assert not (INFRA / "omnissa-analysis" / "omnissa-analysis-scan.timer").exists()
    assert not (INFRA / "omnissa-analysis" / "omnissa-analysis-brief.timer").exists()


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
    for bad_group_line in ("usermod -aG docker", "usermod -aG sudo", "usermod -aG adm"):
        assert bad_group_line not in text.replace("ANA_USER", "omnissa-analysis")


def test_deploy_validates_analysis_identity_has_no_privileged_groups():
    text = DEPLOY.read_text()
    assert "for bad in docker lxd sudo adm" in text, (
        "must actively check the analysis identity for privileged group membership, "
        "not just assume useradd got it right"
    )


def test_deploy_splits_scheduled_agent_isolation_from_operator_account_risk():
    """The two findings must never be reported as one merged verdict --
    the operator explicitly asked not to see the scheduled-agent
    boundary labeled BLOCKED just because georjero has Docker access."""
    text = DEPLOY.read_text()
    assert "SCHEDULED-AGENT ISOLATION: PASS" in text
    assert "SCHEDULED-AGENT ISOLATION: FAIL" in text
    assert "OPERATOR ACCOUNT RISK" in text
    assert "root-equivalent" in text.lower()
    # the old merged wording must be gone, not just supplemented
    assert "AGENT PRIVILEGE BOUNDARY = BLOCKED" not in text


def test_deploy_never_requires_removing_docker_for_isolation_to_pass():
    """Isolation PASS must not be conditioned on georjero's group
    membership -- the FAIL variable is only ever set by the actual
    cross-identity/privileged-group checks, never by the docker finding."""
    text = DEPLOY.read_text()
    # the docker/sudo disclosure block (the printed runtime one, not its
    # mention in the header comment) must not contain a FAIL=1 assignment
    disclosure_start = text.index("OPERATOR ACCOUNT RISK (separate, pre-existing")
    disclosure_end = text.index("======================================================================", disclosure_start)
    disclosure_block = text[disclosure_start:disclosure_end]
    assert "FAIL=1" not in disclosure_block


def test_deploy_does_not_use_break_system_packages_or_disable_venv_safety():
    text = DEPLOY.read_text()
    assert "--break-system-packages" not in text


def test_deploy_removes_stale_analysis_timers_on_reinstall():
    text = DEPLOY.read_text()
    assert "rm -f /etc/systemd/system/omnissa-analysis-scan.timer" in text


def test_rollback_preserves_checkpoints_reports_and_unconsumed_drop_data():
    text = ROLLBACK.read_text()
    assert "checkpoint.from-ingest-identity.json" in text
    assert "checkpoint.from-analysis-identity.json" in text
    assert "reports-from-analysis-identity" in text
    assert "drop-from-ingest-identity" in text, (
        "must not delete the only copy of a fetched-but-unprocessed drop file -- "
        "preserve it before removing the drop directory"
    )
    # the preservation copy must happen BEFORE the destructive rm -rf of DROP_DIR
    preserve_idx = text.index("drop-from-ingest-identity")
    delete_idx = text.index('rm -rf "$DROP_DIR"')
    assert preserve_idx < delete_idx, "drop data must be copied out before it's deleted"
    assert "mv \"$ING_HOME/google/client_secret.json\"" in text  # credentials moved back, not deleted


def test_all_units_exist_and_use_absolute_python_path():
    for unit in ALL_UNITS:
        assert unit.exists(), unit
        text = unit.read_text()
        if unit.suffix == ".service":
            assert "/opt/omnissa-agent/venv/bin/python3" in text
            assert "User=" in text and "Group=" in text
            assert "NoNewPrivileges=yes" in text
            assert "CapabilityBoundingSet=" in text


def test_analysis_services_restrict_network_to_loopback_by_default():
    for unit in ANALYSIS_UNITS:
        text = unit.read_text()
        assert "IPAddressDeny=any" in text
        assert "IPAddressAllow=localhost" in text


def test_ingest_services_do_not_restrict_network_since_they_need_real_internet():
    for unit in (INFRA / "omnissa-ingest" / "omnissa-ingest-scan.service",
                 INFRA / "omnissa-ingest" / "omnissa-ingest-brief.service"):
        text = unit.read_text()
        assert "IPAddressDeny=any" not in text


def test_ingest_timers_are_persistent_for_missed_run_catchup():
    for unit in INGEST_UNITS:
        if unit.suffix == ".timer":
            assert "Persistent=true" in unit.read_text()


def test_ingest_services_chain_to_analysis_via_onsuccess_not_a_fixed_timer():
    """The real fix for 'a brief must not read a partially-written drop
    file / failed ingestion must not yield a success-looking brief':
    analysis runs only immediately after a clean (exit 0) ingest, via
    systemd's own OnSuccess=, not via an independently-scheduled timer
    that could race or process something failed/partial."""
    scan = (INFRA / "omnissa-ingest" / "omnissa-ingest-scan.service").read_text()
    brief = (INFRA / "omnissa-ingest" / "omnissa-ingest-brief.service").read_text()
    assert "OnSuccess=omnissa-analysis-scan.service" in scan
    assert "OnSuccess=omnissa-analysis-brief.service" in brief

    ana_scan = (INFRA / "omnissa-analysis" / "omnissa-analysis-scan.service").read_text()
    ana_brief = (INFRA / "omnissa-analysis" / "omnissa-analysis-brief.service").read_text()
    # confirm the analysis units document that they are NOT independently timer-driven
    assert "NOT independently timer-triggered" in ana_scan
    assert "NOT independently timer-triggered" in ana_brief


def test_ingest_writes_are_atomic_so_a_reader_never_sees_a_partial_file():
    """Cross-check against the actual ingest implementation, not just the
    unit file -- the atomic rename is what really prevents a torn read,
    independent of any systemd ordering."""
    cli_src = (Path(__file__).resolve().parent.parent / "src" / "omnissa_agent" / "cli.py").read_text()
    assert "tmp_path.replace(out_path)" in cli_src, (
        "ingest must write to a temp path and atomically replace the real "
        "drop file -- a reader must never observe a partially-written file"
    )


def test_units_reference_the_privilege_separated_cli_commands():
    ingest_services = [u for u in INGEST_UNITS if u.suffix == ".service"]
    for u in ingest_services:
        assert "omnissa_agent.cli ingest" in u.read_text()
    for u in ANALYSIS_UNITS:
        assert "omnissa_agent.cli classify" in u.read_text()
        assert "--report-group-readable" in u.read_text()
        assert "--max-age-s" in u.read_text(), "must refuse stale drop data, not just old-but-present"


def test_shell_syntax_is_valid():
    for p in (DEPLOY, ROLLBACK):
        result = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
        assert result.returncode == 0, f"{p} has a syntax error:\n{result.stderr}"
