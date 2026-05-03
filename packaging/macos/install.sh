#!/usr/bin/env bash
# Install the staywake LaunchDaemon system-wide.
#
#   sudo ./packaging/install.sh                 # install + bootstrap
#   sudo ./packaging/install.sh --uninstall     # bootout + remove
#
# Requires that `staywake` be importable. We resolve the binary path of the
# user's pip-installed CLI via `python3 -m staywake.cli`, which avoids
# hard-coding /usr/local/bin and works for `pip install --user`.
set -euo pipefail

LABEL="dev.staywake.daemon"
PLIST_DEST="/Library/LaunchDaemons/${LABEL}.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/${LABEL}.plist.template"

if [[ "${EUID}" -ne 0 ]]; then
    echo "error: must run as root (sudo)." >&2
    exit 1
fi

# Resolve target user. SUDO_USER is the invoking user, not root.
TARGET_USER="${SUDO_USER:-${USER}}"
TARGET_HOME="$(eval echo "~${TARGET_USER}")"

STATE_PATH="${TARGET_HOME}/.local/state/staywake/holders.json"
CONFIG_PATH="${TARGET_HOME}/.config/staywake/config.toml"

# Find the python that has staywake installed (prefer target user's env).
RESOLVE_PY="
import shutil, sys
print(sys.executable)
"
TARGET_PY="$(sudo -u "${TARGET_USER}" python3 -c "${RESOLVE_PY}" 2>/dev/null || true)"
if [[ -z "${TARGET_PY}" ]]; then
    echo "error: python3 not found for user ${TARGET_USER}." >&2
    exit 1
fi

# Make sure staywake is installed for the target user, and pull in any
# changes since the last install (i.e., a fresh `git pull`). We always
# --force-reinstall so the user can't end up with a daemon running an
# older version than what's checked out — that's a confusing failure mode.
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
echo "=== installing/upgrading staywake from ${REPO_DIR} for ${TARGET_USER} ==="
if ! sudo -u "${TARGET_USER}" "${TARGET_PY}" -m pip install --user --upgrade --force-reinstall --quiet "${REPO_DIR}"; then
    echo "error: pip install failed for ${TARGET_USER}." >&2
    exit 1
fi

# Sanity-check after install.
if ! sudo -u "${TARGET_USER}" "${TARGET_PY}" -c "import staywake; print('  installed: staywake', staywake.__version__)"; then
    echo "error: 'staywake' still not importable after install." >&2
    exit 1
fi

# launchd runs us as root. To let root's python find the user-installed
# package, we forward the user's site-packages dir into the daemon's
# environment via PYTHONPATH.
TARGET_USER_SITE="$(sudo -u "${TARGET_USER}" "${TARGET_PY}" -c 'import site,sys; print(site.getusersitepackages())')"
if [[ -z "${TARGET_USER_SITE}" || ! -d "${TARGET_USER_SITE}" ]]; then
    echo "error: could not resolve user-site dir for ${TARGET_USER}." >&2
    exit 1
fi

# We launch the daemon via "<python> -m staywake.cli daemon", which needs the
# launchd ProgramArguments array to start with the python binary, not staywake.
STAYWAKE_BIN="${TARGET_PY}"

uninstall() {
    if launchctl print "system/${LABEL}" >/dev/null 2>&1; then
        launchctl bootout "system/${LABEL}" || true
    fi
    rm -f "${PLIST_DEST}"
    echo "uninstalled."
}

install_plist() {
    mkdir -p "$(dirname "${PLIST_DEST}")"
    sudo -u "${TARGET_USER}" mkdir -p "$(dirname "${STATE_PATH}")" "$(dirname "${CONFIG_PATH}")"

    # Build a slightly different ProgramArguments — we need:
    #   <python> -m staywake.cli daemon --state-path ... --config-path ...
    cat > "${PLIST_DEST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${STAYWAKE_BIN}</string>
    <string>-m</string>
    <string>staywake.cli</string>
    <string>daemon</string>
    <string>--state-path</string>
    <string>${STATE_PATH}</string>
    <string>--config-path</string>
    <string>${CONFIG_PATH}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key>
    <string>${TARGET_USER_SITE}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/var/log/staywake.log</string>
  <key>StandardErrorPath</key>
  <string>/var/log/staywake.log</string>
</dict>
</plist>
PLIST

    chmod 644 "${PLIST_DEST}"
    chown root:wheel "${PLIST_DEST}"

    # Re-bootstrap (idempotent). Both bootout and bootstrap are async-ish
    # and racy with KeepAlive respawns of a crash-looping daemon, so we
    # poll for unload and retry bootstrap on EIO/EBUSY.
    if launchctl print "system/${LABEL}" >/dev/null 2>&1; then
        echo "  -> already loaded, booting out first"
        launchctl bootout "system/${LABEL}" 2>/dev/null || true
        # Wait for it to actually leave the registry.
        for i in 1 2 3 4 5 6 7 8 9 10; do
            if ! launchctl print "system/${LABEL}" >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        # Belt-and-braces: if the previous daemon was stuck in a respawn
        # loop, KeepAlive may have orphaned a python process for a moment.
        pkill -f "staywake.cli daemon" 2>/dev/null || true
    fi

    # Bootstrap with retry — we sometimes see "5: Input/output error" right
    # after a noisy bootout if launchd's IPC channel is still settling.
    bootstrap_ok=false
    for attempt in 1 2 3 4 5; do
        if launchctl bootstrap system "${PLIST_DEST}" 2>/dev/null; then
            bootstrap_ok=true
            break
        fi
        echo "  -> bootstrap attempt ${attempt} failed, retrying in 2s"
        sleep 2
    done
    if ! $bootstrap_ok; then
        echo "error: launchctl bootstrap kept failing. Try manually:" >&2
        echo "  sudo launchctl bootstrap system ${PLIST_DEST}" >&2
        exit 1
    fi
    launchctl enable "system/${LABEL}"

    echo "installed: ${PLIST_DEST}"
    echo "  user:        ${TARGET_USER}"
    echo "  state path:  ${STATE_PATH}"
    echo "  config path: ${CONFIG_PATH}"
    echo "  log:         /var/log/staywake.log"
    echo
    echo "Try it:"
    echo "  sudo -u ${TARGET_USER} staywake hold demo --reason test"
    echo "  sudo -u ${TARGET_USER} staywake status"
    echo "  pgrep -fl 'caffeinate -dimsu'"
    echo "  sudo -u ${TARGET_USER} staywake release demo"
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
else
    install_plist
fi
