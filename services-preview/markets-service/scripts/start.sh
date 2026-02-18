#!/usr/bin/env bash
# markets-service 启动脚本
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$SERVICE_DIR")")"
LOG_DIR="$SERVICE_DIR/logs"
PID_DIR="$SERVICE_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

# 安全加载 .env（兼容含空格/括号/行尾注释的模板）
safe_load_env() {
    local file="$1"
    [ -f "$file" ] || return 0

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*export ]] && continue
        [[ "$line" =~ \$\( ]] && continue
        [[ "$line" =~ \` ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[2]}"

            if [[ "$val" =~ ^\".*\"$ ]]; then
                val="${val#\"}" && val="${val%\"}"
            elif [[ "$val" =~ ^\'.*\'$ ]]; then
                val="${val#\'}" && val="${val%\'}"
            else
                val="${val%%#*}"
                val="${val%"${val##*[![:space:]]}"}"
            fi

            export "$key=$val"
        fi
    done < "$file"
}

# 加载配置
safe_load_env "$PROJECT_ROOT/config/.env"

cd "$SERVICE_DIR"

# 激活虚拟环境
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

case "${1:-help}" in
    start)
        echo "启动 markets-service..."
        nohup python -m src collect > "$LOG_DIR/collect.log" 2>&1 &
        echo $! > "$PID_DIR/collect.pid"
        echo "✓ 已启动 (PID: $!)"
        ;;
    start-equity)
        # 依赖环境变量：
        # - EQUITY_PROVIDER (默认 alltick, 全市场共用)
        # - EQUITY_US_PROVIDER / EQUITY_CN_PROVIDER / EQUITY_HK_PROVIDER (可选，按市场覆盖)
        # - EQUITY_INTERVAL (默认 1m)
        # - EQUITY_US_SYMBOLS / EQUITY_CN_SYMBOLS / EQUITY_HK_SYMBOLS (逗号分隔)
        PROVIDER="${EQUITY_PROVIDER:-alltick}"
        US_PROVIDER="${EQUITY_US_PROVIDER:-$PROVIDER}"
        CN_PROVIDER="${EQUITY_CN_PROVIDER:-$PROVIDER}"
        HK_PROVIDER="${EQUITY_HK_PROVIDER:-$PROVIDER}"
        INTERVAL="${EQUITY_INTERVAL:-1m}"
        SLEEP="${EQUITY_SLEEP_SECONDS:-60}"
        LIMIT="${EQUITY_LIMIT:-5}"

        start_one () {
            local market="$1"
            local symbols="$2"
            local name="$3"
            local provider="$4"
            if [ -z "$symbols" ]; then
                return 0
            fi
            echo "启动 equity-poll ($name): provider=$provider market=$market interval=$INTERVAL symbols=$symbols"
            nohup python -m src equity-poll \
                --provider "$provider" \
                --market "$market" \
                --interval "$INTERVAL" \
                --symbols "$symbols" \
                --sleep "$SLEEP" \
                --limit "$LIMIT" \
                > "$LOG_DIR/equity_${name}.log" 2>&1 &
            echo $! > "$PID_DIR/equity_${name}.pid"
            echo "✓ equity-$name 已启动 (PID: $!)"
        }

        start_one "us_stock" "${EQUITY_US_SYMBOLS:-}" "us" "$US_PROVIDER"
        start_one "cn_stock" "${EQUITY_CN_SYMBOLS:-}" "cn" "$CN_PROVIDER"
        start_one "hk_stock" "${EQUITY_HK_SYMBOLS:-}" "hk" "$HK_PROVIDER"
        ;;
    stop)
        if [ -f "$PID_DIR/collect.pid" ]; then
            kill $(cat "$PID_DIR/collect.pid") 2>/dev/null && echo "✓ 已停止"
            rm -f "$PID_DIR/collect.pid"
        fi
        for p in "$PID_DIR"/equity_*.pid; do
            [ -f "$p" ] || continue
            kill $(cat "$p") 2>/dev/null || true
            rm -f "$p"
        done
        ;;
    status)
        if [ -f "$PID_DIR/collect.pid" ] && kill -0 $(cat "$PID_DIR/collect.pid") 2>/dev/null; then
            echo "✓ 运行中 (PID: $(cat "$PID_DIR/collect.pid"))"
        else
            echo "✗ 未运行"
        fi
        for p in "$PID_DIR"/equity_*.pid; do
            [ -f "$p" ] || continue
            if kill -0 $(cat "$p") 2>/dev/null; then
                echo "✓ equity 运行中 ($(basename "$p" .pid), PID: $(cat "$p"))"
            else
                echo "✗ equity 未运行 ($(basename "$p" .pid))"
            fi
        done
        ;;
    test)
        python -m src test --provider "${2:-yfinance}" --symbol "${3:-AAPL}"
        ;;
    *)
        echo "用法: $0 {start|start-equity|stop|status|test [provider] [symbol]}"
        ;;
esac
