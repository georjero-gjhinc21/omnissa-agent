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


def test_build_brief_without_focus_is_byte_identical_to_before_this_feature():
    findings = _findings("General", "Renewal")
    from omnissa_agent.agent_a import AgentAResult
    result = AgentAResult(findings=findings)
    brief = build_brief(result, email_status="VERIFIED")
    assert "Focus:" not in brief.split("## Findings")[0]  # no focus line when none is given


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
