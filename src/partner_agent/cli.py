"""Re-export alias -- see partner_agent/__init__.py. Implementation
lives in omnissa_agent.cli; nothing here diverges from it.

``main`` is imported explicitly (not just via ``import *``) because
this file needs to call it directly in its own __main__ guard below --
``python3 -m partner_agent.cli`` must work exactly like
``python3 -m omnissa_agent.cli``. ``main`` still resolves every
underscore-prefixed helper (``_classify``, ``_run``, etc.) against
omnissa_agent.cli's own module globals, not this file's -- a plain
function carries its defining module's globals with it regardless of
where it's re-imported from, so nothing here needs to re-export those
private helpers too.
"""

from omnissa_agent.cli import *  # noqa: F401,F403
from omnissa_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
