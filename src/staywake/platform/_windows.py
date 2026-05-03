"""Windows SleepGuard — ``SetThreadExecutionState`` + optional lid-action override.

A single Win32 API holds all the assertions we need:

  SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)

Unlike macOS, the assertion lives in the *calling thread* of the daemon
process — there's no child process. Releasing is just calling the same API
with ``ES_CONTINUOUS`` alone (clears the flags).

For the headline lid-close use case, ``ES_SYSTEM_REQUIRED`` alone is *not*
enough on most Windows configs — lid-close is a user-initiated power-button
event, not idle sleep. ``aggressive=True`` additionally calls
``powercfg /SETACVALUEINDEX`` to set the lid action to "Do nothing", and
restores it on stop.

Reference: https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# Win32 ES_* constants
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002
_ES_AWAYMODE_REQUIRED = 0x00000040  # opt-in: media-style background mode

# powercfg lid-action sub-id (DC = on battery, AC = plugged in)
# SUB_BUTTONS = 4f971e89-eebd-4455-a8de-9e59040e7347
# LIDACTION   = 5ca83367-6e45-459f-a27b-476b1d01c936
# Action values: 0=Do nothing, 1=Sleep, 2=Hibernate, 3=Shut down
_LID_SUBGROUP = "4f971e89-eebd-4455-a8de-9e59040e7347"
_LID_SETTING = "5ca83367-6e45-459f-a27b-476b1d01c936"


def _set_thread_execution_state(flags: int) -> bool:
    if sys.platform != "win32":  # pragma: no cover
        return False
    import ctypes

    rc = ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
    return bool(rc)


def _powercfg(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powercfg", *args],
        check=False,
        capture_output=True,
        text=True,
    )


class WindowsSleepGuard:
    def __init__(self, aggressive: bool = False) -> None:
        self.aggressive = aggressive
        self._holding = False
        self._saved_lid_ac: Optional[str] = None
        self._saved_lid_dc: Optional[str] = None
        self._warned_admin = False

    def start(self) -> None:
        if not self._holding:
            ok = _set_thread_execution_state(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            )
            if not ok:
                logger.error("SetThreadExecutionState failed; not holding.")
                return
            logger.info("Engaged SetThreadExecutionState (system+display required).")
            self._holding = True

        if self.aggressive:
            self._override_lid_action()

    def stop(self) -> None:
        if self._holding:
            _set_thread_execution_state(_ES_CONTINUOUS)
            logger.info("Released SetThreadExecutionState.")
            self._holding = False

        if self.aggressive:
            self._restore_lid_action()

    def cleanup(self) -> None:
        try:
            self.stop()
        except Exception as exc:  # pragma: no cover
            logger.warning("cleanup ignored exception: %s", exc)

    @property
    def is_running(self) -> bool:
        return self._holding

    def describe(self) -> str:
        parts = ["execution_state=" + ("required" if self._holding else "off")]
        if self.aggressive:
            parts.append("lid_override=" + ("on" if self._saved_lid_ac is not None else "off"))
        return " ".join(parts)

    # ----- lid-action override (powercfg) ----------------------------------

    def _override_lid_action(self) -> None:
        ac = self._read_lid_setting("AC")
        dc = self._read_lid_setting("DC")
        if ac is None and dc is None:
            if not self._warned_admin:
                logger.warning("powercfg query failed; lid override skipped (admin required?).")
                self._warned_admin = True
            return
        # Only save the first time so consecutive starts don't lose the original.
        if self._saved_lid_ac is None:
            self._saved_lid_ac = ac
            self._saved_lid_dc = dc
        for arg, value in (("/SETACVALUEINDEX", ac), ("/SETDCVALUEINDEX", dc)):
            if value is None:
                continue
            res = _powercfg(arg, "SCHEME_CURRENT", _LID_SUBGROUP, _LID_SETTING, "0")
            if res.returncode != 0:
                logger.warning("powercfg %s failed: %s", arg, res.stderr.strip())
        _powercfg("/SETACTIVE", "SCHEME_CURRENT")
        logger.info("Overrode lid action -> Do nothing (AC and DC).")

    def _restore_lid_action(self) -> None:
        if self._saved_lid_ac is None and self._saved_lid_dc is None:
            return
        for arg, value in (
            ("/SETACVALUEINDEX", self._saved_lid_ac),
            ("/SETDCVALUEINDEX", self._saved_lid_dc),
        ):
            if value is None:
                continue
            _powercfg(arg, "SCHEME_CURRENT", _LID_SUBGROUP, _LID_SETTING, str(value))
        _powercfg("/SETACTIVE", "SCHEME_CURRENT")
        logger.info("Restored lid action.")
        self._saved_lid_ac = None
        self._saved_lid_dc = None

    def _read_lid_setting(self, kind: str) -> Optional[str]:
        # `powercfg /QUERY SCHEME_CURRENT SUB_BUTTONS LIDACTION` returns lines like:
        #   Current AC Power Setting Index: 0x00000001
        #   Current DC Power Setting Index: 0x00000001
        res = _powercfg("/QUERY", "SCHEME_CURRENT", _LID_SUBGROUP, _LID_SETTING)
        if res.returncode != 0:
            return None
        marker = f"Current {kind} Power Setting Index:"
        for line in res.stdout.splitlines():
            if marker in line:
                m = re.search(r"0x([0-9a-fA-F]+)", line)
                if m:
                    return str(int(m.group(1), 16))
        return None
