#!/usr/bin/env bash
# trading-service 启动/守护脚本
# 用法: ./scripts/start.sh {start|stop|status|restart|daemon}

set -uo pipefail

# ==================== 配置区 ====================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$SERVICE_DIR")")"
RUN_DIR="$SERVICE_DIR/pids"
LOG_DIR="$SERVICE_DIR/logs"
DAEMON_PID="$RUN_DIR/daemon.pid"
DAEMON_LOG="$LOG_DIR/daemon.log"
SERVICE_PID="$RUN_DIR/service.pid"
SERVICE_LOG="$LOG_DIR/service.log"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"
STOP_TIMEOUT=10

# 安全加载 .env（只读键值解析，拒绝危险行）
safe_load_env() {
    local file="$1"
    [ -f "$file" ] || return 0
    
    # 检查权限（生产环境强制 600）
    if [[ "$file" == *"config/.env" ]] && [[ ! "$file" == *".example" ]]; then
        local perm=$(stat -c %a "$file" 2>/dev/null)
        if [[ "$perm" != "600" && "$perm" != "400" ]]; then
            if [[ "${CODESPACES:-}" == "true" ]]; then
                echo "⚠️  Codespace 环境，跳过权限检查 ($file: $perm)"
            else
                echo "❌ 错误: $file 权限为 $perm，必须设为 600"
                echo "   执行: chmod 600 $file"
                exit 1
            fi
        fi
    fi
    
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
                # 仅当 # 前有空白时才视为注释，避免截断 URL/密码中的 #
                if [[ "$val" =~ ^(.*[^[:space:]])[[:space:]]+#.*$ ]]; then
                    val="${BASH_REMATCH[1]}"
                elif [[ "$val" =~ ^[[:space:]]*#.*$ ]]; then
                    val=""
                fi
                val="${val#"${val%%[![:space:]]*}"}"
                val="${val%"${val##*[![:space:]]}"}"
            fi
            export "$key=$val"
        fi
    done < "$file"
}

# 加载全局配置 → 服务配置
safe_load_env "$PROJECT_ROOT/config/.env"
# 配置已统一到 config/.env

# ==================== 计算后端强制配置 ====================
# 避免线程后端被 GIL 限制，默认走进程后端；如需覆盖，设置 FORCE_* 环境变量。
cpu_count="$(command -v nproc >/dev/null 2>&1 && nproc || getconf _NPROCESSORS_ONLN || echo 4)"
export COMPUTE_BACKEND="${FORCE_COMPUTE_BACKEND:-process}"
export MAX_CPU_WORKERS="${FORCE_MAX_CPU_WORKERS:-$cpu_count}"
export MAX_WORKERS="${FORCE_MAX_WORKERS:-$cpu_count}"

# 校验 SYMBOLS_* 格式
validate_symbols() {
    local errors=0
    for var in $(env | grep -E '^SYMBOLS_(GROUP_|EXTRA|EXCLUDE)' | cut -d= -f1); do
        local val="${!var}"
        [ -z "$val" ] && continue
        for sym in ${val//,/ }; do
            sym="${sym^^}"
            if [[ ! "$sym" =~ ^[A-Z0-9]+USDT$ ]]; then
                echo "❌ 无效币种 $var: $sym"
                errors=1
            fi
        done
    done
    [ $errors -eq 1 ] && exit 1
}
validate_symbols

# 代理自检（重试3次+指数退避冷却）
check_proxy() {
    local proxy="${HTTP_PROXY:-${HTTPS_PROXY:-}}"
    [ -z "$proxy" ] && return 0
    
    local retries=3
    local delay=1
    local i=0
    
    while [ $i -lt $retries ]; do
        if curl -s --max-time 3 --proxy "$proxy" https://api.binance.com/api/v3/ping >/dev/null 2>&1; then
            echo "✓ 代理可用: $proxy"
            return 0
        fi
        ((i++))
        if [ $i -lt $retries ]; then
            echo "  代理检测失败，${delay}秒后重试 ($i/$retries)..."
            sleep $delay
            delay=$((delay * 2))
        fi
    done
    
    echo "⚠️  代理不可用（重试${retries}次失败），已禁用: $proxy"
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
}

# 启动模式：
# - simple: 旧版调度器（依赖 candles_5m 等表；在只有 candles_1m 的环境下会报错）
# - listener: K线监听器（实验）
# - engine: 指标计算引擎（推荐，兼容仅有 candles_1m 的环境；缺失周期会从 1m 重采样）
MODE="${MODE:-engine}"

# ==================== 工具函数 ====================
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DAEMON_LOG"
}

init_dirs() {
    mkdir -p "$RUN_DIR" "$LOG_DIR"
}

is_running() {
    local pid=$1
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

get_service_pid() {
    [ -f "$SERVICE_PID" ] && cat "$SERVICE_PID"
}

run_detached() {
    # Detach from the launching shell/pipeline so the service survives session cleanup.
    # Prefer setsid when available; fall back to nohup.
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

# ==================== 服务管理 ====================
start_service() {
    init_dirs
    local pid=$(get_service_pid)
    if is_running "$pid"; then
        echo "✓ 服务已运行 (PID: $pid)"
        return 0
    fi
    
    cd "$SERVICE_DIR"
    # Use venv python explicitly (avoid PATH/hash issues in non-interactive shells).
    local vpy="$SERVICE_DIR/.venv/bin/python"
    if [[ ! -x "$vpy" ]]; then
        echo "❌ 未找到虚拟环境: $vpy"
        echo "   先执行: ./scripts/init.sh trading-service"
        return 1
    fi
    export PYTHONPATH="$SERVICE_DIR"

    if [[ "${MODE:-engine}" == "listener" ]]; then
        new_pid=$(run_detached "$SERVICE_LOG" "$vpy" -u -m src.kline_listener)
    elif [[ "${MODE:-engine}" == "simple" ]]; then
        new_pid=$(run_detached "$SERVICE_LOG" "$vpy" -u -m src.simple_scheduler)
    else
        # engine loop: run --once periodically; avoids relying on candles_5m table.
        local interval_s="${ENGINE_LOOP_INTERVAL_SECONDS:-60}"
        new_pid=$(run_detached "$SERVICE_LOG" bash -c "
while true; do
  \"$vpy\" -u -m src --once --mode \"${ENGINE_MODE:-all}\" || true
  sleep \"$interval_s\" || true
done
")
    fi
    echo "$new_pid" > "$SERVICE_PID"
    
    sleep 1
    if is_running "$new_pid"; then
        log "START 服务 (PID: $new_pid, MODE: $MODE)"
        echo "✓ 服务已启动 (PID: $new_pid, MODE: $MODE)"
        return 0
    else
        log "ERROR 服务启动失败"
        echo "✗ 服务启动失败"
        return 1
    fi
}

stop_service() {
    local pid=$(get_service_pid)
    if ! is_running "$pid"; then
        echo "服务未运行"
        rm -f "$SERVICE_PID"
        return 0
    fi
    
    kill "$pid" 2>/dev/null
    local waited=0
    while is_running "$pid" && [ $waited -lt $STOP_TIMEOUT ]; do
        sleep 1
        ((waited++))
    done
    
    if is_running "$pid"; then
        kill -9 "$pid" 2>/dev/null
        log "KILL 服务 (PID: $pid) 强制终止"
    else
        log "STOP 服务 (PID: $pid)"
    fi
    
    rm -f "$SERVICE_PID"
    echo "✓ 服务已停止"
}

status_service() {
    local pid=$(get_service_pid)
    if is_running "$pid"; then
        local uptime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
        echo "✓ 服务运行中 (PID: $pid, 运行: $uptime)"
        echo ""
        echo "=== 最近日志 ==="
        tail -10 "$SERVICE_LOG" 2>/dev/null
        return 0
    else
        echo "✗ 服务未运行"
        return 1
    fi
}

# ==================== 入口 ====================
case "${1:-status}" in
    start)   check_proxy; start_service ;;
    stop)    stop_service ;;
    status)  status_service ;;
    restart) check_proxy; stop_service; sleep 2; start_service ;;
    *)
        echo "用法: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
