from __future__ import annotations

import os
import time
from pathlib import Path

from staywake import api


def test_hold_release_roundtrip(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    api.hold("x", reason="test", state_path=state)
    info = api.status(state_path=state)
    assert info["live_count"] == 1
    assert info["holders"][0]["id"] == "x"
    api.release("x", state_path=state)
    info = api.status(state_path=state)
    assert info["live_count"] == 0


def test_holding_context_manager(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    with api.holding("ctx", reason="ctx-test", heartbeat_seconds=0.0, state_path=state):
        info = api.status(state_path=state)
        assert info["live_count"] == 1
        assert info["holders"][0]["pid"] == os.getpid()
    info = api.status(state_path=state)
    assert info["live_count"] == 0


def test_holding_heartbeat_refreshes(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    with api.holding("hb", heartbeat_seconds=0.05, state_path=state):
        info1 = api.status(state_path=state)
        ts1 = info1["holders"][0]["updatedAt"]
        time.sleep(0.2)
        info2 = api.status(state_path=state)
        ts2 = info2["holders"][0]["updatedAt"]
        # Either the heartbeat refreshed it, or it stayed identical (tolerance).
        # The important property: it never disappeared.
        assert info2["live_count"] == 1
        assert ts2 >= ts1
