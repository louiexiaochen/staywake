from __future__ import annotations

import os
import time
from pathlib import Path

from staywake.monitors import GlobMonitor, build_default_monitors


def test_glob_monitor_active_on_fresh_file(tmp_path: Path) -> None:
    f = tmp_path / "live.jsonl"
    f.write_text("hello\n")
    mon = GlobMonitor("test", [str(tmp_path / "*.jsonl")], idle_after_seconds=60.0)
    result = mon.poll()
    assert result.active is True
    assert result.newest_path == str(f)


def test_glob_monitor_idle_when_old(tmp_path: Path) -> None:
    f = tmp_path / "old.jsonl"
    f.write_text("stale\n")
    # backdate the file 1h
    old = time.time() - 3600
    os.utime(f, (old, old))
    mon = GlobMonitor("test", [str(tmp_path / "*.jsonl")], idle_after_seconds=60.0)
    result = mon.poll()
    assert result.active is False


def test_glob_monitor_no_files(tmp_path: Path) -> None:
    mon = GlobMonitor("test", [str(tmp_path / "*.jsonl")], idle_after_seconds=60.0)
    result = mon.poll()
    assert result.active is False
    assert result.newest_path is None


def test_glob_monitor_picks_newest(tmp_path: Path) -> None:
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text("a")
    time.sleep(0.05)
    b.write_text("b")
    mon = GlobMonitor("test", [str(tmp_path / "*.jsonl")], idle_after_seconds=60.0)
    result = mon.poll()
    assert result.newest_path == str(b)


def test_default_monitors_includes_claude_and_codex() -> None:
    monitors = build_default_monitors()
    names = {m.name for m in monitors}
    assert "claude_code" in names
    assert "codex" in names


def test_default_monitors_can_disable_via_overrides() -> None:
    monitors = build_default_monitors(overrides={"claude_code": {"enabled": False}})
    names = {m.name for m in monitors}
    assert "claude_code" not in names
    assert "codex" in names


def test_builtin_defaults_match_production_tuning() -> None:
    """Lock in the CodeIsland-derived idle thresholds.

    These are NOT 30s. Extended thinking, large-context LLM calls, and
    waitingApproval all create multi-minute gaps in transcript writes that
    must not be confused with "agent finished".
    """
    from staywake.monitors import BUILTIN_MONITORS

    assert BUILTIN_MONITORS["claude_code"]["idle_after_seconds"] == 300.0
    assert BUILTIN_MONITORS["codex"]["idle_after_seconds"] == 90.0


def test_default_monitors_extra_added(tmp_path: Path) -> None:
    monitors = build_default_monitors(
        extra={"my_tool": {"globs": [str(tmp_path / "*.log")], "idle_after_seconds": 5}}
    )
    by_name = {m.name: m for m in monitors}
    assert "my_tool" in by_name
    assert by_name["my_tool"].idle_after_seconds == 5
