"""基础数据路由（直连 SQLite 指标库）"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from src.config import get_settings
from src.utils.errors import ErrorCode, api_response, error_response
from src.utils.symbol import normalize_symbol

router = APIRouter(tags=["futures"])

BASE_TABLE = "基础数据同步器.py"


def _parse_ts(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except Exception:
        return None


@router.get("/base-data")
async def get_base_data(
    symbol: str = Query(..., description="交易对 (BTC 或 BTCUSDT)"),
    interval: str = Query(default="1h", description="周期 (如 1h/4h/1d)"),
    limit: int = Query(default=200, ge=1, le=5000, description="返回数量"),
    auto_resolve: bool = Query(default=True, description="自动解析交易对/周期"),
) -> dict:
    """读取 SQLite 基础数据（成交额/主动买卖比等）"""
    settings = get_settings()
    db_path = settings.SQLITE_INDICATORS_PATH

    if not db_path.exists():
        return error_response(ErrorCode.SERVICE_UNAVAILABLE, "SQLite 数据库不可用")

    input_symbol = symbol.strip()
    input_interval = interval.strip()

    def _fetch_rows():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (BASE_TABLE,),
            )
            if not cur.fetchone():
                raise RuntimeError("基础数据表不存在")

            resolved_symbol = normalize_symbol(input_symbol)
            resolved_interval = input_interval

            def _query(sym: str | None, itv: str | None):
                where = []
                params: list = []
                if sym:
                    where.append("交易对 = ?")
                    params.append(sym)
                if itv:
                    where.append("周期 = ?")
                    params.append(itv)
                where_sql = f"WHERE {' AND '.join(where)}" if where else ""
                cur.execute(
                    f'SELECT 交易对, 周期, 数据时间, 开盘价, 最高价, 最低价, 收盘价, 当前价格, '
                    f'成交量, 成交额, 振幅, 变化率, 交易次数, 成交笔数, 主动买入量, 主动买量, '
                    f'主动买额, 主动卖出量, 主动买卖比, 主动卖出额, 资金流向, 平均每笔成交额 '
                    f'FROM "{BASE_TABLE}" {where_sql} ORDER BY 数据时间 DESC LIMIT ?',
                    params + [limit],
                )
                return cur.fetchall()

            rows = _query(resolved_symbol, resolved_interval)

            if not rows and auto_resolve:
                # ==================== 自动解析交易对与周期 ====================
                base = resolved_symbol.replace("USDT", "")
                cur.execute(
                    f'SELECT 交易对, COUNT(*) AS c FROM "{BASE_TABLE}" WHERE 交易对 LIKE ? '
                    f'GROUP BY 交易对 ORDER BY c DESC LIMIT 1',
                    (f"{base}%",),
                )
                row = cur.fetchone()
                if row:
                    resolved_symbol = row[0]
                if resolved_symbol:
                    cur.execute(
                        f'SELECT 周期, COUNT(*) AS c FROM "{BASE_TABLE}" WHERE 交易对 = ? '
                        f'GROUP BY 周期 ORDER BY c DESC LIMIT 1',
                        (resolved_symbol,),
                    )
                    row = cur.fetchone()
                    if row:
                        resolved_interval = row[0]
                rows = _query(resolved_symbol, resolved_interval)

            data = []
            for row in reversed(rows):
                data.append(
                    {
                        "交易对": row["交易对"],
                        "周期": row["周期"],
                        "数据时间": row["数据时间"],
                        "timestamp_ms": _parse_ts(row["数据时间"]),
                        "开盘价": row["开盘价"],
                        "最高价": row["最高价"],
                        "最低价": row["最低价"],
                        "收盘价": row["收盘价"],
                        "当前价格": row["当前价格"],
                        "成交量": row["成交量"],
                        "成交额": row["成交额"],
                        "振幅": row["振幅"],
                        "变化率": row["变化率"],
                        "交易次数": row["交易次数"],
                        "成交笔数": row["成交笔数"],
                        "主动买入量": row["主动买入量"],
                        "主动买量": row["主动买量"],
                        "主动买额": row["主动买额"],
                        "主动卖出量": row["主动卖出量"],
                        "主动买卖比": row["主动买卖比"],
                        "主动卖出额": row["主动卖出额"],
                        "资金流向": row["资金流向"],
                        "平均每笔成交额": row["平均每笔成交额"],
                    }
                )

            return {
                "filters": {"symbol": input_symbol, "interval": input_interval},
                "resolved_filters": {"symbol": resolved_symbol, "interval": resolved_interval},
                "list": data,
            }

    try:
        payload = await run_in_threadpool(_fetch_rows)
        return api_response(payload)
    except Exception as e:
        return error_response(ErrorCode.INTERNAL_ERROR, f"查询失败: {e}")
