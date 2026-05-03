# Wiring staywake from Claude Code hooks

Claude Code lets you run shell commands on session lifecycle events. Use that
to hold awake for the entire duration of a session.

In `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command",
            "command": "staywake hold claude-${CLAUDE_SESSION_ID:-default} --reason 'claude-code session'" }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command",
            "command": "staywake release claude-${CLAUDE_SESSION_ID:-default}" }
        ]
      }
    ]
  }
}
```

If a session ends abnormally (process killed, machine rebooted), the daemon
will still drop your stale holder automatically — staleness defaults to 10 min.

## For Codex CLI / OpenCode / any other agent

Same idea. Wrap your launcher:

```sh
#!/usr/bin/env bash
ID="codex-$$"
staywake hold "$ID" --reason "codex run"
trap 'staywake release "$ID"' EXIT INT TERM
exec codex "$@"
```
