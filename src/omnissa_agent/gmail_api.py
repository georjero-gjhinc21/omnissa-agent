"""Minimal, read-only Gmail REST v1 client.

Implements only the handful of GET endpoints this project needs:
profile, labels.list, messages.list (scoped to one label), messages.get
(metadata format only -- no body, no attachments). There is no modify,
trash, send, or insert method anywhere in this class -- not stubbed out,
not present -- so there is nothing here for an over-eager or compromised
caller to misuse. See tests/test_no_write_capability.py, which scans
this file too.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
HttpGetFn = Callable[[str, dict], "tuple[int, dict]"]


class GmailApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class GmailAuthError(GmailApiError):
    """401 -- expired/invalid access token."""


class GmailRateLimitError(GmailApiError):
    """429, or 403 with a rate/quota reason."""


def _default_http_get(url: str, headers: dict) -> "tuple[int, dict]":
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"error": {"message": raw[:300]}}
        return exc.code, body


def _error_message(body: dict, default: str) -> str:
    return (body.get("error") or {}).get("message", default)


def _is_rate_limited(status: int, body: dict) -> bool:
    if status == 429:
        return True
    if status == 403:
        reasons = [e.get("reason", "") for e in (body.get("error") or {}).get("errors", [])]
        return any("rate" in r.lower() or "quota" in r.lower() for r in reasons)
    return False


class GmailReadonlyClient:
    def __init__(self, access_token: str, *, http_get: HttpGetFn = _default_http_get):
        self._token = access_token
        self._get = http_get

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, path: str, params: dict | None = None) -> dict:
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        status, body = self._get(url, self._headers())
        if status == 401:
            raise GmailAuthError(status, _error_message(body, "unauthorized"))
        if _is_rate_limited(status, body):
            raise GmailRateLimitError(status, _error_message(body, "rate limited"))
        if status >= 400:
            raise GmailApiError(status, _error_message(body, "request failed"))
        return body

    def get_profile(self) -> dict:
        return self._call("/profile")

    def list_labels(self) -> list[dict]:
        return self._call("/labels").get("labels", [])

    def list_message_ids(
        self, *, label_id: str, page_token: str | None = None, max_results: int = 25
    ) -> "tuple[list[str], str | None]":
        params: dict = {"labelIds": label_id, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        body = self._call("/messages", params)
        ids = [m["id"] for m in body.get("messages", [])]
        return ids, body.get("nextPageToken")

    def get_message_metadata(self, message_id: str) -> dict:
        return self._call(
            f"/messages/{message_id}",
            {"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
        )
