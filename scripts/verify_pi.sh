#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v pi >/dev/null 2>&1; then
  printf 'ERROR: pi CLI is required for Pi installation verification\n' >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/package"
tar \
    --exclude='.git' \
    --exclude='.omc' \
    --exclude='.herdr' \
    --exclude='__pycache__' \
    --exclude='.DS_Store' \
    -cf - -C "$ROOT" . | tar -xf - -C "$TMP_DIR/package"

export PI_CODING_AGENT_DIR="$TMP_DIR/pi-home"
export PI_OFFLINE=1
pi install "$TMP_DIR/package"
pi list

python3 - "$TMP_DIR/package" "$TMP_DIR/pi-stderr.log" <<'PY'
import json
import os
from pathlib import Path
import select
import subprocess
import sys

package = Path(sys.argv[1]).resolve()
with open(sys.argv[2], "w+", encoding="utf-8") as stderr:
    process = subprocess.Popen(
        ["pi", "--mode", "rpc", "--no-session", "--offline", "--no-mcp"],
        cwd=package,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        env=os.environ.copy(),
    )
    response = None
    try:
        process.stdin.write(json.dumps({"id": "skills-1", "type": "get_commands"}) + "\n")
        process.stdin.flush()
        while True:
            ready, _, _ = select.select([process.stdout], [], [], 30)
            if not ready:
                raise RuntimeError("timed out waiting for Pi get_commands")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("Pi RPC exited before get_commands responded")
            event = json.loads(line)
            if event.get("id") == "skills-1":
                response = event
                break
        process.stdin.close()
        process.wait(timeout=20)
    except Exception:
        process.kill()
        process.wait()
        stderr.seek(0)
        print(stderr.read(), file=sys.stderr)
        raise

if not response.get("success"):
    raise SystemExit(f"Pi get_commands failed: {response}")
expected = "skill:project-collaboration"
matches = [
    command for command in response["data"]["commands"]
    if command.get("name") == expected
    and command.get("source") == "skill"
    and command.get("sourceInfo", {}).get("origin") == "package"
    and Path(command["sourceInfo"]["baseDir"]).resolve() == package
]
if len(matches) != 1:
    raise SystemExit(f"Installed Pi package did not provide exactly one {expected}: {matches}")
print(f"OK: Pi package provides {expected}")
PY

sh "$TMP_DIR/package/skills/project-collaboration/scripts/validate-task.sh" \
  "$TMP_DIR/package/templates/current-task.json" --phase draft
sh "$TMP_DIR/package/skills/project-collaboration/scripts/agent-eval.sh" \
  check "$TMP_DIR/package/templates/agent-evaluation.json" --final >/dev/null
printf 'OK: installed Pi package wrappers\n'
