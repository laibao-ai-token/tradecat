"""Shared priority symbol selection used by core and experimental engines."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import config
from ..db.reader import shared_pg_conn

LOG = logging.getLogger("indicator_service.priority")


def _query_kline_priority(top_n: int) -> set[str]:
    symbols: set[str] = set()
    try:
        with shared_pg_conn() as conn:
            table = "market_data.candles_5m"
            try:
                row = conn.execute("SELECT to_regclass(%s) AS reg", ("market_data.candles_5m",)).fetchone()
                reg = None
                if row:
                    reg = row.get("reg") if hasattr(row, "get") else (row[0] if len(row) > 0 else None)
                if not reg:
                    table = "market_data.candles_1m"
            except Exception:
                table = "market_data.candles_1m"

            sql = f"""
                WITH base AS (
                    SELECT symbol,
                           SUM(quote_volume) AS total_qv,
                           AVG((high-low)/NULLIF(close,0)) AS volatility
                    FROM {table}
                    WHERE exchange = %s AND bucket_ts > NOW() - INTERVAL '24 hours'
                    GROUP BY symbol
                ),
                volume_rank AS (
                    SELECT symbol FROM base ORDER BY total_qv DESC LIMIT %s
                ),
                volatility_rank AS (
                    SELECT symbol FROM base ORDER BY volatility DESC LIMIT %s
                ),
                change_rank AS (
                    WITH latest AS (
                        SELECT DISTINCT ON (symbol) symbol, close
                        FROM {table}
                        WHERE exchange = %s AND bucket_ts > NOW() - INTERVAL '1 hour'
                        ORDER BY symbol, bucket_ts DESC
                    ),
                    prev AS (
                        SELECT DISTINCT ON (symbol) symbol, close AS prev_close
                        FROM {table}
                        WHERE exchange = %s
                          AND bucket_ts BETWEEN NOW() - INTERVAL '25 hours' AND NOW() - INTERVAL '23 hours'
                        ORDER BY symbol, bucket_ts DESC
                    )
                    SELECT l.symbol
                    FROM latest l JOIN prev p ON l.symbol = p.symbol
                    ORDER BY ABS((l.close - p.prev_close) / NULLIF(p.prev_close, 0)) DESC
                    LIMIT %s
                )
                SELECT DISTINCT symbol FROM (
                    SELECT symbol FROM volume_rank
                    UNION SELECT symbol FROM volatility_rank
                    UNION SELECT symbol FROM change_rank
                ) combined
            """
            cur = conn.execute(sql, (config.exchange, top_n, top_n, config.exchange, config.exchange, top_n))
            symbols.update(r[0] for r in cur.fetchall())
    except Exception as exc:
        LOG.warning("K线优先级查询失败: %s", exc)

    return symbols


def _get_futures_priority(top_n: int = 15) -> tuple[set[str], dict[str, int]]:
    """Get futures-priority symbols from the latest 5m futures metrics."""

    result: set[str] = set()
    debug: dict[str, int] = {
        "oi_value": 0,
        "oi_change": 0,
        "taker_extreme": 0,
        "ls_extreme": 0,
        "top_ls_change": 0,
        "futures_total": 0,
    }
    try:
        with shared_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (symbol)
                        symbol,
                        sum_open_interest_value AS oi_val,
                        sum_taker_long_short_vol_ratio AS taker_ratio,
                        count_long_short_ratio AS ls_ratio
                    FROM market_data.binance_futures_metrics_5m
                    WHERE create_time > NOW() - INTERVAL '7 days'
                    ORDER BY symbol, create_time DESC
                    """
                )
                rows = cur.fetchall()

                oi_value_rank: list[tuple[str, float]] = []
                taker_extreme: set[str] = set()
                ls_extreme: set[str] = set()

                for sym, oi_val, taker, ls in rows:
                    if oi_val:
                        oi_value_rank.append((sym, float(oi_val)))
                    if taker:
                        taker_value = float(taker)
                        if taker_value < 0.2 or taker_value > 5.0:
                            taker_extreme.add(sym)
                    if ls:
                        ls_value = float(ls)
                        if ls_value < 0.5 or ls_value > 4.0:
                            ls_extreme.add(sym)

                top_oi_value = {sym for sym, _ in sorted(oi_value_rank, key=lambda item: item[1], reverse=True)[:top_n]}
                result = top_oi_value | taker_extreme | ls_extreme
                debug = {
                    "oi_value": len(top_oi_value),
                    "oi_change": 0,
                    "taker_extreme": len(taker_extreme),
                    "ls_extreme": len(ls_extreme),
                    "top_ls_change": 0,
                    "futures_total": len(result),
                }
    except Exception as exc:
        LOG.warning("获取期货优先级失败: %s", exc)

    return result, debug


def get_high_priority_symbols_fast(top_n: int = 30) -> set[str]:
    """Compute high-priority symbols with kline/futures dimensions in parallel."""

    result: set[str] = set()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_query_kline_priority, top_n),
            executor.submit(lambda: _get_futures_priority(top_n)[0]),
        ]
        for future in as_completed(futures):
            try:
                result.update(future.result())
            except Exception as exc:
                LOG.warning("优先级查询失败: %s", exc)

    LOG.info("高优先级币种: %s 个", len(result))
    return result

