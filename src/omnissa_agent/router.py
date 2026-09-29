"""Thin, bounded wrapper around the machine-level OmniRoute gateway.

Talks to the gateway's own HTTP API (``/v1/chat/completions``) directly,
not the ``omniroute`` CLI wrapper -- the CLI throws away the
``omni_backend`` field the gateway returns, and callers here need to
record which actual provider answered (some are third-party free-tier
services; see docs/gmail-ingestion-security-review.md).

Every LLM call in this project uses ``combo-continuous`` by default
(see .agent/HANDOFF.md) OR ``combo-private`` (local Ollama only) when the
caller is handling real, non-synthetic content. ``opencode-zen-free``
burned its free quota once already and trapped a terminal in a 2h27m
retry loop -- never route through it here, and never let a single
failing backend trap a job: retries are capped, and total failure raises
``DeferredError`` instead of looping.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_COMBO = "combo-continuous"
LOCAL_ONLY_COMBO = "combo-private"  # Ollama only -- see agent_a.build_brief's llm_combo default
FORBIDDEN_COMBOS = {"combo-coding", "combo-chat"}  # route through opencode-zen-free first
GATEWAY_PORT = int(os.environ.get("OMNIROUTE_PORT", "11435"))
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}/v1/chat/completions"


class DeferredError(RuntimeError):
    """All attempts failed (quota/outage/etc). Caller should record and move on."""


@dataclass
class AskResult:
    text: str
    backend: str  # e.g. "omni/ollama-local" -- which provider actually answered
    attempts: int
    elapsed_s: float


def _post_chat(url: str, model: str, prompt: str, *, timeout_s: int) -> dict:
    """Single HTTP call to the gateway. Raises on transport/HTTP error."""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode())


def ask(
    prompt: str,
    *,
    combo: str = DEFAULT_COMBO,
    timeout_s: int = 60,
    max_attempts: int = 3,
    backoff_s: float = 2.0,
) -> AskResult:
    """Ask ``combo`` for ``prompt``, capped retries with backoff.

    Never retries forever: after ``max_attempts`` failures this raises
    ``DeferredError`` so the caller can record a clean "deferred" state
    and exit, rather than trapping the process in a retry loop.
    """
    if combo in FORBIDDEN_COMBOS:
        raise ValueError(f"{combo!r} is forbidden here -- use {DEFAULT_COMBO!r}")

    last_err: Exception | None = None
    start = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        try:
            data = _post_chat(GATEWAY_URL, f"omni/{combo}", prompt, timeout_s=timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = exc
        else:
            if "error" in data:
                last_err = RuntimeError(f"gateway error: {data['error']}")
            else:
                try:
                    text = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError) as exc:
                    last_err = RuntimeError(f"unexpected gateway response: {exc}")
                else:
                    return AskResult(
                        text=text.strip(),
                        backend=data.get("omni_backend", "unknown"),
                        attempts=attempt,
                        elapsed_s=time.monotonic() - start,
                    )
        if attempt < max_attempts:
            time.sleep(backoff_s * attempt)

    raise DeferredError(
        f"combo {combo!r} failed after {max_attempts} attempts: {last_err}"
    )
