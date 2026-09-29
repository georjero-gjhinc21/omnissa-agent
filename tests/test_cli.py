import json

from omnissa_agent import cli


def test_refuses_when_not_verified(tmp_path, capsys):
    rc = cli.main(
        [
            "scan",
            "--email-status",
            "BLOCKED: token expired",
            "--messages",
            "-",
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == cli.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert not (tmp_path / "reports").exists()


def test_verified_scan_processes_messages_and_writes_report(tmp_path, capsys):
    messages = [
        {
            "id": "real-1",
            "subject": "Omnissa training voucher renewal",
            "snippet": "Your voucher is due for renewal.",
            "sender": "training@omnissa.com",
            "date": "2026-09-29",
            "label_ids": ["omnissa"],
        }
    ]
    msg_file = tmp_path / "messages.json"
    msg_file.write_text(json.dumps(messages))

    rc = cli.main(
        [
            "scan",
            "--email-status",
            "VERIFIED account=george@gjh-inc.com label_id=Label_42",
            "--messages",
            str(msg_file),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote " in out
    reports = list((tmp_path / "reports").glob("*-scan.md"))
    assert len(reports) == 1
    assert (reports[0].stat().st_mode & 0o777) == 0o600


def test_malformed_messages_is_unexpected_error_not_partial_write(tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json")
    rc = cli.main(
        [
            "scan",
            "--email-status",
            "VERIFIED account=x label_id=y",
            "--messages",
            str(bad_file),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == cli.UNEXPECTED_ERROR
    assert not (tmp_path / "reports").exists()


def test_all_backends_down_returns_llm_deferred_code(tmp_path, monkeypatch):
    import urllib.error

    from omnissa_agent import router

    def always_fail(url, model, prompt, *, timeout_s):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(router, "_post_chat", always_fail)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)

    messages = [
        {
            "id": "real-2",
            "subject": "Omnissa grant program",
            "snippet": "New grant window open.",
            "sender": "grants@omnissa.com",
            "date": "2026-09-29",
            "label_ids": ["omnissa"],
        }
    ]
    msg_file = tmp_path / "messages.json"
    msg_file.write_text(json.dumps(messages))

    rc = cli.main(
        [
            "brief",
            "--email-status",
            "VERIFIED account=x label_id=y",
            "--messages",
            str(msg_file),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == cli.LLM_DEFERRED
