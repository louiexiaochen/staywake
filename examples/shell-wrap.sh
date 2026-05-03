#!/usr/bin/env bash
# Wrap any long-running command so the Mac stays awake until it finishes.
#
#     ./shell-wrap.sh ./my-long-build.sh
#     ./shell-wrap.sh make test
#
# trap ensures we release even on Ctrl-C / failure.
set -euo pipefail

ID="cmd-$$"
staywake hold "${ID}" --reason "${*:-shell-wrap}"
trap 'staywake release "${ID}"' EXIT INT TERM

"$@"
