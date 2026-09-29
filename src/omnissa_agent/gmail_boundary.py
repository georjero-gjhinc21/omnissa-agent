"""Verify the Gmail account + label boundary BEFORE trusting any ingestion.

Do not assume a label name or account. There have been three different
claims across this project's own history (repo docs say account
``consult@gjh-inc.com`` / label ``Omnissa``; operator instructions have
separately said the connected account / label ``@omnissa.com``). This
module never guesses -- it calls the live connector (injected, so it's
testable without one) and only reports VERIFIED when exactly one label
matching an Omnissa-ish hint exists and a label-restricted query against
it succeeds without widening scope.

This module has NO write-capable calls: it only accepts read callables
(``list_labels_fn``, ``search_fn``) and never imports send/draft/label/
trash tools. There is nothing here for an unattended runtime to misuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

OMNISSA_HINTS = ("omnissa",)  # case-insensitive substring match on label name


class GmailAuthError(RuntimeError):
    """The connector itself is not authorized (e.g. token expired)."""


@dataclass
class BoundaryResult:
    status: str  # "VERIFIED" or "BLOCKED"
    account: str | None = None
    label_id: str | None = None
    label_name: str | None = None
    reason: str = ""
    evidence: list[str] = field(default_factory=list)


def _candidate_labels(labels: Iterable[dict]) -> list[dict]:
    out = []
    for lbl in labels:
        name = (lbl.get("name") or "").lower()
        if any(hint in name for hint in OMNISSA_HINTS):
            out.append(lbl)
    return out


def verify_boundary(
    *,
    list_labels_fn: Callable[[], dict],
    search_fn: Callable[[str], dict],
    account_hint: str | None = None,
) -> BoundaryResult:
    """Discover the exact Omnissa-ish label and prove a scoped query works.

    ``list_labels_fn()`` -> {"labels": [{"id": ..., "name": ...}, ...], "account": ...}
    ``search_fn(query)`` -> {"threads": [...]} (or raises on auth failure)
    """
    try:
        listing = list_labels_fn()
    except Exception as exc:  # connector down / token expired / etc.
        return BoundaryResult(
            status="BLOCKED",
            reason=f"label listing failed: {exc}",
            evidence=[repr(exc)],
        )

    labels = listing.get("labels", [])
    account = listing.get("account") or account_hint
    candidates = _candidate_labels(labels)

    if len(candidates) == 0:
        return BoundaryResult(
            status="BLOCKED",
            account=account,
            reason="no label name contains 'omnissa' -- cannot assume one",
            evidence=[l.get("name", "") for l in labels],
        )
    if len(candidates) > 1:
        return BoundaryResult(
            status="BLOCKED",
            account=account,
            reason="multiple Omnissa-ish labels found -- ambiguous, needs a human pick",
            evidence=[l.get("name", "") for l in candidates],
        )

    label = candidates[0]
    query = f"label:{label['id']}"
    try:
        result = search_fn(query)
    except Exception as exc:
        return BoundaryResult(
            status="BLOCKED",
            account=account,
            label_id=label.get("id"),
            label_name=label.get("name"),
            reason=f"scoped query failed: {exc}",
            evidence=[repr(exc)],
        )

    return BoundaryResult(
        status="VERIFIED",
        account=account,
        label_id=label.get("id"),
        label_name=label.get("name"),
        reason="label-restricted query succeeded",
        evidence=[f"query={query!r}", f"threads_returned={len(result.get('threads', []))}"],
    )
