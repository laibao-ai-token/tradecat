#!/usr/bin/env bash
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
LOG_DIR="$SERVICE_DIR/logs"
PID_DIR="$SERVICE_DIR/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

export PYTHONPATH="$SERVICE_DIR/src:$ROOT_DIR/libs${PYTHONPATH:+:$PYTHONPATH}"
export DATACAT_OUTPUT_MODE="${DATACAT_OUTPUT_MODE:-db}"

LOG_FILE="$LOG_DIR/metrics-24h.log"
PID_FILE="$PID_DIR/metrics-24h.pid"

PY_BIN="python3"
if [ -x "$SERVICE_DIR/.venv/bin/python" ]; then
  PY_BIN="$SERVICE_DIR/.venv/bin/python"
fi

nohup timeout 86400 bash -lc '
  end=$((SECONDS+86400))
  while [ $SECONDS -lt $end ]; do
    "'"$PY_BIN"'" "'"$SERVICE_DIR"'/src/collectors/binance/um_futures/all/realtime/pull/rest/metrics/http.py" >> "'"$LOG_FILE"'" 2>&1
    sleep 300
  done
' >> "$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
echo "metrics-24h started: PID=$(cat "$PID_FILE")"
