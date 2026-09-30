"""Checkpoint / dedup state, kept OUTSIDE the git-tracked repo.

Never store full email bodies here -- ids, hashes, and short sanitized
summaries only. Default location is under the user's XDG state dir, not
inside the repo, so a stray ``git add -A`` can never pick it up.

Dedup is namespaced by ``key`` because two independent layers dedup on
Gmail message ids for different reasons: ``gmail_ingest.py`` tracks
"have we ever fetched this message" (key ``"gmail_ingested_ids"``),
while ``pipeline.py``/``agent_a.py`` track "has this message ever been
classified" (key ``"seen_ids"``, the default). They MUST stay separate:
sharing one bucket means ingestion's own bookkeeping makes every message
look like a duplicate to the classifier on the very run it was fetched.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

DEFAULT_STATE_DIR = Path(
    os.environ.get(
        "OMNISSA_AGENT_STATE_DIR",
        os.path.expanduser("~/.local/state/omnissa-agent"),
    )
)
MAX_SEEN_IDS = 5000  # bounded growth -- oldest ids drop off, per key
ID_KEYS = ("seen_ids", "gmail_ingested_ids")
DEFAULT_KEY = "seen_ids"


def state_dir(base: Path | None = None) -> Path:
    d = base or DEFAULT_STATE_DIR
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _checkpoint_path(base: Path | None = None) -> Path:
    return state_dir(base) / "checkpoint.json"


def load_state(base: Path | None = None) -> dict:
    path = _checkpoint_path(base)
    if not path.exists():
        return {"seen_ids": [], "gmail_ingested_ids": [], "last_run": None}
    with path.open() as f:
        state = json.load(f)
    state.setdefault("seen_ids", [])
    state.setdefault("gmail_ingested_ids", [])
    return state


def save_state(state: dict, base: Path | None = None) -> None:
    path = _checkpoint_path(base)
    for key in ID_KEYS:
        ids = state.get(key, [])
        if len(ids) > MAX_SEEN_IDS:
            state[key] = ids[-MAX_SEEN_IDS:]

    # Capture the EXISTING file's owner before we replace it. Confirmed
    # live (2026-09-30) as a real production outage, not a theoretical
    # concern: `requeue --verify-unclassified-against` must run as root
    # (it reads a second identity's private checkpoint to cross-check
    # against), and root rewriting this file via the atomic tmp+replace
    # below silently made it root-owned -- the systemd service running
    # as the ORIGINAL owning identity (omnissa-ingest) could then no
    # longer even read its own checkpoint. Preserve the original
    # owner whenever we're root rewriting a file we don't natively own.
    original_owner = None
    if path.exists():
        st = path.stat()
        original_owner = (st.st_uid, st.st_gid)

    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 600

    if original_owner is not None and os.geteuid() == 0:
        try:
            os.chown(tmp, *original_owner)
        except OSError:
            pass  # best-effort; a real failure here still leaves the file readable by root

    tmp.replace(path)  # atomic on same filesystem


def is_seen(state: dict, message_id: str, *, key: str = DEFAULT_KEY) -> bool:
    return message_id in state.get(key, [])


def mark_seen(state: dict, message_id: str, *, key: str = DEFAULT_KEY) -> None:
    seen = state.setdefault(key, [])
    if message_id not in seen:
        seen.append(message_id)


def dedup_new(state: dict, message_ids: list[str], *, key: str = DEFAULT_KEY) -> list[str]:
    """Return only the ids not already recorded as seen, preserving order."""
    return [m for m in message_ids if not is_seen(state, m, key=key)]
