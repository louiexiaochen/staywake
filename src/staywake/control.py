"""Soft-pause state — let users disable the daemon without sudo.

The daemon is a system-level LaunchDaemon, but users shouldn't need sudo
just to say "leave me alone for an hour". This module manages a tiny
user-writable JSON file at ``~/.local/state/staywake/control.json`` that
the daemon polls on every tick. When ``paused = true``, the daemon
short-circuits to idle regardless of holders or monitors.

Schema:

    {
      "paused": true,
      "pausedAt": "2026-05-04T01:23:45Z",
      "pausedReason": "writing prose, want laptop to sleep normally",
      "pausedUntil": "2026-05-04T02:23:45Z"   // optional auto-resume
    }
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


def default_control_path() -> Path:
    env = os.environ.get("STAYWAKE_CONTROL_PATH")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return Path(base) / "staywake" / "control.json"
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "staywake" / "control.json"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@dataclass
class Control:
    paused: bool = False
    pausedAt: str = ""
    pausedReason: str = ""
    pausedUntil: str = ""

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def empty(cls) -> "Control":
        return cls()

    @property
    def auto_resume_due(self) -> bool:
        """True if this paused state has an expired auto-resume timer."""
        if not self.paused or not self.pausedUntil:
            return False
        until = parse_iso(self.pausedUntil)
        if until is None:
            return False
        return datetime.now(timezone.utc) >= until

    def describe(self) -> str:
        if not self.paused:
            return "running"
        bits = ["PAUSED"]
        if self.pausedUntil:
            bits.append(f"until {self.pausedUntil}")
        if self.pausedReason:
            bits.append(f"reason: {self.pausedReason}")
        return " ".join(bits)


def read_control(path: Optional[Path] = None) -> Control:
    path = path or default_control_path()
    if not path.exists():
        return Control.empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Control.empty()
    if not isinstance(data, dict):
        return Control.empty()
    return Control(
        paused=bool(data.get("paused", False)),
        pausedAt=str(data.get("pausedAt", "")),
        pausedReason=str(data.get("pausedReason", "")),
        pausedUntil=str(data.get("pausedUntil", "")),
    )


def write_control(ctrl: Control, path: Optional[Path] = None) -> None:
    path = path or default_control_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(ctrl.to_json(), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".control.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Match the holder-file behavior: if we're root, hand ownership back to
    # the user so subsequent CLI reads/writes don't collide. (Realistically
    # only the user's CLI writes this file today, but the daemon does call
    # write_control() on auto-resume.)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            parent_stat = path.parent.stat()
            os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
            os.chmod(path, 0o644)
        except OSError:
            pass


def parse_duration(text: str) -> Optional[float]:
    """Parse "30s" / "5m" / "1h" / "8h" / a bare number (= seconds)."""
    text = text.strip().lower()
    if not text:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text[-1] in units:
        try:
            value = float(text[:-1])
        except ValueError:
            return None
        return value * units[text[-1]]
    try:
        return float(text)
    except ValueError:
        return None


def pause(
    reason: str = "",
    duration_seconds: Optional[float] = None,
    path: Optional[Path] = None,
) -> Control:
    until = ""
    if duration_seconds is not None and duration_seconds > 0:
        until_dt = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        until = until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    ctrl = Control(
        paused=True,
        pausedAt=utcnow_iso(),
        pausedReason=reason,
        pausedUntil=until,
    )
    write_control(ctrl, path)
    return ctrl


def resume(path: Optional[Path] = None) -> Control:
    ctrl = Control.empty()
    write_control(ctrl, path)
    return ctrl
