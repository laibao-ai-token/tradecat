#!/usr/bin/env bash
# PG 恢复后的真实窗口回测校准脚本。
# 串行执行：DB 检查 -> 覆盖率预检 -> 对齐 gate -> 基础回测 -> Walk-Forward。

set -euo pipefail

ORIGINAL_ARGS=("$@")

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BACKTEST_SH="$REPO_ROOT/scripts/backtest.sh"
DB_URL_HELPER="$REPO_ROOT/scripts/lib/db_url.sh"

if [[ -f "$DB_URL_HELPER" ]]; then
    # shellcheck disable=SC1090
    source "$DB_URL_HELPER"
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}→${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }

usage() {
    cat <<'USAGE'
用法：./scripts/backtest_real_window_validation.sh [options]

说明：
  用于 TimescaleDB 恢复后，串行执行真实窗口回测校准闭环：
  1) 覆盖率预检查
  2) history vs rule 对齐 gate
  3) 基础 history_signal 回测
  4) Walk-Forward 验证

常用参数：
  --start DATETIME              开始时间，默认 2026-01-14 00:00:00
  --end DATETIME                结束时间，默认 2026-02-13 00:00:00
  --symbols CSV                 币种，默认 BTCUSDT,ETHUSDT
  --config PATH                 回测配置，默认 src/backtest/strategies/default.crypto.yaml
  --run-prefix TEXT             产物 run_id 前缀，默认 real-window-<utc timestamp>
  --alignment-min-score N       对齐分下限，默认 70
  --alignment-max-risk-level L  对齐风险等级上限，默认 medium
  --min-signal-days N           历史信号天数门槛，默认 7
  --min-signal-count N          历史信号条数门槛，默认 200
  --min-candle-coverage-pct N   K 线覆盖率门槛，默认 95
  --initial-equity N            本金，默认 3000
  --leverage N                  杠杆，默认 2
  --position-size-pct N         仓位比例，默认 0.2
  --wf-train-days N             Walk-Forward 训练窗天数，默认 7
  --wf-test-days N              Walk-Forward 测试窗天数，默认 3
  --wf-step-days N              Walk-Forward 步进天数，默认 3
  --walk-forward-max-folds N    Walk-Forward 最大折数，默认 6
  --force                       预检失败时继续执行（不建议默认使用）
  --skip-db-check               跳过 TimescaleDB 端口检查
  --dry-run                     只打印将执行的命令，不真正执行
  --auto-apply-issues           校准完成后自动把摘要写回 `#006-01/#006-02/#006-03/#006-04`
  --help                        显示帮助

示例：
  ./scripts/backtest_real_window_validation.sh
  ./scripts/backtest_real_window_validation.sh --dry-run
  ./scripts/backtest_real_window_validation.sh --symbols BTCUSDT,ETHUSDT,SOLUSDT --start "2026-02-01 00:00:00" --end "2026-03-01 00:00:00"
USAGE
}

quote_cmd() {
    printf '%q ' "$@"
    echo
}

resolve_database_url() {
    if declare -f tc_resolve_db_url >/dev/null 2>&1; then
        tc_resolve_db_url "$REPO_ROOT" "postgresql://postgres:postgres@localhost:5434/market_data" "DATABASE_URL"
    else
        echo "postgresql://postgres:postgres@localhost:5434/market_data"
    fi
}

resolve_database_target() {
    local db_url="$1"
    if declare -f tc_db_url_target >/dev/null 2>&1; then
        tc_db_url_target "$db_url"
    else
        echo "localhost:5434/market_data"
    fi
}

check_database_reachable() {
    local db_url="$1"

    if command -v pg_isready >/dev/null 2>&1; then
        pg_isready -d "$db_url" -q
        return $?
    fi

    python3 - "$db_url" <<'PY'
import socket
import sys
from urllib.parse import urlparse

raw = (sys.argv[1] or '').strip()
parsed = urlparse(raw)
host = parsed.hostname or 'localhost'
port = parsed.port or 5432
try:
    with socket.create_connection((host, port), timeout=1.0):
        raise SystemExit(0)
except Exception:
    raise SystemExit(1)
PY
}

run_step() {
    local name="$1"
    shift

    info "$name"
    echo "  $(quote_cmd "$@")"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        return 0
    fi
    "$@"
}

START="2026-01-14 00:00:00"
END="2026-02-13 00:00:00"
SYMBOLS="BTCUSDT,ETHUSDT"
CONFIG="src/backtest/strategies/default.crypto.yaml"
RUN_PREFIX="real-window-$(date -u +%Y%m%d-%H%M%S)"
ALIGNMENT_MIN_SCORE="70"
ALIGNMENT_MAX_RISK_LEVEL="medium"
MIN_SIGNAL_DAYS="7"
MIN_SIGNAL_COUNT="200"
MIN_CANDLE_COVERAGE_PCT="95"
INITIAL_EQUITY="3000"
LEVERAGE="2"
POSITION_SIZE_PCT="0.2"
WF_TRAIN_DAYS="7"
WF_TEST_DAYS="3"
WF_STEP_DAYS="3"
WF_MAX_FOLDS="6"
FORCE="0"
SKIP_DB_CHECK="0"
DRY_RUN="0"
AUTO_APPLY_ISSUES="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start) START="$2"; shift 2 ;;
        --end) END="$2"; shift 2 ;;
        --symbols) SYMBOLS="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        --run-prefix) RUN_PREFIX="$2"; shift 2 ;;
        --alignment-min-score) ALIGNMENT_MIN_SCORE="$2"; shift 2 ;;
        --alignment-max-risk-level) ALIGNMENT_MAX_RISK_LEVEL="$2"; shift 2 ;;
        --min-signal-days) MIN_SIGNAL_DAYS="$2"; shift 2 ;;
        --min-signal-count) MIN_SIGNAL_COUNT="$2"; shift 2 ;;
        --min-candle-coverage-pct) MIN_CANDLE_COVERAGE_PCT="$2"; shift 2 ;;
        --initial-equity) INITIAL_EQUITY="$2"; shift 2 ;;
        --leverage) LEVERAGE="$2"; shift 2 ;;
        --position-size-pct) POSITION_SIZE_PCT="$2"; shift 2 ;;
        --wf-train-days) WF_TRAIN_DAYS="$2"; shift 2 ;;
        --wf-test-days) WF_TEST_DAYS="$2"; shift 2 ;;
        --wf-step-days) WF_STEP_DAYS="$2"; shift 2 ;;
        --walk-forward-max-folds) WF_MAX_FOLDS="$2"; shift 2 ;;
        --force) FORCE="1"; shift ;;
        --skip-db-check) SKIP_DB_CHECK="1"; shift ;;
        --dry-run) DRY_RUN="1"; shift ;;
        --auto-apply-issues) AUTO_APPLY_ISSUES="1"; shift ;;
        --help|-h) usage; exit 0 ;;
        *)
            fail "未知参数: $1"
            echo
            usage
            exit 1
            ;;
    esac
done

if [[ ! -x "$BACKTEST_SH" ]]; then
    fail "回测入口不存在或不可执行: $BACKTEST_SH"
    exit 1
fi

DB_URL="$(resolve_database_url)"
if declare -f tc_db_url_with_local_fallback >/dev/null 2>&1; then
    DB_URL="$(tc_db_url_with_local_fallback "$DB_URL")"
fi
DB_TARGET="$(resolve_database_target "$DB_URL")"

info "真实窗口校准 run_prefix=$RUN_PREFIX"
info "时间窗口: $START -> $END"
info "交易标的: $SYMBOLS"
info "数据库目标: $DB_TARGET"

if [[ "$SKIP_DB_CHECK" -eq 1 || "$DRY_RUN" -eq 1 ]]; then
    warn "跳过数据库连通性检查"
else
    if check_database_reachable "$DB_URL"; then
        success "TimescaleDB 可达: $DB_TARGET"
    else
        fail "TimescaleDB 不可达: $DB_TARGET"
        echo "      可先使用 --dry-run 检查命令，待数据库恢复后再执行。"
        exit 2
    fi
fi

COMMON_ARGS=(
    --config "$CONFIG"
    --start "$START"
    --end "$END"
    --symbols "$SYMBOLS"
    --min-signal-days "$MIN_SIGNAL_DAYS"
    --min-signal-count "$MIN_SIGNAL_COUNT"
    --min-candle-coverage-pct "$MIN_CANDLE_COVERAGE_PCT"
)
CAPITAL_ARGS=(
    --initial-equity "$INITIAL_EQUITY"
    --leverage "$LEVERAGE"
    --position-size-pct "$POSITION_SIZE_PCT"
)
FORCE_ARGS=()
if [[ "$FORCE" -eq 1 ]]; then
    FORCE_ARGS+=(--force)
fi

run_step \
    "Step 1/4: 覆盖率预检查" \
    "$BACKTEST_SH" \
    "${COMMON_ARGS[@]}" \
    --check-only \
    "${FORCE_ARGS[@]}"

run_step \
    "Step 2/4: 历史信号 vs 规则重放对齐 gate" \
    "$BACKTEST_SH" \
    "${COMMON_ARGS[@]}" \
    --mode compare_history_rule \
    --run-id "${RUN_PREFIX}-compare" \
    --alignment-min-score "$ALIGNMENT_MIN_SCORE" \
    --alignment-max-risk-level "$ALIGNMENT_MAX_RISK_LEVEL" \
    "${FORCE_ARGS[@]}"

run_step \
    "Step 3/4: 基础 history_signal 回测" \
    "$BACKTEST_SH" \
    "${COMMON_ARGS[@]}" \
    "${CAPITAL_ARGS[@]}" \
    --mode history_signal \
    --run-id "${RUN_PREFIX}-history" \
    "${FORCE_ARGS[@]}"

run_step \
    "Step 4/4: Walk-Forward 验证" \
    "$BACKTEST_SH" \
    "${COMMON_ARGS[@]}" \
    "${CAPITAL_ARGS[@]}" \
    --mode history_signal \
    --run-id "${RUN_PREFIX}-wf" \
    --walk-forward \
    --wf-train-days "$WF_TRAIN_DAYS" \
    --wf-test-days "$WF_TEST_DAYS" \
    --wf-step-days "$WF_STEP_DAYS" \
    --walk-forward-max-folds "$WF_MAX_FOLDS" \
    "${FORCE_ARGS[@]}"

ISSUE_FILL_CMD=(python3 "$REPO_ROOT/scripts/backtest_issue_fill.py" --run-prefix "$RUN_PREFIX" --db-target "$DB_TARGET" --executed-command "$(quote_cmd ./scripts/backtest_real_window_validation.sh "${ORIGINAL_ARGS[@]}")")
ISSUE_FILL_SUGGEST_ARGS=(--print)
if [[ "$AUTO_APPLY_ISSUES" -eq 1 ]]; then
    ISSUE_FILL_SUGGEST_ARGS=(--apply-issues)
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    success "dry-run 完成：已输出真实窗口校准命令清单"
    info "校准完成后可运行：$(quote_cmd "${ISSUE_FILL_CMD[@]}" "${ISSUE_FILL_SUGGEST_ARGS[@]}")"
else
    success "真实窗口校准流程执行完成"
    info "建议重点检查 artifacts/backtest/latest 下的 metrics.json / report.md / stability_report.json/.md"
    info "compare 模式产物位于本次 session 目录下，run_id 前缀为 ${RUN_PREFIX}-compare"
    info "可用 issue 草稿提取：$(quote_cmd "${ISSUE_FILL_CMD[@]}" --print)"
    if [[ "$AUTO_APPLY_ISSUES" -eq 1 ]]; then
        info "自动写回 issue 模板"
        "${ISSUE_FILL_CMD[@]}" --apply-issues
    fi
fi
