import json
import socket
import threading
import urllib.parse
import urllib.request

import pytest

from omnissa_agent import google_oauth as goauth

CLIENT_CONFIG = {"client_id": "fake-client-id.apps.googleusercontent.com", "client_secret": "fake-secret"}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_pkce_pair_is_url_safe_and_challenge_derives_from_verifier():
    pair = goauth.new_pkce_pair()
    assert "=" not in pair.verifier and "=" not in pair.challenge
    assert 43 <= len(pair.verifier) <= 128
    # deterministic derivation, not just "looks random"
    import base64
    import hashlib

    expected = base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest()).rstrip(b"=").decode()
    assert pair.challenge == expected


def test_authorization_url_contains_readonly_scope_and_pkce_no_secret():
    pkce = goauth.new_pkce_pair()
    url = goauth.build_authorization_url(
        CLIENT_CONFIG, redirect_uri="http://127.0.0.1:9999/", state="st123", pkce=pkce
    )
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert qs["scope"] == [goauth.SCOPE]
    assert qs["code_challenge"] == [pkce.challenge]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["state"] == ["st123"]
    assert "client_secret" not in url  # never in the browser-facing URL


def test_check_private_file_refuses_group_readable(tmp_path):
    p = tmp_path / "secret.json"
    p.write_text("{}")
    p.chmod(0o644)
    with pytest.raises(goauth.InsecurePermissionsError):
        goauth.check_private_file(p)


def test_check_private_file_accepts_owner_only(tmp_path):
    p = tmp_path / "secret.json"
    p.write_text("{}")
    p.chmod(0o600)
    goauth.check_private_file(p)  # must not raise


def test_load_client_config_refuses_insecure_permissions(tmp_path):
    p = tmp_path / "client_secret.json"
    p.write_text(json.dumps({"installed": CLIENT_CONFIG}))
    p.chmod(0o664)
    with pytest.raises(goauth.InsecurePermissionsError):
        goauth.load_client_config(p)


def test_load_token_file_refuses_insecure_permissions(tmp_path):
    p = tmp_path / "token.json"
    p.write_text(json.dumps({"access_token": "at", "refresh_token": "rt"}))
    p.chmod(0o644)
    with pytest.raises(goauth.InsecurePermissionsError):
        goauth.load_token_file(p)


def test_load_client_config_rejects_missing_fields(tmp_path):
    bad = tmp_path / "client_secret.json"
    bad.write_text(json.dumps({"installed": {"client_id": "x"}}))
    with pytest.raises(goauth.OAuthError):
        goauth.load_client_config(bad)


def test_load_client_config_accepts_installed_shape(tmp_path):
    good = tmp_path / "client_secret.json"
    good.write_text(json.dumps({"installed": CLIENT_CONFIG}))
    good.chmod(0o600)
    cfg = goauth.load_client_config(good)
    assert cfg["client_id"] == CLIENT_CONFIG["client_id"]


def test_exchange_requires_refresh_token_in_response():
    pkce = goauth.new_pkce_pair()

    def fake_post(url, data):
        return {"access_token": "at", "expires_in": 3600}  # no refresh_token

    with pytest.raises(goauth.OAuthError, match="refresh_token"):
        goauth.exchange_code_for_tokens(
            CLIENT_CONFIG, code="c", redirect_uri="http://127.0.0.1:1/", pkce=pkce, post_fn=fake_post
        )


def test_exchange_success_and_token_roundtrip(tmp_path):
    pkce = goauth.new_pkce_pair()

    def fake_post(url, data):
        assert data["code_verifier"] == pkce.verifier  # PKCE actually sent
        return {"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600}

    token = goauth.exchange_code_for_tokens(
        CLIENT_CONFIG, code="c", redirect_uri="http://127.0.0.1:1/", pkce=pkce, post_fn=fake_post
    )
    path = tmp_path / "token.json"
    goauth.save_token_file(path, token)
    assert (path.stat().st_mode & 0o777) == 0o600
    reloaded = goauth.load_token_file(path)
    assert reloaded["refresh_token"] == "rt1"


def test_refresh_updates_access_token_and_rotates_refresh_token(tmp_path):
    path = tmp_path / "token.json"
    goauth.save_token_file(path, {"access_token": "old", "refresh_token": "rt-old"})

    def fake_post(url, data):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "rt-old"
        return {"access_token": "new-at", "refresh_token": "rt-new", "expires_in": 3600}

    at = goauth.refresh_access_token(CLIENT_CONFIG, path, post_fn=fake_post)
    assert at == "new-at"
    assert goauth.load_token_file(path)["refresh_token"] == "rt-new"


def test_refresh_without_refresh_token_on_file_raises():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path

        path = Path(d) / "token.json"
        goauth.save_token_file(path, {"access_token": "old"})  # no refresh_token
        with pytest.raises(goauth.OAuthError):
            goauth.refresh_access_token(CLIENT_CONFIG, path, post_fn=lambda u, d: {})


def test_refresh_propagates_provider_error():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path

        path = Path(d) / "token.json"
        goauth.save_token_file(path, {"access_token": "old", "refresh_token": "rt"})
        with pytest.raises(goauth.OAuthError):
            goauth.refresh_access_token(
                CLIENT_CONFIG, path, post_fn=lambda u, d: {"error": "invalid_grant"}
            )


def test_loopback_receiver_captures_code_and_checks_state():
    port = _free_port()
    result = {}

    def server_thread():
        result["code"] = goauth.run_loopback_and_get_code(port=port, expected_state="expected", timeout_s=5)

    t = threading.Thread(target=server_thread)
    t.start()
    t.join(0.2)  # let the server start listening
    urllib.request.urlopen(f"http://127.0.0.1:{port}/?code=abc123&state=expected", timeout=5).read()
    t.join(5)
    assert result["code"] == "abc123"


def test_loopback_receiver_rejects_state_mismatch():
    port = _free_port()
    outcome = {}

    def server_thread():
        try:
            goauth.run_loopback_and_get_code(port=port, expected_state="expected", timeout_s=5)
        except goauth.OAuthError as exc:
            outcome["error"] = exc

    t = threading.Thread(target=server_thread)
    t.start()
    t.join(0.2)
    urllib.request.urlopen(f"http://127.0.0.1:{port}/?code=abc123&state=WRONG", timeout=5).read()
    t.join(5)
    assert "state mismatch" in str(outcome["error"])
