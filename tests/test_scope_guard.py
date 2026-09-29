"""Scope-guard tests: the agent must never leave label Omnissa."""

import pytest

from omnissa_agent.gmail_scope import (
    ALLOWED_LABEL_ID,
    ScopeError,
    build_query,
    check_fetch_args,
)

GOOD = {"account": "consult@gjh-inc.com", "label_ids": ["Omnissa"]}


def test_query_is_label_constrained():
    q = build_query("partner grants")
    assert "label:Omnissa" in q
    assert "in:all" not in q


def test_query_rejects_widening():
    with pytest.raises(ScopeError):
        build_query("in:all partner")


def test_fetch_requires_exact_label():
    check_fetch_args(readonly=True, **GOOD)
    with pytest.raises(ScopeError):
        check_fetch_args(account="consult@gjh-inc.com", label_ids=[], readonly=True)
    with pytest.raises(ScopeError):
        check_fetch_args(
            account="consult@gjh-inc.com",
            label_ids=["Omnissa", "INBOX"],
            readonly=True,
        )


def test_fetch_rejects_other_accounts_and_writes():
    with pytest.raises(ScopeError):
        check_fetch_args(account="other@gjh-inc.com", label_ids=["Omnissa"])
    with pytest.raises(ScopeError):
        check_fetch_args(readonly=False, **GOOD)


def test_allowed_label_constant():
    assert ALLOWED_LABEL_ID == "Omnissa"
