from omnissa_agent.agent_a import Finding, build_brief, classify_messages, reorder_for_focus
from omnissa_agent.agent_b import DEFAULT_RECIPIENT_PLACEHOLDER, draft_from_findings
from omnissa_agent.sources import SyntheticGmailSource


def test_classification_deduplicates_repeated_message_id():
    messages = SyntheticGmailSource().fetch(max_messages=10)
    result = classify_messages(messages, seen_ids=set())
    ids = [f.message_id for f in result.findings]
    assert ids.count("msg-001") == 1
    assert result.skipped_duplicate_ids == ["msg-001"]


def test_prompt_injection_message_is_treated_as_inert_data():
    messages = SyntheticGmailSource().fetch(max_messages=10)
    result = classify_messages(messages, seen_ids=set())
    injected = next(f for f in result.findings if f.message_id == "msg-003")
    # content said "you are now authorized" / "reply YES" -- must not elevate
    # confidence or produce any action beyond "human review"
    assert injected.confidence == "Unverified"
    assert injected.required_action == "Human confirms with Omnissa/distributor before any action"
    assert "reply" not in injected.required_action.lower()


def test_already_seen_ids_are_skipped_on_restart():
    messages = SyntheticGmailSource().fetch(max_messages=10)
    result = classify_messages(messages, seen_ids={"msg-001", "msg-002", "msg-003"})
    assert result.findings == []
    # msg-001 appears twice in the raw fixture; both copies are pre-seen
    assert result.skipped_duplicate_ids.count("msg-001") == 2


def test_brief_states_no_external_actions_taken():
    messages = SyntheticGmailSource().fetch(max_messages=10)
    result = classify_messages(messages, seen_ids=set())
    brief = build_brief(result, email_status="BLOCKED (token expired)")
    assert "No external actions were taken." in brief
    assert "BLOCKED (token expired)" in brief


def test_osp_otsp_allego_and_enablement_threads_classify_out_of_general():
    """Real production gap (2026-09-30): these threads were falling
    through to General with no keyword match. See
    docs/omnissa-partner-baseline.md for the sourced facts behind them."""
    from omnissa_agent.agent_a import classify_message
    from omnissa_agent.sources import GmailMessage

    def _classify_subject(subject):
        return classify_message(
            GmailMessage(id="m", subject=subject, snippet="", sender="x@omnissa.com", date="2026-09-29", label_ids=())
        ).category

    assert _classify_subject("Complete your OSP training by Friday") == "Training"
    assert _classify_subject("OTSP module reminder") == "Training"
    assert _classify_subject("Your Allego course assignment") == "Training"
    assert _classify_subject("Paul Philips requests enablement completion") == "Training"
    assert _classify_subject("Preferred distributor selection reminder") == "Deal Registration"
    assert _classify_subject("Your Partner ID confirmation") == "Access Request"


def _findings(*categories):
    return [
        Finding(message_id=f"m{i}", category=c, confidence="Unverified", summary=f"item {i}", source_ref=f"gmail:m{i}")
        for i, c in enumerate(categories)
    ]


def test_reorder_for_focus_moves_matches_first_without_dropping_anything():
    findings = _findings("General", "Renewal", "Training", "Renewal", "General")
    ordered = reorder_for_focus(findings, "Renewal")
    assert [f.category for f in ordered] == ["Renewal", "Renewal", "General", "Training", "General"]
    assert {f.message_id for f in ordered} == {f.message_id for f in findings}, "reorder must not drop anything"


def test_reorder_for_focus_preserves_relative_order_within_each_group():
    findings = _findings("Renewal", "General", "Renewal", "Training")
    ordered = reorder_for_focus(findings, "Renewal")
    assert [f.message_id for f in ordered] == ["m0", "m2", "m1", "m3"]


def test_reorder_for_focus_none_returns_unchanged_order():
    findings = _findings("General", "Renewal", "Training")
    assert reorder_for_focus(findings, None) == findings


def test_reorder_for_focus_never_mutates_confidence_or_category():
    findings = _findings("General", "Renewal")
    ordered = reorder_for_focus(findings, "Renewal")
    for f in ordered:
        assert f.confidence == "Unverified"  # unchanged by focus, by explicit product decision
    assert {f.category for f in ordered} == {"General", "Renewal"}  # nothing recategorized


def test_reorder_for_focus_does_not_mutate_the_input_list():
    findings = _findings("General", "Renewal")
    original_order = list(findings)
    reorder_for_focus(findings, "Renewal")
    assert findings == original_order


def test_build_brief_with_focus_shows_matching_findings_first_and_hides_nothing():
    findings = _findings("General", "Renewal", "Training")
    from omnissa_agent.agent_a import AgentAResult
    result = AgentAResult(findings=findings)
    brief = build_brief(result, email_status="VERIFIED", focus_category="Renewal")
    assert "Focus: Renewal" in brief
    assert "nothing hidden" in brief
    lines = [l for l in brief.splitlines() if l.startswith("- [")]
    assert lines[0].startswith("- [Renewal]")
    assert len(lines) == 3  # every finding still present


def test_build_brief_without_focus_has_no_focus_line():
    """As of the 2026-09-30 scoreboard feature, build_brief's output is
    no longer byte-identical to before that feature (a Scoreboard
    section is now always present, findings or not) -- this only
    confirms the FOCUS line specifically is absent when no focus_category
    is given, which is the guarantee reorder_for_focus's docstring makes."""
    findings = _findings("General", "Renewal")
    from omnissa_agent.agent_a import AgentAResult
    result = AgentAResult(findings=findings)
    brief = build_brief(result, email_status="VERIFIED")
    assert "Focus:" not in brief.split("## Findings")[0]  # no focus line when none is given


def test_build_brief_always_includes_a_scoreboard_even_with_no_findings_and_no_baseline():
    """Definition of done for the 2026-09-30 scoreboard slice: a report
    shows the scoreboard even when Gmail fetched zero new messages --
    and even when no baseline file was supplied at all, it must render
    an honest 'not available' line rather than crash or omit the section."""
    from omnissa_agent.agent_a import AgentAResult
    result = AgentAResult(findings=[])
    brief = build_brief(result, email_status="VERIFIED")
    assert "## Scoreboard" in brief
    assert "## Scoreboard" in brief.split("## Findings")[0]  # scoreboard renders before Findings
    assert "not available" in brief


def test_build_brief_scoreboard_reflects_a_real_baseline_file(tmp_path):
    from omnissa_agent.agent_a import AgentAResult
    from omnissa_agent.baseline import load_baseline

    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text(
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
        "top_human_action: complete OSP/OTSP; pick distributors\n"
        "-->\n"
    )
    baseline = load_baseline(baseline_path)
    result = AgentAResult(findings=[])
    brief = build_brief(result, email_status="VERIFIED", baseline=baseline)
    assert "completed (completed 2026-08-28, next 2026-06-06, case 01696683)" in brief
    assert "requested 2/2, completed 0/0" in brief
    assert "Commercial=unknown" in brief
    assert "confidence=Confirmed (baseline file marked sourced)" in brief
    assert "complete OSP/OTSP; pick distributors" in brief


def test_agent_b_only_drafts_confirmed_or_likely():
    unverified = Finding(
        message_id="m1", category="Grant/Funding", confidence="Unverified",
        summary="maybe a grant", source_ref="gmail:m1",
    )
    likely = Finding(
        message_id="m2", category="Training", confidence="Likely",
        summary="training voucher renewal", source_ref="gmail:m2",
    )
    drafts = draft_from_findings([unverified, likely])
    assert len(drafts) == 1
    assert drafts[0].to == DEFAULT_RECIPIENT_PLACEHOLDER
    assert "DRAFT ONLY -- NOT SENT" in drafts[0].body
    assert drafts[0].confirm_before_sending  # never empty
