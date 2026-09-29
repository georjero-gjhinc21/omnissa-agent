"""Gmail scope guard: every fetch MUST stay inside label ``Omnissa``.

Day-one policy (see AGENTS.md / worksplit.md):
- read-only: no send, no label mutation
- mailbox: consult@gjh-inc.com, label ``Omnissa`` only
- helpers reject any call that omits/scopes beyond the label

Real Gmail API wiring lands after the user approves OAuth (readonly).
Until then these helpers define the interface + enforce the guard so
tests stay green and reviewers have something to check.
"""

ALLOWED_LABEL_ID = "Omnissa"
ALLOWED_ACCOUNT = "consult@gjh-inc.com"
READONLY = True  # day one: never send


class ScopeError(ValueError):
    """Raised when a fetch would leave the allowed label/account."""


def build_query(user_query=""):
    """Return the Gmail search query constrained to the Omnissa label."""
    user_query = (user_query or "").strip()
    if "in:all" in user_query or "in:anywhere" in user_query:
        raise ScopeError("query must not widen beyond label:Omnissa")
    base = "label:Omnissa"
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
