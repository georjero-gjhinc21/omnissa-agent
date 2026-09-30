"""Loads config/partners.yaml -- the partner Gmail-label allowlist.

Deliberately NOT a general YAML parser (no new dependency -- this
project has stayed stdlib-only throughout, see google_oauth.py's own
"implemented stdlib-only" precedent). config/partners.yaml's shape is
narrow and fixed (a top-level ``partners:`` key, a list of two-key
mappings), so a small, explicit line-by-line parser tailored exactly
to what this file's own generator produces is safer than depending on
a general-purpose parser's full feature surface for a config this
simple. Comments (anything after ``#``) and blank lines are always
ignored, which is also how the allowlist's own commented-out,
not-yet-verified entries (see the file itself) stay inert until
uncommented.

``gmail_ingest.run_ingestion`` refuses any label not returned by this
loader -- there is no other code path that can request an arbitrary
label, so "refuse any other label" is structural, not a runtime check
that could be bypassed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "partners.yaml"


@dataclass(frozen=True)
class PartnerConfig:
    id: str
    label: str


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_partners_yaml(text: str) -> list[PartnerConfig]:
    """Parses exactly the shape this project's config/partners.yaml
    uses -- a ``partners:`` key followed by ``- id: <x>`` / ``label:
    <y>`` pairs, two-space indented, one pair per partner. Anything
    else (a different structure, YAML features this doesn't need) is
    out of scope for this narrow parser by design."""
    partners: list[PartnerConfig] = []
    current_id: str | None = None
    current_label: str | None = None

    def _flush():
        if current_id is not None and current_label is not None:
            partners.append(PartnerConfig(id=current_id, label=current_label))

    for raw_line in text.splitlines():
        line = _strip_comment(raw_line)
        stripped = line.strip()
        if not stripped or stripped == "partners:":
            continue
        if stripped.startswith("- id:"):
            _flush()
            current_id = _unquote(stripped[len("- id:"):])
            current_label = None
        elif stripped.startswith("label:"):
            current_label = _unquote(stripped[len("label:"):])
    _flush()
    return partners


def load_partner_allowlist(path: Path | str | None = None) -> list[PartnerConfig]:
    """Loads the allowlist from `path` (default config/partners.yaml).

    Falls back to a single-entry allowlist (id="omnissa", the real
    production label) if the resolved config file doesn't exist --
    this preserves every caller/test written before this file existed,
    rather than silently ingesting nothing at all.
    """
    resolved = Path(path) if path else DEFAULT_CONFIG_PATH
    if not resolved.exists():
        from .gmail_ingest import EXPECTED_LABEL_NAME  # deferred: avoid a module-load cycle

        return [PartnerConfig(id="omnissa", label=EXPECTED_LABEL_NAME)]
    return parse_partners_yaml(resolved.read_text())
