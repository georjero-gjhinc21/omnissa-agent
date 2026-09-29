import pytest

from omnissa_agent.gmail_boundary import verify_boundary


def test_blocked_when_connector_auth_fails():
    def list_labels_fn():
        raise RuntimeError("requires re-authorization (token expired)")

    def search_fn(q):
        raise AssertionError("must not be called when listing failed")

    result = verify_boundary(list_labels_fn=list_labels_fn, search_fn=search_fn)
    assert result.status == "BLOCKED"
    assert "re-authorization" in result.reason


def test_blocked_when_no_omnissa_label_exists():
    def list_labels_fn():
        return {"account": "someone@example.com", "labels": [{"id": "INBOX", "name": "INBOX"}]}

    def search_fn(q):
        raise AssertionError("must not be called")

    result = verify_boundary(list_labels_fn=list_labels_fn, search_fn=search_fn)
    assert result.status == "BLOCKED"
    assert "omnissa" in result.reason.lower()


def test_blocked_when_label_name_is_ambiguous():
    def list_labels_fn():
        return {
            "account": "someone@example.com",
            "labels": [
                {"id": "L1", "name": "Omnissa"},
                {"id": "L2", "name": "@omnissa.com"},
            ],
        }

    def search_fn(q):
        raise AssertionError("must not be called")

    result = verify_boundary(list_labels_fn=list_labels_fn, search_fn=search_fn)
    assert result.status == "BLOCKED"
    assert "ambiguous" in result.reason.lower()


def test_verified_on_exact_single_label_and_working_scoped_query():
    def list_labels_fn():
        return {
            "account": "someone@example.com",
            "labels": [{"id": "Label_42", "name": "@omnissa.com"}],
        }

    calls = []

    def search_fn(q):
        calls.append(q)
        return {"threads": [{"id": "t1"}]}

    result = verify_boundary(list_labels_fn=list_labels_fn, search_fn=search_fn)
    assert result.status == "VERIFIED"
    assert result.label_id == "Label_42"
    assert calls == ["label:Label_42"]


def test_blocked_when_scoped_query_itself_fails():
    def list_labels_fn():
        return {"account": "x@example.com", "labels": [{"id": "L1", "name": "Omnissa"}]}

    def search_fn(q):
        raise RuntimeError("wrong account")

    result = verify_boundary(list_labels_fn=list_labels_fn, search_fn=search_fn)
    assert result.status == "BLOCKED"
    assert "wrong account" in result.reason
