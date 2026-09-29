"""Minimal, stdlib-only OAuth2 "Desktop app" flow for Gmail read-only access.

No google-auth / google-api-python-client dependency (not installed on
this box, and not worth adding for one scope). This module never prints
or logs a client secret, authorization code, access token, or refresh
token -- only status strings. It builds the authorization URL and stops
there: opening it and completing consent is the operator's action, not
this code's.

Credential/token files live OUTSIDE this repo (default
``~/.config/omnissa-agent-google/``), mode 700 dir / 600 files. See
docs/gmail-ingestion-security-review.md for why file permissions alone
do not isolate this from other processes running as the same OS user.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import stat
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
DEFAULT_CRED_DIR = Path(
    os.environ.get("OMNISSA_GOOGLE_CRED_DIR", os.path.expanduser("~/.config/omnissa-agent-google"))
)
LOOPBACK_HOST = "127.0.0.1"


class OAuthError(RuntimeError):
    pass


class InsecurePermissionsError(OAuthError):
    """A credential file is readable by more than its owner. Refuse, don't
    silently proceed or auto-fix -- the operator should know why."""


def check_private_file(path: Path) -> None:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise InsecurePermissionsError(
            f"{path} has mode {oct(mode)} (group/other accessible) -- "
            f"refusing to use it. Fix with: chmod 600 {path}"
        )


@dataclass
class PKCEPair:
    verifier: str
    challenge: str


def new_pkce_pair() -> PKCEPair:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PKCEPair(verifier=verifier, challenge=challenge)


def ensure_cred_dir(path: Path | None = None) -> Path:
    d = path or DEFAULT_CRED_DIR
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)  # mkdir's mode is umask-affected; enforce it explicitly
    return d


def load_client_config(client_secret_path: Path) -> dict:
    """Read the downloaded 'Desktop app' OAuth client JSON.

    Never logs its contents. Validates shape only.
    """
    check_private_file(client_secret_path)
    with open(client_secret_path) as f:
        data = json.load(f)
    section = data.get("installed") or data.get("web")
    if not section or "client_id" not in section or "client_secret" not in section:
        raise OAuthError("client secret JSON missing expected 'installed' client_id/client_secret")
    return section


def build_authorization_url(
    client_config: dict, *, redirect_uri: str, state: str, pkce: PKCEPair
) -> str:
    params = {
        "client_id": client_config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        self.server.result = urllib.parse.parse_qs(parsed.query)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorization received. You can close this tab.</body></html>")

    def log_message(self, fmt, *args):
        return  # never log the request line -- it contains the auth code


def run_loopback_and_get_code(*, port: int, expected_state: str, timeout_s: int = 300) -> str:
    """Block until the OAuth redirect lands on 127.0.0.1:<port>, return the code.

    Only ever invoked interactively by the operator completing the
    browser handoff -- never by an unattended/scheduled run.
    """
    server = http.server.HTTPServer((LOOPBACK_HOST, port), _RedirectHandler)
    server.timeout = timeout_s
    server.result = None  # type: ignore[attr-defined]
    server.handle_request()  # blocks for exactly one request or until timeout
    server.server_close()

    result = server.result  # type: ignore[attr-defined]
    if result is None:
        raise OAuthError("no redirect received within timeout -- authorization was not completed")
    flat = {k: v[0] for k, v in result.items()}
    if flat.get("state") != expected_state:
        raise OAuthError("state mismatch on OAuth redirect -- possible CSRF, aborting")
    if "error" in flat:
        raise OAuthError(f"authorization denied: {flat['error']}")
    if "code" not in flat:
        raise OAuthError("no authorization code in redirect")
    return flat["code"]


def _default_post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def exchange_code_for_tokens(
    client_config: dict,
    *,
    code: str,
    redirect_uri: str,
    pkce: PKCEPair,
    post_fn: Callable[[str, dict], dict] = _default_post_form,
) -> dict:
    payload = {
        "client_id": client_config["client_id"],
        "client_secret": client_config["client_secret"],
        "code": code,
        "code_verifier": pkce.verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    resp = post_fn(TOKEN_ENDPOINT, payload)
    if "error" in resp:
        raise OAuthError(f"token exchange failed: {resp['error']}")
    if "refresh_token" not in resp:
        raise OAuthError(
            "no refresh_token in response -- re-run with prompt=consent "
            "(Google omits it on repeat consents without that)"
        )
    return resp


def save_token_file(token_path: Path, token_data: dict) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = token_path.with_suffix(".tmp")
    to_write = dict(token_data)
    to_write["obtained_at"] = time.time()
    with tmp.open("w") as f:
        json.dump(to_write, f)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 600
    tmp.replace(token_path)


def load_token_file(token_path: Path) -> dict:
    check_private_file(token_path)
    with open(token_path) as f:
        return json.load(f)


def refresh_access_token(
    client_config: dict,
    token_path: Path,
    *,
    post_fn: Callable[[str, dict], dict] = _default_post_form,
) -> str:
    """Exchange the stored refresh_token for a fresh access_token.

    This is the ONLY call a scheduled/unattended run makes -- no browser,
    no new consent, as long as the refresh_token is still valid (see
    docs/gmail-ingestion-security-review.md for testing-mode / revocation
    caveats that can invalidate it).
    """
    token = load_token_file(token_path)
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise OAuthError("no refresh_token on file -- re-run the interactive authorization handoff")

    payload = {
        "client_id": client_config["client_id"],
        "client_secret": client_config["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    resp = post_fn(TOKEN_ENDPOINT, payload)
    if "error" in resp:
        raise OAuthError(f"token refresh failed: {resp['error']}")

    token["access_token"] = resp["access_token"]
    token["expires_in"] = resp.get("expires_in", 3600)
    token["refresh_token"] = resp.get("refresh_token", refresh_token)  # Google may rotate it
    save_token_file(token_path, token)
    return token["access_token"]
