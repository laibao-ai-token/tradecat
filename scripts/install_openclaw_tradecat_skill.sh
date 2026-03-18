#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-$HOME/.openclaw/workspace}"
TARGET_DIR="$WORKSPACE_DIR/skills/tradecat-bridge"
TEMPLATE_PATH="$ROOT/skills/tradecat-bridge/SKILL.md.template"

if [[ ! -f "$TEMPLATE_PATH" ]]; then
  echo "missing template: $TEMPLATE_PATH" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

python3 - "$ROOT" "$TEMPLATE_PATH" "$TARGET_DIR/SKILL.md" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
template_path = Path(sys.argv[2]).resolve()
target_path = Path(sys.argv[3]).resolve()

template = template_path.read_text(encoding="utf-8")
rendered = template.replace("__TRADECAT_ROOT__", str(root))
target_path.write_text(rendered, encoding="utf-8")
print(target_path)
PY

echo "Installed workspace skill: $TARGET_DIR/SKILL.md"
echo "Next step: start a new openclaw session or respawn the right pane."
