"""Daemon main loop: poll holder list + process scan + file monitors → toggle SleepGuard."""

from __future__ import annotations

import atexit
import logging
import signal
import time
from pathlib import Path
from typing import Optional

from .config import Config
from .control import default_control_path, read_control, resume as resume_control
from .monitors import build_default_monitors, builtin_process_patterns_flat
from .platform import SleepGuard
from .sources import HolderSource, ProcessSource
from .state import default_state_path

logger = logging.getLogger(__name__)


def run_daemon(
    state_path: Optional[Path] = None,
    config: Optional[Config] = None,
    control_path: Optional[Path] = None,
) -> int:
    state_path = state_path or default_state_path()
    control_path = control_path or default_control_path()
    config = config or Config.load()

    guard = SleepGuard(aggressive=config.aggressive)
    holder_src = HolderSource(state_path, config.stale_after_seconds)

    proc_patterns: list[str] = []
    if config.process_scan_enabled:
        if config.process_scan_use_builtins:
            proc_patterns.extend(builtin_process_patterns_flat())
        proc_patterns.extend(config.process_scan_patterns)
    proc_src = ProcessSource(
        proc_patterns,
        config.process_scan_idle_patterns if config.process_scan_enabled else (),
    )
    monitors = build_default_monitors(
        overrides=config.monitor_overrides,
        extra=config.monitor_extra,
    )

    atexit.register(guard.cleanup)

    def _handle_signal(signum, _frame):
        logger.info("Received signal %d; cleaning up.", signum)
        guard.cleanup()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    monitor_names = ", ".join(m.name for m in monitors) or "(none)"
    logger.info(
        "staywake daemon starting; state=%s interval=%.1fs stale_after=%.0fs aggressive=%s "
        "proc_scan=%s monitors=%s",
        state_path,
        config.interval_seconds,
        config.stale_after_seconds,
        config.aggressive,
        "on" if proc_src.configured else "off",
        monitor_names,
    )

    keeping = False
    paused_logged = False
    while True:
        # 0. Soft pause — user-writable, no sudo. Short-circuits everything.
        ctrl = read_control(control_path)
        if ctrl.auto_resume_due:
            logger.info("Auto-resume timer expired; clearing pause.")
            resume_control(control_path)
            ctrl = read_control(control_path)
            paused_logged = False

        if ctrl.paused:
            if not paused_logged:
                logger.info("Paused (%s); will not engage sleep guard.", ctrl.describe())
                paused_logged = True
            if keeping:
                logger.info("Releasing sleep guard because pause was requested.")
                guard.stop()
                keeping = False
            time.sleep(config.interval_seconds)
            continue

        if paused_logged:
            logger.info("Resumed.")
            paused_logged = False

        holder_result = holder_src.poll()
        proc_result = proc_src.poll()
        monitor_results = [m.poll() for m in monitors]
        active_monitors = [r for r in monitor_results if r.active]

        active = holder_result.active or proc_result.active or bool(active_monitors)

        if active and not keeping:
            reasons: list[str] = []
            if holder_result.active:
                reasons.append(holder_result.detail)
            if proc_result.active:
                reasons.append(proc_result.detail)
            for r in active_monitors:
                reasons.append(f"{r.name} {r.detail}")
            logger.info("Active (%s); engaging sleep guard.", "; ".join(reasons))
            guard.start()
            keeping = True
        elif not active and keeping:
            logger.info("Idle; releasing sleep guard.")
            guard.stop()
            keeping = False

        time.sleep(config.interval_seconds)
