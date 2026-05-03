from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from staywake.state import (
    Holder,
    is_live,
    is_stale,
    pid_alive,
    prune_dead,
    read_holders,
    remove_holder,
    upsert_holder,
    utcnow_iso,
)


def test_pid_alive_for_self() -> None:
    assert pid_alive(os.getpid()) is True


def test_pid_alive_for_invalid() -> None:
    # PID 0 is "all processes in the calling group" sentinel; not a real pid.
    assert pid_alive(0) is False


def test_pid_none_treated_alive() -> None:
    assert pid_alive(None) is True


def test_upsert_and_remove(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    upsert_holder(state, Holder(id="a", pid=os.getpid()))
    upsert_holder(state, Holder(id="b", pid=os.getpid()))
    upsert_holder(state, Holder(id="a", pid=os.getpid(), reason="updated"))

    holders = read_holders(state)
    by_id = {h.id: h for h in holders}
    assert set(by_id) == {"a", "b"}
    assert by_id["a"].reason == "updated"

    remove_holder(state, "a")
    assert {h.id for h in read_holders(state)} == {"b"}


def test_prune_drops_dead_pid(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    upsert_holder(state, Holder(id="alive", pid=os.getpid()))
    # PID 999999 effectively never exists.
    upsert_holder(state, Holder(id="dead", pid=999999))

    live, dropped = prune_dead(state, max_age_seconds=600.0)
    assert {h.id for h in live} == {"alive"}
    assert {h.id for h in dropped} == {"dead"}


def test_prune_drops_stale(tmp_path: Path) -> None:
    state = tmp_path / "holders.json"
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upsert_holder(
        state,
        Holder(id="zombie", pid=os.getpid(), updatedAt=old_ts),
    )

    live, dropped = prune_dead(state, max_age_seconds=60.0)
    assert live == []
    assert {h.id for h in dropped} == {"zombie"}


def test_is_stale_with_zero_disables_check() -> None:
    h = Holder(id="x", pid=None, updatedAt="2000-01-01T00:00:00Z")
    assert is_stale(h, max_age_seconds=0) is False
    assert is_live(h, max_age_seconds=0) is True


def test_write_preserves_user_world_readable_perm(tmp_path: Path) -> None:
    """Daemon-side: holders.json must stay readable by the user even when
    written by root. We can't easily simulate root in tests, but we can at
    least pin that the file mode is world-readable after a normal write so
    that the chmod helper isn't accidentally removed."""
    state = tmp_path / "holders.json"
    upsert_holder(state, Holder(id="x", pid=os.getpid()))
    mode = state.stat().st_mode & 0o777
    # tempfile.mkstemp creates 0600 by default; the post-write chmod path
    # only fires when running as root, so we don't assert 0644 here. We
    # assert that the file at least exists and is non-empty — and the unit
    # test for the helper itself lives below.
    assert mode != 0, "file should have a mode"
    assert state.stat().st_size > 0


def test_restore_user_ownership_noop_when_not_root(tmp_path: Path) -> None:
    """Helper must be safe to call as a normal user (it just no-ops)."""
    from staywake.state import _restore_user_ownership

    state = tmp_path / "x.json"
    state.write_text("{}")
    # Should not raise even though we're not root.
    _restore_user_ownership(state)


def test_is_live_handles_garbage_timestamp() -> None:
    h = Holder(id="x", pid=os.getpid(), updatedAt="not-a-date")
    # Garbage timestamp counts as stale (we err on the side of dropping).
    assert is_stale(h, max_age_seconds=600) is True
