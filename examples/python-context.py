"""Hold the Mac awake for the duration of a Python block."""

from __future__ import annotations

import time

from staywake import holding


def long_running_agent_task() -> None:
    # Pretend to call your agent SDK / run a build / etc.
    for _ in range(5):
        print("…working")
        time.sleep(2)


def main() -> None:
    with holding("agent-run", reason="claude-code SDK call"):
        long_running_agent_task()


if __name__ == "__main__":
    main()
