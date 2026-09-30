"""Gmail scope guard: read-only, exactly the configured partner labels.

Originally written against a DAY-ONE placeholder mailbox before real
production ingest existed (``consult@gjh-inc.com``, label ``"Omnissa"``
-- a label that was never real). Confirmed stale (2026-09-30): this
guard was never actually wired into the real ingest path at all --
``gmail_ingest.py`` enforced the same constraints independently, via
its own ``verify_account``/``resolve_label``.

The single source of truth for the account is ``gmail_ingest.py``; for
the label allowlist it's ``config/partners.yaml`` (via ``partners.py``)
-- this module imports both rather than duplicating its own copies, so
none of the three can drift apart again the way account/label already
had. ``gmail_ingest.run_ingestion`` calls ``check_fetch_args`` as a
second, redundant guard layer right after resolving each partner's
label -- defense in depth, never a replacement for its own checks, and
it can never actually fire in normal operation since all three sides
now read from the same two sources.
"""

from . import partners as partners_mod
from .gmail_ingest import EXPECTED_ACCOUNT, EXPECTED_LABEL_NAME

ALLOWED_LABELS = tuple(p.label for p in partners_mod.load_partner_allowlist())
ALLOWED_LABEL_ID = EXPECTED_LABEL_NAME  # the historical single-partner ("omnissa") value
ALLOWED_ACCOUNT = EXPECTED_ACCOUNT
READONLY = True  # unconditional -- there is no write path anywhere in this codebase


class ScopeError(ValueError):
    """Raised when a fetch would leave the allowed account/label allowlist."""


def build_query(user_query=""):
    """Return a Gmail search query constrained to the historical single
    label. NOT what the real production path actually uses --
    gmail_ingest.py never builds a free-text query string at all; it
    lists messages by each resolved label ID directly via the API's own
    ``labelIds`` parameter (see gmail_ingest.py's module docstring: "no
    free-text query, no in:anywhere/in:all"). Kept for any future/
    alternate fetch path that might want a query string, with the same
    widening guard either way.
    """
    user_query = (user_query or "").strip()
    if "in:all" in user_query or "in:anywhere" in user_query:
        raise ScopeError(f"query must not widen beyond label:{ALLOWED_LABEL_ID}")
    base = f"label:{ALLOWED_LABEL_ID}"
    return f"{base} {user_query}".strip() if user_query else base


def check_fetch_args(*, account, label_ids, readonly=True, allowed_labels=ALLOWED_LABELS):
    """Validate a fetch call. `label_ids` may be one or more label
    NAMES (not Gmail's opaque per-account label ids) -- every one of
    them must be in `allowed_labels` (default: the real
    config/partners.yaml, loaded once at import time), or this raises.
    A fake/unlisted label (e.g. "Archive_/@linkedin.com") is refused
    even though this function has no idea LinkedIn isn't a partner --
    it only ever knows the allowlist, never a denylist.

    `allowed_labels` is overridable so a caller using a non-default
    partners config (tests; a future alternate deployment) can pass
    the SAME list it resolved its own labels from -- this call is
    defense in depth, re-validating "these came from somewhere on this
    caller's own allowlist," not a second, independently-maintained
    source of truth that could drift from a custom config.
    """
    if account != ALLOWED_ACCOUNT:
        raise ScopeError(f"account {account!r} not allowed (want {ALLOWED_ACCOUNT!r})")
    if not label_ids or any(name not in allowed_labels for name in label_ids):
        raise ScopeError(
            f"label_ids must all be in the allowed partner list {tuple(allowed_labels)!r}, got {list(label_ids)!r}"
        )
    if not readonly:
        raise ScopeError("writes/sends are disabled -- readonly, unconditionally")
    return True
