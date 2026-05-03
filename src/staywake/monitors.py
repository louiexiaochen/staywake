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
# Two tunings ported from CodeIsland production:
#
#   * `monitoredThinkingTimeout` = 300 s   (Claude Code: long pure-CLI thinks
#                                           regularly produce 2–4 min silent
#                                           stretches mid-call)
#   * `nativeAppTranscriptQuietTimeout` = 90 s   (native-app embedded agents:
#                                                Codex/Cursor/Qoder/CodeBuddy —
#                                                turns are bursty but shorter)
#
# Override per-monitor in TOML if your workflow differs.
BUILTIN_MONITORS: dict[str, dict] = {
    # Anthropic Claude Code (CLI) — flat per-session JSONL.
    "claude_code": {
        "globs": ["~/.claude/projects/**/*.jsonl"],
        "idle_after_seconds": 300.0,
    },
    # OpenAI Codex CLI — date-bucketed rollout files.
    "codex": {
        "globs": ["~/.codex/sessions/**/rollout-*.jsonl"],
        "idle_after_seconds": 90.0,
    },
    # Cursor IDE agent transcripts.
    "cursor_agent": {
        "globs": ["~/.cursor/projects/**/agent-transcripts/*.jsonl"],
        "idle_after_seconds": 90.0,
    },
    # Qoder (Alibaba) — per-project transcript dir.
    "qoder": {
        "globs": ["~/.qoder/projects/**/transcript/*.jsonl"],
        "idle_after_seconds": 90.0,
    },
    # CodeBuddy (Tencent) — flat per-session JSONL under per-project dir.
    "codebuddy": {
        "globs": ["~/.codebuddy/projects/**/*.jsonl"],
        "idle_after_seconds": 90.0,
    },
}


# Process-name patterns ported from CodeIsland's process-detection map.
# These cover agents that don't write a transcript file (or write to a path
# we don't know). Kept here so users can enable them via TOML without having
# to know the regex tricks. Disabled by default to avoid surprising matches.
BUILTIN_PROCESS_PATTERNS: dict[str, list[str]] = {
    "claude_code": [r"@anthropic-ai/claude-code/cli\.js", r"\.local/share/claude/versions/"],
    "codex": [r"\.app/Contents/MacOS/Codex\b", r"@openai/codex"],
    "cursor": [r"\.app/Contents/MacOS/[Cc]ursor\b", r"\.local/share/cursor-agent/versions/", r"/cursor-agent/index\.js"],
    "qoder": [r"\.app/Contents/MacOS/[Qq]oder\b", r"/\.qoder/bin/qodercli/"],
    "codebuddy": [r"\.app/Contents/MacOS/[Cc]odebuddy\b", r"@tencent-ai/codebuddy-code/bin/codebuddy"],
    "opencode": [r"\.app/Contents/MacOS/[Oo]pen[Cc]ode", r"/\.opencode/bin/opencode\b", r"\bopencode\s+(serve|web)\b"],
    "gemini_cli": [r"/gemini-cli/bundle/gemini\.js", r"(/opt/homebrew|/usr/local)/bin/gemini\b"],
    "copilot_cli": [r"@github/copilot/npm-loader\.js", r"(/opt/homebrew|/usr/local)/bin/copilot\b"],
    "trae": [r"\.app/Contents/MacOS/[Tt]rae\b", r"/\.trae/"],
    "trae_cn": [r"/[Tt]raecn\.app/Contents/", r"/[Tt]rae-cn\.app/Contents/", r"/\.traecn/"],
    "codebuddy_cn": [r"/[Cc]odebuddycn\.app/", r"/\.codebuddycn/"],
    "droid": [r"/[Ff]actory\.app/Contents/MacOS/", r"/\.local/bin/droid\b"],
    "stepfun": [r"\.app/Contents/MacOS/[Ss]tepfun\b", r"/\.stepfun/"],
    "antigravity": [r"\.app/Contents/MacOS/[Aa]ntigravity\b", r"/\.antigravity/antigravity/bin/antigravity"],
    "workbuddy": [r"\.app/Contents/MacOS/[Ww]orkbuddy\b", r"/\.workbuddy/"],
    "hermes": [r"\.app/Contents/MacOS/[Hh]ermes\b", r"/\.local/bin/hermes\b", r"/\.hermes/hermes-agent/"],
    "openwork": [r"\.app/Contents/MacOS/[Oo]penwork", r"\bopenwork-(orchestrator|server)\b"],
}


def builtin_process_patterns_flat() -> list[str]:
    """Flatten BUILTIN_PROCESS_PATTERNS into a single list for ProcessSource."""
    out: list[str] = []
    for patterns in BUILTIN_PROCESS_PATTERNS.values():
        out.extend(patterns)
    return out


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
