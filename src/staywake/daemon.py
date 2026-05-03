"""Daemon main loop: poll sources → toggle SleepGuard."""

from __future__ import annotations

import atexit
import logging
import signal
import time
from pathlib import Path
from typing import Optional

from .config import Config
from .guard import SleepGuard
from .sources import HolderSource, ProcessSource
from .state import default_state_path

logger = logging.getLogger(__name__)


def run_daemon(
    state_path: Optional[Path] = None,
    config: Optional[Config] = None,
) -> int:
    state_path = state_path or default_state_path()
    config = config or Config.load()

    guard = SleepGuard(aggressive=config.aggressive)
    holder_src = HolderSource(state_path, config.stale_after_seconds)
    proc_src = ProcessSource(
        config.process_scan_patterns if config.process_scan_enabled else (),
        config.process_scan_idle_patterns if config.process_scan_enabled else (),
    )

    atexit.register(guard.cleanup)

    def _handle_signal(signum, _frame):
        logger.info("Received signal %d; cleaning up.", signum)
        guard.cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "staywake daemon starting; state=%s interval=%.1fs stale_after=%.0fs aggressive=%s proc_scan=%s",
        state_path,
        config.interval_seconds,
        config.stale_after_seconds,
        config.aggressive,
        "on" if proc_src.configured else "off",
    )

    keeping = False
    while True:
        holder_result = holder_src.poll()
        proc_result = proc_src.poll()

        active = holder_result.active or proc_result.active

        if active and not keeping:
            reasons = []
            if holder_result.active:
                reasons.append(holder_result.detail)
            if proc_result.active:
                reasons.append(proc_result.detail)
            logger.info("Active (%s); engaging sleep guard.", "; ".join(reasons))
            guard.start()
            keeping = True
        elif not active and keeping:
            logger.info("Idle; releasing sleep guard.")
            guard.stop()
            keeping = False

        time.sleep(config.interval_seconds)
