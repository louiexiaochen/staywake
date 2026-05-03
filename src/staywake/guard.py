"""SleepGuard — own the lifetime of one ``caffeinate`` child + optional ``pmset``.

Wraps two power knobs:

* ``caffeinate -dimsu``  — holds PreventDisplayIdleSleep, PreventUserIdleSystemSleep,
  PreventDiskIdleSleep, **PreventSystemSleep** (the lid-close blocker on AC) and
  fires a synthetic user activity tick. The assertions live as long as the
  child process; killing it releases them all atomically.

* ``pmset -a disablesleep 1`` — system-wide "computer never sleeps" toggle.
  Stronger than caffeinate (covers edge cases where assertions get GC'd) but
  requires root and survives across processes, so we *must* restore it on
  shutdown. ``aggressive=False`` skips it entirely.

Designed to be idempotent: ``start()`` is safe to call when already running,
``stop()`` is safe to call when already stopped.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


CAFFEINATE_FLAGS = ["-dimsu"]


def _orphan_caffeinate_sweep() -> int:
    """Best-effort kill any prior ``caffeinate -dimsu`` left by a crashed daemon.

    We only target the exact flag combo we use, so we don't step on
    user-launched ``caffeinate`` invocations.

    Returns the count we believe we reaped (best-effort; pkill tells us 0/1).
    """
    try:
        proc = subprocess.run(
            ["pkill", "-f", f"caffeinate {CAFFEINATE_FLAGS[0]}"],
            check=False,
            capture_output=True,
            text=True,
        )
        return 1 if proc.returncode == 0 else 0
    except FileNotFoundError:
        return 0


class SleepGuard:
    def __init__(self, aggressive: bool = False) -> None:
        self.aggressive = aggressive
        self._caffeinate: Optional[subprocess.Popen[str]] = None
        self._changed_disablesleep = False
        self._warned_root = False

    # ----- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._caffeinate is None or self._caffeinate.poll() is not None:
            _orphan_caffeinate_sweep()
            self._caffeinate = subprocess.Popen(
                ["caffeinate", *CAFFEINATE_FLAGS],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            logger.info("Started caffeinate (pid=%d).", self._caffeinate.pid)
        if self.aggressive:
            self._enable_disablesleep()

    def stop(self) -> None:
        if self._caffeinate is not None and self._caffeinate.poll() is None:
            self._caffeinate.terminate()
            try:
                self._caffeinate.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._caffeinate.kill()
                try:
                    self._caffeinate.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            logger.info("Stopped caffeinate.")
        self._caffeinate = None
        if self._changed_disablesleep:
            try:
                self._set_disablesleep(False)
            finally:
                self._changed_disablesleep = False
            logger.info("Restored pmset disablesleep=0.")

    def cleanup(self) -> None:
        # Defensive: catch every error so that atexit / signal paths can't fail.
        try:
            self.stop()
        except Exception as exc:  # pragma: no cover - best-effort
            logger.warning("cleanup ignored exception: %s", exc)

    # ----- introspection ---------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._caffeinate is not None and self._caffeinate.poll() is None

    @property
    def caffeinate_pid(self) -> Optional[int]:
        if not self.is_running:
            return None
        return self._caffeinate.pid  # type: ignore[union-attr]

    def disablesleep_state(self) -> str:
        if not self.aggressive:
            return "n/a"
        if os.geteuid() != 0:
            return "no-root"
        return "1" if self._read_disablesleep() else "0"

    # ----- pmset internals -------------------------------------------------

    def _enable_disablesleep(self) -> None:
        if os.geteuid() != 0:
            if not self._warned_root:
                logger.warning("aggressive mode requested but not running as root; pmset skipped.")
                self._warned_root = True
            return
        if self._read_disablesleep():
            return
        self._set_disablesleep(True)
        self._changed_disablesleep = True
        logger.info("Enabled pmset disablesleep=1.")

    def _read_disablesleep(self) -> bool:
        try:
            result = subprocess.run(["pmset", "-g"], check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
        match = re.search(
            r"^\s*(?:disablesleep|SleepDisabled)\s+(\d)\s*$",
            result.stdout,
            re.MULTILINE,
        )
        return bool(match and match.group(1) == "1")

    def _set_disablesleep(self, enabled: bool) -> None:
        subprocess.run(
            ["pmset", "-a", "disablesleep", "1" if enabled else "0"],
            check=True,
            capture_output=True,
            text=True,
        )
