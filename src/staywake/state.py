"""Holder list state file: read, write, atomic mutate, prune.

The state file is a single JSON document at ``~/.local/state/staywake/holders.json``
(override with ``$STAYWAKE_STATE_PATH`` or ``--state-path``):

    {
      "holders": [
        { "id": "...", "pid": 12345, "reason": "...", "updatedAt": "2026-..." }
      ]
    }

A holder is *live* iff:
    - its ``pid`` (if non-null) is alive on the system, AND
    - ``now() - updatedAt`` is below the configured staleness threshold.

The daemon caffeinates iff at least one live holder exists. Producers
(CLI, library, hooks) only ever add/refresh/remove their own holder by ``id``;
the daemon is the sole entity that prunes dead/stale holders.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

# Cross-platform exclusive file lock. fcntl on POSIX, msvcrt on Windows.
if sys.platform == "win32":
    import msvcrt  # type: ignore[import-not-found]
    fcntl = None  # type: ignore[assignment]
else:
    import fcntl  # type: ignore[no-redef]
    msvcrt = None  # type: ignore[assignment]


def default_state_path() -> Path:
    env = os.environ.get("STAYWAKE_STATE_PATH")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return Path(base) / "staywake" / "holders.json"
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "staywake" / "holders.json"


def utcnow_iso() -> str:
    # Always Z-suffixed UTC. Easy to parse, easy to eyeball.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Python 3.11 fromisoformat handles 'Z'; older versions need a swap.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@dataclass
class Holder:
    id: str
    pid: Optional[int] = None
    reason: str = ""
    updatedAt: str = field(default_factory=utcnow_iso)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, obj: dict) -> "Holder":
        return cls(
            id=str(obj.get("id", "")),
            pid=int(obj["pid"]) if obj.get("pid") is not None else None,
            reason=str(obj.get("reason", "")),
            updatedAt=str(obj.get("updatedAt", "")),
        )


def pid_alive(pid: Optional[int]) -> bool:
    """Return True if the given pid corresponds to an existing process.

    A holder may be pid-less (pid=None); we consider those always alive
    from the pid-check perspective and rely entirely on staleness.
    """
    if pid is None:
        return True
    if pid <= 0:
        return False

    if sys.platform == "win32":
        return _pid_alive_windows(pid)

    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            # Process exists but we don't own it; that still counts as alive.
            return True
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:  # pragma: no cover - exercised only on Windows
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong(0)
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def is_stale(holder: Holder, max_age_seconds: float) -> bool:
    if max_age_seconds <= 0:
        return False
    ts = parse_iso(holder.updatedAt)
    if ts is None:
        return True
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age > max_age_seconds


def is_live(holder: Holder, max_age_seconds: float) -> bool:
    return pid_alive(holder.pid) and not is_stale(holder, max_age_seconds)


# ---------------------------------------------------------------------------
# File IO with cross-process locking and atomic replace.
# ---------------------------------------------------------------------------

@contextmanager
def _locked(path: Path) -> Iterator[int]:
    """Acquire an exclusive lock on a sibling lock file (fcntl on POSIX, msvcrt on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if sys.platform == "win32":
            # msvcrt.locking blocks until granted; lock 1 byte at offset 0.
            os.lseek(fd, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # type: ignore[union-attr]
                    break
                except OSError as exc:
                    if exc.errno != errno.EDEADLK:
                        raise
        else:
            fcntl.flock(fd, fcntl.LOCK_EX)  # type: ignore[union-attr]
        yield fd
    finally:
        try:
            if sys.platform == "win32":
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[union-attr]
                except OSError:
                    pass
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
        finally:
            os.close(fd)


def _read_unlocked(path: Path) -> list[Holder]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    holders_raw = data.get("holders") if isinstance(data, dict) else None
    if not isinstance(holders_raw, list):
        return []
    return [Holder.from_json(item) for item in holders_raw if isinstance(item, dict)]


def _write_unlocked(path: Path, holders: Iterable[Holder]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"holders": [h.to_json() for h in holders]}
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".holders.", dir=str(path.parent))
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


def read_holders(path: Path) -> list[Holder]:
    """Read holders without locking. Cheap; used for status display."""
    return _read_unlocked(path)


def mutate_holders(
    path: Path,
    fn,
) -> list[Holder]:
    """Atomically read → transform → write the holder list under a flock.

    ``fn`` receives the current list and returns the new list.
    """
    with _locked(path):
        current = _read_unlocked(path)
        new = list(fn(current))
        _write_unlocked(path, new)
        return new


def upsert_holder(path: Path, holder: Holder) -> list[Holder]:
    def _fn(current: list[Holder]) -> list[Holder]:
        return [h for h in current if h.id != holder.id] + [holder]

    return mutate_holders(path, _fn)


def remove_holder(path: Path, holder_id: str) -> list[Holder]:
    def _fn(current: list[Holder]) -> list[Holder]:
        return [h for h in current if h.id != holder_id]

    return mutate_holders(path, _fn)


def prune_dead(path: Path, max_age_seconds: float) -> tuple[list[Holder], list[Holder]]:
    """Remove dead/stale holders. Returns (live_remaining, dropped)."""
    dropped: list[Holder] = []

    def _fn(current: list[Holder]) -> list[Holder]:
        live: list[Holder] = []
        for h in current:
            if is_live(h, max_age_seconds):
                live.append(h)
            else:
                dropped.append(h)
        return live

    live = mutate_holders(path, _fn)
    return live, dropped
