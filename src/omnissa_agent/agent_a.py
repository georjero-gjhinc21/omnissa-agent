"""Agent A -- Omnissa Intelligence: read-only classification, never action.

Classification is pure keyword/rule based over subject + snippet text
(metadata only -- see gmail_api.py's format=metadata fetch; there is no
message-body access anywhere in this codebase). This is a deliberate
design choice, not an oversight: rather than build and audit a second,
separately-bounded body-access policy, every message-derived finding
simply stays at the lowest confidence level ("Unverified") forever.
Confidence can only ever be raised by a HUMAN, out of band, after they
personally check an official source -- see ``agent_b``'s
``draft_from_finding`` and ``cli.py draft``, which is the only path
that ever produces a real, reviewable draft. Nothing in this module
raises its own confidence, marks itself confirmed, or triggers any
action from message content alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import router
from .router import AskResult, DeferredError
from .sources import GmailMessage

CATEGORY_KEYWORDS = {
    "Grant/Funding": ("grant", "mdf", "development fund", "marketing fund"),
    "Incentive": ("incentive", "rebate", "promotion"),
    "Training": ("training", "course", "enablement"),
    "Certification": ("certification", "cert", "voucher", "exam"),
    "Renewal": ("renewal", "expir",),
    "Deal Registration": ("deal registration", "opportunity registration"),
    "Access Request": ("access", "partner connect", "portal"),
    "Product/NFR": ("nfr", "evaluation", "test-drive", "proving ground"),
}


@dataclass
class Finding:
    message_id: str
    category: str
    confidence: str  # "Confirmed" | "Likely" | "Unverified"
    summary: str
    source_ref: str
    urgency: str = "Normal"
    due_date: str = "not stated"
    required_action: str = "Human review"


@dataclass
class AgentAResult:
    findings: list[Finding] = field(default_factory=list)
    skipped_duplicate_ids: list[str] = field(default_factory=list)
    brief_markdown: str = ""
    llm_deferred: bool = False
    llm_deferred_reason: str = ""
    llm_backend: str = ""  # e.g. "omni/ollama-local" -- which provider actually saw this text


def _categorize(text: str) -> str:
    lowered = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(k in lowered for k in keywords):
            return category
    return "General"


def classify_message(msg: GmailMessage) -> Finding:
    category = _categorize(f"{msg.subject} {msg.snippet}")
    # Confidence never exceeds "Unverified" from message content alone --
    # only an official public source or explicit human confirmation could
    # raise it, and neither is wired into this classifier.
    return Finding(
        message_id=msg.id,
        category=category,
        confidence="Unverified",
        summary=msg.subject,
        source_ref=f"gmail:{msg.id}",
        due_date="not stated",
        required_action="Human confirms with Omnissa/distributor before any action",
    )


def classify_messages(messages: list[GmailMessage], *, seen_ids: set[str]) -> AgentAResult:
    result = AgentAResult()
    fresh_seen = set(seen_ids)
    for msg in messages:
        if msg.id in fresh_seen:
            result.skipped_duplicate_ids.append(msg.id)
            continue
        fresh_seen.add(msg.id)
        result.findings.append(classify_message(msg))
    return result


def reorder_for_focus(findings: list[Finding], focus_category: str | None) -> list[Finding]:
    """Return a NEW list for rendering, with any finding whose category
    matches `focus_category` moved first -- stable order preserved
    within each group otherwise. Never mutates `findings`, never drops
    or alters an entry: reorder only, by explicit product decision
    (2026-09-30) -- a focus instruction must not hide anything, and
    must not change a finding's classification or urgency merely
    because it matched. `focus_category=None` (no valid, fresh
    instruction) returns an unchanged copy -- the normal brief.
    """
    if not focus_category:
        return list(findings)
    matching = [f for f in findings if f.category == focus_category]
    rest = [f for f in findings if f.category != focus_category]
    return matching + rest


def build_brief(
    result: AgentAResult,
    *,
    email_status: str,
    use_llm: bool = False,
    llm_combo: str = router.LOCAL_ONLY_COMBO,
    focus_category: str | None = None,
) -> str:
    """Build the markdown brief.

    ``llm_combo`` defaults to ``combo-private`` (Ollama only, fully
    local) -- NOT ``combo-continuous`` -- because real finding summaries
    can contain real subject-line text, and several combo-continuous
    steps are third-party free-tier providers with their own data-use
    policies (see docs/gmail-ingestion-security-review.md). Pass
    ``combo-continuous`` explicitly only when the operator has approved
    sending this content off-box (fine for synthetic/test data).

    ``focus_category``: set only from a validated, fresh
    ``focus.FocusInstruction`` -- see reorder_for_focus above. Anything
    else (no instruction, invalid, stale) must pass ``None`` here,
    which reproduces the exact unmodified brief.
    """
    lines = [
        "# GJH INC -- Omnissa Daily Brief",
        "",
        f"Authorized email label status: {email_status}",
        "",
    ]
    if focus_category:
        lines.append(
            f"Focus: {focus_category} (matching findings shown first this run -- "
            "nothing hidden, no classification or urgency changed)"
        )
        lines.append("")
    lines.append("## Findings")
    ordered_findings = reorder_for_focus(result.findings, focus_category)
    if not ordered_findings:
        lines.append("- none this run")
    for f in ordered_findings:
        lines.append(
            f"- [{f.category}] {f.summary} -- confidence={f.confidence}, "
            f"due={f.due_date}, action={f.required_action} (ref {f.source_ref})"
        )
    lines.append("")
    lines.append(f"Deduplicated (already seen): {len(result.skipped_duplicate_ids)}")
    lines.append("")
    lines.append("## Evidence and limitations")
    lines.append("- No external actions were taken.")
    text = "\n".join(lines)

    if use_llm and result.findings:
        try:
            summary: AskResult = router.ask(
                "Summarize these Omnissa partner findings for a busy operator "
                "in 3 bullet points, no invented facts, keep every stated "
                f"confidence level as-is:\n{text}",
                combo=llm_combo,
            )
            result.llm_backend = summary.backend
            text += f"\n\n## LLM summary ({llm_combo}, answered by {summary.backend})\n" + summary.text
        except DeferredError as exc:
            result.llm_deferred = True
            result.llm_deferred_reason = str(exc)
            text += f"\n\n## LLM summary ({llm_combo})\ndeferred: {exc}"
    return text
