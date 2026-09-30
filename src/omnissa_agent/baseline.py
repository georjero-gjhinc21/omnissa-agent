"""Parses docs/omnissa-partner-baseline.md's machine-readable block and
renders the brief's always-present scoreboard section.

Design: the baseline file stays human-owned prose (Agent-B's file, per
worksplit.md) with one small, clearly-delimited ``key: value`` block
appended specifically for this scoreboard -- never scraped from free
prose, which would be fragile and silently break on a wording change.

Confidence on every baseline-derived row is ``Unverified`` unless the
file's own ``sourced: true`` line says otherwise -- this module never
upgrades confidence on its own judgment; it only reports what the file
itself claims, exactly as agent_a.py's own metadata-only policy already
insists elsewhere. A missing/unreadable file renders as an honest
"not available" scoreboard, never a crash and never invented values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_BLOCK_START = "<!-- SCOREBOARD-DATA"
_BLOCK_END = "-->"


@dataclass(frozen=True)
class Baseline:
    fields: dict[str, str]

    @property
    def sourced(self) -> bool:
        return self.fields.get("sourced", "false").strip().lower() == "true"

    def get(self, key: str, default: str = "unknown") -> str:
        return self.fields.get(key, default)


def load_partner_baselines(baseline_dir: Path | str | None, partner_ids: list[str]) -> dict[str, "Baseline | None"]:
    """Loads ``<baseline_dir>/<id>-baseline.md`` for each id in
    `partner_ids` (e.g. from config/partners.yaml). A missing file for
    a given partner maps to None -- callers render an honest "not
    available" scoreboard for that partner, never a crash and never an
    invented status. `baseline_dir=None` maps every partner to None.
    """
    if baseline_dir is None:
        return {pid: None for pid in partner_ids}
    base = Path(baseline_dir)
    return {pid: load_baseline(base / f"{pid}-baseline.md") for pid in partner_ids}


def load_baseline(path: Path | None) -> Baseline | None:
    """Returns None on anything short of a fully-readable, well-formed
    block -- callers must render a graceful "not available" scoreboard
    in that case, never crash the whole brief over a missing/edited
    baseline file."""
    if path is None:
        return None
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    if _BLOCK_START not in text:
        return None
    block = text.split(_BLOCK_START, 1)[1]
    if _BLOCK_END not in block:
        return None
    block = block.split(_BLOCK_END, 1)[0]

    fields: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return Baseline(fields=fields)


def render_scoreboard(baseline: Baseline | None, findings: list) -> str:
    """`findings` is this run's classified list (agent_a.Finding) --
    used only to compute live counts (OSP/OTSP mentions this run,
    deal-registration-ready count); every other value comes from the
    baseline file, never inferred from message content.
    """
    confidence = "Confirmed (baseline file marked sourced)" if baseline and baseline.sourced else "Unverified"

    osp_mentions_this_run = sum(
        1 for f in findings if "osp" in f.summary.lower() or "otsp" in f.summary.lower()
    )
    deal_reg_ready = sum(1 for f in findings if f.category == "Deal Registration")

    lines = ["## Scoreboard"]
    if baseline is None:
        lines.append("- Baseline file not available -- see docs/omnissa-partner-baseline.md")
        lines.append(f"- Deal-registration-ready findings this run: {deal_reg_ready}")
        return "\n".join(lines)

    lines.append(
        f"- Renewal: {baseline.get('renewal_status')} "
        f"(completed {baseline.get('renewal_completed_date')}, "
        f"next {baseline.get('renewal_next_date')}, case {baseline.get('renewal_case')}) "
        f"-- confidence={confidence}"
    )
    lines.append(
        f"- OSP/OTSP: requested {baseline.get('osp_requested')}/{baseline.get('otsp_requested')}, "
        f"completed {baseline.get('osp_completed')}/{baseline.get('otsp_completed')} "
        f"(this run's related findings: {osp_mentions_this_run}) -- confidence={confidence}"
    )
    lines.append(
        "- Preferred distributors: "
        f"Commercial={baseline.get('distributor_commercial')}, "
        f"Healthcare={baseline.get('distributor_healthcare')}, "
        f"Public Sector={baseline.get('distributor_public_sector')} "
        f"(deadline {baseline.get('distributor_deadline')}) -- confidence={confidence}"
    )
    lines.append(f"- Deal-registration-ready findings this run: {deal_reg_ready}")
    lines.append(f"- Top human action: {baseline.get('top_human_action', 'none stated')}")
    return "\n".join(lines)
