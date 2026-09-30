"""Scope-guard tests: the agent must never leave the production label.

Updated 2026-09-30 -- these used to test a day-one placeholder mailbox
(consult@gjh-inc.com, label "Omnissa") that was never real. Now tests
the actual production values, imported from gmail_ingest.py (the
single source of truth) rather than a second, independently-maintained
copy -- see gmail_scope.py's module docstring for why that drift was a
real, confirmed problem, not a hypothetical one.
"""

import pytest

from omnissa_agent.gmail_ingest import EXPECTED_ACCOUNT, EXPECTED_LABEL_NAME
from omnissa_agent.gmail_scope import (
    ALLOWED_LABEL_ID,
    ScopeError,
    build_query,
    check_fetch_args,
)

GOOD = {"account": EXPECTED_ACCOUNT, "label_ids": [EXPECTED_LABEL_NAME]}


def test_query_is_label_constrained():
    q = build_query("partner grants")
    assert f"label:{EXPECTED_LABEL_NAME}" in q
    assert "in:all" not in q


def test_query_rejects_widening():
    with pytest.raises(ScopeError):
        build_query("in:all partner")


def test_fetch_requires_exact_label():
    check_fetch_args(readonly=True, **GOOD)
    with pytest.raises(ScopeError):
        check_fetch_args(account=EXPECTED_ACCOUNT, label_ids=[], readonly=True)
    with pytest.raises(ScopeError):
        check_fetch_args(
            account=EXPECTED_ACCOUNT,
            label_ids=[EXPECTED_LABEL_NAME, "INBOX"],
            readonly=True,
        )


def test_fetch_rejects_other_accounts_and_writes():
    with pytest.raises(ScopeError):
        check_fetch_args(account="other@gjh-inc.com", label_ids=[EXPECTED_LABEL_NAME])
    with pytest.raises(ScopeError):
        check_fetch_args(readonly=False, **GOOD)


def test_allowed_label_constant_matches_the_real_production_label():
    assert ALLOWED_LABEL_ID == EXPECTED_LABEL_NAME == "Archive_/@omnissa.com"


def test_gmail_scope_and_gmail_ingest_can_never_drift_apart_again():
    """gmail_scope.py must import its constants FROM gmail_ingest.py,
    not define its own -- this is what actually prevents the exact
    staleness this test file used to have (consult@ + "Omnissa")."""
    from omnissa_agent import gmail_scope
    assert gmail_scope.ALLOWED_ACCOUNT is EXPECTED_ACCOUNT
    assert gmail_scope.ALLOWED_LABEL_ID is EXPECTED_LABEL_NAME
