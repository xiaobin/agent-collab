#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m unittest discover -s tests -v
python3 scripts/validate_task.py templates/current-task.json --phase draft
python3 scripts/validate_task.py tests/fixtures/valid-task.json --phase dispatch

if ! command -v claude >/dev/null 2>&1; then
  printf 'ERROR: claude CLI is required for plugin validation\n' >&2
  exit 1
fi
claude plugin validate "$ROOT"
