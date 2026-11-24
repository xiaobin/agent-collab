#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m unittest discover -s tests -v
python3 scripts/validate_task.py templates/current-task.json --phase draft
python3 scripts/validate_task.py tests/fixtures/valid-task.json --phase dispatch
python3 scripts/agent_eval.py check templates/agent-evaluation.json --final

TMP_EVAL_DIR="$(mktemp -d)"
trap 'rm -f "$TMP_EVAL_DIR"/*; rmdir "$TMP_EVAL_DIR"' EXIT
python3 - "$TMP_EVAL_DIR/evaluations.jsonl" <<'PY'
import json
import sys
from pathlib import Path

record = json.loads(Path("templates/agent-evaluation.json").read_text(encoding="utf-8"))
Path(sys.argv[1]).write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
PY
python3 scripts/agent_eval.py rollup "$TMP_EVAL_DIR/evaluations.jsonl" --min-samples 1 \
  > "$TMP_EVAL_DIR/rollup.json"
python3 - "$TMP_EVAL_DIR/rollup.json" <<'PY'
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert result["final_record_count"] == 1
assert result["skipped_attempt_records"] == 0
assert result["groups"][0]["sample_count"] == 1
assert result["groups"][0]["status"] == "observed"
assert result["groups"][0]["pass_rate"] == 1.0
print("OK: agent evaluation rollup snapshot")
PY

if ! command -v claude >/dev/null 2>&1; then
  printf 'ERROR: claude CLI is required for plugin validation\n' >&2
  exit 1
fi
claude plugin validate "$ROOT"
