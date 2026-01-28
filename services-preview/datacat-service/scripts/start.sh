#!/usr/bin/env bash
# datacat-service 启动脚本（占位）
# 用法: ./scripts/start.sh {start|stop|status|restart}

set -uo pipefail

SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$SERVICE_DIR/pids"
LOG_DIR="$SERVICE_DIR/logs"
PID_FILE="$RUN_DIR/service.pid"
LOG_FILE="$LOG_DIR/service.log"

mkdir -p "$RUN_DIR" "$LOG_DIR"

is_running() {
    local pid=$1
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

case "${1:-status}" in
    start)
        if [ -f "$PID_FILE" ] && is_running "$(cat "$PID_FILE")"; then
            echo "datacat-service 已运行 (PID: $(cat "$PID_FILE"))"
            exit 0
        fi
        cd "$SERVICE_DIR"
        source .venv/bin/activate 2>/dev/null || true
        export PYTHONPATH=src
        nohup python3 -m src >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "datacat-service 已启动 (PID: $(cat "$PID_FILE"))"
        ;;
    stop)
        if [ -f "$PID_FILE" ]; then
            pid=$(cat "$PID_FILE")
            if is_running "$pid"; then
                kill "$pid" 2>/dev/null || true
            fi
            rm -f "$PID_FILE"
            echo "datacat-service 已停止"
        else
            echo "datacat-service 未运行"
        fi
        ;;
    status)
        if [ -f "$PID_FILE" ] && is_running "$(cat "$PID_FILE")"; then
            echo "datacat-service 运行中 (PID: $(cat "$PID_FILE"))"
        else
            echo "datacat-service 未运行"
        fi
        ;;
    restart)
        "$0" stop
        sleep 1
        "$0" start
        ;;
    *)
        echo "用法: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
