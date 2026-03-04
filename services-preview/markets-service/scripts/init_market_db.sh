#!/bin/bash
# ============================================================
# TradeCat 全市场数据库初始化脚本
# 用法: ./init_market_db.sh [host] [port] [database]
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$(dirname "$SERVICE_DIR")")"
source "$PROJECT_ROOT/scripts/lib/db_url.sh"

DB_URL="$(
    tc_resolve_db_url \
        "$PROJECT_ROOT" \
        "postgresql://postgres:postgres@localhost:5434/market_data" \
        "MARKETS_SERVICE_DATABASE_URL" \
        "DATABASE_URL"
)"

read -r DEFAULT_HOST DEFAULT_PORT DEFAULT_DATABASE <<< "$(python3 - "$DB_URL" <<'PY'
import sys
from urllib.parse import urlparse

url = sys.argv[1]
p = urlparse(url)
host = p.hostname or "localhost"
port = p.port or 5432
database = (p.path or "/market_data").lstrip("/") or "market_data"
sys.stdout.write(f"{host} {port} {database}")
PY
)"

HOST=${1:-$DEFAULT_HOST}
PORT=${2:-$DEFAULT_PORT}
DATABASE=${3:-$DEFAULT_DATABASE}
USER=${PGUSER:-postgres}
PASSWORD=${PGPASSWORD:-postgres}

DDL_DIR="$SCRIPT_DIR/ddl"

echo "=============================================="
echo "TradeCat 数据库初始化"
echo "=============================================="
echo "Host: $HOST:$PORT"
echo "Database: $DATABASE"
echo "DDL Dir: $DDL_DIR"
echo "=============================================="

# 检查连接
echo "[1/10] 检查数据库连接..."
PGPASSWORD=$PASSWORD psql -h $HOST -p $PORT -U $USER -d $DATABASE -c "SELECT 1" > /dev/null 2>&1 || {
    echo "❌ 无法连接数据库"
    exit 1
}
echo "✅ 数据库连接正常"

# 执行 DDL
for i in 01 02 03 04 05 06 07 08 09; do
    SQL_FILE=$(ls $DDL_DIR/${i}_*.sql 2>/dev/null | head -1)
    if [ -f "$SQL_FILE" ]; then
        echo "[执行] $(basename $SQL_FILE)..."
        PGPASSWORD=$PASSWORD psql -h $HOST -p $PORT -U $USER -d $DATABASE -f "$SQL_FILE" > /dev/null 2>&1 || {
            echo "❌ 执行失败: $SQL_FILE"
            PGPASSWORD=$PASSWORD psql -h $HOST -p $PORT -U $USER -d $DATABASE -f "$SQL_FILE" 2>&1 | tail -20
            exit 1
        }
        echo "✅ 完成"
    fi
done

echo ""
echo "=============================================="
echo "✅ 数据库初始化完成!"
echo "=============================================="

# 验证
echo ""
echo "表统计:"
PGPASSWORD=$PASSWORD psql -h $HOST -p $PORT -U $USER -d $DATABASE -c "
SELECT schemaname as schema, count(*) as tables
FROM pg_tables 
WHERE schemaname IN ('reference', 'raw', 'fundamental', 'alternative', 'agg', 'indicators', 'quality')
GROUP BY schemaname ORDER BY schemaname;
"

echo "连续聚合物化视图:"
PGPASSWORD=$PASSWORD psql -h $HOST -p $PORT -U $USER -d $DATABASE -c "
SELECT view_name FROM timescaledb_information.continuous_aggregates ORDER BY view_name;
"
