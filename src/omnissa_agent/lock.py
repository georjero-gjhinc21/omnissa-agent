"""Single-instance lock so overlapping scheduled runs never stack up."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .state import DEFAULT_STATE_DIR, state_dir


class AlreadyRunningError(RuntimeError):
    """Another instance already holds the lock."""


class SingleInstanceLock:
    def __init__(self, base: Path | None = None, name: str = "run.lock"):
        self._path = state_dir(base or DEFAULT_STATE_DIR) / name
        self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        self._fh = open(self._path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fh.close()
            self._fh = None
            raise AlreadyRunningError(f"lock held: {self._path}") from exc
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
