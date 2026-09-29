"""cli.py's `run` subcommand -- the live path. No real network anywhere
here: google_oauth.refresh_access_token and gmail_ingest.run_ingestion
are monkeypatched at their call sites in cli.py, so these tests prove
the exit-code/email-status wiring, not real Gmail behavior (that needs
live OAuth, covered separately as a proposed one-shot test).
"""

from omnissa_agent import cli, gmail_ingest, google_oauth
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


def test_token_refresh_failure_refuses_before_any_ingestion(tmp_path, monkeypatch):
    called = {"ingest": False}

    def fake_refresh(client_config, token_path):
        raise google_oauth.OAuthError("invalid_grant")

    def fake_ingest(*a, **k):
        called["ingest"] = True
        raise AssertionError("must not be called")

    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(cli.gmail_ingest, "run_ingestion", fake_ingest)

    rc = cli.main(
        [
            "run",
            "--kind",
            "scan",
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(_fake_token(tmp_path)),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == cli.REFUSED
    assert called["ingest"] is False


def test_wrong_account_maps_to_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(
            status=gmail_ingest.IngestStatus.WRONG_ACCOUNT, reason="wrong account"
        ),
    )
    rc = cli.main(
        [
            "run",
            "--kind",
            "scan",
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(_fake_token(tmp_path)),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == cli.REFUSED


def test_rate_limited_maps_to_its_own_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(
            status=gmail_ingest.IngestStatus.RATE_LIMITED, reason="quota"
        ),
    )
    rc = cli.main(
        [
            "run",
            "--kind",
            "scan",
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(_fake_token(tmp_path)),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == cli.RATE_LIMITED


def test_ok_ingestion_builds_email_status_from_code_not_caller(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(
            status=gmail_ingest.IngestStatus.OK,
            account="george@gjh-inc.com",
            label_id="Label_42",
            label_name="@omnissa.com",
            messages=[
                GmailMessage(
                    id="m1", subject="Omnissa training voucher", snippet="due soon",
                    sender="training@omnissa.com", date="2026-09-29", label_ids=("Label_42",),
                )
            ],
        ),
    )
    rc = cli.main(
        [
            "run",
            "--kind",
            "scan",
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(_fake_token(tmp_path)),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    reports = list((tmp_path / "reports").glob("*-scan.md"))
    assert len(reports) == 1
    text = reports[0].read_text()
    assert "runtime-checked by gmail_ingest.run_ingestion" in text
    assert "george@gjh-inc.com" in text


def test_llm_policy_local_only_uses_combo_private_not_combo_continuous(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(
        cli.gmail_ingest,
        "run_ingestion",
        lambda *a, **k: gmail_ingest.IngestionResult(
            status=gmail_ingest.IngestStatus.OK,
            account="george@gjh-inc.com",
            label_id="L1",
            label_name="@omnissa.com",
            messages=[
                GmailMessage(
                    id="m1", subject="Omnissa grant window", snippet="open now",
                    sender="grants@omnissa.com", date="2026-09-29", label_ids=("L1",),
                )
            ],
        ),
    )
    models_used = []

    def fake_post_chat(url, model, prompt, *, timeout_s):
        models_used.append(model)
        return {"omni_backend": "omni/ollama-local", "choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(cli.router, "_post_chat", fake_post_chat)

    rc = cli.main(
        [
            "run",
            "--kind",
            "brief",  # brief turns on use_llm
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(_fake_token(tmp_path)),
            "--state-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert models_used == [f"omni/{cli.router.LOCAL_ONLY_COMBO}"]
