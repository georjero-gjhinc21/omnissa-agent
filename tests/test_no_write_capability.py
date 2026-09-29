"""Static guard: no write-capable Gmail symbol anywhere under src/.

This operationalizes "do not expose a write-capable connector to an
unattended agent" as a standing, automated check instead of a one-time
promise. If this test ever fails, someone introduced send/draft/trash/
label-mutation capability into the codebase -- that's a hard stop, not a
style nit.
"""

from pathlib import Path

FORBIDDEN_SUBSTRINGS = (
    "create_draft",
    "trash_message",
    "trash_thread",
    "label_message",
    "label_thread",
    "unlabel_",
    "mark_message_spam",
    "mark_thread_spam",
    "apply_sensitive",
    "smtp",
    "send_message",
    ".send(",
)

SRC = Path(__file__).resolve().parent.parent / "src"


def test_no_write_capable_symbols_in_src():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text().lower()
        for bad in FORBIDDEN_SUBSTRINGS:
            if bad in text:
                offenders.append((str(path), bad))
    assert offenders == [], f"write-capable symbols found: {offenders}"


def test_agent_b_has_no_network_or_mail_imports():
    text = (SRC / "omnissa_agent" / "agent_b.py").read_text()
    for bad in ("import smtplib", "import requests", "import urllib", "mcp__"):
        assert bad not in text
