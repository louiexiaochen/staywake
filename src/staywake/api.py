"""High-level Python API: ``hold()``, ``release()``, ``status()``, ``holding()``.

These are the producer-side helpers — they only mutate the holder list. Whether
the daemon is actually running is independent: if no daemon is up, your hold
calls are silently no-ops from the system's perspective (just a JSON file).
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .state import (
    Holder,
    default_state_path,
    is_live,
    read_holders,
    remove_holder,
    upsert_holder,
    utcnow_iso,
)


def hold(
    holder_id: str,
    reason: str = "",
    pid: Optional[int] = None,
    state_path: Optional[Path] = None,
) -> Holder:
    """Add or refresh a holder. PID defaults to the calling process."""
    h = Holder(
        id=holder_id,
        pid=os.getpid() if pid is None else pid,
        reason=reason,
        updatedAt=utcnow_iso(),
    )
    upsert_holder(state_path or default_state_path(), h)
    return h


def release(holder_id: str, state_path: Optional[Path] = None) -> None:
    remove_holder(state_path or default_state_path(), holder_id)


def status(state_path: Optional[Path] = None, stale_after_seconds: float = 600.0) -> dict:
    path = state_path or default_state_path()
    holders = read_holders(path)
    live = [h for h in holders if is_live(h, stale_after_seconds)]
    return {
        "state_path": str(path),
        "holders": [h.to_json() for h in holders],
        "live_count": len(live),
        "active": len(live) > 0,
    }


@contextmanager
def holding(
    holder_id: str,
    reason: str = "",
    heartbeat_seconds: float = 60.0,
    state_path: Optional[Path] = None,
) -> Iterator[Holder]:
    """Context manager that holds for the duration of a block.

    Spawns a daemon thread that refreshes ``updatedAt`` every
    ``heartbeat_seconds`` so the staleness check can't drop a long-running
    block, even if the configured ``stale_after_seconds`` is short.
    """
    h = hold(holder_id, reason=reason, state_path=state_path)
    stop = threading.Event()

    def _heartbeat() -> None:
        while not stop.wait(heartbeat_seconds):
            try:
                hold(holder_id, reason=reason, state_path=state_path)
            except Exception:
                # The daemon will prune us if our PID dies; never let
                # a heartbeat error tank the user's actual workload.
                pass

    t: Optional[threading.Thread] = None
    if heartbeat_seconds > 0:
        t = threading.Thread(target=_heartbeat, name=f"staywake-heartbeat-{holder_id}", daemon=True)
        t.start()

    try:
        yield h
    finally:
        stop.set()
        if t is not None:
            t.join(timeout=1.0)
        try:
            release(holder_id, state_path=state_path)
        except Exception:
            pass
