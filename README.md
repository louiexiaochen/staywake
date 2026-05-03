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

## Supported agents

Out of the box, **zero config needed**:

| Agent | How it's detected | Idle threshold | Source |
|---|---|---|---|
| **Claude Code** (CLI) | `~/.claude/projects/**/*.jsonl` mtime | 300 s | builtin monitor `claude_code` |
| **Claude Code** (alt session dir) | `~/.claude/sessions/**/*.jsonl` mtime | 300 s | builtin monitor `claude_code_logs` |
| **OpenAI Codex CLI** | `~/.codex/sessions/**/rollout-*.jsonl` mtime | 90 s | builtin monitor `codex` |

The 300/90 s thresholds are tuned to ride out long thinking pauses without
prematurely "letting go" while the agent is mid-LLM-call.

### Adding your own agent (no code change)

Drop a snippet in `~/.config/staywake/config.toml` (macOS) or
`%APPDATA%\staywake\config.toml` (Windows):

```toml
# Examples — pick whichever matches your tool's log/transcript layout.

[monitors.opencode]
globs = ["~/.local/share/opencode/sessions/**/*.json*"]
idle_after_seconds = 120

[monitors.aider]
globs = ["~/.aider.chat.history.md", "~/**/.aider.chat.history.md"]
idle_after_seconds = 120

[monitors.cursor_agent]
globs = ["~/.cursor/logs/agent/**/*.log"]
idle_after_seconds = 120

[monitors.my_orchestrator]
globs = ["/var/log/my-pipeline/*.log"]
idle_after_seconds = 60
```

If your tool doesn't write a transcript file at all, you have two more
escape hatches:

1. **Wrap the launcher** with `staywake hold` / `staywake release` — works
   for anything that runs in a shell (see "Manual hold" below).
2. **Process-scan fallback** — match by `ps` regex; opt-in via
   `[process_scan]` in the same config file.

PRs adding new built-in monitors are welcome — if it ships with a
predictable transcript path, we'll bake it in.

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

### Pause / resume (no sudo)

Sometimes you actually *want* the laptop to sleep — you're writing prose,
or going to bed, or just curious. The daemon supports a soft pause that
needs no privileges:

```sh
staywake pause --reason "going to bed"      # paused indefinitely
staywake pause --for 1h                     # auto-resumes after 1 hour
staywake resume                             # back to normal
staywake status                             # shows PAUSED state prominently
```

When paused, the daemon stays running but short-circuits to idle on every
tick. If `--for` was set, it auto-resumes the moment that timer expires.

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
