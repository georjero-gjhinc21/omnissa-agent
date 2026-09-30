"""Validated, labeled "focus" instructions -- Stage 1 of the plan in
docs/autonomous-vision-and-open-decisions.md.

Deliberately narrow trust model: a focus instruction is honored ONLY if
a message carries the EXACT Gmail label ``FOCUS_LABEL_NAME`` -- never
inferred from body text, sender, or any other signal. Email bodies,
transcripts, and any other content anywhere in this pipeline are
untrusted evidence, never commands (see agent_a.py's own metadata-only
policy) -- a focus instruction is the ONE narrow exception, and even it
reads only the Subject header of a message the operator personally,
deliberately labeled themselves in their own already-verified mailbox
(the same account/boundary gmail_ingest.py enforces everywhere else).

Reorder-only, by explicit product decision (2026-09-30): a valid focus
instruction changes the ORDER findings are presented in, never what is
classified, never any finding's confidence/urgency, and never hides
anything. An invalid or stale instruction is exactly equivalent to no
instruction at all -- the normal brief, unchanged. See
agent_a.reorder_for_focus for the rendering-order logic itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .agent_a import CATEGORY_KEYWORDS
from .gmail_api import GmailApiError, GmailAuthError, GmailRateLimitError, GmailReadonlyClient

FOCUS_LABEL_NAME = "Agent-Focus"
KNOWN_CATEGORIES = frozenset(CATEGORY_KEYWORDS) | {"General"}
FOCUS_MAX_AGE_HOURS = 48.0
_SUBJECT_PATTERN = re.compile(r"^\s*focus\s*:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class FocusInstruction:
    category: str
    source_message_id: str

    def to_json_dict(self) -> dict:
        return {"category": self.category, "source_message_id": self.source_message_id}

    @staticmethod
    def from_json_dict(d: dict) -> "FocusInstruction | None":
        category = d.get("category")
        source_message_id = d.get("source_message_id")
        if category not in KNOWN_CATEGORIES or not source_message_id:
            return None  # corrupt/foreign file -- never trust blindly, ignore instead
        return FocusInstruction(category=category, source_message_id=source_message_id)


def parse_focus_subject(subject: str) -> str | None:
    """Return the requested category if `subject` matches the exact
    'Focus: <category>' format (case-insensitive) and the category is
    one of the known, canonical categories -- else None. Deliberately
    strict: no fuzzy matching, no substring category matching -- an
    unrecognized category is an invalid instruction, not a best-effort
    guess at what the operator might have meant.
    """
    m = _SUBJECT_PATTERN.match(subject or "")
    if not m:
        return None
    requested = m.group(1).strip()
    for known in KNOWN_CATEGORIES:
        if requested.lower() == known.lower():
            return known
    return None


def _is_stale(date_header: str, *, now: datetime, max_age_hours: float) -> bool:
    try:
        sent = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return True  # unparseable date -- treat as stale, never as fresh
    if sent is None:
        return True
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    age = now.astimezone(timezone.utc) - sent.astimezone(timezone.utc)
    return age > timedelta(hours=max_age_hours)


def resolve_focus_instruction(
    client: GmailReadonlyClient,
    *,
    now: datetime | None = None,
    max_age_hours: float = FOCUS_MAX_AGE_HOURS,
) -> FocusInstruction | None:
    """Best-effort, non-fatal: any problem here (label missing or
    ambiguous, an API error, an unparseable message) means "no focus
    this run" -- NEVER a refusal of the underlying scan/brief. Only the
    single MOST RECENT message under the label is considered; an
    operator changes focus by labeling a new message, and an old one
    simply ages out via `max_age_hours` with no separate "clear" step.
    """
    now = now or datetime.now(timezone.utc)
    try:
        labels = client.list_labels()
    except (GmailApiError, GmailAuthError, GmailRateLimitError):
        return None
    matches = [lbl for lbl in labels if lbl.get("name") == FOCUS_LABEL_NAME]
    if len(matches) != 1:
        return None  # missing or ambiguous -- not an error, just no focus this run

    try:
        ids, _ = client.list_message_ids(label_id=matches[0]["id"], max_results=1)
    except (GmailApiError, GmailAuthError, GmailRateLimitError):
        return None
    if not ids:
        return None

    try:
        raw = client.get_message_metadata(ids[0])
    except (GmailApiError, GmailAuthError, GmailRateLimitError):
        return None

    headers = (raw.get("payload") or {}).get("headers") or []
    subject = next((h.get("value", "") for h in headers if h.get("name", "").lower() == "subject"), "")
    date_header = next((h.get("value", "") for h in headers if h.get("name", "").lower() == "date"), "")

    category = parse_focus_subject(subject)
    if category is None:
        return None  # invalid format or unrecognized category -- ignored, not an error
    if _is_stale(date_header, now=now, max_age_hours=max_age_hours):
        return None

    return FocusInstruction(category=category, source_message_id=ids[0])
