"""cli.py's `authorize` subcommand. No real network/browser here --
google_oauth's loopback + exchange calls are monkeypatched at their
call sites in cli.py, proving the wiring without ever hitting Google.
"""

from omnissa_agent import cli, google_oauth


FAKE_SECRET_VALUE = "super-secret-do-not-print-abc123"


def _fake_client_secret(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text(
        '{"installed": {"client_id": "x.apps.googleusercontent.com", '
        f'"client_secret": "{FAKE_SECRET_VALUE}"}}}}'
    )
    p.chmod(0o600)
    return p


def test_authorize_saves_token_on_success(tmp_path, capsys):
    def fake_loopback(*, port, expected_state, timeout_s):
        return "auth-code-123"

    def fake_exchange(client_config, *, code, redirect_uri, pkce):
        assert code == "auth-code-123"
        return {"access_token": "at", "refresh_token": "rt"}

    orig_loopback = google_oauth.run_loopback_and_get_code
    orig_exchange = google_oauth.exchange_code_for_tokens
    cli.google_oauth.run_loopback_and_get_code = fake_loopback
    cli.google_oauth.exchange_code_for_tokens = fake_exchange
    try:
        token_path = tmp_path / "token.json"
        rc = cli.main(
            [
                "authorize",
                "--client-secret",
                str(_fake_client_secret(tmp_path)),
                "--token",
                str(token_path),
            ]
        )
    finally:
        cli.google_oauth.run_loopback_and_get_code = orig_loopback
        cli.google_oauth.exchange_code_for_tokens = orig_exchange

    assert rc == 0
    assert token_path.exists()
    assert (token_path.stat().st_mode & 0o777) == 0o600
    out = capsys.readouterr().out
    assert "accounts.google.com" in out  # the URL was printed
    assert FAKE_SECRET_VALUE not in out  # client_secret value never echoed


def test_authorize_propagates_loopback_timeout_as_error(tmp_path, monkeypatch, capsys):
    def fake_loopback(*, port, expected_state, timeout_s):
        raise google_oauth.OAuthError("no redirect received within timeout")

    monkeypatch.setattr(cli.google_oauth, "run_loopback_and_get_code", fake_loopback)

    rc = cli.main(
        [
            "authorize",
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(tmp_path / "token.json"),
            "--timeout",
            "1",
        ]
    )
    assert rc == cli.UNEXPECTED_ERROR
    assert "timeout" in capsys.readouterr().err.lower()
