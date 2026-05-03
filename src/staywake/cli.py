"""``staywake`` CLI — hold / release / status / daemon."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from . import api
from . import control as ctrl
from .config import Config, default_config_path
from .daemon import run_daemon
from .state import default_state_path


def _default_holder_id() -> str:
    """Use parent shell PID by default so dumb shell idiom works."""
    return f"shell-{os.getppid()}"


def _setup_logging(verbose: bool, foreground: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    if foreground:
        logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    else:
        logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%d %H:%M:%S")


# ---- subcommands -----------------------------------------------------------

def cmd_hold(args: argparse.Namespace) -> int:
    holder_id = args.id or _default_holder_id()
    # The CLI process exits immediately, so its own PID is useless as a
    # liveness handle. Default to the parent (the invoking shell), so the
    # idiomatic `staywake hold; long_command; staywake release` pattern works.
    pid = args.pid if args.pid is not None else os.getppid()
    h = api.hold(
        holder_id=holder_id,
        reason=args.reason or "",
        pid=pid,
        state_path=args.state_path,
    )
    if args.json:
        print(json.dumps(h.to_json(), ensure_ascii=False))
    else:
        print(f"holding id={h.id} pid={h.pid} reason={h.reason!r}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    holder_id = args.id or _default_holder_id()
    api.release(holder_id, state_path=args.state_path)
    if not args.json:
        print(f"released id={holder_id}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config_path)
    info = api.status(state_path=args.state_path, stale_after_seconds=cfg.stale_after_seconds)
    control = ctrl.read_control(args.control_path)
    if args.json:
        out = dict(info)
        out["control"] = control.to_json()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if control.paused:
        print(f"\033[33mPAUSED\033[0m  {control.describe()}")
    else:
        print("\033[32mrunning\033[0m")
    print(f"state file: {info['state_path']}")
    print(f"live holders: {info['live_count']}  active: {info['active']}")
    if info["holders"]:
        print()
        print(f"  {'id':<24} {'pid':>7}  reason")
        print(f"  {'-'*24} {'-'*7}  {'-'*40}")
        for h in info["holders"]:
            pid = h.get("pid")
            print(f"  {str(h.get('id',''))[:24]:<24} {str(pid) if pid else '-':>7}  {h.get('reason','')}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    duration: Optional[float] = None
    if args.for_:
        duration = ctrl.parse_duration(args.for_)
        if duration is None or duration <= 0:
            print(f"error: could not parse duration {args.for_!r}; use e.g. 30s, 5m, 1h", file=sys.stderr)
            return 2
    state = ctrl.pause(reason=args.reason or "", duration_seconds=duration, path=args.control_path)
    if args.json:
        print(json.dumps(state.to_json(), ensure_ascii=False))
    else:
        print(state.describe())
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    ctrl.resume(path=args.control_path)
    if not args.json:
        print("resumed")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config_path)
    return run_daemon(
        state_path=args.state_path,
        config=cfg,
        control_path=args.control_path,
    ) or 0


def cmd_config_path(_args: argparse.Namespace) -> int:
    print(default_config_path())
    return 0


# ---- argparse --------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--state-path",
        type=Path,
        default=None,
        help=f"Override holder state file (default: {default_state_path()}).",
    )
    p.add_argument(
        "--config-path",
        type=Path,
        default=None,
        help=f"Override TOML config (default: {default_config_path()}).",
    )
    p.add_argument(
        "--control-path",
        type=Path,
        default=None,
        help=f"Override pause control file (default: {ctrl.default_control_path()}).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="staywake",
        description="Keep macOS awake while named work is in flight.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_hold = sub.add_parser("hold", help="Add or refresh a holder.")
    p_hold.add_argument("id", nargs="?", help="Holder id. Defaults to shell-<PPID>.")
    p_hold.add_argument("--reason", default="", help="Human-readable reason for logs/status.")
    p_hold.add_argument("--pid", type=int, default=None, help="PID to track (default: current process).")
    _add_common(p_hold)
    p_hold.set_defaults(func=cmd_hold)

    p_rel = sub.add_parser("release", help="Remove a holder.")
    p_rel.add_argument("id", nargs="?", help="Holder id. Defaults to shell-<PPID>.")
    _add_common(p_rel)
    p_rel.set_defaults(func=cmd_release)

    p_st = sub.add_parser("status", help="Show current holders + active state.")
    _add_common(p_st)
    p_st.set_defaults(func=cmd_status)

    p_pause = sub.add_parser(
        "pause",
        help="Pause the daemon (stop blocking sleep) without sudo.",
    )
    p_pause.add_argument("--reason", default="", help="Why you're pausing — saved for status output.")
    p_pause.add_argument(
        "--for",
        dest="for_",
        default="",
        help="Auto-resume after this duration. Examples: 30s, 5m, 1h, 8h. Omit for indefinite.",
    )
    _add_common(p_pause)
    p_pause.set_defaults(func=cmd_pause)

    p_resume = sub.add_parser("resume", help="Resume the daemon (clear pause).")
    _add_common(p_resume)
    p_resume.set_defaults(func=cmd_resume)

    p_dae = sub.add_parser("daemon", help="Run the daemon in the foreground.")
    p_dae.add_argument("--foreground", action="store_true", help="(default) — kept for clarity in plist args.")
    _add_common(p_dae)
    p_dae.set_defaults(func=cmd_daemon)

    p_cfg = sub.add_parser("config-path", help="Print where the config file lives.")
    p_cfg.set_defaults(func=cmd_config_path)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False), foreground=(args.cmd == "daemon"))
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
