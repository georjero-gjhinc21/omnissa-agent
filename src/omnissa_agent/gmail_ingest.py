"""Deterministic, fail-closed Gmail ingestion across the partner-label
allowlist (config/partners.yaml, see partners.py).

Every property below is a hard requirement enforced in code, not a
convention documented elsewhere:

- exact account match against ``EXPECTED_ACCOUNT`` (george@gjh-inc.com)
- exact label NAME match against each allowlisted partner's ``label``
  -- no substring, no case-fold, no sender-domain search as a
  substitute, and NO label outside the allowlist is ever requested --
  there is no code path here that can construct an arbitrary label
- messages are listed only by the resolved label ID (no free-text
  query, no ``in:anywhere``/``in:all``)
- each message is re-fetched by ID and its own ``labelIds`` must still
  contain the resolved label ID -- if the label was removed between the
  list and the get, the message is rejected, not included
- bounded pages/messages, dedup via the shared checkpoint (state.py)
- a total wall-clock deadline (``deadline_s``, default 90s) AND a total
  ``max_messages`` cap are shared ACROSS THE WHOLE allowlist (not
  per-partner) -- extending to more partners never multiplies the
  resource/time envelope a single run is bounded by. A slow run or a
  very large label produces a partial ``OK`` result with
  ``deadline_hit=True`` rather than running unbounded.
- a partner label that's missing or ambiguous is skipped (recorded in
  ``skipped_partners``, never silently dropped) -- it does NOT refuse
  the whole run, since other partners' mail is independent. Only if
  NONE of the allowlisted labels resolve at all does the run refuse
  (``LABEL_MISSING``), since that's a real configuration problem, not
  a should any subset of partners moved/renamed their label meanwhile.

No date-range query is applied on top of any label filter. That is
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

from . import partners as partners_mod
from . import state as state_mod
from .gmail_api import GmailApiError, GmailAuthError, GmailRateLimitError, GmailReadonlyClient
from .sources import GmailMessage

EXPECTED_ACCOUNT = "george@gjh-inc.com"
# Kept as the historical single-label constant -- still the "omnissa"
# entry's real value, still what partners.load_partner_allowlist()
# falls back to when no config/partners.yaml is present (preserves
# every caller/test written before partner-ops existed). Confirmed
# live via `cli.py list-labels` (2026-09-29): the one and only label
# containing "omnissa" out of 83 user labels on the real account --
# nested under a parent "Archive_" label (Gmail's API represents
# nesting as "/" in the name itself), an artifact of an Outlook-style
# migration. Operator confirmed this is the correct label.
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
    # Multi-partner: comma-joined summary of every label that resolved
    # successfully THIS run (each message's own `partner_id`/label
    # membership is the ground truth -- these two fields are a
    # human-readable summary for the email_status line, not something
    # code should parse back apart).
    label_id: str = ""
    label_name: str = ""
    messages: list[GmailMessage] = field(default_factory=list)
    duplicates_skipped: int = 0
    rejected_stale_label_ids: list[str] = field(default_factory=list)
    malformed_ids: list[str] = field(default_factory=list)
    reason: str = ""
    deadline_hit: bool = False  # stopped early -- partial result, not a failure
    skipped_partners: dict[str, str] = field(default_factory=dict)  # partner_id -> reason

    def to_json_dict(self) -> dict:
        """Restricted, sanitized serialization for the privilege-separated
        drop file -- ids/subject/snippet/sender/date/labels/partner_id
        only, never a token or any other credential-shaped field."""
        return {
            "status": self.status.value,
            "account": self.account,
            "label_id": self.label_id,
            "label_name": self.label_name,
            "messages": [
                {
                    "id": m.id,
                    "subject": m.subject,
                    "snippet": m.snippet,
                    "sender": m.sender,
                    "date": m.date,
                    "label_ids": list(m.label_ids),
                    "partner_id": m.partner_id,
                }
                for m in self.messages
            ],
            "duplicates_skipped": self.duplicates_skipped,
            "rejected_stale_label_ids": list(self.rejected_stale_label_ids),
            "malformed_ids": list(self.malformed_ids),
            "reason": self.reason,
            "deadline_hit": self.deadline_hit,
            "skipped_partners": dict(self.skipped_partners),
        }

    @staticmethod
    def from_json_dict(d: dict) -> "IngestionResult":
        return IngestionResult(
            status=IngestStatus(d["status"]),
            account=d.get("account", ""),
            label_id=d.get("label_id", ""),
            label_name=d.get("label_name", ""),
            messages=[
                GmailMessage(
                    id=m["id"],
                    subject=m["subject"],
                    snippet=m["snippet"],
                    sender=m["sender"],
                    date=m["date"],
                    label_ids=tuple(m.get("label_ids", [])),
                    partner_id=m.get("partner_id", ""),
                )
                for m in d.get("messages", [])
            ],
            duplicates_skipped=d.get("duplicates_skipped", 0),
            rejected_stale_label_ids=list(d.get("rejected_stale_label_ids", [])),
            malformed_ids=list(d.get("malformed_ids", [])),
            reason=d.get("reason", ""),
            deadline_hit=d.get("deadline_hit", False),
            skipped_partners=dict(d.get("skipped_partners", {})),
        )


def merge_pending(old: "IngestionResult", new: "IngestionResult") -> "IngestionResult":
    """Combine an unconsumed prior drop with a fresh ingest run instead of
    overwriting it.

    Confirmed live (2026-09-29) that overwriting was a real bug, not a
    theoretical one: an ingest run fetched 50 real messages and wrote
    them to the drop file; the NEXT ingest run found 0 new messages
    (correctly -- they were already in ``gmail_ingested_ids``) and
    overwrote the drop file with an empty one before analysis ever read
    it. Those 50 messages are marked ingested (so will never be
    re-fetched) but were never classified -- permanently lost from the
    pipeline, though never altered on the Gmail side.

    Safe to concatenate message lists without a further dedup pass: ids
    across two ingest runs are guaranteed disjoint by construction,
    since ``run_ingestion`` never re-fetches an id already recorded in
    ``gmail_ingested_ids`` from a prior run.
    """
    if old.status != IngestStatus.OK:
        return new  # nothing meaningful to merge from a failed/refused prior snapshot
    merged_skips = dict(old.skipped_partners)
    merged_skips.update(new.skipped_partners)  # newer run's reason wins per partner
    return IngestionResult(
        status=new.status,
        account=new.account,
        label_id=new.label_id or old.label_id,
        label_name=new.label_name or old.label_name,
        messages=list(old.messages) + list(new.messages),
        duplicates_skipped=old.duplicates_skipped + new.duplicates_skipped,
        rejected_stale_label_ids=list(old.rejected_stale_label_ids) + list(new.rejected_stale_label_ids),
        malformed_ids=list(old.malformed_ids) + list(new.malformed_ids),
        reason=new.reason,
        deadline_hit=old.deadline_hit or new.deadline_hit,
        skipped_partners=merged_skips,
    )


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


def resolve_label(client: GmailReadonlyClient, label_name: str = EXPECTED_LABEL_NAME) -> "tuple[str, str]":
    """Resolve one EXACT label name to its (id, name). `label_name`
    defaults to the historical single-partner constant so any existing
    caller that doesn't pass one keeps working unchanged."""
    labels = client.list_labels()
    matches = [l for l in labels if l.get("name") == label_name]  # exact, case-sensitive
    if not matches:
        raise IngestionRefused(
            IngestStatus.LABEL_MISSING,
            f"no label named exactly {label_name!r} (substrings/renames don't count)",
        )
    if len(matches) > 1:
        raise IngestionRefused(
            IngestStatus.LABEL_AMBIGUOUS,
            f"{len(matches)} labels named exactly {label_name!r} -- needs a human to fix",
        )
    label = matches[0]
    return label["id"], label["name"]


def _extract_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _to_gmail_message(raw: dict, *, partner_id: str) -> GmailMessage | None:
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
        partner_id=partner_id,
    )


def run_ingestion(
    client: GmailReadonlyClient,
    *,
    max_pages: int = 5,
    max_messages: int = 50,
    deadline_s: float = 90.0,
    state_base: Path | None = None,
    partners_config: Path | str | None = None,
) -> IngestionResult:
    deadline_at = time.monotonic() + deadline_s

    try:
        account = verify_account(client)
    except IngestionRefused as exc:
        return IngestionResult(status=exc.status, reason=exc.reason)

    allowlist = partners_mod.load_partner_allowlist(partners_config)
    allowed_labels = [p.label for p in allowlist]

    # Second, redundant guard layer -- gmail_scope.py's own check, kept
    # deliberately in sync with EXPECTED_ACCOUNT above (it imports it,
    # not its own copy) and now checked against the FULL allowlist, not
    # a single label -- see gmail_scope.py's module docstring for the
    # real staleness incident this replaces. Deferred import: gmail_scope
    # imports FROM this module at its own top level, so importing it at
    # THIS module's top level would be a circular import -- safe here
    # since gmail_ingest is already fully loaded by the time
    # run_ingestion is ever called.
    from . import gmail_scope
    try:
        gmail_scope.check_fetch_args(
            account=account, readonly=True, label_ids=allowed_labels, allowed_labels=allowed_labels
        )
    except gmail_scope.ScopeError as exc:
        return IngestionResult(status=IngestStatus.UNEXPECTED_ERROR, account=account, reason=f"scope guard: {exc}")

    st = state_mod.load_state(state_base)
    all_messages: list[GmailMessage] = []
    duplicates_skipped = 0
    rejected_stale: list[str] = []
    malformed: list[str] = []
    skipped_partners: dict[str, str] = {}
    resolved_labels: list[str] = []
    listing_deadline_hit = False
    fetch_deadline_hit = False

    for partner in allowlist:
        if time.monotonic() >= deadline_at:
            listing_deadline_hit = True
            break
        if len(all_messages) >= max_messages:
            break

        try:
            label_id, label_name = resolve_label(client, partner.label)
        except IngestionRefused as exc:
            skipped_partners[partner.id] = exc.reason
            continue

        try:
            page_token = None
            label_ids_seen: list[str] = []
            for _ in range(max_pages):
                if time.monotonic() >= deadline_at:
                    listing_deadline_hit = True
                    break
                remaining = max_messages - len(all_messages) - len(label_ids_seen)
                if remaining <= 0:
                    break
                ids, page_token = client.list_message_ids(
                    label_id=label_id, page_token=page_token, max_results=min(50, remaining)
                )
                label_ids_seen.extend(ids)
                if not page_token:
                    break
        except GmailAuthError as exc:
            return IngestionResult(
                status=IngestStatus.AUTH_FAILURE, account=account, messages=all_messages,
                duplicates_skipped=duplicates_skipped, rejected_stale_label_ids=rejected_stale,
                malformed_ids=malformed, skipped_partners=skipped_partners, reason=str(exc),
            )
        except GmailRateLimitError as exc:
            return IngestionResult(
                status=IngestStatus.RATE_LIMITED, account=account, messages=all_messages,
                duplicates_skipped=duplicates_skipped, rejected_stale_label_ids=rejected_stale,
                malformed_ids=malformed, skipped_partners=skipped_partners, reason=str(exc),
            )
        except GmailApiError as exc:
            return IngestionResult(
                status=IngestStatus.UNEXPECTED_ERROR, account=account, messages=all_messages,
                duplicates_skipped=duplicates_skipped, rejected_stale_label_ids=rejected_stale,
                malformed_ids=malformed, skipped_partners=skipped_partners, reason=str(exc),
            )

        resolved_labels.append(label_name)
        new_ids = state_mod.dedup_new(st, label_ids_seen, key="gmail_ingested_ids")
        duplicates_skipped += len(label_ids_seen) - len(new_ids)

        for mid in new_ids:
            if time.monotonic() >= deadline_at:
                fetch_deadline_hit = True
                break
            if len(all_messages) >= max_messages:
                break
            try:
                raw = client.get_message_metadata(mid)
            except GmailAuthError as exc:
                return IngestionResult(
                    status=IngestStatus.AUTH_FAILURE, account=account,
                    label_id=",".join(resolved_labels), label_name=",".join(resolved_labels),
                    messages=all_messages, duplicates_skipped=duplicates_skipped,
                    rejected_stale_label_ids=rejected_stale, malformed_ids=malformed,
                    skipped_partners=skipped_partners, reason=str(exc),
                )
            except GmailRateLimitError as exc:
                return IngestionResult(
                    status=IngestStatus.RATE_LIMITED, account=account,
                    label_id=",".join(resolved_labels), label_name=",".join(resolved_labels),
                    messages=all_messages, duplicates_skipped=duplicates_skipped,
                    rejected_stale_label_ids=rejected_stale, malformed_ids=malformed,
                    skipped_partners=skipped_partners, reason=str(exc),
                )
            except GmailApiError:
                malformed.append(mid)
                continue

            if label_id not in (raw.get("labelIds") or []):
                rejected_stale.append(mid)  # label removed between list and get -- reject
                continue

            msg = _to_gmail_message(raw, partner_id=partner.id)
            if msg is None:
                malformed.append(mid)
                continue

            all_messages.append(msg)
            state_mod.mark_seen(st, mid, key="gmail_ingested_ids")

        if fetch_deadline_hit:
            break

    state_mod.save_state(st, state_base)

    if not resolved_labels:
        # Two different situations collapse to "nothing resolved" --
        # distinguish them: if every allowlisted partner was actually
        # ATTEMPTED (skipped_partners covers the whole allowlist) and
        # none resolved, that's a genuine configuration problem
        # (LABEL_MISSING). If the deadline/budget was exhausted before
        # even attempting some of them, that's just "ran out of time,"
        # not "nothing is configured" -- a later run with more budget
        # may resolve fine, so this must be a normal partial OK, not a
        # refusal (confirmed as a real distinction, not hypothetical:
        # without it, a slow FIRST partner could make an otherwise-fine
        # multi-partner run misreport as fully misconfigured).
        if len(skipped_partners) < len(allowlist):
            return IngestionResult(
                status=IngestStatus.OK,
                account=account,
                messages=[],
                duplicates_skipped=duplicates_skipped,
                skipped_partners=skipped_partners,
                deadline_hit=True,
            )
        return IngestionResult(
            status=IngestStatus.LABEL_MISSING,
            account=account,
            reason=f"none of {len(allowlist)} allowlisted partner label(s) resolved: {skipped_partners}",
            skipped_partners=skipped_partners,
        )

    return IngestionResult(
        status=IngestStatus.OK,
        account=account,
        label_id=",".join(resolved_labels),
        label_name=",".join(resolved_labels),
        messages=all_messages,
        duplicates_skipped=duplicates_skipped,
        rejected_stale_label_ids=rejected_stale,
        malformed_ids=malformed,
        deadline_hit=listing_deadline_hit or fetch_deadline_hit,
        skipped_partners=skipped_partners,
    )
