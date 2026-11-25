#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PACKAGE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd -P)

if ! command -v python3 >/dev/null 2>&1; then
  echo "bot: python3 is required to validate and run bot definitions" >&2
  exit 127
fi

exec python3 "$PACKAGE_ROOT/scripts/bot.py" "$@"