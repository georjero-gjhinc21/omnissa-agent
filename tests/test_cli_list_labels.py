from omnissa_agent import cli


class FakeGmailClient:
    def __init__(self, access_token):
        self.access_token = access_token

    def get_profile(self):
        return {"emailAddress": "george@gjh-inc.com"}

    def list_labels(self):
        return [
            {"id": "L1", "name": "INBOX", "type": "system"},
            {"id": "L2", "name": "Omnissa", "type": "user"},
            {"id": "L3", "name": "Work/Receipts", "type": "user"},
        ]


def _fake_client_secret(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text('{"installed": {"client_id": "x", "client_secret": "y"}}')
    p.chmod(0o600)
    return p


def test_list_labels_prints_names_and_flags_omnissa_hint(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(cli, "GmailReadonlyClient", FakeGmailClient)

    rc = cli.main(
        [
            "list-labels",
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(tmp_path / "token.json"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "george@gjh-inc.com" in out
    assert "'Omnissa'" in out and "omnissa" in out.lower()
    assert "INBOX" not in out  # system labels excluded, only user labels shown
    assert "Work/Receipts" in out


def test_list_labels_refuses_on_wrong_account(tmp_path, monkeypatch, capsys):
    class WrongAccountClient(FakeGmailClient):
        def get_profile(self):
            return {"emailAddress": "someone-else@example.com"}

    monkeypatch.setattr(cli.google_oauth, "refresh_access_token", lambda cc, tp: "at")
    monkeypatch.setattr(cli, "GmailReadonlyClient", WrongAccountClient)

    rc = cli.main(
        [
            "list-labels",
            "--client-secret",
            str(_fake_client_secret(tmp_path)),
            "--token",
            str(tmp_path / "token.json"),
        ]
    )
    assert rc == cli.REFUSED
    assert "WRONG_ACCOUNT" in capsys.readouterr().err
