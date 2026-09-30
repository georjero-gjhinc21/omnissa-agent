"""Bounded pilot run: lock -> checkpoint -> Agent A -> Agent B -> report.

Designed for a short, resumable scheduled job, not an infinite loop:
- single-instance lock (no overlapping runs)
- a total deadline (partial progress is fine; hanging is not)
- capped pagination (``max_messages``)
- checkpoint/dedup persisted outside git
- a clean DEFERRED state if the LLM step fails outright
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import agent_a, agent_b, router
from . import state as state_mod
from .agent_a import AgentAResult
from .baseline import Baseline
from .agent_b import DraftEmail
from .lock import AlreadyRunningError, SingleInstanceLock
from .sources import GmailMessage


@dataclass
class PilotReport:
    email_status: str
    messages_seen: int
    findings_count: int
    duplicates_skipped: int
    drafts_count: int
    deadline_hit: bool
    brief_markdown: str
    drafts: list[DraftEmail] = field(default_factory=list)
    llm_deferred: bool = False
    llm_backend: str = ""


def run_pilot(
    *,
    source,
    email_status: str,
    max_messages: int = 50,
    deadline_seconds: float = 120.0,
    use_llm: bool = False,
    llm_combo: str = router.LOCAL_ONLY_COMBO,
    state_base: Path | None = None,
    demo_findings: list[agent_a.Finding] | None = None,
    focus_category: str | None = None,
    baseline: Baseline | None = None,
    partner_ids: list[str] | None = None,
    partner_baselines: dict[str, Baseline | None] | None = None,
) -> PilotReport:
    start = time.monotonic()
    try:
        with SingleInstanceLock(base=state_base):
            st = state_mod.load_state(state_base)
            messages: list[GmailMessage] = source.fetch(max_messages=max_messages)

            deadline_hit = (time.monotonic() - start) > deadline_seconds
            seen_before = set(st.get("seen_ids", []))
            result: AgentAResult = agent_a.classify_messages(messages, seen_ids=seen_before)

            for f in result.findings:
                state_mod.mark_seen(st, f.message_id)
            st["last_run"] = {
                "email_status": email_status,
                "messages_seen": len(messages),
                "findings": len(result.findings),
            }
            state_mod.save_state(st, state_base)

            # partner_ids given (the real production classify path) ->
            # one section per configured partner, always, even with
            # zero findings. Otherwise (scan/brief/manual, and every
            # test predating partner-ops) -> the original flat brief,
            # completely unchanged.
            if partner_ids is not None:
                brief = agent_a.build_partner_ops_brief(
                    result,
                    email_status=email_status,
                    partner_ids=partner_ids,
                    partner_baselines=partner_baselines,
                    use_llm=use_llm,
                    llm_combo=llm_combo,
                    focus_category=focus_category,
                )
            else:
                brief = agent_a.build_brief(
                    result,
                    email_status=email_status,
                    use_llm=use_llm,
                    llm_combo=llm_combo,
                    focus_category=focus_category,
                    baseline=baseline,
                )

            findings_for_drafting = list(result.findings) + list(demo_findings or [])
            drafts = agent_b.draft_from_findings(findings_for_drafting)

            return PilotReport(
                email_status=email_status,
                messages_seen=len(messages),
                findings_count=len(result.findings),
                duplicates_skipped=len(result.skipped_duplicate_ids),
                drafts_count=len(drafts),
                deadline_hit=deadline_hit,
                brief_markdown=brief,
                drafts=drafts,
                llm_deferred=result.llm_deferred,
                llm_backend=result.llm_backend,
            )
    except AlreadyRunningError:
        return PilotReport(
            email_status="SKIPPED_ALREADY_RUNNING",
            messages_seen=0,
            findings_count=0,
            duplicates_skipped=0,
            drafts_count=0,
            deadline_hit=False,
            brief_markdown="Another instance is already running; this run exited cleanly.",
        )
