# staywake

> Keep your laptop awake **only while AI agents are actually working**, and
> let it sleep the moment they finish. macOS and Windows.

`caffeinate` and friends keep your Mac awake — but they have no idea what
your agents are doing. Close the lid, agent finishes 2 minutes later → your
Mac keeps burning battery for hours.

`staywake` is a tiny daemon that **watches your agent's transcript files**
(Claude Code, Codex, …) and toggles sleep blocking automatically. Out of the
box, no scripts, no wrapping. Or use the explicit `hold` / `release` API for
custom tooling.

```sh
# install once. then just run your agent normally.
claude
# … staywake notices the JSONL transcript growing → blocks sleep
# … your agent finishes → file mtime stops moving → daemon releases → laptop sleeps
```

## Why a daemon

* **macOS**: `caffeinate` alone doesn't block lid-close on AC; you need
  `pmset disablesleep`, which requires root. A user-level app gives up that
  privilege every time it crashes or is force-quit.
* **Windows**: `SetThreadExecutionState` lives on the *thread* that holds
  it, so you want a long-running daemon process holding the assertion in
  your interactive session.

A small platform-native background process driven by a state file solves
both, with zero IPC and no permissions dance.

## Install

### macOS

Requires Python 3.9+.

```sh
git clone https://github.com/<you>/staywake && cd staywake
pip install --user .
sudo ./packaging/macos/install.sh

# smoke test
staywake status
```

Uninstall: `sudo ./packaging/macos/install.sh --uninstall`

### Windows

Requires Python 3.9+ on PATH (the `py` launcher is fine).

```powershell
git clone https://github.com/<you>/staywake; cd staywake
pip install --user .

# Run PowerShell as Administrator:
.\packaging\windows\install.ps1
```

See [`packaging/windows/README.md`](packaging/windows/README.md) for details
on lid-action handling.

## How it works

```
                              ┌─────────────────────┐
   Claude Code transcript ───►│                     │
   Codex rollout JSONL    ───►│                     │
   custom log glob        ───►│  staywake daemon    │
                              │  (polls every 2s)   │── caffeinate -dimsu        (macOS)
   `staywake hold` (CLI)  ───►│                     │── pmset disablesleep=1     (macOS, root)
   `with holding(...)`    ───►│                     │── SetThreadExecutionState  (Windows)
                              │                     │── powercfg lid override    (Windows, admin)
                              └─────────────────────┘
                                          ▲
                                          │
                       any source active  │  no sources active
                          → engage        │  → release
```

A monitor is **active** if any matching file's mtime is within
``idle_after_seconds``. Defaults are deliberately generous —
**300s for Claude Code**, **90s for Codex** — because extended thinking,
huge-context calls, and waitingApproval routinely create multi-minute
gaps between transcript writes that should *not* be confused with
"agent finished". Tune per monitor in TOML.

A holder is **live** if its PID is alive *and* its `updatedAt` isn't stale.
A crashed agent → PID dies → daemon drops it on next tick. No supervision
channel to break.

## Use

### Auto mode (default)

Just install. The daemon ships with built-in monitors for Claude Code
(`~/.claude/projects/**/*.jsonl`) and OpenAI Codex CLI
(`~/.codex/sessions/**/rollout-*.jsonl`).

### Manual hold (CLI)

```sh
staywake hold my-task --reason "long build"
trap 'staywake release my-task' EXIT
long_running_command
```

### Manual hold (Python)

```python
from staywake import holding

with holding("agent-run", reason="claude SDK call"):
    run_agent()
```

### Custom monitors

Add to `~/.config/staywake/config.toml` (macOS) or
`%APPDATA%\staywake\config.toml` (Windows):

```toml
[monitors.my_pipeline]
globs = ["/var/log/my-agent/*.log"]
idle_after_seconds = 60
```

See [`examples/config.example.toml`](examples/config.example.toml).

## Platform support

| | macOS | Windows | Linux |
|---|---|---|---|
| daemon | ✅ LaunchDaemon | ✅ Scheduled Task | ⚠️ runs but no-ops on sleep |
| sleep blocking | `caffeinate -dimsu` | `SetThreadExecutionState` | (TODO: `systemd-inhibit`) |
| lid override | `pmset disablesleep` (root) | `powercfg` lid action (admin) | n/a |
| monitors | ✅ | ✅ | ✅ |
| holder API | ✅ | ✅ | ✅ |

Linux runs but doesn't actually block sleep yet — the monitors and CLI
work, the SleepGuard is a no-op. PRs welcome.

## Status

Pre-1.0. Single-author. Open to issues and PRs.

## License

MIT.
