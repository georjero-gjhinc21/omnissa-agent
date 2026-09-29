import pytest

from omnissa_agent import router
from omnissa_agent.agent_a import Finding
from omnissa_agent.pipeline import run_pilot
from omnissa_agent.sources import (
    LiveGmailSource,
    LiveGmailUnavailable,
    StaticMessageSource,
    SyntheticGmailSource,
)


def test_live_gmail_source_refuses_instead_of_silently_no_op():
    with pytest.raises(LiveGmailUnavailable):
        LiveGmailSource().fetch(max_messages=10)


def test_pilot_runs_end_to_end_with_synthetic_source(tmp_path):
    report = run_pilot(
        source=SyntheticGmailSource(),
        email_status="BLOCKED (token expired) -- synthetic pilot only",
        state_base=tmp_path,
    )
    assert report.messages_seen == 4
    assert report.findings_count == 3  # one duplicate id collapsed
    assert report.duplicates_skipped == 1
    assert "No external actions were taken." in report.brief_markdown
    assert report.drafts_count == 0  # raw inbound mail never reaches Confirmed/Likely alone


def test_pilot_restart_recovery_skips_previously_seen(tmp_path):
    run_pilot(source=SyntheticGmailSource(), email_status="synthetic", state_base=tmp_path)
    second = run_pilot(source=SyntheticGmailSource(), email_status="synthetic", state_base=tmp_path)
    assert second.findings_count == 0
    assert second.duplicates_skipped == 4  # every id in the fixture already seen


def test_pilot_demo_findings_flow_into_agent_b_drafts(tmp_path):
    demo = [
        Finding(
            message_id="demo-1",
            category="Training",
            confidence="Likely",
            summary="demo: corroborated training voucher item",
            source_ref="demo:public-source",
        )
    ]
    report = run_pilot(
        source=SyntheticGmailSource(messages=[]),
        email_status="synthetic-demo",
        state_base=tmp_path,
        demo_findings=demo,
    )
    assert report.drafts_count == 1


def test_ingestion_dedup_does_not_blind_the_classifier_on_first_run(tmp_path):
    """Regression: gmail_ingest.run_ingestion and pipeline.run_pilot share
    one checkpoint file. Before the fix, both used the same 'seen_ids' key,
    so ingestion marking a message as fetched made it look already-seen
    to the classifier on the SAME run it first appeared -- zero findings
    on every live run, forever. They must use separate namespaces.
    """
    from omnissa_agent import gmail_ingest, state as state_mod
    from omnissa_agent.gmail_api import GmailReadonlyClient

    calls = {"n": 0}

    def fake_get(url, headers):
        calls["n"] += 1
        if "/profile" in url:
            return 200, {"emailAddress": gmail_ingest.EXPECTED_ACCOUNT}
        if url.endswith("/labels"):
            return 200, {"labels": [{"id": "L1", "name": gmail_ingest.EXPECTED_LABEL_NAME}]}
        if "/messages/" in url:
            return 200, {
                "id": "m1",
                "snippet": "hi",
                "labelIds": ["L1"],
                "payload": {"headers": [{"name": "Subject", "value": "Omnissa training voucher"}]},
            }
        return 200, {"messages": [{"id": "m1"}]}

    client = GmailReadonlyClient("fake-token", http_get=fake_get)
    ingest_result = gmail_ingest.run_ingestion(client, state_base=tmp_path)
    assert ingest_result.status == gmail_ingest.IngestStatus.OK
    assert len(ingest_result.messages) == 1

    report = run_pilot(
        source=StaticMessageSource(ingest_result.messages),
        email_status="VERIFIED (test)",
        state_base=tmp_path,
    )
    assert report.findings_count == 1  # NOT 0 -- this is the bug's signature
    assert report.duplicates_skipped == 0

    st = state_mod.load_state(tmp_path)
    assert "m1" in st["gmail_ingested_ids"]
    assert "m1" in st["seen_ids"]  # both namespaces end up populated, independently


def test_pilot_all_backends_down_does_not_crash_or_hang(tmp_path, monkeypatch):
    import urllib.error

    def always_fail(url, model, prompt, *, timeout_s):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(router, "_post_chat", always_fail)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)

    report = run_pilot(
        source=SyntheticGmailSource(),
        email_status="synthetic",
        state_base=tmp_path,
        use_llm=True,
    )
    assert "deferred" in report.brief_markdown.lower()
