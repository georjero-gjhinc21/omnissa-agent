"""Deterministic, fail-closed Gmail ingestion for the exact ``@omnissa.com`` label.

Every property below is a hard requirement enforced in code, not a
convention documented elsewhere:

- exact account match against ``EXPECTED_ACCOUNT`` (george@gjh-inc.com)
- exact label NAME match against ``EXPECTED_LABEL_NAME`` -- no substring,
  no case-fold, no sender-domain search as a substitute
- messages are listed only by the resolved label ID (no free-text query,
  no ``in:anywhere``)
- each message is re-fetched by ID and its own ``labelIds`` must still
  contain the resolved label ID -- if the label was removed between the
  list and the get, the message is rejected, not included
- bounded pages/messages, dedup via the shared checkpoint (state.py)
- a total wall-clock deadline (``deadline_s``, default 90s) checked
  between page fetches AND between per-message fetches -- a slow or very
  large label produces a partial ``OK`` result with ``deadline_hit=True``
  rather than running unbounded

No date-range query is applied on top of the label filter. That is
deliberate, not an oversight: filtering by ``q=after:...`` would be an
extra, broader query beyond "list only by the resolved label ID", and a
plain per-label listing always returns everything CURRENTLY labeled --
which is exactly what's needed to never skip an older message that gets
labeled after the last run. Recency is bounded by ``max_messages``/
``max_pages`` instead, not by date.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import state as state_mod
from .gmail_api import GmailApiError, GmailAuthError, GmailRateLimitError, GmailReadonlyClient
from .sources import GmailMessage

EXPECTED_ACCOUNT = "george@gjh-inc.com"
# EXACT match only. Confirmed live via `cli.py list-labels` (2026-09-29): this
# is the one and only label containing "omnissa" out of 83 user labels on the
# real account -- it's nested under a parent "Archive_" label (Gmail's API
# represents nesting as "/" in the name itself), an artifact of an
# Outlook-style migration. Operator confirmed this is the correct label.
EXPECTED_LABEL_NAME = "Archive_/@omnissa.com"


class IngestStatus(enum.Enum):
    OK = "OK"
    AUTH_FAILURE = "AUTH_FAILURE"
    WRONG_ACCOUNT = "WRONG_ACCOUNT"
    LABEL_MISSING = "LABEL_MISSING"
    LABEL_AMBIGUOUS = "LABEL_AMBIGUOUS"
    RATE_LIMITED = "RATE_LIMITED"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


class IngestionRefused(RuntimeError):
    def __init__(self, status: IngestStatus, reason: str):
        super().__init__(f"{status.value}: {reason}")
        self.status = status
        self.reason = reason


@dataclass
class IngestionResult:
    status: IngestStatus
    account: str = ""
    label_id: str = ""
    label_name: str = ""
    messages: list[GmailMessage] = field(default_factory=list)
    duplicates_skipped: int = 0
    rejected_stale_label_ids: list[str] = field(default_factory=list)
    malformed_ids: list[str] = field(default_factory=list)
    reason: str = ""
    deadline_hit: bool = False  # stopped early -- partial result, not a failure


def verify_account(client: GmailReadonlyClient) -> str:
    try:
        profile = client.get_profile()
    except GmailAuthError as exc:
        raise IngestionRefused(IngestStatus.AUTH_FAILURE, str(exc)) from exc
    email = profile.get("emailAddress", "")
    if email != EXPECTED_ACCOUNT:
        raise IngestionRefused(
            IngestStatus.WRONG_ACCOUNT,
            f"authenticated as {email!r}, expected exactly {EXPECTED_ACCOUNT!r}",
        )
    return email


def resolve_label(client: GmailReadonlyClient) -> "tuple[str, str]":
    labels = client.list_labels()
    matches = [l for l in labels if l.get("name") == EXPECTED_LABEL_NAME]  # exact, case-sensitive
    if not matches:
        raise IngestionRefused(
            IngestStatus.LABEL_MISSING,
            f"no label named exactly {EXPECTED_LABEL_NAME!r} (substrings/renames don't count)",
        )
    if len(matches) > 1:
        raise IngestionRefused(
            IngestStatus.LABEL_AMBIGUOUS,
            f"{len(matches)} labels named exactly {EXPECTED_LABEL_NAME!r} -- needs a human to fix",
        )
    label = matches[0]
    return label["id"], label["name"]


def _extract_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _to_gmail_message(raw: dict) -> GmailMessage | None:
    if "id" not in raw:
        return None
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []
    return GmailMessage(
        id=raw["id"],
        subject=_extract_header(headers, "Subject"),
        snippet=raw.get("snippet", ""),
        sender=_extract_header(headers, "From"),
        date=_extract_header(headers, "Date"),
        label_ids=tuple(raw.get("labelIds", [])),
    )


def run_ingestion(
    client: GmailReadonlyClient,
    *,
    max_pages: int = 5,
    max_messages: int = 50,
    deadline_s: float = 90.0,
    state_base: Path | None = None,
) -> IngestionResult:
    deadline_at = time.monotonic() + deadline_s

    try:
        account = verify_account(client)
        label_id, label_name = resolve_label(client)
    except IngestionRefused as exc:
        return IngestionResult(status=exc.status, reason=exc.reason)

    st = state_mod.load_state(state_base)
    all_ids: list[str] = []
    page_token = None
    listing_deadline_hit = False
    try:
        for _ in range(max_pages):
            if time.monotonic() >= deadline_at:
                listing_deadline_hit = True
                break
            ids, page_token = client.list_message_ids(
                label_id=label_id, page_token=page_token, max_results=min(50, max_messages)
            )
            all_ids.extend(ids)
            if not page_token or len(all_ids) >= max_messages:
                break
    except GmailAuthError as exc:
        return IngestionResult(status=IngestStatus.AUTH_FAILURE, account=account, reason=str(exc))
    except GmailRateLimitError as exc:
        return IngestionResult(status=IngestStatus.RATE_LIMITED, account=account, reason=str(exc))
    except GmailApiError as exc:
        return IngestionResult(status=IngestStatus.UNEXPECTED_ERROR, account=account, reason=str(exc))

    all_ids = all_ids[:max_messages]
    new_ids = state_mod.dedup_new(st, all_ids, key="gmail_ingested_ids")
    duplicates_skipped = len(all_ids) - len(new_ids)

    messages: list[GmailMessage] = []
    rejected_stale: list[str] = []
    malformed: list[str] = []
    fetch_deadline_hit = False

    for mid in new_ids:
        if time.monotonic() >= deadline_at:
            fetch_deadline_hit = True
            break
        try:
            raw = client.get_message_metadata(mid)
        except GmailAuthError as exc:
            return IngestionResult(
                status=IngestStatus.AUTH_FAILURE,
                account=account,
                label_id=label_id,
                label_name=label_name,
                messages=messages,
                duplicates_skipped=duplicates_skipped,
                rejected_stale_label_ids=rejected_stale,
                malformed_ids=malformed,
                reason=str(exc),
            )
        except GmailRateLimitError as exc:
            return IngestionResult(
                status=IngestStatus.RATE_LIMITED,
                account=account,
                label_id=label_id,
                label_name=label_name,
                messages=messages,
                duplicates_skipped=duplicates_skipped,
                rejected_stale_label_ids=rejected_stale,
                malformed_ids=malformed,
                reason=str(exc),
            )
        except GmailApiError:
            malformed.append(mid)
            continue

        if label_id not in (raw.get("labelIds") or []):
            rejected_stale.append(mid)  # label removed between list and get -- reject
            continue

        msg = _to_gmail_message(raw)
        if msg is None:
            malformed.append(mid)
            continue

        messages.append(msg)
        state_mod.mark_seen(st, mid, key="gmail_ingested_ids")

    state_mod.save_state(st, state_base)

    return IngestionResult(
        status=IngestStatus.OK,
        account=account,
        label_id=label_id,
        label_name=label_name,
        messages=messages,
        duplicates_skipped=duplicates_skipped,
        rejected_stale_label_ids=rejected_stale,
        malformed_ids=malformed,
        deadline_hit=listing_deadline_hit or fetch_deadline_hit,
    )
