# Windows install

Requires Python 3.9+ on PATH (the `py` launcher is fine).

```powershell
# 1. Install the library + CLI for your user.
pip install --user .

# 2. Register the daemon as a Scheduled Task (run PowerShell as Administrator).
.\install.ps1

# 3. Smoke test.
py -m staywake.cli hold demo --reason test
py -m staywake.cli status
py -m staywake.cli release demo
```

Uninstall:

```powershell
.\install.ps1 -Uninstall
```

## How sleep is blocked on Windows

`staywake` calls `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED |
ES_DISPLAY_REQUIRED)` from the daemon process. That blocks idle sleep and
display sleep but **not** lid-close sleep — lid-close is a power-button event
governed by your power scheme.

If `aggressive = true` in your config (default) **and** the task runs
elevated, the daemon also calls `powercfg /SETACVALUEINDEX SCHEME_CURRENT
4f971e89-... 5ca83367-... 0` to set the lid action to "Do nothing" while
work is in flight, and restores it on idle.

If you can't run elevated, manually set "When I close the lid" → "Do nothing"
in Settings → System → Power & battery, and the daemon's idle/display
assertions will keep your machine awake even when the lid is closed.

## Why a Scheduled Task instead of a Service

`SetThreadExecutionState` lives on the calling process's thread; the assertion
must be in the user's interactive session for display/system sleep to be
correctly blocked while you're logged in. A user-session Scheduled Task
("AtLogOn", `RunLevel Highest`) is the simplest way to get that without
shipping a Windows Service binary.
