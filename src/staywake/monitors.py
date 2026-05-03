"""File-activity monitors — auto-detect agent work without explicit ``hold``.

This is the headline feature: just install staywake and run your agent
normally. The daemon watches each agent's transcript/log files and infers
"active" from file growth.

A monitor is *active* iff the newest matching file's mtime is within
``idle_after_seconds``. Cross-platform — pure ``stat()`` polling, no fsevents,
no inotify, no platform-specific syscalls.

Built-in monitors:

* ``claude_code`` — ``~/.claude/projects/<cwd>/<session>.jsonl`` (Claude Code CLI)
* ``codex``       — ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` (OpenAI Codex CLI)

Custom monitors via TOML; see :class:`GlobMonitor`.
"""

from __future__ import annotations

import glob as _glob
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class MonitorResult:
    name: str
    active: bool
    detail: str = ""
    newest_path: Optional[str] = None
    age_seconds: Optional[float] = None


class GlobMonitor:
    """Watch the newest mtime across a glob pattern.

    Designed for transcript / log files that get appended to as the agent
    works. We treat "newest mtime within ``idle_after_seconds``" as active.

    The glob is re-expanded every poll, so newly created session files are
    picked up automatically.
    """

    def __init__(
        self,
        name: str,
        glob_patterns: Sequence[str],
        idle_after_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self.patterns = [os.path.expanduser(p) for p in glob_patterns]
        self.idle_after_seconds = float(idle_after_seconds)

    def poll(self) -> MonitorResult:
        newest_path: Optional[str] = None
        newest_mtime: float = 0.0

        for pat in self.patterns:
            for path in _glob.iglob(pat, recursive=True):
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_mtime > newest_mtime:
                    newest_mtime = st.st_mtime
                    newest_path = path

        if newest_path is None:
            return MonitorResult(name=self.name, active=False, detail="no matching files")

        age = time.time() - newest_mtime
        active = age <= self.idle_after_seconds
        return MonitorResult(
            name=self.name,
            active=active,
            detail=f"newest age={age:.1f}s threshold={self.idle_after_seconds:.0f}s",
            newest_path=newest_path,
            age_seconds=age,
        )


# ---------------------------------------------------------------------------
# Built-in defaults — opinionated, but each can be disabled in TOML.
# ---------------------------------------------------------------------------

# Idle thresholds by agent — generous on purpose.
#
# An agent is *not* idle just because the transcript paused. Extended thinking,
# big LLM calls, slow tool round-trips, and waitingApproval all create gaps in
# the file-write stream that should NOT be confused with "agent finished".
# Numbers below are ported from CodeIsland's production tuning:
#
#   * Claude Code (`monitoredThinkingTimeout`): 300s = 5 min. Long thinks
#     and large-context calls regularly produce 2–4 min silent stretches.
#   * OpenAI Codex CLI (`nativeAppTranscriptQuietTimeout`): 90s. Turns are
#     shorter but still bursty; 90s clears most thinking pauses without
#     leaving the laptop awake forever after the user wandered off.
#
# Override per-monitor in TOML if your workflow differs.
BUILTIN_MONITORS: dict[str, dict] = {
    # Claude Code CLI: appends to a JSONL per session under
    # ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
    "claude_code": {
        "globs": ["~/.claude/projects/**/*.jsonl"],
        "idle_after_seconds": 300.0,
    },
    # OpenAI Codex CLI: rotated rollout files under
    # ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
    "codex": {
        "globs": ["~/.codex/sessions/**/rollout-*.jsonl"],
        "idle_after_seconds": 90.0,
    },
    # Claude Code's session_id-based plain logs (some setups). Same threshold
    # as claude_code; harmless if the directory doesn't exist.
    "claude_code_logs": {
        "globs": ["~/.claude/sessions/**/*.jsonl"],
        "idle_after_seconds": 300.0,
    },
}


def build_default_monitors(
    overrides: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> List[GlobMonitor]:
    """Build the daemon's monitor list.

    ``overrides`` toggles or tweaks built-ins (``{"claude_code": {"enabled": false}}``).
    ``extra`` adds user-defined monitors keyed by name.
    """
    overrides = overrides or {}
    extra = extra or {}

    monitors: List[GlobMonitor] = []
    for name, defaults in BUILTIN_MONITORS.items():
        cfg = {**defaults, **overrides.get(name, {})}
        if cfg.get("enabled", True) is False:
            continue
        monitors.append(
            GlobMonitor(
                name=name,
                glob_patterns=list(cfg.get("globs", [])),
                idle_after_seconds=float(cfg.get("idle_after_seconds", 30.0)),
            )
        )

    for name, cfg in extra.items():
        if cfg.get("enabled", True) is False:
            continue
        globs = cfg.get("globs") or cfg.get("paths") or []
        if not globs:
            continue
        monitors.append(
            GlobMonitor(
                name=name,
                glob_patterns=list(globs),
                idle_after_seconds=float(cfg.get("idle_after_seconds", 30.0)),
            )
        )

    return monitors
