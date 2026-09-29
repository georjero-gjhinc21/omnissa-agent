"""Agent B -- Consult Drafting: pure text in, text out. No mail I/O at all.

This module imports nothing network- or mailbox-capable. It cannot send
or create a Gmail draft even if a prompt inside a message told it to --
there is no such function here to call.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agent_a import Finding

DRAFTABLE_CONFIDENCE = {"Confirmed", "Likely"}
DEFAULT_RECIPIENT_PLACEHOLDER = "[Omnissa Partner Support Contact]"


@dataclass
class DraftEmail:
    to: str
    subject: str
    body: str
    purpose: str
    confirm_before_sending: str


def draft_from_finding(finding: Finding) -> DraftEmail | None:
    if finding.confidence not in DRAFTABLE_CONFIDENCE:
        return None
    subject = f"Question re: {finding.category} -- {finding.summary[:60]}"
    body = (
        f"Hello,\n\n"
        f"We're following up on the following item related to our Omnissa "
        f"partnership:\n\n"
        f"  {finding.summary}\n\n"
        f"Could you confirm the current status and next steps for GJH INC?\n\n"
        f"Thanks,\nconsult@gjh-inc.com\n\n"
        f"DRAFT ONLY -- NOT SENT"
    )
    return DraftEmail(
        to=DEFAULT_RECIPIENT_PLACEHOLDER,
        subject=subject,
        body=body,
        purpose=f"Clarify {finding.category.lower()} item ({finding.source_ref})",
        confirm_before_sending=(
            "Recipient is a placeholder, not a verified contact. Confidence "
            f"was recorded as {finding.confidence!r} -- confirm the underlying "
            f"claim against an official Omnissa source (ref {finding.source_ref}) "
            "before this is ever sent."
        ),
    )


def draft_from_findings(findings: list[Finding]) -> list[DraftEmail]:
    drafts = []
    for f in findings:
        d = draft_from_finding(f)
        if d is not None:
            drafts.append(d)
    return drafts
