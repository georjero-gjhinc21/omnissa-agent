"""baseline.py: parses docs/omnissa-partner-baseline.md's machine-
readable block and renders the always-present brief scoreboard."""

from omnissa_agent.agent_a import Finding
from omnissa_agent.baseline import Baseline, load_baseline, render_scoreboard

BLOCK = (
    "some human prose here\n"
    "<!-- SCOREBOARD-DATA\n"
    "sourced: true\n"
    "renewal_status: completed\n"
    "renewal_completed_date: 2026-08-28\n"
    "renewal_next_date: 2026-06-06\n"
    "renewal_case: 01696683\n"
    "osp_requested: 2\n"
    "otsp_requested: 2\n"
    "osp_completed: 0\n"
    "otsp_completed: 0\n"
    "distributor_commercial: unknown\n"
    "distributor_healthcare: unknown\n"
    "distributor_public_sector: unknown\n"
    "distributor_deadline: 2026-07-31\n"
    "top_human_action: complete OSP/OTSP\n"
    "-->\n"
    "more prose after\n"
)


def test_load_baseline_parses_the_block(tmp_path):
    p = tmp_path / "b.md"
    p.write_text(BLOCK)
    baseline = load_baseline(p)
    assert baseline is not None
    assert baseline.sourced is True
    assert baseline.get("renewal_case") == "01696683"


def test_load_baseline_returns_none_for_missing_file(tmp_path):
    assert load_baseline(tmp_path / "does-not-exist.md") is None


def test_load_baseline_returns_none_for_none_path():
    assert load_baseline(None) is None


def test_load_baseline_returns_none_without_the_marker(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("just prose, no scoreboard block")
    assert load_baseline(p) is None


def test_load_baseline_returns_none_for_an_unterminated_block(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("<!-- SCOREBOARD-DATA\nsourced: true\n")  # no closing -->
    assert load_baseline(p) is None


def test_sourced_defaults_to_false_when_absent(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("<!-- SCOREBOARD-DATA\nrenewal_status: completed\n-->\n")
    baseline = load_baseline(p)
    assert baseline.sourced is False


def test_render_scoreboard_with_no_baseline_is_honest_not_a_guess():
    text = render_scoreboard(None, findings=[])
    assert "not available" in text
    assert "docs/omnissa-partner-baseline.md" in text


def test_render_scoreboard_unverified_when_not_marked_sourced(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("<!-- SCOREBOARD-DATA\nrenewal_status: completed\n-->\n")
    baseline = load_baseline(p)
    text = render_scoreboard(baseline, findings=[])
    assert "confidence=Unverified" in text


def test_render_scoreboard_confirmed_when_marked_sourced(tmp_path):
    p = tmp_path / "b.md"
    p.write_text(BLOCK)
    baseline = load_baseline(p)
    text = render_scoreboard(baseline, findings=[])
    assert "confidence=Confirmed (baseline file marked sourced)" in text


def test_render_scoreboard_counts_osp_otsp_mentions_from_this_runs_findings(tmp_path):
    p = tmp_path / "b.md"
    p.write_text(BLOCK)
    baseline = load_baseline(p)
    findings = [
        Finding(message_id="m1", category="Training", confidence="Unverified", summary="Complete your OSP course", source_ref="gmail:m1"),
        Finding(message_id="m2", category="Training", confidence="Unverified", summary="OTSP reminder", source_ref="gmail:m2"),
        Finding(message_id="m3", category="General", confidence="Unverified", summary="unrelated", source_ref="gmail:m3"),
    ]
    text = render_scoreboard(baseline, findings=findings)
    assert "this run's related findings: 2" in text


def test_render_scoreboard_counts_deal_registration_ready_findings():
    findings = [
        Finding(message_id="m1", category="Deal Registration", confidence="Unverified", summary="x", source_ref="gmail:m1"),
        Finding(message_id="m2", category="General", confidence="Unverified", summary="y", source_ref="gmail:m2"),
    ]
    text = render_scoreboard(None, findings=findings)
    assert "Deal-registration-ready findings this run: 1" in text


def test_baseline_get_defaults_to_unknown_for_a_missing_key():
    baseline = Baseline(fields={})
    assert baseline.get("renewal_status") == "unknown"
