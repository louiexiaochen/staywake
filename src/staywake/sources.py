"""Activity sources — the daemon ORs them together to decide active/idle.

Two sources ship in v1:

* :class:`HolderSource` — the canonical mode: read the holder list, prune dead
  ones, return ``True`` iff at least one live holder remains.

* :class:`ProcessSource` — opt-in fallback for tools you can't modify to call
  the staywake CLI. Configure regex patterns in TOML; the source greps
  ``ps -axo pid,ppid,command`` and walks each match's subtree, ignoring
  configurable "idle" patterns and bare login shells.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence

from .state import Holder, prune_dead

logger = logging.getLogger(__name__)


@dataclass
class SourceResult:
    active: bool
    detail: str = ""
    holders: List[Holder] = field(default_factory=list)


class HolderSource:
    def __init__(self, state_path: Path, stale_after_seconds: float):
        self.state_path = state_path
        self.stale_after_seconds = stale_after_seconds

    def poll(self) -> SourceResult:
        live, dropped = prune_dead(self.state_path, self.stale_after_seconds)
        if dropped:
            logger.info(
                "Pruned %d dead/stale holder(s): %s",
                len(dropped),
                ", ".join(f"{h.id}(pid={h.pid})" for h in dropped),
            )
        if not live:
            return SourceResult(active=False, detail="no live holders")
        return SourceResult(
            active=True,
            detail=f"{len(live)} live holder(s)",
            holders=live,
        )


# ---------------------------------------------------------------------------
# Process scan source
# ---------------------------------------------------------------------------

_SHELLS = {
    "/bin/zsh", "/bin/bash", "/bin/sh", "/bin/dash",
    "/bin/zsh -l", "/bin/bash -l", "-zsh", "-bash",
}


@dataclass
class _Proc:
    pid: int
    ppid: int
    command: str


def _list_processes() -> List[_Proc]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    out: List[_Proc] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            out.append(_Proc(int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return out


class ProcessSource:
    """Fallback: scan ``ps`` for matching agent processes.

    Disabled by default; opt in via TOML:

        [process_scan]
        enabled = true
        patterns = ["\\\\bcodex\\\\b", "\\\\bclaude\\\\b"]
        idle_patterns = ["language-server", "tsc --watch"]
    """

    def __init__(
        self,
        patterns: Sequence[str],
        idle_patterns: Sequence[str] = (),
    ) -> None:
        self._roots = [re.compile(p, re.IGNORECASE) for p in patterns]
        self._idle = [re.compile(p, re.IGNORECASE) for p in idle_patterns]

    @property
    def configured(self) -> bool:
        return bool(self._roots)

    def poll(self) -> SourceResult:
        if not self.configured:
            return SourceResult(active=False, detail="process scan disabled")

        my_pid = os.getpid()
        procs = _list_processes()
        by_pid = {p.pid: p for p in procs}
        children: dict[int, list[int]] = {}
        roots: set[int] = set()

        for p in procs:
            if p.pid == my_pid or "staywake" in p.command:
                # Don't fold ourselves or our siblings into "active".
                continue
            children.setdefault(p.ppid, []).append(p.pid)
            for pat in self._roots:
                if pat.search(p.command):
                    roots.add(p.pid)
                    break

        if not roots:
            return SourceResult(active=False, detail="no matching root processes")

        active: list[_Proc] = []
        seen: set[int] = set()

        def walk(pid: int) -> None:
            if pid in seen:
                return
            seen.add(pid)
            proc = by_pid.get(pid)
            if proc is None:
                return
            if not self._is_idle(proc.command) and proc.command not in _SHELLS:
                active.append(proc)
            for child in children.get(pid, []):
                walk(child)

        for r in roots:
            walk(r)

        if not active:
            return SourceResult(active=False, detail="all matched processes look idle")
        return SourceResult(
            active=True,
            detail=f"{len(active)} active process(es): "
            + ", ".join(f"pid={p.pid}" for p in active[:5])
            + ("…" if len(active) > 5 else ""),
        )

    def _is_idle(self, command: str) -> bool:
        return any(p.search(command) for p in self._idle)
