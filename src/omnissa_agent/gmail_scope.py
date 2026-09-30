"""Gmail scope guard: read-only, exactly one label.

Originally written against a DAY-ONE placeholder mailbox before real
production ingest existed (``consult@gjh-inc.com``, label ``"Omnissa"``
-- a label that was never real). Confirmed stale (2026-09-30): this
guard was never actually wired into the real ingest path at all --
``gmail_ingest.py`` enforces the same two constraints independently,
via its own ``verify_account``/``resolve_label``, against the real
production values (``george@gjh-inc.com``, exact label
``Archive_/@omnissa.com``).

The single source of truth for both constants is now
``gmail_ingest.py`` -- this module imports them rather than duplicating
its own copies, so the two can never drift apart again the way they
already had. ``gmail_ingest.run_ingestion`` calls ``check_fetch_args``
as a second, redundant guard layer right after resolving the real
account/label -- defense in depth, never a replacement for its own
checks, and it can never actually fire in normal operation since both
sides now read from the same constants.
"""

from .gmail_ingest import EXPECTED_ACCOUNT, EXPECTED_LABEL_NAME

ALLOWED_LABEL_ID = EXPECTED_LABEL_NAME
ALLOWED_ACCOUNT = EXPECTED_ACCOUNT
READONLY = True  # unconditional -- there is no write path anywhere in this codebase


class ScopeError(ValueError):
    """Raised when a fetch would leave the allowed label/account."""


def build_query(user_query=""):
    """Return the Gmail search query constrained to the allowed label.

    NOT what the real production path actually uses -- gmail_ingest.py
    never builds a free-text query string at all; it lists messages by
    the resolved label ID directly via the API's own ``labelIds``
    parameter (see gmail_ingest.py's module docstring: "no free-text
    query, no in:anywhere"). This function is kept for any future/
    alternate fetch path that might want a query string, with the same
    widening guard either way.
    """
    user_query = (user_query or "").strip()
    if "in:all" in user_query or "in:anywhere" in user_query:
        raise ScopeError(f"query must not widen beyond label:{ALLOWED_LABEL_ID}")
    base = f"label:{ALLOWED_LABEL_ID}"
    return f"{base} {user_query}".strip() if user_query else base


def check_fetch_args(*, account, label_ids, readonly=True):
    """Validate a fetch call. Raises ScopeError on any violation."""
    if account != ALLOWED_ACCOUNT:
        raise ScopeError(f"account {account!r} not allowed (want {ALLOWED_ACCOUNT!r})")
    if not label_ids or list(label_ids) != [ALLOWED_LABEL_ID]:
        raise ScopeError(
            f"label_ids must be exactly [{ALLOWED_LABEL_ID!r}], got {label_ids!r}"
        )
    if not readonly:
        raise ScopeError("writes/sends are disabled on day one (readonly)")
    return True
