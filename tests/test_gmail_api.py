import pytest

from omnissa_agent.gmail_api import GmailApiError, GmailAuthError, GmailRateLimitError, GmailReadonlyClient


def _client(responses):
    """responses: list of (status, body) returned in call order."""
    calls = []

    def fake_get(url, headers):
        calls.append((url, headers))
        return responses.pop(0)

    return GmailReadonlyClient("fake-token", http_get=fake_get), calls


def test_get_profile_sends_bearer_token():
    client, calls = _client([(200, {"emailAddress": "george@gjh-inc.com"})])
    profile = client.get_profile()
    assert profile["emailAddress"] == "george@gjh-inc.com"
    assert calls[0][1] == {"Authorization": "Bearer fake-token"}
    assert calls[0][0].endswith("/profile")


def test_401_raises_auth_error():
    client, _ = _client([(401, {"error": {"message": "invalid_token"}})])
    with pytest.raises(GmailAuthError):
        client.get_profile()


def test_429_raises_rate_limit_error():
    client, _ = _client([(429, {"error": {"message": "rate limited"}})])
    with pytest.raises(GmailRateLimitError):
        client.list_labels()


def test_403_with_quota_reason_raises_rate_limit_error():
    body = {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
    client, _ = _client([(403, body)])
    with pytest.raises(GmailRateLimitError):
        client.list_labels()


def test_403_without_quota_reason_raises_plain_api_error_not_rate_limit():
    body = {"error": {"message": "forbidden", "errors": [{"reason": "insufficientPermissions"}]}}
    client, _ = _client([(403, body)])
    with pytest.raises(GmailApiError) as exc_info:
        client.list_labels()
    assert not isinstance(exc_info.value, GmailRateLimitError)


def test_500_raises_plain_api_error():
    client, _ = _client([(500, {"error": {"message": "boom"}})])
    with pytest.raises(GmailApiError):
        client.list_labels()


def test_list_message_ids_sends_label_id_and_returns_next_token():
    body = {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "tok"}
    client, calls = _client([(200, body)])
    ids, next_token = client.list_message_ids(label_id="Label_42", max_results=10)
    assert ids == ["m1", "m2"]
    assert next_token == "tok"
    assert "labelIds=Label_42" in calls[0][0]
    assert "maxResults=10" in calls[0][0]


def test_list_message_ids_no_next_token_when_absent():
    client, _ = _client([(200, {"messages": []})])
    ids, next_token = client.list_message_ids(label_id="Label_42")
    assert ids == []
    assert next_token is None


def test_get_message_metadata_requests_metadata_format_only():
    client, calls = _client([(200, {"id": "m1", "labelIds": ["Label_42"]})])
    client.get_message_metadata("m1")
    url = calls[0][0]
    assert "format=metadata" in url
    assert "metadataHeaders=Subject" in url
    assert "/messages/m1" in url


def test_client_has_no_write_methods():
    forbidden = ("modify", "trash", "delete", "send", "insert", "import_", "draft")
    methods = [m for m in dir(GmailReadonlyClient) if not m.startswith("_")]
    for m in methods:
        assert not any(f in m.lower() for f in forbidden), f"unexpected write-capable method: {m}"
