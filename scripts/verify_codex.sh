#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
command -v codex >/dev/null 2>&1 || {
    echo "codex CLI is required for Codex package verification" >&2
    exit 2
}
command -v python3 >/dev/null 2>&1 || {
    echo "python3 is required for Codex package verification" >&2
    exit 2
}

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
mkdir -p "$TMP_DIR/package" "$TMP_DIR/codex-home"
tar \
    --exclude='.git' \
    --exclude='.omc' \
    --exclude='.herdr' \
    --exclude='__pycache__' \
    --exclude='.DS_Store' \
    -cf - -C "$ROOT" . | tar -xf - -C "$TMP_DIR/package"

export CODEX_HOME="$TMP_DIR/codex-home"
codex plugin marketplace add "$TMP_DIR/package" --json > "$TMP_DIR/marketplace-add.json"
codex plugin add agent-collab@agent-collab --json > "$TMP_DIR/plugin-add.json"
codex plugin list
codex debug prompt-input > "$TMP_DIR/prompt-input.json"

INSTALLED_PATH=$(python3 - "$TMP_DIR/plugin-add.json" "$TMP_DIR/prompt-input.json" <<'PY'
import json
import sys
from pathlib import Path

install = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert install["pluginId"] == "agent-collab@agent-collab", install
assert install["name"] == "agent-collab", install
assert install["marketplaceName"] == "agent-collab", install
assert install["version"] == "0.3.0", install
installed = Path(install["installedPath"])
manifest = json.loads((installed / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
assert manifest["name"] == "agent-collab", manifest
assert manifest["version"] == "0.3.0", manifest
skill = installed / "skills" / "project-collaboration" / "SKILL.md"
assert skill.is_file(), skill
prompt = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
rendered = json.dumps(prompt, ensure_ascii=False)
assert "- agent-collab:project-collaboration:" in rendered, "namespaced skill missing from Codex prompt input"
assert "agent-collab/0.3.0/skills/project-collaboration/SKILL.md" in rendered, rendered[:1000]
print(installed)
PY
)

sh "$INSTALLED_PATH/skills/project-collaboration/scripts/validate-task.sh" \
    "$TMP_DIR/package/templates/current-task.json" --phase draft
sh "$INSTALLED_PATH/skills/project-collaboration/scripts/agent-eval.sh" \
    check "$TMP_DIR/package/templates/agent-evaluation.json" --final

echo "OK: Codex package provides agent-collab:project-collaboration and runnable installed wrappers"
