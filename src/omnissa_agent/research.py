"""Stage 2 scaffolding -- the research-to-brief workflow described in
docs/autonomous-vision-and-open-decisions.md.

STATUS: data model, dedup/carry-forward, and brief rendering only.
There is NO live web/webinar/transcript discovery wired into any
scheduled path here -- `omnissa-analysis` has no external network
access by design (`IPAddressDeny=any` in its systemd unit), and giving
any identity that access is a real architecture decision that needs
its own review, not something to add silently inside this module.
Exercised today only via `cli.py research-demo`, a manual, offline
command using clearly-labeled SAMPLE data (see `sample_opportunities`
below) -- never scheduled, never touching a real credential, never
mixed into the real classify pipeline's own report.

Every field here is deliberately explicit about provenance --
confirmed-vs-inferred, an evidence reference, and access limitations
are first-class, not optional -- because a rendering that blurs this
distinction is exactly how "the agent watched the recording" gets
silently implied when it never had access to it. Nothing here
fabricates a transcript excerpt, a quotation, or a contact address: an
unverified contact route renders as unverified, a missing one renders
as missing, never guessed.

A "needs your decision" item is never inferred by a heuristic here --
`Opportunity.blocking_question`, when set, is a plain, explicit string
an operator or a human-reviewed process wrote. Rendering just surfaces
whatever is explicitly marked, so routine research never turns into
homework on its own.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceProvenance:
    source_url: str  # an official event page, a permitted public source, or "internal:<gmail id>"
    event_date: str
    speaker_attribution: str = ""  # a named session leader, only if publicly stated -- never invented
    transcript_available: bool = False
    access_limitations: str = ""  # e.g. "recording behind a login -- not accessed" -- state plainly


@dataclass(frozen=True)
class ContactRecommendation:
    name: str
    role: str
    organization: str
    contact_route_kind: str  # "verified_direct" | "generic_form" | "none_found"
    relevance_reason: str
    draft_outreach_angle: str  # DRAFT text only -- for human review, never sent
    verified_contact_route: str | None = None  # a real, publicly-listed address/URL, or None

    def __post_init__(self):
        if self.contact_route_kind == "none_found" and self.verified_contact_route:
            raise ValueError("none_found contact must not carry a route -- never guess one")
        if self.contact_route_kind != "none_found" and not self.verified_contact_route:
            raise ValueError(f"contact_route_kind={self.contact_route_kind!r} needs verified_contact_route")


@dataclass(frozen=True)
class RevenuePath:
    possible_action: str  # e.g. "propose an MDF-funded co-marketing webinar"
    customer_need: str
    evidence_for_demand: str
    next_step: str
    uncertainty: str  # explicit, never omitted for being inconvenient


@dataclass
class Opportunity:
    dedup_key: str  # stable across scans -- e.g. normalized (topic + event/date)
    topic: str  # same category vocabulary as agent_a.CATEGORY_KEYWORDS where applicable
    what_is_new: str
    why_it_matters: str
    confirmed: list[str] = field(default_factory=list)  # facts checked against an official/permitted source
    inferred: list[str] = field(default_factory=list)  # explicitly labeled inferred, never presented as confirmed
    confidence: str = "Unverified"  # same vocabulary as agent_a.Finding.confidence
    next_step: str = ""
    deadline: str | None = None
    provenance: SourceProvenance | None = None
    contact: ContactRecommendation | None = None
    revenue_path: RevenuePath | None = None
    first_seen: str = ""  # date this dedup_key was first surfaced -- set by dedupe_and_carry_forward
    carried_forward: bool = False  # True if unresolved from a prior brief rather than found again this run
    blocking_question: str | None = None  # set explicitly, never inferred -- see module docstring
    is_sample_data: bool = False  # rendering MUST flag this loudly, never blend with real findings


def dedupe_and_carry_forward(
    previous: list[Opportunity], current: list[Opportunity], *, today: str
) -> list[Opportunity]:
    """An opportunity already known by `dedup_key` is not duplicated --
    the newer scan's facts win, but `first_seen` carries over from when
    it was first noticed. An opportunity from `previous` that this scan
    did NOT find again is carried forward (`carried_forward=True`)
    instead of silently disappearing after one scan -- it stays in the
    brief until the operator resolves it or it's genuinely superseded.
    """
    prior_by_key = {o.dedup_key: o for o in previous}
    result: list[Opportunity] = []
    for opp in current:
        prior = prior_by_key.get(opp.dedup_key)
        first_seen = prior.first_seen if prior else (opp.first_seen or today)
        result.append(dataclasses.replace(opp, first_seen=first_seen, carried_forward=False))
    current_keys = {o.dedup_key for o in current}
    for opp in previous:
        if opp.dedup_key not in current_keys:
            result.append(dataclasses.replace(opp, carried_forward=True))
    return result


def _reorder(opportunities: list[Opportunity], focus_category: str | None) -> list[Opportunity]:
    if not focus_category:
        return list(opportunities)
    matching = [o for o in opportunities if o.topic == focus_category]
    rest = [o for o in opportunities if o.topic != focus_category]
    return matching + rest


def render_research_brief(opportunities: list[Opportunity], *, focus_category: str | None = None) -> str:
    """Markdown, in exactly the shape requested: what's new/why it
    matters, opportunities grouped by topic (focused first, nothing
    suppressed), who-to-contact, a ranked revenue-path section, and a
    needs-your-decision section limited to explicitly-flagged blockers.
    """
    ordered = _reorder(opportunities, focus_category)
    lines: list[str] = ["## Research notes"]
    if any(o.is_sample_data for o in ordered):
        lines.append("")
        lines.append(
            "**SAMPLE DATA -- not from a live source.** This section demonstrates the "
            "shape of the research workflow; see docs/autonomous-vision-and-open-decisions.md."
        )
    if focus_category:
        lines.append("")
        lines.append(f"Focus: {focus_category} (matching opportunities shown first; nothing hidden)")
    lines.append("")

    if not ordered:
        lines.append("- nothing new this run")
        return "\n".join(lines)

    lines.append("### What is new and why it matters")
    for o in ordered:
        carried = " (carried forward, still unresolved)" if o.carried_forward else ""
        lines.append(f"- **[{o.topic}]** {o.what_is_new}{carried} -- {o.why_it_matters}")
    lines.append("")

    lines.append("### Opportunities by topic")
    for o in ordered:
        lines.append(f"#### [{o.topic}] {o.what_is_new}")
        if o.provenance:
            p = o.provenance
            lines.append(f"- Source: {p.source_url} (event date {p.event_date})")
            if p.speaker_attribution:
                lines.append(f"- Speaker: {p.speaker_attribution}")
            lines.append(f"- Transcript available: {p.transcript_available}")
            if p.access_limitations:
                lines.append(f"- Access limitation: {p.access_limitations}")
        lines.append(f"- Confidence: {o.confidence}")
        if o.confirmed:
            lines.append(f"- Confirmed: {'; '.join(o.confirmed)}")
        if o.inferred:
            lines.append(f"- Inferred (not confirmed): {'; '.join(o.inferred)}")
        lines.append(f"- Next step: {o.next_step or 'not specified'}")
        lines.append(f"- Deadline: {o.deadline or 'none stated'}")
        lines.append(f"- First seen: {o.first_seen}")
        lines.append("")

    contacts = [o for o in ordered if o.contact]
    if contacts:
        lines.append("### Who to contact")
        for o in contacts:
            c = o.contact
            lines.append(f"- **{c.name}** ({c.role}, {c.organization}) -- re: [{o.topic}] {o.what_is_new}")
            lines.append(f"  - Why relevant: {c.relevance_reason}")
            if c.contact_route_kind == "none_found":
                lines.append("  - Contact route: none found -- do not guess an address")
            else:
                lines.append(f"  - Contact route ({c.contact_route_kind}): {c.verified_contact_route}")
            lines.append(f"  - Draft outreach angle (for your review, not sent): {c.draft_outreach_angle}")
        lines.append("")

    revenue = [o for o in ordered if o.revenue_path]
    if revenue:
        lines.append("### Revenue path (ranked by evidence, no claims of a sale/eligibility/margin)")
        for o in revenue:
            r = o.revenue_path
            lines.append(f"- **{r.possible_action}** -- re: [{o.topic}] {o.what_is_new}")
            lines.append(f"  - Customer need: {r.customer_need}")
            lines.append(f"  - Evidence for demand: {r.evidence_for_demand}")
            lines.append(f"  - Next step: {r.next_step}")
            lines.append(f"  - Uncertainty: {r.uncertainty}")
        lines.append("")

    blockers = [o for o in ordered if o.blocking_question]
    lines.append("### Needs your decision")
    if not blockers:
        lines.append("- none this run")
    else:
        for o in blockers:
            lines.append(f"- [{o.topic}] {o.blocking_question}")

    return "\n".join(lines)


def sample_opportunities() -> list[Opportunity]:
    """Clearly-labeled SAMPLE data (is_sample_data=True on every entry)
    demonstrating the shape of a representative opportunity, contact
    recommendation, and revenue path -- not derived from any real
    account, event, or person. Used only by `cli.py research-demo`.
    """
    return [
        Opportunity(
            dedup_key="sample-webinar-2026-q4-partner-summit",
            topic="Training",
            what_is_new="Sample: an Omnissa partner enablement webinar was announced for Q4",
            why_it_matters="Sample: attending keeps GJH INC current on partner program changes",
            confirmed=["Sample: event page lists a public registration link"],
            inferred=["Sample: likely covers the same program updates as last quarter's session"],
            confidence="Unverified",
            next_step="Sample: register via the official event page; recording access unknown until then",
            deadline="Sample: 2026-11-01 (registration closes)",
            provenance=SourceProvenance(
                source_url="https://example-omnissa-events.invalid/partner-summit-q4",
                event_date="2026-11-15",
                speaker_attribution="Sample: Jane Doe, Partner Programs Lead (as listed on the event page)",
                transcript_available=False,
                access_limitations="Sample: recording requires event registration -- not accessed, not claimed",
            ),
            contact=ContactRecommendation(
                name="Jane Doe",
                role="Partner Programs Lead",
                organization="Omnissa",
                contact_route_kind="generic_form",
                relevance_reason="Sample: listed as the session leader on the public event page",
                draft_outreach_angle="Sample draft: introduce GJH INC and ask about MDF eligibility for Q1 co-marketing -- FOR YOUR REVIEW, NOT SENT",
                verified_contact_route="https://example-omnissa-events.invalid/contact-partner-programs",
            ),
            revenue_path=RevenuePath(
                possible_action="Sample: propose an MDF-funded co-marketing webinar with a shared customer",
                customer_need="Sample: customer X has an open evaluation that this program could accelerate",
                evidence_for_demand="Sample: inferred from the event's stated program focus, not confirmed with the customer",
                next_step="Sample: confirm MDF eligibility with the contact above before any commitment",
                uncertainty="Sample: no confirmation yet that GJH INC qualifies for this specific fund",
            ),
            first_seen="2026-09-30",
            is_sample_data=True,
        ),
        Opportunity(
            dedup_key="sample-stale-renewal-follow-up",
            topic="Renewal",
            what_is_new="Sample: a renewal opportunity flagged two scans ago with no resolution yet",
            why_it_matters="Sample: demonstrates carry-forward -- this must not vanish after one scan",
            confidence="Unverified",
            next_step="Sample: awaiting your decision on which distributor to route this through",
            first_seen="2026-09-28",
            carried_forward=True,
            blocking_question="Sample: which distributor should handle this renewal -- needs your call, not a guess",
            is_sample_data=True,
        ),
    ]
