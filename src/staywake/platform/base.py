"""Cross-platform SleepGuard protocol."""

from __future__ import annotations

from typing import Optional, Protocol


class SleepGuardProtocol(Protocol):
    aggressive: bool

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def cleanup(self) -> None: ...

    @property
    def is_running(self) -> bool: ...

    def describe(self) -> str: ...
