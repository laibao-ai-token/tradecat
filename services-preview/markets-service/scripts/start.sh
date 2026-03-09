#!/usr/bin/env bash
# markets-service 启动脚本
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$SERVICE_DIR")")"
LOG_DIR="$SERVICE_DIR/logs"
PID_DIR="$SERVICE_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

source "$PROJECT_ROOT/scripts/lib/db_url.sh"
RESOLVED_MARKETS_DB_URL="$(
    tc_db_url_with_local_fallback "$(
        tc_resolve_db_url \
            "$PROJECT_ROOT" \
            "postgresql://postgres:postgres@localhost:5434/market_data" \
            "MARKETS_SERVICE_DATABASE_URL" \
            "DATABASE_URL"
    )"
)"
RESOLVED_MARKETS_DB_TARGET="$(tc_db_url_target "$RESOLVED_MARKETS_DB_URL")"

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
                # 仅处理“空白后 #”的行尾注释，避免截断真实值中的 '#'
                # 例如: PASSWORD=abc#123 不应被截断；PASSWORD=abc # note 应去掉注释
                if [[ "$val" =~ ^(.*[^[:space:]])[[:space:]]+#.*$ ]]; then
                    val="${BASH_REMATCH[1]}"
                fi
                val="${val%"${val##*[![:space:]]}"}"
            fi

            export "$key=$val"
        fi
    done < "$file"
}

# 加载配置
safe_load_env "$PROJECT_ROOT/config/.env"

# markets-service 优先使用解析后的数据库地址；本地配置若误指向 5432，会自动回退到可达端口。
export MARKETS_SERVICE_DATABASE_URL="$RESOLVED_MARKETS_DB_URL"
export DATABASE_URL="$RESOLVED_MARKETS_DB_URL"

cd "$SERVICE_DIR"

# 激活虚拟环境
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

case "${1:-help}" in
    start)
        echo "启动 markets-service... (db: $RESOLVED_MARKETS_DB_TARGET)"
        nohup python -m src collect > "$LOG_DIR/collect.log" 2>&1 &
        echo $! > "$PID_DIR/collect.pid"
        echo "✓ 已启动 (PID: $!)"
        ;;
    start-news)
        NEWS_PROVIDER="${NEWS_PROVIDER:-rss}"
        NEWS_SLEEP="${NEWS_RSS_POLL_INTERVAL_SECONDS:-2}"
        NEWS_LIMIT="${NEWS_RSS_LIMIT:-100}"
        NEWS_WINDOW_HOURS="${NEWS_RSS_WINDOW_HOURS:-72}"
        NEWS_TIMEOUT="${NEWS_RSS_TIMEOUT_SECONDS:-20}"

        if [ -f "$PID_DIR/news.pid" ] && kill -0 $(cat "$PID_DIR/news.pid") 2>/dev/null; then
            echo "✓ news collector 已运行 (PID: $(cat "$PID_DIR/news.pid"))"
            exit 0
        fi
        rm -f "$PID_DIR/news.pid"

        echo "启动 markets-service news collector... (db: $RESOLVED_MARKETS_DB_TARGET)"
        if command -v setsid >/dev/null 2>&1; then
            setsid nohup python -u -m src collect-news-poll \
                --provider "$NEWS_PROVIDER" \
                --sleep "$NEWS_SLEEP" \
                --news-limit "$NEWS_LIMIT" \
                --window-hours "$NEWS_WINDOW_HOURS" \
                --timeout "$NEWS_TIMEOUT" \
                > "$LOG_DIR/news_collect.log" 2>&1 < /dev/null &
        else
            nohup python -u -m src collect-news-poll \
                --provider "$NEWS_PROVIDER" \
                --sleep "$NEWS_SLEEP" \
                --news-limit "$NEWS_LIMIT" \
                --window-hours "$NEWS_WINDOW_HOURS" \
                --timeout "$NEWS_TIMEOUT" \
                > "$LOG_DIR/news_collect.log" 2>&1 < /dev/null &
        fi
        echo $! > "$PID_DIR/news.pid"
        echo "✓ news collector 已启动 (PID: $!)"
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
            echo "启动 equity-poll ($name): provider=$provider market=$market interval=$INTERVAL symbols=$symbols db=$RESOLVED_MARKETS_DB_TARGET"
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
        if [ -f "$PID_DIR/news.pid" ]; then
            kill $(cat "$PID_DIR/news.pid") 2>/dev/null || true
            rm -f "$PID_DIR/news.pid"
        fi
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
        echo "db: $RESOLVED_MARKETS_DB_TARGET"
        if [ -f "$PID_DIR/news.pid" ] && kill -0 $(cat "$PID_DIR/news.pid") 2>/dev/null; then
            echo "✓ news 运行中 (PID: $(cat "$PID_DIR/news.pid"))"
        else
            echo "✗ news 未运行"
        fi
        ;;
    test)
        python -m src test --provider "${2:-yfinance}" --symbol "${3:-AAPL}"
        ;;
    *)
        echo "用法: $0 {start|start-news|start-equity|stop|status|test [provider] [symbol]}"
        ;;
esac
