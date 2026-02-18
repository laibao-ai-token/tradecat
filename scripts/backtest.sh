#!/usr/bin/env bash
# 顶层回测脚本转发（signal-service）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

exec "$REPO_ROOT/services/signal-service/scripts/backtest.sh" "$@"
