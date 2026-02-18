#!/usr/bin/env bash
set -euo pipefail

# Generate a dedicated indicator SQLite DB for backtests (offline_rule_replay),
# and run it in background via nohup. This does NOT touch config/.env.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/run"
LOG_DIR="${ROOT_DIR}/logs"
OUT_DIR="${ROOT_DIR}/artifacts/indicator_db"

PID_FILE="${RUN_DIR}/bt_indicator_db.pid"
META_FILE="${RUN_DIR}/bt_indicator_db.meta"
OUT_DB_FILE="${RUN_DIR}/bt_indicator_db.out_db"
LOG_FILE="${RUN_DIR}/bt_indicator_db.log"

DEFAULT_SYMBOLS=("BTCUSDT" "ETHUSDT")
DEFAULT_INTERVALS=("1m")
DEFAULT_DAYS="30"
DEFAULT_EXCHANGE="binance_futures_um"
DEFAULT_FAST_WORKERS="4"

usage() {
  cat <<'EOF'
Usage:
  scripts/bt_indicator_db.sh start   # start background backfill (writes run/* pointers)
  scripts/bt_indicator_db.sh status  # show current job status + tail log
  scripts/bt_indicator_db.sh stop    # stop current job (SIGTERM)
  scripts/bt_indicator_db.sh paths   # print output db + log paths

Environment overrides (optional):
  BT_SYMBOLS="BTCUSDT ETHUSDT"
  BT_INTERVALS="1m 1h 4h 1d"
  BT_DAYS=30
  BT_EXCHANGE=binance_futures_um
  BT_FAST_WORKERS=4

Notes:
  - Output DB is written under artifacts/indicator_db/ (dedicated backtest DB).
  - This uses services/trading-service/src/scripts/backfill_indicators.py (default enables --fast).
  - --fast uses vectorized computations for common indicators, and bulk-writes tables in one transaction.
EOF
}

is_running() {
  local pid="$1"
  if [[ -z "${pid}" ]]; then
    return 1
  fi
  if kill -0 "${pid}" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

cmd_start() {
  mkdir -p "${RUN_DIR}" "${LOG_DIR}" "${OUT_DIR}"

  local pid=""
  if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  fi
  if [[ -n "${pid}" ]] && is_running "${pid}"; then
    echo "already running: pid=${pid}"
    echo "log=$(cat "${LOG_FILE}" 2>/dev/null || true)"
    echo "db=$(cat "${OUT_DB_FILE}" 2>/dev/null || true)"
    return 0
  fi

  local days="${BT_DAYS:-${DEFAULT_DAYS}}"
  local exchange="${BT_EXCHANGE:-${DEFAULT_EXCHANGE}}"
  local fast_workers="${BT_FAST_WORKERS:-${DEFAULT_FAST_WORKERS}}"
  local symbols_txt="${BT_SYMBOLS:-${DEFAULT_SYMBOLS[*]}}"
  local intervals_txt="${BT_INTERVALS:-${DEFAULT_INTERVALS[*]}}"
  local database_url="${DATABASE_URL:-}"
  if [[ -z "${database_url}" ]] && [[ -f "${ROOT_DIR}/config/.env" ]]; then
    # Read only DATABASE_URL from config/.env (do not source the full file).
    database_url="$(grep -E '^DATABASE_URL=' "${ROOT_DIR}/config/.env" | tail -n 1 | sed -E 's/^DATABASE_URL=//')"
    database_url="$(echo "${database_url}" | sed -E 's/^[\"\\x27]//; s/[\"\\x27]$//')"
  fi

  # Retention rows needed to cover N days per interval (approx, for continuous markets).
  # These values control SQLite cleanup (keep last N rows per (symbol, interval, table)).
  local retention_1m=$(( days * 24 * 60 ))
  local retention_1h=$(( days * 24 ))
  local retention_4h=$(( (days * 24 + 3) / 4 ))
  local retention_1d=$(( days ))
  local retention_overrides="1m=${retention_1m},1h=${retention_1h},4h=${retention_4h},1d=${retention_1d}"
  # Trim retention overrides to the intervals we are actually computing.
  # backfill_indicators.py uses this retention map to size both retention and bar_limit.
  local overrides_filtered=()
  for kv in ${retention_overrides//,/ }; do
    k="${kv%%=*}"
    for iv in ${intervals_txt}; do
      if [[ "${iv}" == "${k}" ]]; then
        overrides_filtered+=("${kv}")
        break
      fi
    done
  done
  retention_overrides="$(IFS=,; echo "${overrides_filtered[*]}")"

  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  local slug_symbols slug_intervals
  slug_symbols="$(echo "${symbols_txt}" | tr ' ,/' '_' | tr -cd 'A-Za-z0-9_')"
  slug_intervals="$(echo "${intervals_txt}" | tr ' ,/' '_' | tr -cd 'A-Za-z0-9_')"

  local out_db="${OUT_DIR}/bt_indicators_${slug_symbols}_${slug_intervals}_${days}d_${ts}.db"
  local log_path="${LOG_DIR}/bt_indicators_${slug_symbols}_${slug_intervals}_${days}d_${ts}.log"

  # Persist pointers for status/stop.
  printf "started_at=%s\nout_db=%s\nlog=%s\ndays=%s\nexchange=%s\nsymbols=%s\nintervals=%s\nretention_overrides=%s\n" \
    "$(date -Is)" \
    "${out_db}" \
    "${log_path}" \
    "${days}" \
    "${exchange}" \
    "${symbols_txt}" \
    "${intervals_txt}" \
    "${retention_overrides}" \
    > "${META_FILE}"
  echo "${out_db}" > "${OUT_DB_FILE}"
  echo "${log_path}" > "${LOG_FILE}"
  : > "${PID_FILE}"

  # Start detached so the job survives non-interactive shells.
  # Write pid from inside the detached process (so status/stop works reliably).
  if command -v setsid >/dev/null 2>&1; then
    export _BT_ROOT_DIR="${ROOT_DIR}"
    export _BT_PID_FILE="${PID_FILE}"
    export _BT_OUT_DB="${out_db}"
    export _BT_EXCHANGE="${exchange}"
    export _BT_FAST_WORKERS="${fast_workers}"
    export _BT_SYMBOLS="${symbols_txt}"
    export _BT_INTERVALS="${intervals_txt}"
    export _BT_RETENTION_OVERRIDES="${retention_overrides}"
    export _BT_DATABASE_URL="${database_url}"

    setsid -f bash -lc '
      cd "$_BT_ROOT_DIR"
      echo $$ > "$_BT_PID_FILE"
      export INDICATOR_SQLITE_PATH="$_BT_OUT_DB"
      export KLINE_DB_EXCHANGE="$_BT_EXCHANGE"
      if [[ -n "${_BT_DATABASE_URL:-}" ]]; then
        export DATABASE_URL="$_BT_DATABASE_URL"
      fi
      export PYTHONUNBUFFERED=1
      export PYTHONWARNINGS=ignore
      exec nice -n 10 services/trading-service/.venv/bin/python -u services/trading-service/src/scripts/backfill_indicators.py \
        --symbols $_BT_SYMBOLS \
        --intervals $_BT_INTERVALS \
        --all-indicators \
        --fast \
        --fast-workers "$_BT_FAST_WORKERS" \
        --retention-overrides "$_BT_RETENTION_OVERRIDES"
    ' > "${log_path}" 2>&1 < /dev/null
  else
    nohup bash -lc "
      cd '${ROOT_DIR}'
      echo \\$\\$ > '${PID_FILE}'
      export INDICATOR_SQLITE_PATH='${out_db}'
      export KLINE_DB_EXCHANGE='${exchange}'
      if [[ -n '${database_url}' ]] && [[ -z \"\\${DATABASE_URL:-}\" ]]; then
        export DATABASE_URL='${database_url}'
      fi
      export PYTHONUNBUFFERED=1
      export PYTHONWARNINGS=ignore
      exec nice -n 10 services/trading-service/.venv/bin/python -u services/trading-service/src/scripts/backfill_indicators.py \
        --symbols ${symbols_txt} \
        --intervals ${intervals_txt} \
        --all-indicators \
        --fast \
        --fast-workers '${fast_workers}' \
        --retention-overrides '${retention_overrides}'
    " > "${log_path}" 2>&1 < /dev/null &
  fi

  # Wait briefly for the pidfile to appear.
  for _ in $(seq 1 20); do
    if [[ -s "${PID_FILE}" ]]; then
      break
    fi
    sleep 0.2
  done

  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"

  echo "started: pid=${pid}"
  echo "db=${out_db}"
  echo "log=${log_path}"
}

cmd_status() {
  if [[ ! -f "${PID_FILE}" ]]; then
    echo "no job (missing ${PID_FILE})"
    return 0
  fi

  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  local log_path out_db
  log_path="$(cat "${LOG_FILE}" 2>/dev/null || true)"
  out_db="$(cat "${OUT_DB_FILE}" 2>/dev/null || true)"

  echo "pid=${pid}"
  echo "db=${out_db}"
  echo "log=${log_path}"

  if [[ -n "${pid}" ]] && is_running "${pid}"; then
    ps -p "${pid}" -o pid,etime,%cpu,%mem,cmd
  else
    echo "status=not_running"
  fi

  if [[ -n "${out_db}" ]] && [[ -f "${out_db}" ]]; then
    ls -lh "${out_db}"
  fi

  if [[ -n "${log_path}" ]] && [[ -f "${log_path}" ]]; then
    echo "--- tail log (last 40 lines) ---"
    tail -n 40 "${log_path}" || true
  fi
}

cmd_stop() {
  if [[ ! -f "${PID_FILE}" ]]; then
    echo "no job (missing ${PID_FILE})"
    return 0
  fi
  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    echo "no pid in ${PID_FILE}"
    return 0
  fi
  if ! is_running "${pid}"; then
    echo "not running: pid=${pid}"
    return 0
  fi
  echo "stopping pid=${pid} ..."
  kill "${pid}" || true
  for _ in $(seq 1 30); do
    if ! is_running "${pid}"; then
      echo "stopped"
      return 0
    fi
    sleep 1
  done
  echo "still running after 30s: pid=${pid} (try: kill -9 ${pid})"
}

cmd_paths() {
  echo "db=$(cat "${OUT_DB_FILE}" 2>/dev/null || true)"
  echo "log=$(cat "${LOG_FILE}" 2>/dev/null || true)"
  echo "meta=${META_FILE}"
}

main() {
  local cmd="${1:-}"
  case "${cmd}" in
    start) cmd_start ;;
    status) cmd_status ;;
    stop) cmd_stop ;;
    paths) cmd_paths ;;
    -h|--help|"") usage ;;
    *) echo "unknown command: ${cmd}"; usage; exit 2 ;;
  esac
}

main "$@"
