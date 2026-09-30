"""The privilege-separated production path: `ingest` (privileged, writes a
drop file) and `classify` (unprivileged, reads it). No real network/OAuth
here -- refresh_access_token and run_ingestion are monkeypatched.
"""

import json

from omnissa_agent import cli, gmail_ingest
from omnissa_agent.sources import GmailMessage


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


def test_ingest_writes_sanitized_drop_file_with_no_credential_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(
            status=gmail_ingest.IngestStatus.OK,
            account="george@gjh-inc.com",
            label_id="L1",
            label_name="Archive_/@omnissa.com",
            messages=[
                GmailMessage(
                    id="m1", subject="Omnissa training", snippet="hi",
                    sender="training@omnissa.com", date="2026-09-29", label_ids=("L1",),
                )
            ],
        ),
    )
    out = tmp_path / "drop" / "latest-scan.json"
    rc = cli.main(
        [
            "ingest",
            "--client-secret", str(_fake_client_secret(tmp_path)),
            "--token", str(_fake_token(tmp_path)),
            "--out", str(out),
            "--state-dir", str(tmp_path),
        ]
    )
    assert rc == 0
    assert out.exists()
    assert (out.stat().st_mode & 0o777) == 0o640
    text = out.read_text()
    assert "access_token" not in text and "refresh_token" not in text and "at" != text.strip()
    data = json.loads(text)
    assert data["status"] == "OK"
    assert data["messages"][0]["id"] == "m1"


def test_ingest_refuses_on_wrong_account_and_writes_status_in_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(
            status=gmail_ingest.IngestStatus.WRONG_ACCOUNT, reason="wrong account"
        ),
    )
    out = tmp_path / "latest-scan.json"
    rc = cli.main(
        [
            "ingest",
            "--client-secret", str(_fake_client_secret(tmp_path)),
            "--token", str(_fake_token(tmp_path)),
            "--out", str(out),
            "--state-dir", str(tmp_path),
        ]
    )
    assert rc == cli.REFUSED
    assert json.loads(out.read_text())["status"] == "WRONG_ACCOUNT"


def test_classify_reads_drop_file_and_never_takes_email_status_flag(tmp_path):
    drop = tmp_path / "latest-scan.json"
    result = gmail_ingest.IngestionResult(
        status=gmail_ingest.IngestStatus.OK,
        account="george@gjh-inc.com",
        label_id="L1",
        label_name="Archive_/@omnissa.com",
        messages=[
            GmailMessage(
                id="m1", subject="Omnissa renewal", snippet="due",
                sender="partner@omnissa.com", date="2026-09-29", label_ids=("L1",),
            )
        ],
    )
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main(
        [
            "classify", "--kind", "scan",
            "--ingest-result", str(drop),
            "--state-dir", str(tmp_path),
        ]
    )
    assert rc == 0
    reports = list((tmp_path / "reports").glob("*-scan.md"))
    assert len(reports) == 1
    text = reports[0].read_text()
    assert "runtime-checked by gmail_ingest.run_ingestion" in text
    assert "george@gjh-inc.com" in text


def test_classify_has_no_email_status_flag_to_assert(tmp_path):
    import pytest

    drop = tmp_path / "d.json"
    drop.write_text(json.dumps(gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK).to_json_dict()))
    with pytest.raises(SystemExit):
        cli.main(
            [
                "classify", "--kind", "scan",
                "--ingest-result", str(drop),
                "--email-status", "VERIFIED account=fake",  # must not exist as an option
            ]
        )


def test_classify_propagates_blocked_status_from_drop_file_verbatim(tmp_path, capsys):
    drop = tmp_path / "latest-scan.json"
    result = gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.LABEL_MISSING, reason="no label")
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main(["classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(tmp_path)])
    assert rc == cli.REFUSED
    assert "LABEL_MISSING" in capsys.readouterr().err


def test_classify_refuses_stale_drop_file_missed_run_detection(tmp_path):
    import os
    import time

    drop = tmp_path / "latest-scan.json"
    result = gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK, account="x", label_id="L", label_name="L")
    drop.write_text(json.dumps(result.to_json_dict()))
    old = time.time() - 3600
    os.utime(drop, (old, old))

    rc = cli.main(
        [
            "classify", "--kind", "scan",
            "--ingest-result", str(drop),
            "--max-age-s", "300",
            "--state-dir", str(tmp_path),
        ]
    )
    assert rc == cli.REFUSED


def test_classify_accepts_fresh_drop_file_within_max_age(tmp_path):
    drop = tmp_path / "latest-scan.json"
    result = gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK, account="x", label_id="L", label_name="L")
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main(
        [
            "classify", "--kind", "scan",
            "--ingest-result", str(drop),
            "--max-age-s", "300",
            "--state-dir", str(tmp_path),
        ]
    )
    assert rc == 0


def test_ingest_skips_cleanly_when_another_instance_holds_the_lock(tmp_path, monkeypatch, capsys):
    """Regression: scan and brief ingest share one --state-dir (one
    checkpoint per identity). Without a lock this is a real race; with
    it, a concurrent attempt must be skipped cleanly, not corrupt state
    or crash.
    """
    from omnissa_agent.lock import SingleInstanceLock

    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK, account="x", label_id="L", label_name="L"),
    )

    state_dir = tmp_path / "ingest-state"
    out = tmp_path / "drop.json"
    with SingleInstanceLock(base=state_dir):  # simulate a concurrent (e.g. brief) ingest run
        rc = cli.main(
            [
                "ingest",
                "--client-secret", str(_fake_client_secret(tmp_path)),
                "--token", str(_fake_token(tmp_path)),
                "--out", str(out),
                "--state-dir", str(state_dir),
            ]
        )
    assert rc == cli.LOCKED
    assert not out.exists(), "a skipped run must not write anything"
    assert "LOCKED" in capsys.readouterr().err


def test_classify_skips_cleanly_when_another_instance_holds_the_lock(tmp_path):
    """classify's lock protection comes from pipeline.run_pilot (existing
    since before the ingest/classify split) -- confirm it actually works
    end to end through `classify`, and that classify does NOT also
    acquire a second, colliding lock of its own (regression: adding one
    caused every classify run to self-block, since flock is per-open-file
    -description, not per-process -- fixed 2026-09-30).
    """
    from omnissa_agent.lock import SingleInstanceLock

    drop = tmp_path / "drop.json"
    result = gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK, account="x", label_id="L", label_name="L")
    drop.write_text(json.dumps(result.to_json_dict()))

    state_dir = tmp_path / "analysis-state"
    with SingleInstanceLock(base=state_dir):  # simulate a concurrent (e.g. brief) classify run
        rc = cli.main(["classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(state_dir)])
    assert rc == cli.LOCKED
    assert drop.exists(), "a lock-skipped run must not delete input it never actually processed"
    assert not (state_dir / "reports").exists(), "a lock-skipped run must not write a misleading placeholder report"


def test_classify_does_not_self_block_on_a_normal_uncontended_run(tmp_path):
    """A plain, uncontended classify call must succeed and consume its
    input -- guards against ever reintroducing the double-lock
    regression, where EVERY run (not just concurrent ones) self-blocked.
    """
    drop = tmp_path / "drop.json"
    result = gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK, account="x", label_id="L", label_name="L")
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main(["classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(tmp_path / "state")])
    assert rc == 0
    assert not drop.exists()


def test_ingest_reports_partial_not_ok_when_deadline_hit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(
            status=gmail_ingest.IngestStatus.OK,
            account="george@gjh-inc.com",
            label_id="L1",
            label_name="Archive_/@omnissa.com",
            messages=[],
            deadline_hit=True,
        ),
    )
    out = tmp_path / "drop.json"
    rc = cli.main(
        [
            "ingest",
            "--client-secret", str(_fake_client_secret(tmp_path)),
            "--token", str(_fake_token(tmp_path)),
            "--out", str(out),
            "--state-dir", str(tmp_path),
        ]
    )
    assert rc == cli.PARTIAL
    assert "status=PARTIAL" in capsys.readouterr().out


def test_classify_report_group_readable_flag_controls_file_mode(tmp_path):
    result = gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK, account="x", label_id="L", label_name="L")

    # classify deletes the drop file on successful consumption, so each
    # call gets its own fresh file rather than reusing one across calls
    drop_a = tmp_path / "drop-a.json"
    drop_a.write_text(json.dumps(result.to_json_dict()))
    rc = cli.main(["classify", "--kind", "scan", "--ingest-result", str(drop_a), "--state-dir", str(tmp_path / "a")])
    assert rc == 0
    assert not drop_a.exists()
    default_report = list((tmp_path / "a" / "reports").glob("*.md"))[0]
    assert (default_report.stat().st_mode & 0o777) == 0o600

    drop_b = tmp_path / "drop-b.json"
    drop_b.write_text(json.dumps(result.to_json_dict()))
    rc2 = cli.main(
        [
            "classify", "--kind", "scan", "--ingest-result", str(drop_b),
            "--state-dir", str(tmp_path / "b"), "--report-group-readable",
        ]
    )
    assert rc2 == 0
    assert not drop_b.exists()
    group_report = list((tmp_path / "b" / "reports").glob("*.md"))[0]
    assert (group_report.stat().st_mode & 0o777) == 0o640


def test_classify_reports_dir_override_writes_outside_state_dir(tmp_path):
    """Regression for a real bug (2026-09-30): the default reports path
    nested under --state-dir was unreachable by a reader group when
    --state-dir itself is a private identity home (0700/0750) --
    permissions on the ancestor directory always win over the child's
    own permissions. --reports-dir must write to a fully separate path.
    """
    drop = tmp_path / "drop.json"
    result = gmail_ingest.IngestionResult(status=gmail_ingest.IngestStatus.OK, account="x", label_id="L", label_name="L")
    drop.write_text(json.dumps(result.to_json_dict()))

    separate_reports_dir = tmp_path / "shared-reports"
    rc = cli.main(
        [
            "classify", "--kind", "scan", "--ingest-result", str(drop),
            "--state-dir", str(tmp_path / "private-state"),
            "--reports-dir", str(separate_reports_dir),
        ]
    )
    assert rc == 0
    reports = list(separate_reports_dir.glob("*-scan.md"))
    assert len(reports) == 1
    # nothing was written under the private state dir's own reports/ subpath
    assert not (tmp_path / "private-state" / "reports").exists()


def test_classify_reports_partial_and_surfaces_it_in_the_brief_text(tmp_path):
    drop = tmp_path / "drop.json"
    result = gmail_ingest.IngestionResult(
        status=gmail_ingest.IngestStatus.OK,
        account="george@gjh-inc.com",
        label_id="L1",
        label_name="Archive_/@omnissa.com",
        messages=[
            GmailMessage(
                id="m1", subject="Omnissa update", snippet="hi",
                sender="x@omnissa.com", date="2026-09-29", label_ids=("L1",),
            )
        ],
        deadline_hit=True,
    )
    drop.write_text(json.dumps(result.to_json_dict()))

    rc = cli.main(["classify", "--kind", "scan", "--ingest-result", str(drop), "--state-dir", str(tmp_path)])
    assert rc == cli.PARTIAL

    report = list((tmp_path / "reports").glob("*-scan.md"))[0].read_text()
    assert "PARTIAL" in report
    assert "VERIFIED (PARTIAL -- ingestion deadline reached)" in report


def test_ingest_and_classify_end_to_end_produce_the_same_result_as_run(tmp_path, monkeypatch):
    """The split path and the single-process `run` path must agree."""
    fake_result = gmail_ingest.IngestionResult(
        status=gmail_ingest.IngestStatus.OK,
        account="george@gjh-inc.com",
        label_id="L1",
        label_name="Archive_/@omnissa.com",
        messages=[
            GmailMessage(
                id="m1", subject="Omnissa NFR access", snippet="eval",
                sender="nfr@omnissa.com", date="2026-09-29", label_ids=("L1",),
            )
        ],
    )
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(cli.gmail_ingest, "run_ingestion", lambda *a, **k: fake_result)

    drop = tmp_path / "drop.json"
    rc1 = cli.main(
        [
            "ingest",
            "--client-secret", str(_fake_client_secret(tmp_path)),
            "--token", str(_fake_token(tmp_path)),
            "--out", str(drop),
            "--state-dir", str(tmp_path / "ingest-state"),
        ]
    )
    assert rc1 == 0

    rc2 = cli.main(
        [
            "classify", "--kind", "scan",
            "--ingest-result", str(drop),
            "--state-dir", str(tmp_path / "classify-state"),
        ]
    )
    assert rc2 == 0
    report = list((tmp_path / "classify-state" / "reports").glob("*-scan.md"))[0].read_text()
    assert "Omnissa NFR access" in report
