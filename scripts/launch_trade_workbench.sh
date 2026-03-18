#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SESSION_NAME="${TRADE_WORKBENCH_SESSION:-tradecat-workbench}"
WINDOW_NAME="${TRADE_WORKBENCH_WINDOW:-main}"
DRY_RUN=0
PRINT_MANUAL=0

usage() {
  cat <<'EOF'
Usage: ./scripts/launch_trade_workbench.sh [options]

Launch a dual TUI workbench in tmux:
- left pane: native TradeCat TUI
- right pane: native openclaw tui

Options:
  --dry-run         Print the resolved pane commands and exit
  --print-manual    Print the two standalone pane commands and exit
  --session NAME    Override tmux session name (default: tradecat-workbench)
  -h, --help        Show this help

Environment overrides:
  TRADE_WORKBENCH_SESSION       tmux session name override
  TRADE_WORKBENCH_WINDOW        tmux window name override
  TRADE_WORKBENCH_TRADECAT_CMD  Full command for the left pane
  TRADE_WORKBENCH_OPENCLAW_CMD  Full command for the right pane

Notes:
  - tmux is required for the side-by-side workspace.
  - If tmux is unavailable, the script prints a manual two-terminal fallback.
  - If openclaw is not on PATH, set TRADE_WORKBENCH_OPENCLAW_CMD explicitly.
EOF
}

die() {
  echo "✗ $*" >&2
  exit 1
}

build_tradecat_cmd() {
  if [[ -n "${TRADE_WORKBENCH_TRADECAT_CMD:-}" ]]; then
    printf '%s\n' "$TRADE_WORKBENCH_TRADECAT_CMD"
    return 0
  fi

  local tui_script="$ROOT/services-preview/tui-service/scripts/start.sh"
  if [[ ! -f "$tui_script" ]]; then
    die "未找到 TradeCat TUI 启动脚本: $tui_script"
  fi

  printf 'cd %q && export TUI_SINGLETON=%q && export TUI_HOT_RELOAD=%q && bash %q run' \
    "$ROOT" "${TUI_SINGLETON:-0}" "${TUI_HOT_RELOAD:-0}" "$tui_script"
  printf '\n'
}

build_openclaw_cmd() {
  if [[ -n "${TRADE_WORKBENCH_OPENCLAW_CMD:-}" ]]; then
    printf '%s\n' "$TRADE_WORKBENCH_OPENCLAW_CMD"
    return 0
  fi

  if command -v openclaw >/dev/null 2>&1; then
    printf 'cd %q && openclaw tui\n' "$ROOT"
    return 0
  fi

  local submodule_status=""
  submodule_status="$(git -C "$ROOT" submodule status -- repository/openclaw 2>/dev/null || true)"

  echo "✗ 未找到 openclaw CLI（PATH 中无 \`openclaw\`）" >&2
  if [[ "$submodule_status" == -* ]]; then
    echo "  检测到 repository/openclaw 子模块尚未初始化。" >&2
    echo "  先执行: git submodule update --init --recursive repository/openclaw" >&2
  fi
  echo "  可改为设置 TRADE_WORKBENCH_OPENCLAW_CMD 指向本机可用的 openclaw 启动命令。" >&2
  exit 1
}

print_manual_fallback() {
  local tradecat_cmd="$1"
  local openclaw_cmd="$2"

  cat <<EOF
Manual fallback (run in two terminals):
1. Left / TradeCat TUI
   $tradecat_cmd
2. Right / openclaw tui
   $openclaw_cmd
EOF
}

require_tmux() {
  if command -v tmux >/dev/null 2>&1; then
    return 0
  fi

  echo "✗ 未安装 tmux，无法自动创建双 TUI 工作台。" >&2
  echo "  请先安装 tmux，或使用下面的手工降级方案。" >&2
  print_manual_fallback "$1" "$2" >&2
  exit 1
}

require_tty() {
  if [[ -t 0 && -t 1 ]]; then
    return 0
  fi

  echo "✗ 当前会话不是交互式终端，无法 attach tmux 会话。" >&2
  echo "  请在本地终端或 ssh -t 会话中运行此脚本。" >&2
  exit 1
}

create_session() {
  local session="$1"
  local window="$2"
  local tradecat_cmd="$3"
  local openclaw_cmd="$4"

  tmux new-session -d -s "$session" -n "$window" -c "$ROOT"
  tmux split-window -h -t "$session:$window" -c "$ROOT"
  tmux set-window-option -t "$session:$window" remain-on-exit on >/dev/null
  tmux select-layout -t "$session:$window" even-horizontal >/dev/null
  tmux send-keys -t "$session:$window.0" -l "$tradecat_cmd"
  tmux send-keys -t "$session:$window.0" C-m
  tmux send-keys -t "$session:$window.1" -l "$openclaw_cmd"
  tmux send-keys -t "$session:$window.1" C-m
  tmux select-pane -t "$session:$window.0" >/dev/null
  tmux select-pane -t "$session:$window.0" -T "TradeCat TUI" 2>/dev/null || true
  tmux select-pane -t "$session:$window.1" -T "openclaw tui" 2>/dev/null || true
}

attach_session() {
  local session="$1"
  if [[ -n "${TMUX:-}" ]]; then
    exec tmux switch-client -t "$session"
  fi
  exec tmux attach-session -t "$session"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --print-manual)
      PRINT_MANUAL=1
      ;;
    --session)
      shift
      [[ $# -gt 0 ]] || die "--session 需要一个值"
      SESSION_NAME="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "未知参数: $1"
      ;;
  esac
  shift
done

TRADECAT_CMD="$(build_tradecat_cmd)"
OPENCLAW_CMD="$(build_openclaw_cmd)"

if [[ "$DRY_RUN" == "1" ]]; then
  cat <<EOF
session=$SESSION_NAME
window=$WINDOW_NAME
left=$TRADECAT_CMD
right=$OPENCLAW_CMD
EOF
  exit 0
fi

if [[ "$PRINT_MANUAL" == "1" ]]; then
  print_manual_fallback "$TRADECAT_CMD" "$OPENCLAW_CMD"
  exit 0
fi

require_tmux "$TRADECAT_CMD" "$OPENCLAW_CMD"
require_tty

if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  create_session "$SESSION_NAME" "$WINDOW_NAME" "$TRADECAT_CMD" "$OPENCLAW_CMD"
fi

attach_session "$SESSION_NAME"
