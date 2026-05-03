"""Fallback SleepGuard for unsupported platforms (Linux, BSD, …).

Does nothing useful — daemon still runs, monitors still poll, holders still
work, but there's no platform-specific sleep blocking. Logs a warning once.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


class FallbackSleepGuard:
    def __init__(self, aggressive: bool = False) -> None:
        self.aggressive = aggressive
        self._holding = False
        self._warned = False

    def start(self) -> None:
        if not self._warned:
            logger.warning(
                "staywake has no SleepGuard implementation for sys.platform=%r; "
                "monitors will still detect activity, but the system won't actually be kept awake. "
                "Linux PRs welcome (systemd-inhibit-based).",
                sys.platform,
            )
            self._warned = True
        self._holding = True

    def stop(self) -> None:
        self._holding = False

    def cleanup(self) -> None:
        self.stop()

    @property
    def is_running(self) -> bool:
        return self._holding

    def describe(self) -> str:
        return f"fallback={'engaged' if self._holding else 'off'}"
