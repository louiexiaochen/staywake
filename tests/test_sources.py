from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from staywake.sources import HolderSource, ProcessSource
from staywake.state import Holder, upsert_holder


def test_holder_source_active_with_live_holder(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    upsert_holder(state, Holder(id="t", pid=os.getpid()))
    src = HolderSource(state, stale_after_seconds=600.0)
    result = src.poll()
    assert result.active is True
    assert len(result.holders) == 1


def test_holder_source_idle_when_dead(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    upsert_holder(state, Holder(id="t", pid=999999))
    src = HolderSource(state, stale_after_seconds=600.0)
    result = src.poll()
    assert result.active is False


def test_holder_source_prunes_stale_in_place(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upsert_holder(state, Holder(id="zombie", pid=os.getpid(), updatedAt=old))
    src = HolderSource(state, stale_after_seconds=60.0)
    src.poll()
    # After polling, the stale holder should have been pruned from the file.
    from staywake.state import read_holders
    assert read_holders(state) == []


def test_process_source_disabled_when_no_patterns() -> None:
    src = ProcessSource(patterns=[])
    assert src.configured is False
    result = src.poll()
    assert result.active is False
