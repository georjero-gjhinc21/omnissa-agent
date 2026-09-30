"""Scope-guard tests: the agent must never leave the configured partner
label allowlist (config/partners.yaml).

Updated 2026-09-30 (twice) -- first from a day-one placeholder mailbox
(consult@gjh-inc.com, label "Omnissa") that was never real, to the real
single production label; now from a single label to the full partner
allowlist for the partner-ops extension. gmail_scope.py imports its
account constant from gmail_ingest.py and its label allowlist from
partners.py (both loaded from config/partners.yaml) rather than
maintaining its own copies -- see gmail_scope.py's module docstring for
why that drift was a real, confirmed problem, not a hypothetical one.
"""

import pytest

from omnissa_agent import gmail_scope, partners as partners_mod
from omnissa_agent.gmail_ingest import EXPECTED_ACCOUNT, EXPECTED_LABEL_NAME
from omnissa_agent.gmail_scope import (
    ALLOWED_LABEL_ID,
    ALLOWED_LABELS,
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
    """gmail_scope.py must import its account constant FROM
    gmail_ingest.py, not define its own -- this is what actually
    prevents the exact staleness this test file used to have
    (consult@ + "Omnissa")."""
    assert gmail_scope.ALLOWED_ACCOUNT is EXPECTED_ACCOUNT


def test_allowed_labels_matches_the_real_config_partners_yaml():
    """The real config/partners.yaml, as loaded by partners.py -- not a
    second, hand-maintained copy inside gmail_scope.py."""
    expected = tuple(p.label for p in partners_mod.load_partner_allowlist())
    assert ALLOWED_LABELS == expected
    assert "Archive_/@omnissa.com" in ALLOWED_LABELS
    assert "Archive_/@microsoft.com" in ALLOWED_LABELS
    assert "Archive_/@google.com" in ALLOWED_LABELS
    assert "Archive_/@nvidia.com" in ALLOWED_LABELS
    assert "Archive_/@planetbids.com" in ALLOWED_LABELS


def test_a_fake_unlisted_partner_label_is_refused():
    """The hard rule this whole guard exists for: only the yaml list is
    ever allowed. A label that looks plausible (another real company,
    formatted exactly like the others) but was never added to
    config/partners.yaml must still be refused."""
    with pytest.raises(ScopeError):
        check_fetch_args(account=EXPECTED_ACCOUNT, label_ids=["Archive_/@linkedin.com"])


def test_a_fake_label_is_refused_even_alongside_real_ones():
    with pytest.raises(ScopeError):
        check_fetch_args(
            account=EXPECTED_ACCOUNT,
            label_ids=["Archive_/@omnissa.com", "Archive_/@linkedin.com"],
        )


def test_custom_allowed_labels_override_is_honored_for_non_default_configs():
    """A caller using a non-default partners config (e.g. a test) can
    pass its own allowlist explicitly rather than being forced to match
    the real config/partners.yaml -- this is what keeps the redundant
    guard call inside run_ingestion from spuriously failing for any
    caller that supplies a custom `partners_config`."""
    check_fetch_args(
        account=EXPECTED_ACCOUNT,
        label_ids=["Archive_/@some-new-partner.com"],
        allowed_labels=["Archive_/@some-new-partner.com"],
    )
    with pytest.raises(ScopeError):
        check_fetch_args(
            account=EXPECTED_ACCOUNT,
            label_ids=["Archive_/@linkedin.com"],
            allowed_labels=["Archive_/@some-new-partner.com"],
        )


def test_barracuda_is_not_yet_in_the_allowlist():
    """Confirms the explicit operator instruction was followed: this
    session had no live Gmail credential to run `cli.py list-labels`
    and confirm the exact barracuda label name, so it stays commented
    out in config/partners.yaml -- not guessed, not added speculatively."""
    assert "Archive_/@barracuda.com" not in ALLOWED_LABELS
