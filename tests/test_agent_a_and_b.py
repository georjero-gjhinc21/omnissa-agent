from omnissa_agent.agent_a import Finding, build_brief, classify_messages
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
