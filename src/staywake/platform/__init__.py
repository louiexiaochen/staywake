"""Platform dispatch — pick the right SleepGuard at import time.

Public surface: :class:`SleepGuard` is whatever the current OS provides.
"""

from __future__ import annotations

import sys

from .base import SleepGuardProtocol

if sys.platform == "darwin":
    from ._macos import MacOSSleepGuard as SleepGuard
elif sys.platform == "win32":
    from ._windows import WindowsSleepGuard as SleepGuard
else:
    from ._fallback import FallbackSleepGuard as SleepGuard

__all__ = ["SleepGuard", "SleepGuardProtocol"]
