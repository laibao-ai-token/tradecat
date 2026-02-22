#!/usr/bin/env bash
# signal-service 回测脚本（M1 最小闭环）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$(dirname "$PROJECT_DIR")")"
CALLER_INDICATOR_SQLITE_PATH="${INDICATOR_SQLITE_PATH:-}"

resolve_default_backtest_indicator_db() {
    local dir="$REPO_ROOT/artifacts/indicator_db"
    local best=""

    if [[ -d "$dir" ]]; then
        shopt -s nullglob
        for f in "$dir"/bt_indicators_*_1m_30d_*.db; do
            if [[ -z "$best" || "$f" -nt "$best" ]]; then
                best="$f"
            fi
        done
        shopt -u nullglob
    fi

    if [[ -n "$best" ]]; then
        echo "$best"
        return 0
    fi

    echo "$REPO_ROOT/libs/database/services/telegram-service/market_data.db"
}

safe_load_env() {
    local file="$1"
    [ -f "$file" ] || return 0

    # Backtests should be reproducible and not accidentally inherit rule-timeframe
    # overrides from `config/.env` (e.g. SIGNAL_RULE_TIMEFRAMES=1m), otherwise
    # offline_rule_replay may filter out most historical rows and emit 0 signals.
    local -a skip_keys=("SIGNAL_RULE_TIMEFRAMES")
    local -A pre_set=()
    while IFS= read -r name; do
        [[ -z "$name" ]] && continue
        pre_set["$name"]=1
    done < <(compgen -e)

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*export ]] && continue
        [[ "$line" =~ \$\( ]] && continue
        [[ "$line" =~ \` ]] && continue
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[2]}"

            for sk in "${skip_keys[@]}"; do
                if [[ "$key" == "$sk" ]]; then
                    continue 2
                fi
            done

            # Do not override variables already provided by the caller.
            if [[ -n "${pre_set[$key]+x}" ]]; then
                continue
            fi

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

safe_load_env "$REPO_ROOT/config/.env"

# Backtest default: prefer the latest 30d indicator snapshot in artifacts/indicator_db.
# If caller explicitly provides INDICATOR_SQLITE_PATH, keep caller value unchanged.
if [[ -z "$CALLER_INDICATOR_SQLITE_PATH" ]]; then
    export INDICATOR_SQLITE_PATH="$(resolve_default_backtest_indicator_db)"
else
    export INDICATOR_SQLITE_PATH="$CALLER_INDICATOR_SQLITE_PATH"
fi

echo "回测指标库: $INDICATOR_SQLITE_PATH"

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
DEFAULT_CONFIG="src/backtest/strategies/default.crypto.yaml"

args=("$@")
has_config=0
for arg in "${args[@]}"; do
    if [[ "$arg" == "--config" ]]; then
        has_config=1
        break
    fi
done

if [[ $has_config -eq 0 ]]; then
    args=("--config" "$DEFAULT_CONFIG" "${args[@]}")
fi

cd "$PROJECT_DIR"
exec "$PYTHON" -m src.backtest "${args[@]}"
