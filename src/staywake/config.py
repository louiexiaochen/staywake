"""TOML config loader. Pure stdlib on 3.11+; falls back to ``tomli`` on older."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]


def default_config_path() -> Path:
    env = os.environ.get("STAYWAKE_CONFIG_PATH")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
        return Path(base) / "staywake" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "staywake" / "config.toml"


@dataclass
class Config:
    interval_seconds: float = 2.0
    stale_after_seconds: float = 600.0
    aggressive: bool = True

    process_scan_enabled: bool = False
    process_scan_use_builtins: bool = False
    process_scan_patterns: List[str] = field(default_factory=list)
    process_scan_idle_patterns: List[str] = field(default_factory=list)

    # Built-in monitor toggles/overrides (e.g. {"claude_code": {"enabled": false}}).
    monitor_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # User-defined custom monitors keyed by name (e.g. {"my_tool": {"globs": [...]}}).
    monitor_extra: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        cfg = cls()
        path = path or default_config_path()
        if not path.exists() or tomllib is None:
            return cfg
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, ValueError):
            return cfg

        d = data.get("daemon", {}) if isinstance(data, dict) else {}
        if isinstance(d, dict):
            cfg.interval_seconds = float(d.get("interval_seconds", cfg.interval_seconds))
            cfg.stale_after_seconds = float(d.get("stale_after_seconds", cfg.stale_after_seconds))
            cfg.aggressive = bool(d.get("aggressive", cfg.aggressive))

        ps = data.get("process_scan", {}) if isinstance(data, dict) else {}
        if isinstance(ps, dict):
            cfg.process_scan_enabled = bool(ps.get("enabled", False))
            cfg.process_scan_use_builtins = bool(ps.get("use_builtins", False))
            cfg.process_scan_patterns = [str(p) for p in ps.get("patterns", []) or []]
            cfg.process_scan_idle_patterns = [str(p) for p in ps.get("idle_patterns", []) or []]

        # [monitors.<name>] tables → overrides for built-ins, extras for new ones.
        monitors = data.get("monitors", {}) if isinstance(data, dict) else {}
        if isinstance(monitors, dict):
            from .monitors import BUILTIN_MONITORS

            for name, raw in monitors.items():
                if not isinstance(raw, dict):
                    continue
                bucket = cfg.monitor_overrides if name in BUILTIN_MONITORS else cfg.monitor_extra
                bucket[name] = {str(k): v for k, v in raw.items()}

        return cfg
