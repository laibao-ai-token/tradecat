#!/usr/bin/env bash
# tui-service 启动脚本（预览）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$(dirname "$SERVICE_DIR")")"

# NOTE: tui-service does not require config/.env.
# Do NOT source it here, because template values may include spaces/parentheses and break bash parsing.

VENV_DIR="$SERVICE_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  echo "创建虚拟环境..."
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install -q --upgrade pip
  if [[ -f "$SERVICE_DIR/requirements.txt" ]]; then
    "$VENV_DIR/bin/pip" install -q -r "$SERVICE_DIR/requirements.txt"
  fi
fi

PYTHON="$VENV_DIR/bin/python"

SIGNAL_START_SCRIPT="$REPO_ROOT/services/signal-service/scripts/start.sh"
SIGNAL_PID_FILE="$REPO_ROOT/services/signal-service/logs/signal-service.pid"
DATA_START_SCRIPT="$REPO_ROOT/services/data-service/scripts/start.sh"
DATA_PID_FILE="$REPO_ROOT/services/data-service/pids/daemon.pid"

# 默认自动拉起 signal/data-service，可用 TUI_AUTO_START_* =0 关闭。
AUTO_START_SIGNAL="${TUI_AUTO_START_SIGNAL:-1}"
AUTO_START_DATA="${TUI_AUTO_START_DATA:-1}"

# 默认退出 TUI 后 1 小时再停止由 TUI 启动的服务。
SIGNAL_STOP_DELAY_SECONDS="${TUI_SIGNAL_STOP_DELAY_SECONDS:-3600}"
DATA_STOP_DELAY_SECONDS="${TUI_DATA_STOP_DELAY_SECONDS:-3600}"

# 仅在本脚本启动该服务时置为 1，退出 TUI 时才会自动 stop。
SIGNAL_STARTED_BY_TUI=0
DATA_STARTED_BY_TUI=0

if ! [[ "$SIGNAL_STOP_DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
  SIGNAL_STOP_DELAY_SECONDS=3600
fi
if ! [[ "$DATA_STOP_DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
  DATA_STOP_DELAY_SECONDS=3600
fi

_schedule_delayed_stop() {
  local managed_pid="$1"
  local delay="$2"
  local pid_file="$3"
  local start_script="$4"

  TUI_DELAY_MANAGED_PID="$managed_pid" \
  TUI_DELAY_SECONDS="$delay" \
  TUI_DELAY_PID_FILE="$pid_file" \
  TUI_DELAY_START_SCRIPT="$start_script" \
  nohup /bin/bash -lc '
    sleep "${TUI_DELAY_SECONDS}"
    if [[ -f "${TUI_DELAY_PID_FILE}" ]] && [[ "$(cat "${TUI_DELAY_PID_FILE}" 2>/dev/null || true)" == "${TUI_DELAY_MANAGED_PID}" ]]; then
      "${TUI_DELAY_START_SCRIPT}" stop >/dev/null 2>&1 || true
    fi
  ' >/dev/null 2>&1 &
}

_start_signal_for_tui() {
  if [[ "$AUTO_START_SIGNAL" != "1" ]]; then
    return 0
  fi

  if [[ ! -x "$SIGNAL_START_SCRIPT" ]]; then
    echo "⚠️ 未找到 signal-service 启动脚本: $SIGNAL_START_SCRIPT"
    echo "   将继续启动 TUI（仅行情/历史视图可用）"
    return 0
  fi

  if "$SIGNAL_START_SCRIPT" status >/dev/null 2>&1; then
    echo "✓ signal-service 已运行（TUI 复用现有进程）"
    return 0
  fi

  echo "启动 signal-service（随 TUI 一并启动）..."
  if "$SIGNAL_START_SCRIPT" start; then
    SIGNAL_STARTED_BY_TUI=1
  else
    echo "⚠️ signal-service 启动失败，继续进入 TUI（仅行情/历史视图可用）"
  fi
}

_start_data_for_tui() {
  if [[ "$AUTO_START_DATA" != "1" ]]; then
    return 0
  fi

  if [[ ! -x "$DATA_START_SCRIPT" ]]; then
    echo "⚠️ 未找到 data-service 启动脚本: $DATA_START_SCRIPT"
    echo "   将继续启动 TUI（仅行情/历史视图可用）"
    return 0
  fi

  if "$DATA_START_SCRIPT" status >/dev/null 2>&1; then
    echo "✓ data-service 已运行（TUI 复用现有进程）"
    return 0
  fi

  echo "启动 data-service（随 TUI 一并启动）..."
  if "$DATA_START_SCRIPT" start; then
    DATA_STARTED_BY_TUI=1
  else
    echo "⚠️ data-service 启动失败，继续进入 TUI（行情仍可直接抓取）"
  fi
}

_stop_signal_if_needed() {
  if [[ "${SIGNAL_STARTED_BY_TUI:-0}" != "1" ]]; then
    return 0
  fi

  if [[ "${TUI_KEEP_SIGNAL_ON_EXIT:-0}" == "1" ]]; then
    echo "保留 signal-service 运行中（TUI_KEEP_SIGNAL_ON_EXIT=1）"
    return 0
  fi

  if [[ "$SIGNAL_STOP_DELAY_SECONDS" == "0" ]]; then
    echo "停止 signal-service（由 TUI 启动）..."
    "$SIGNAL_START_SCRIPT" stop >/dev/null 2>&1 || true
    return 0
  fi

  local managed_pid=""
  if [[ -f "$SIGNAL_PID_FILE" ]]; then
    managed_pid="$(cat "$SIGNAL_PID_FILE" 2>/dev/null || true)"
  fi

  if [[ -z "$managed_pid" ]]; then
    echo "停止 signal-service（由 TUI 启动）..."
    "$SIGNAL_START_SCRIPT" stop >/dev/null 2>&1 || true
    return 0
  fi

  echo "已安排 ${SIGNAL_STOP_DELAY_SECONDS}s 后停止 signal-service（可用 TUI_SIGNAL_STOP_DELAY_SECONDS=0 立即停止）"
  _schedule_delayed_stop "$managed_pid" "$SIGNAL_STOP_DELAY_SECONDS" "$SIGNAL_PID_FILE" "$SIGNAL_START_SCRIPT"
}

_stop_data_if_needed() {
  if [[ "${DATA_STARTED_BY_TUI:-0}" != "1" ]]; then
    return 0
  fi

  if [[ "${TUI_KEEP_DATA_ON_EXIT:-0}" == "1" ]]; then
    echo "保留 data-service 运行中（TUI_KEEP_DATA_ON_EXIT=1）"
    return 0
  fi

  if [[ "$DATA_STOP_DELAY_SECONDS" == "0" ]]; then
    echo "停止 data-service（由 TUI 启动）..."
    "$DATA_START_SCRIPT" stop >/dev/null 2>&1 || true
    return 0
  fi

  local managed_pid=""
  if [[ -f "$DATA_PID_FILE" ]]; then
    managed_pid="$(cat "$DATA_PID_FILE" 2>/dev/null || true)"
  fi

  if [[ -z "$managed_pid" ]]; then
    echo "停止 data-service（由 TUI 启动）..."
    "$DATA_START_SCRIPT" stop >/dev/null 2>&1 || true
    return 0
  fi

  echo "已安排 ${DATA_STOP_DELAY_SECONDS}s 后停止 data-service（可用 TUI_DATA_STOP_DELAY_SECONDS=0 立即停止）"
  _schedule_delayed_stop "$managed_pid" "$DATA_STOP_DELAY_SECONDS" "$DATA_PID_FILE" "$DATA_START_SCRIPT"
}

_markets_python() {
  local markets_dir="$REPO_ROOT/services-preview/markets-service"
  local py="$markets_dir/.venv/bin/python"
  if [[ -x "$py" ]]; then
    echo "$py"
    return 0
  fi
  return 1
}

_stop_existing_tui_instances() {
  local pattern="$PYTHON -m src"
  local -a pids=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && pids+=("$pid")
  done < <(pgrep -f "$pattern" || true)

  if [[ ${#pids[@]} -eq 0 ]]; then
    return 0
  fi

  echo "检测到已有 TUI 实例: ${pids[*]}，正在停止旧实例..."
  kill "${pids[@]}" >/dev/null 2>&1 || true

  for _ in $(seq 1 20); do
    local alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        alive=1
        break
      fi
    done
    if [[ "$alive" == "0" ]]; then
      break
    fi
    sleep 0.1
  done

  local -a remain=()
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      remain+=("$pid")
    fi
  done

  if [[ ${#remain[@]} -gt 0 ]]; then
    echo "旧实例未完全退出，强制停止: ${remain[*]}"
    kill -9 "${remain[@]}" >/dev/null 2>&1 || true
  fi
}

run() {
  # NOTE: curses TUI must run in the foreground attached to a real TTY.
  if [[ ! -t 0 || ! -t 1 ]]; then
    echo "✗ 当前会话不是交互式 TTY，无法显示 TUI（curses 需要真实终端）"
    echo "  请在本地终端或 ssh -t 会话中运行。"
    echo "  示例: ./scripts/start.sh run --view market_micro --micro-symbol BTC_USDT --micro-interval 5"
    return 2
  fi
  if [[ -z "${TERM:-}" || "${TERM:-}" == "dumb" ]]; then
    echo "⚠️ TERM 未设置或为 dumb，建议先执行: export TERM=xterm-256color"
  fi

  cd "$SERVICE_DIR"

  local singleton="${TUI_SINGLETON:-1}"
  if [[ "$singleton" != "0" && "$singleton" != "1" ]]; then
    singleton="1"
  fi
  if [[ "$singleton" == "1" ]]; then
    _stop_existing_tui_instances
  fi

  _start_data_for_tui
  _start_signal_for_tui

  local hot_reload="${TUI_HOT_RELOAD:-1}"
  if [[ "$hot_reload" != "0" && "$hot_reload" != "1" ]]; then
    hot_reload="1"
  fi

  local poll="${TUI_HOT_RELOAD_POLL:-1.0}"
  if ! [[ "$poll" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    poll="1.0"
  fi

  local -a tui_args=()
  if [[ "$hot_reload" == "1" ]]; then
    tui_args+=(--hot-reload --hot-reload-poll "$poll")
  fi

  set +e
  "$PYTHON" -m src "${tui_args[@]}" "$@"
  local rc=$?
  set -e

  _stop_signal_if_needed
  _stop_data_if_needed
  return "$rc"
}

run_dev() {
  local poll="${TUI_HOT_RELOAD_POLL:-1.0}"
  if ! [[ "$poll" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    poll="1.0"
  fi
  echo "开发模式: 已启用 TUI 热重载（监听 services-preview/tui-service/src/*.py, poll=${poll}s）"
  TUI_HOT_RELOAD=1 TUI_HOT_RELOAD_POLL="$poll" run "$@"
}

run_equity() {
  # Run a markets-service equity-poll in background, then run the TUI in foreground.
  # This keeps TUI stdlib-only while still providing "one-command collection + view".
  local market="${1:-us_stock}"
  local provider="${2:-nasdaq}"
  local symbols="${3:-NVDA}"
  local sleep_s="${4:-60}"
  local limit="${5:-5}"
  local detach="${6:-0}"

  local markets_py
  if ! markets_py="$(_markets_python)"; then
    echo "❌ markets-service 未初始化（缺少 $REPO_ROOT/services-preview/markets-service/.venv/bin/python）"
    echo "   先执行: ./scripts/init.sh --all  或  ./scripts/init.sh markets-service"
    return 1
  fi

  if [[ -z "${MARKETS_SERVICE_DATABASE_URL:-}" && -z "${DATABASE_URL:-}" ]]; then
    echo "⚠️ 未设置 MARKETS_SERVICE_DATABASE_URL/DATABASE_URL，将由 markets-service 使用默认 localhost:5433"
    echo "   建议导出: export MARKETS_SERVICE_DATABASE_URL='postgresql://postgres:postgres@localhost:5434/market_data'"
  fi

  echo "启动采集: market=$market provider=$provider symbols=$symbols sleep=$sleep_s limit=$limit"
  (
    cd "$REPO_ROOT/services-preview/markets-service"
    exec "$markets_py" -m src equity-poll \
      --provider "$provider" \
      --market "$market" \
      --interval "1m" \
      --symbols "$symbols" \
      --sleep "$sleep_s" \
      --limit "$limit"
  ) &
  local pid=$!

  if [[ "$detach" != "1" ]]; then
    trap 'kill "$pid" 2>/dev/null || true' EXIT INT TERM
  else
    echo "采集进程已后台运行 (PID: $pid)；退出 TUI 不会停止采集。"
  fi

  # Show a watchlist for all provided symbols by default.
  run --quote-symbols "$symbols" --quote-market "$market"
}


start() { shift || true; run "$@"; }

stop() {
  echo "tui-service 是交互式 TUI，需要前台运行。停止请使用 Ctrl+C。"
}

status() {
  echo "tui-service 是交互式 TUI，无后台状态。请直接运行: ./scripts/start.sh run（默认单实例 + 热重载）"

  if [[ "$AUTO_START_DATA" == "1" ]]; then
    echo "默认行为：run/start 会自动尝试启动 data-service（可用 TUI_AUTO_START_DATA=0 关闭）"
    echo "默认行为：退出 TUI 后 ${DATA_STOP_DELAY_SECONDS}s 自动停止由 TUI 启动的 data-service"
  else
    echo "当前已关闭自动 data 启动（TUI_AUTO_START_DATA=0）"
  fi

  if [[ "$AUTO_START_SIGNAL" == "1" ]]; then
    echo "默认行为：run/start 会自动尝试启动 signal-service（可用 TUI_AUTO_START_SIGNAL=0 关闭）"
    echo "默认行为：退出 TUI 后 ${SIGNAL_STOP_DELAY_SECONDS}s 自动停止由 TUI 启动的 signal-service"
  else
    echo "当前已关闭自动 signal 启动（TUI_AUTO_START_SIGNAL=0）"
  fi
}

case "${1:-status}" in
  run) shift; run "$@" ;;
  run-dev) shift; run_dev "$@" ;;
  run-equity) shift; run_equity "$@" ;;
  start) start "$@" ;;
  stop) stop ;;
  status) status ;;
  restart) stop; sleep 1; start ;;
  *)
    echo "用法: $0 {run|run-dev|run-equity|start|stop|status|restart}"
    echo ""
    echo "环境变量："
    echo "  TUI_AUTO_START_DATA=0        禁用 run/start 自动启动 data-service"
    echo "  TUI_DATA_STOP_DELAY_SECONDS=3600  退出 TUI 后延迟停止 data-service（默认 1h）"
    echo "  TUI_KEEP_DATA_ON_EXIT=1      退出 TUI 时保留自动启动的 data-service"
    echo "  TUI_AUTO_START_SIGNAL=0      禁用 run/start 自动启动 signal-service"
    echo "  TUI_SIGNAL_STOP_DELAY_SECONDS=3600  退出 TUI 后延迟停止 signal-service（默认 1h）"
    echo "  TUI_KEEP_SIGNAL_ON_EXIT=1    退出 TUI 时保留自动启动的 signal-service"
    echo "  TUI_SINGLETON=0              允许多开 TUI（默认单实例）"
    echo "  TUI_HOT_RELOAD=0             关闭 run/start 默认热重载（默认开启）"
    echo "  TUI_HOT_RELOAD_POLL=1.0      run/run-dev 热重载轮询间隔（秒）"
    echo ""
    echo "示例:"
    echo "  $0 run                  # 默认开启热重载"
    echo "  $0 run-dev"
    echo "  TUI_SINGLETON=0 $0 run  # 允许多开实例"
    echo "  TUI_HOT_RELOAD=0 $0 run # 关闭热重载"
    echo "  TUI_AUTO_START_DATA=0 TUI_AUTO_START_SIGNAL=0 $0 run"
    echo "  TUI_DATA_STOP_DELAY_SECONDS=0 TUI_SIGNAL_STOP_DELAY_SECONDS=0 $0 run"
    echo "  $0 run-equity us_stock nasdaq NVDA 60 5"
    echo "  $0 run-equity hk_stock eastmoney 1810.HK 60 5"
    exit 1
    ;;
esac
