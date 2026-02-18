#!/usr/bin/env bash
# signal-service 启动脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$(dirname "$PROJECT_DIR")")"
PID_FILE="$PROJECT_DIR/logs/signal-service.pid"
LOG_FILE="$PROJECT_DIR/logs/signal-service.log"

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
safe_load_env "$REPO_ROOT/config/.env"

# 确保虚拟环境存在
VENV_DIR="$PROJECT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install -q --upgrade pip
    if [[ -f "$PROJECT_DIR/requirements.txt" ]]; then
        "$VENV_DIR/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
    fi
fi

PYTHON="$VENV_DIR/bin/python"
mkdir -p "$PROJECT_DIR/logs"

run_detached() {
    # Some environments kill background jobs tied to the launching shell/pipeline.
    # setsid detaches the process into its own session to survive that.
    # NOTE: log redirection must happen inside this function, otherwise command substitution would swallow the PID.
    local log_file="$1"
    shift
    if command -v setsid >/dev/null 2>&1; then
        setsid "$@" >> "$log_file" 2>&1 < /dev/null &
    else
        nohup "$@" >> "$log_file" 2>&1 < /dev/null &
    fi
    echo $!
}

start() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "signal-service 已在运行 (PID: $(cat "$PID_FILE"))"
        return 1
    fi
    
    echo "启动 signal-service..."
    cd "$PROJECT_DIR"
    PID=$(run_detached "$LOG_FILE" "$PYTHON" -m src --all)
    echo "$PID" > "$PID_FILE"
    echo "signal-service 已启动 (PID: $PID)"
}

stop() {
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "停止 signal-service (PID: $PID)..."
            kill "$PID"
            rm -f "$PID_FILE"
            echo "signal-service 已停止"
        else
            echo "signal-service 未运行"
            rm -f "$PID_FILE"
        fi
    else
        echo "signal-service 未运行"
    fi
}

status() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "signal-service 运行中 (PID: $(cat "$PID_FILE"))"
        return 0
    else
        echo "signal-service 未运行"
        return 1
    fi
}

case "${1:-status}" in
    start)   start ;;
    stop)    stop ;;
    status)  status ;;
    restart) stop; sleep 1; start ;;
    *)
        echo "用法: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
