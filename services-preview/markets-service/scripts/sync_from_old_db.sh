#!/usr/bin/env bash
# ============================================================
# 历史数据迁移：从旧库同步到新库（仅迁移场景使用双库 URL）
#
# 用法:
#   MIGRATION_OLD_DATABASE_URL=postgresql://... \
#   MIGRATION_NEW_DATABASE_URL=postgresql://... \
#   ./sync_from_old_db.sh
#
# URL 优先级:
#   旧库: MIGRATION_OLD_DATABASE_URL > LEGACY_DATABASE_URL > 默认(5433)
#   新库: MIGRATION_NEW_DATABASE_URL > MARKETS_SERVICE_DATABASE_URL > DATABASE_URL > 默认(5434)
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$SERVICE_DIR")")"

DB_URL_HELPER="$PROJECT_ROOT/scripts/lib/db_url.sh"
if [[ ! -f "$DB_URL_HELPER" ]]; then
    echo "❌ 缺少数据库 URL 解析工具: $DB_URL_HELPER"
    exit 1
fi
# shellcheck disable=SC1090
source "$DB_URL_HELPER"

DEFAULT_OLD_DB_URL="postgresql://postgres:postgres@localhost:5433/market_data"
DEFAULT_NEW_DB_URL="postgresql://postgres:postgres@localhost:5434/market_data"
BATCH_SIZE="${MIGRATION_BATCH_SIZE:-1000000}"  # 默认每批 100 万条

resolve_db_url() {
    local default_url="$1"
    shift
    if declare -f tc_resolve_db_url >/dev/null 2>&1; then
        tc_resolve_db_url "$PROJECT_ROOT" "$default_url" "$@"
    else
        echo "$default_url"
    fi
}

parse_db_url() {
    local db_url="$1"
    python3 - "$db_url" <<'PY'
import sys
from urllib.parse import unquote, urlparse

url = (sys.argv[1] or "").strip()
p = urlparse(url)
host = p.hostname or "localhost"
try:
    port = p.port or 5432
except ValueError:
    port = 5432
database = (p.path or "/market_data").lstrip("/") or "market_data"
user = unquote(p.username or "postgres")
password = unquote(p.password or "")

for field in (host, str(port), database, user, password):
    print(field)
PY
}

escape_sql_literal() {
    local value="$1"
    printf "%s" "${value//\'/\'\'}"
}

require_integer() {
    local value="$1"
    local label="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "❌ ${label} 不是合法整数: $value"
        exit 1
    fi
}

psql_old() {
    PGPASSWORD="$OLD_PASS" psql -h "$OLD_HOST" -p "$OLD_PORT" -U "$OLD_USER" -d "$OLD_DB" "$@"
}

psql_new() {
    PGPASSWORD="$NEW_PASS" psql -h "$NEW_HOST" -p "$NEW_PORT" -U "$NEW_USER" -d "$NEW_DB" "$@"
}

OLD_DB_URL="$(
    resolve_db_url \
        "$DEFAULT_OLD_DB_URL" \
        "MIGRATION_OLD_DATABASE_URL" \
        "LEGACY_DATABASE_URL"
)"

NEW_DB_URL="$(
    resolve_db_url \
        "$DEFAULT_NEW_DB_URL" \
        "MIGRATION_NEW_DATABASE_URL" \
        "MARKETS_SERVICE_DATABASE_URL" \
        "DATABASE_URL"
)"

mapfile -t OLD_PARTS < <(parse_db_url "$OLD_DB_URL")
mapfile -t NEW_PARTS < <(parse_db_url "$NEW_DB_URL")

OLD_HOST="${OLD_PARTS[0]}"
OLD_PORT="${OLD_PARTS[1]}"
OLD_DB="${OLD_PARTS[2]}"
OLD_USER="${OLD_PARTS[3]}"
OLD_PASS="${OLD_PARTS[4]}"

NEW_HOST="${NEW_PARTS[0]}"
NEW_PORT="${NEW_PARTS[1]}"
NEW_DB="${NEW_PARTS[2]}"
NEW_USER="${NEW_PARTS[3]}"
NEW_PASS="${NEW_PARTS[4]}"

[[ -z "$OLD_USER" ]] && OLD_USER="postgres"
[[ -z "$NEW_USER" ]] && NEW_USER="postgres"
require_integer "$OLD_PORT" "旧库端口"
require_integer "$NEW_PORT" "新库端口"
require_integer "$BATCH_SIZE" "迁移批大小"

OLD_DBLINK_CONN="host=$OLD_HOST port=$OLD_PORT dbname=$OLD_DB user=$OLD_USER"
if [[ -n "$OLD_PASS" ]]; then
    OLD_DBLINK_CONN+=" password=$OLD_PASS"
fi
OLD_DBLINK_CONN_SQL="$(escape_sql_literal "$OLD_DBLINK_CONN")"

OLD_TARGET="$OLD_HOST:$OLD_PORT/$OLD_DB"
NEW_TARGET="$NEW_HOST:$NEW_PORT/$NEW_DB"
if declare -f tc_db_url_target >/dev/null 2>&1; then
    OLD_TARGET="$(tc_db_url_target "$OLD_DB_URL")"
    NEW_TARGET="$(tc_db_url_target "$NEW_DB_URL")"
fi

echo "=============================================="
echo "历史数据同步（迁移模式）"
echo "旧库: $OLD_TARGET"
echo "新库: $NEW_TARGET"
echo "批大小: $BATCH_SIZE"
echo "=============================================="

# 同步 K线数据 (增量)
sync_klines() {
    echo ""
    echo "[K线同步] 检查增量..."
    
    # 获取新库最大时间
    MAX_TIME="$(psql_new -t -A -c "
        SELECT COALESCE(MAX(open_time), '1970-01-01'::timestamptz) FROM raw.kline_1m;
    ")"
    
    echo "[K线同步] 新库最大时间: $MAX_TIME"
    
    # 统计需要同步的数量
    COUNT="$(psql_old -t -A -c "
        SELECT count(*) FROM market_data.candles_1m WHERE bucket_ts > '$MAX_TIME';
    " | tr -d '[:space:]')"
    require_integer "$COUNT" "K线需同步数量"
    
    echo "[K线同步] 需要同步: $COUNT 条"
    
    if (( COUNT == 0 )); then
        echo "[K线同步] 无新数据"
        return
    fi
    
    # 分批同步
    OFFSET=0
    while (( OFFSET < COUNT )); do
        echo "[K线同步] 同步 $OFFSET / $COUNT ..."
        
        psql_new -c "
        INSERT INTO raw.kline_1m (
            exchange, symbol, open_time, close_time,
            open, high, low, close, volume, quote_volume,
            trades, taker_buy_volume, taker_buy_quote_volume,
            is_closed, source, ingest_batch_id, ingested_at, updated_at
        )
        SELECT 
            exchange, symbol, bucket_ts, NULL,
            open, high, low, close, volume, quote_volume,
            trade_count, taker_buy_volume, taker_buy_quote_volume,
            is_closed, source, 0, ingested_at, updated_at
        FROM dblink(
            '$OLD_DBLINK_CONN_SQL',
            'SELECT exchange, symbol, bucket_ts, open, high, low, close, volume, quote_volume,
                    trade_count, taker_buy_volume, taker_buy_quote_volume, is_closed, source, ingested_at, updated_at
             FROM market_data.candles_1m 
             WHERE bucket_ts > ''$MAX_TIME''
             ORDER BY bucket_ts
             LIMIT $BATCH_SIZE OFFSET $OFFSET'
        ) AS t(
            exchange text, symbol text, bucket_ts timestamptz,
            open numeric, high numeric, low numeric, close numeric,
            volume numeric, quote_volume numeric, trade_count bigint,
            taker_buy_volume numeric, taker_buy_quote_volume numeric,
            is_closed boolean, source text, ingested_at timestamptz, updated_at timestamptz
        )
        ON CONFLICT (exchange, symbol, open_time) DO NOTHING;
        " 2>&1 | grep -E "INSERT|ERROR" || true
        
        OFFSET=$((OFFSET + BATCH_SIZE))
    done
    
    echo "[K线同步] 完成"
}

# 同步期货指标 (增量)
sync_metrics() {
    echo ""
    echo "[期货指标同步] 检查增量..."
    
    MAX_TIME="$(psql_new -t -A -c "
        SELECT COALESCE(MAX(timestamp), '1970-01-01'::timestamptz) FROM raw.futures_metrics;
    ")"
    
    echo "[期货指标同步] 新库最大时间: $MAX_TIME"
    
    COUNT="$(psql_old -t -A -c "
        SELECT count(*) FROM market_data.binance_futures_metrics_5m WHERE create_time > '$MAX_TIME';
    " | tr -d '[:space:]')"
    require_integer "$COUNT" "期货指标需同步数量"
    
    echo "[期货指标同步] 需要同步: $COUNT 条"
    
    if (( COUNT == 0 )); then
        echo "[期货指标同步] 无新数据"
        return
    fi
    
    OFFSET=0
    while (( OFFSET < COUNT )); do
        echo "[期货指标同步] 同步 $OFFSET / $COUNT ..."
        
        psql_new -c "
        INSERT INTO raw.futures_metrics (
            exchange, symbol, timestamp,
            \"sumOpenInterest\", \"sumOpenInterestValue\",
            \"topAccountLongShortRatio\", \"topPositionLongShortRatio\",
            \"globalLongShortRatio\", \"takerBuySellRatio\",
            source, is_closed, ingest_batch_id, ingested_at, updated_at
        )
        SELECT 
            exchange, symbol, create_time,
            sum_open_interest, sum_open_interest_value,
            sum_toptrader_long_short_ratio, NULL,
            count_long_short_ratio, sum_taker_long_short_vol_ratio,
            source, is_closed, 0, ingested_at, updated_at
        FROM dblink(
            '$OLD_DBLINK_CONN_SQL',
            'SELECT exchange, symbol, create_time, sum_open_interest, sum_open_interest_value,
                    sum_toptrader_long_short_ratio, count_long_short_ratio, sum_taker_long_short_vol_ratio,
                    source, is_closed, ingested_at, updated_at
             FROM market_data.binance_futures_metrics_5m
             WHERE create_time > ''$MAX_TIME''
             ORDER BY create_time
             LIMIT $BATCH_SIZE OFFSET $OFFSET'
        ) AS t(
            exchange text, symbol text, create_time timestamptz,
            sum_open_interest numeric, sum_open_interest_value numeric,
            sum_toptrader_long_short_ratio numeric, count_long_short_ratio numeric,
            sum_taker_long_short_vol_ratio numeric,
            source text, is_closed boolean, ingested_at timestamptz, updated_at timestamptz
        )
        ON CONFLICT (exchange, symbol, timestamp) DO NOTHING;
        " 2>&1 | grep -E "INSERT|ERROR" || true
        
        OFFSET=$((OFFSET + BATCH_SIZE))
    done
    
    echo "[期货指标同步] 完成"
}

# 执行同步
sync_klines
sync_metrics

echo ""
echo "=============================================="
echo "同步完成!"
echo "=============================================="

# 验证
psql_new -c "
SELECT 'raw.kline_1m' as tbl, count(*) as rows FROM raw.kline_1m
UNION ALL SELECT 'raw.futures_metrics', count(*) FROM raw.futures_metrics;
"
