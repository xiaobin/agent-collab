#!/bin/sh
set -eu

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to run agent-collab evaluation" >&2
    exit 127
fi

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
PACKAGE_ROOT=$(CDPATH= cd "$SCRIPT_DIR/../../.." && pwd)
exec python3 "$PACKAGE_ROOT/scripts/agent_eval.py" "$@"
