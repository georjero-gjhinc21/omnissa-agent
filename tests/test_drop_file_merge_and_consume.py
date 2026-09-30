"""Regression tests for a confirmed real incident (2026-09-29): an ingest
run wrote 50 real messages to the drop file; the next ingest run found 0
new messages and OVERWROTE the drop file with an empty one before
analysis ever read it. Those 50 messages were marked ingested (so never
re-fetched) but never classified -- permanently lost from the pipeline.
Fixed by merging (never overwriting) an unconsumed drop, and having
`classify` delete the file only once its data has actually been used.
"""

import json

from omnissa_agent import cli, gmail_ingest
from omnissa_agent.gmail_ingest import IngestionResult, IngestStatus, merge_pending
from omnissa_agent.sources import GmailMessage


def _msg(mid):
    return GmailMessage(
        id=mid, subject=f"Omnissa item {mid}", snippet="hi",
        sender="x@omnissa.com", date="2026-09-29", label_ids=("L1",),
    )


def _fake_client_secret(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text('{"installed": {"client_id": "x", "client_secret": "y"}}')
    p.chmod(0o600)
    return p


def _fake_token(tmp_path):
    p = tmp_path / "token.json"
    p.write_text('{"access_token": "at", "refresh_token": "rt"}')
    p.chmod(0o600)
    return p


def test_merge_pending_concatenates_disjoint_messages_without_data_loss():
    old = IngestionResult(status=IngestStatus.OK, account="a", label_id="L", label_name="L",
                           messages=[_msg("m1"), _msg("m2")], duplicates_skipped=1)
    new = IngestionResult(status=IngestStatus.OK, account="a", label_id="L", label_name="L",
                           messages=[], duplicates_skipped=2)
    merged = merge_pending(old, new)
    assert [m.id for m in merged.messages] == ["m1", "m2"]
    assert merged.duplicates_skipped == 3


def test_merge_pending_ors_deadline_hit():
    old = IngestionResult(status=IngestStatus.OK, deadline_hit=True)
    new = IngestionResult(status=IngestStatus.OK, deadline_hit=False)
    assert merge_pending(old, new).deadline_hit is True


def test_merge_pending_ignores_a_non_ok_prior_snapshot():
    old = IngestionResult(status=IngestStatus.WRONG_ACCOUNT, reason="oops")
    new = IngestionResult(status=IngestStatus.OK, messages=[_msg("m1")])
    merged = merge_pending(old, new)
    assert merged is new


def test_ingest_merges_with_an_unconsumed_prior_drop_instead_of_overwriting(tmp_path, monkeypatch):
    """Reproduces the exact incident: first ingest writes 50 (here: 2)
    real messages; a second ingest run (0 new messages, as would happen
    once they're already in gmail_ingested_ids) must NOT erase them.
    """
    results = iter([
        IngestionResult(status=IngestStatus.OK, account="george@gjh-inc.com",
                         label_id="L1", label_name="Archive_/@omnissa.com",
                         messages=[_msg("m1"), _msg("m2")]),
        IngestionResult(status=IngestStatus.OK, account="george@gjh-inc.com",
                         label_id="L1", label_name="Archive_/@omnissa.com",
                         messages=[]),  # 0 new -- the second real run's actual shape
    ])
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(cli.gmail_ingest, "run_ingestion", lambda *a, **k: next(results))

    out = tmp_path / "latest-scan.json"
    common = [
        "ingest",
        "--client-secret", str(_fake_client_secret(tmp_path)),
        "--token", str(_fake_token(tmp_path)),
        "--out", str(out),
        "--state-dir", str(tmp_path),
    ]

    rc1 = cli.main(common)
    assert rc1 == 0
    assert json.loads(out.read_text())["messages"] == [
        {"id": "m1", "subject": "Omnissa item m1", "snippet": "hi", "sender": "x@omnissa.com",
         "date": "2026-09-29", "label_ids": ["L1"], "partner_id": ""},
        {"id": "m2", "subject": "Omnissa item m2", "snippet": "hi", "sender": "x@omnissa.com",
         "date": "2026-09-29", "label_ids": ["L1"], "partner_id": ""},
    ]

    rc2 = cli.main(common)  # the "0 new messages" run that used to wipe the file
    assert rc2 == 0
    data_after = json.loads(out.read_text())
    ids_after = {m["id"] for m in data_after["messages"]}
    assert ids_after == {"m1", "m2"}, (
        "the original messages were lost -- this is the exact data-loss incident, "
        f"got: {ids_after}"
    )


def test_classify_deletes_the_drop_file_only_after_successful_consumption(tmp_path):
    drop = tmp_path / "drop.json"
    result = IngestionResult(status=IngestStatus.OK, account="a", label_id="L", label_name="L",
                              messages=[_msg("m1")])
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main(["classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(tmp_path)])
    assert rc == 0
    assert not drop.exists(), "consumed drop file must be removed so it can't be double-processed"


def test_classify_does_not_delete_the_drop_file_on_refusal(tmp_path):
    drop = tmp_path / "drop.json"
    result = IngestionResult(status=IngestStatus.WRONG_ACCOUNT, reason="wrong account")
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main(["classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(tmp_path)])
    assert rc == cli.REFUSED
    assert drop.exists(), "a refused/unconsumed drop must be preserved for inspection and retry"


def test_classify_does_not_delete_the_drop_file_when_max_messages_truncates_it(tmp_path):
    """Regression for a real, confirmed-live near-miss (2026-09-29): a
    merged drop held 83 messages (33 old unconsumed + 50 freshly fetched
    after merge_pending); classify's own --max-messages (then defaulting
    to 50) truncated the list BEFORE Agent A ever saw the remaining 33 --
    genuinely real, never-classified messages -- and the drop file was
    deleted anyway because ingest_result.status was OK and rc wasn't
    LOCKED. Nothing distinguished "fully consumed" from "silently
    truncated." The 33 ids were recoverable via gmail_ingested_ids +
    reconcile/requeue this time, but only because this exact scenario was
    caught by a reconcile check the cutover script happened to run --
    the drop file itself must never be deleted when it wasn't fully
    processed, regardless of the exit code that resulted.
    """
    drop = tmp_path / "drop.json"
    messages = [_msg(f"m{i}") for i in range(5)]
    result = IngestionResult(status=IngestStatus.OK, account="a", label_id="L", label_name="L",
                              messages=messages)
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main([
        "classify", "--kind", "scan", "--ingest-result", str(drop),
        "--state-dir", str(tmp_path), "--max-messages", "3",
    ])
    assert rc == cli.PARTIAL
    assert drop.exists(), "a drop file truncated by --max-messages must never be deleted"

    state = json.loads((tmp_path / "checkpoint.json").read_text())
    assert set(state["seen_ids"]) == {"m0", "m1", "m2"}, (
        "only the first --max-messages ids should have been marked seen this run"
    )


def test_classify_reports_partial_and_leaves_file_even_without_deadline_hit(tmp_path):
    """The truncation case has nothing to do with ingest's own deadline --
    deadline_hit can be False and this must still be caught."""
    drop = tmp_path / "drop.json"
    result = IngestionResult(status=IngestStatus.OK, account="a", label_id="L", label_name="L",
                              messages=[_msg("m1"), _msg("m2")], deadline_hit=False)
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main([
        "classify", "--kind", "scan", "--ingest-result", str(drop),
        "--state-dir", str(tmp_path), "--max-messages", "1",
    ])
    assert rc == cli.PARTIAL
    assert drop.exists()


def test_classify_still_deletes_on_a_pure_deadline_hit_with_no_truncation(tmp_path):
    """A deadline_hit PARTIAL where every message in the drop WAS fully
    processed (no --max-messages truncation) has nothing left unconsumed
    in the file -- safe to delete, unlike the truncation case above."""
    drop = tmp_path / "drop.json"
    result = IngestionResult(status=IngestStatus.OK, account="a", label_id="L", label_name="L",
                              messages=[_msg("m1")], deadline_hit=True)
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main([
        "classify", "--kind", "scan", "--ingest-result", str(drop),
        "--state-dir", str(tmp_path), "--max-messages", "50",
    ])
    assert rc == cli.PARTIAL
    assert not drop.exists(), "fully-processed content has nothing left to preserve"


def test_full_incident_replay_ingest_ingest_then_classify_loses_nothing(tmp_path, monkeypatch):
    """End-to-end replay of the exact real sequence: ingest (50 msgs) ->
    ingest (0 new, would have wiped the file before the fix) -> classify.
    The classified report must contain the original messages."""
    results = iter([
        IngestionResult(status=IngestStatus.OK, account="george@gjh-inc.com",
                         label_id="L1", label_name="Archive_/@omnissa.com",
                         messages=[_msg(f"m{i}") for i in range(1, 4)]),
        IngestionResult(status=IngestStatus.OK, account="george@gjh-inc.com",
                         label_id="L1", label_name="Archive_/@omnissa.com", messages=[]),
    ])
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(cli.gmail_ingest, "run_ingestion", lambda *a, **k: next(results))

    drop = tmp_path / "latest-scan.json"
    ingest_common = [
        "ingest",
        "--client-secret", str(_fake_client_secret(tmp_path)),
        "--token", str(_fake_token(tmp_path)),
        "--out", str(drop),
        "--state-dir", str(tmp_path / "ingest-state"),
    ]
    assert cli.main(ingest_common) == 0
    assert cli.main(ingest_common) == 0  # the "0 new" run

    rc = cli.main(
        ["classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(tmp_path / "analysis-state")]
    )
    assert rc == 0
    assert not drop.exists()
    report = list((tmp_path / "analysis-state" / "reports").glob("*-scan.md"))[0].read_text()
    for i in range(1, 4):
        assert f"Omnissa item m{i}" in report, f"m{i} was lost -- the incident recurred"
