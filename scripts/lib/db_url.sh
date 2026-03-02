#!/usr/bin/env bash
# 统一数据库 URL 解析工具（优先环境变量，其次 config/.env）。

tc_read_env_key() {
    local env_file="$1"
    local key="$2"
    if [[ -z "$env_file" || -z "$key" || ! -f "$env_file" ]]; then
        return 0
    fi
    python3 - "$env_file" "$key" <<'PY'
import sys
from pathlib import Path

env_file = Path(sys.argv[1])
target = sys.argv[2]
value = ""

for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or line.startswith("export "):
        continue
    if "=" not in line:
        continue
    key, val = line.split("=", 1)
    key = key.strip()
    if key != target:
        continue
    val = val.strip()
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        val = val[1:-1]
    elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
        val = val[1:-1]
    else:
        # 仅去掉“空白 + #注释”，避免截断 URL 中的 '#'
        comment_pos = val.find(" #")
        if comment_pos >= 0:
            val = val[:comment_pos].rstrip()
    value = val

print(value, end="")
PY
}

tc_resolve_db_url() {
    local repo_root="$1"
    local default_url="$2"
    shift 2

    local key
    for key in "$@"; do
        local direct="${!key:-}"
        if [[ -n "$direct" ]]; then
            echo "$direct"
            return 0
        fi
    done

    local env_file="$repo_root/config/.env"
    for key in "$@"; do
        local from_file
        from_file="$(tc_read_env_key "$env_file" "$key")"
        if [[ -n "$from_file" ]]; then
            echo "$from_file"
            return 0
        fi
    done

    echo "$default_url"
}
