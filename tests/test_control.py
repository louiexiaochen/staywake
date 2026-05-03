from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from staywake import control as ctrl


def test_default_state_is_running(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    state = ctrl.read_control(path)
    assert state.paused is False
    assert state.describe() == "running"


def test_pause_persists(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    ctrl.pause(reason="writing prose", path=path)
    state = ctrl.read_control(path)
    assert state.paused is True
    assert state.pausedReason == "writing prose"
    assert state.pausedUntil == ""
    assert "PAUSED" in state.describe()
    assert "writing prose" in state.describe()


def test_resume_clears(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    ctrl.pause(reason="x", path=path)
    ctrl.resume(path=path)
    state = ctrl.read_control(path)
    assert state.paused is False
    assert state.pausedReason == ""


def test_pause_with_duration_sets_until(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    state = ctrl.pause(duration_seconds=3600, path=path)
    assert state.pausedUntil != ""
    until = ctrl.parse_iso(state.pausedUntil)
    assert until is not None
    delta = (until - datetime.now(timezone.utc)).total_seconds()
    assert 3500 < delta <= 3600


def test_auto_resume_due_when_expired(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    # Backdate the pausedUntil timestamp.
    state = ctrl.Control(
        paused=True,
        pausedAt=ctrl.utcnow_iso(),
        pausedReason="expired",
        pausedUntil=(datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    ctrl.write_control(state, path)
    fresh = ctrl.read_control(path)
    assert fresh.auto_resume_due is True


def test_auto_resume_not_due_when_no_until(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    ctrl.pause(reason="indefinite", path=path)
    state = ctrl.read_control(path)
    assert state.paused is True
    assert state.auto_resume_due is False


def test_parse_duration_units() -> None:
    assert ctrl.parse_duration("30") == 30.0
    assert ctrl.parse_duration("30s") == 30.0
    assert ctrl.parse_duration("5m") == 300.0
    assert ctrl.parse_duration("1h") == 3600.0
    assert ctrl.parse_duration("2d") == 172800.0
    assert ctrl.parse_duration("0.5h") == 1800.0
    assert ctrl.parse_duration("garbage") is None
    assert ctrl.parse_duration("") is None


def test_corrupt_file_returns_default(tmp_path: Path) -> None:
    path = tmp_path / "control.json"
    path.write_text("{ this is not valid json")
    state = ctrl.read_control(path)
    assert state.paused is False
