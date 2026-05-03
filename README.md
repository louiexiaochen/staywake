# staywake

> Keep your Mac awake while AI agents (or any long task) are running — even
> with the lid closed. Sleep again the *moment* they finish.

`caffeinate` and friends keep your Mac awake — but they have no idea what
your agents are doing. Close the lid, agent finishes 2 minutes later → your
Mac keeps burning battery for hours.

`staywake` is a tiny LaunchDaemon that watches a JSON "holder list" and
toggles `caffeinate -dimsu` + `pmset disablesleep` while there's live work,
and only while there's live work. Any script, hook, or program can hold a
slot. The slot auto-releases when its PID dies, so a crashed agent can never
strand your battery.

```sh
staywake hold my-task --reason "long build"
trap 'staywake release my-task' EXIT
long_running_command
# lid-close is now safe; the moment the command finishes, sleep returns.
```

## Why a daemon

Lid-close on AC power is **not** blocked by `caffeinate` alone — you need
`pmset disablesleep 1`, which requires root. Doing it from a user-level app
is fragile (crashes, app quit, App Nap). A small root LaunchDaemon driven by
a state file solves all of this with zero IPC and no permissions dance.

## Install

Requires Python 3.9+ (3.11+ uses stdlib `tomllib`; older needs `tomli`).

```sh
git clone https://github.com/<you>/staywake && cd staywake

# 1. Install the CLI/library for your user.
pip install --user -e .

# 2. Install + bootstrap the LaunchDaemon (root, system-wide).
sudo ./packaging/install.sh

# 3. Smoke test.
staywake hold demo --reason test
staywake status
pgrep -fl 'caffeinate -dimsu'      # should show one process
staywake release demo
pgrep -fl 'caffeinate -dimsu'      # should be empty within ~2s
```

Logs land at `/var/log/staywake.log`.

Uninstall:

```sh
sudo ./packaging/install.sh --uninstall
```

## Use

### CLI

```sh
staywake hold <id> [--reason "..."]    # add or refresh
staywake release <id>                  # remove
staywake status                        # JSON-able snapshot
staywake daemon                        # foreground (used by launchd)
```

If `<id>` is omitted, it defaults to `shell-<PPID>` — convenient for one-off
shell wraps.

### Python

```python
from staywake import holding

with holding("agent-run", reason="claude SDK call"):
    run_agent()                # mac stays awake; releases on exit/exception
```

### Process scan (opt-in, for tools you can't modify)

`~/.config/staywake/config.toml`:

```toml
[process_scan]
enabled = true
patterns      = ["\\bcodex\\b", "\\bclaude\\b"]
idle_patterns = ["language-server", "tsc --watch"]
```

The daemon will then ALSO consider those processes' subtrees as activity.

## Hooking it into agents

* **Claude Code**: see [`examples/claude-code-hooks.md`](examples/claude-code-hooks.md).
* **Codex / OpenCode / anything CLI**: wrap the launcher with
  `staywake hold` + `trap release`. See
  [`examples/shell-wrap.sh`](examples/shell-wrap.sh).
* **Your own Python tool**: `with staywake.holding(...)`.

## How it works (in 6 lines)

```text
producer  ──hold───►  ~/.local/state/staywake/holders.json  ◄─poll(2s)── daemon
                                                                            │
                                                                            ▼
                              caffeinate -dimsu + pmset disablesleep=1
                                              │
                                              ▼
                            release / PID dies / >10min stale  ─────►  reverse
```

A holder is **live** iff its PID is alive *and* its `updatedAt` isn't stale.
The daemon is the sole pruner; producers only ever upsert/remove their own
ids. Crashed producer? PID gone → daemon drops the holder on the next tick.
Stuck file? Stale check drops it. No supervision channel to break.

## Design notes

* **Why not just `caffeinate -t <seconds>`?** You don't know how long the
  agent will take, and you want lid-close-on-finish to be normal again.
* **Why root?** `pmset disablesleep` requires it. We need that knob to make
  lid-close behave; `caffeinate` alone doesn't block it on AC.
* **Why a JSON file, not a socket?** Zero deps, zero language constraints,
  trivial to drive from shell hooks, easy to inspect (`cat`).
* **Why prune dead PIDs?** A whole class of bugs disappears: app crash,
  forgotten `release`, panic — none of them can trap your Mac in awake mode.

## Status

Pre-1.0. Single-author. Works on macOS 14+. Open to issues / PRs.

## License

MIT.
