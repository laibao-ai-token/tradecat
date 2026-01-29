#!/usr/bin/env python3
"""
Timescale K线字段修复（只读默认，显式 --apply 才写入）
- 目标：补齐 quote_volume / trade_count / taker_buy_* 等缺失字段
- 原理：从 Binance 原生 K线拉取完整字段并 upsert 覆盖
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List

DATA_SERVICE_SRC = Path("/home/lenovo/.projects/tradecat/services/data-service/src")
if str(DATA_SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(DATA_SERVICE_SRC))

from adapters.ccxt import fetch_ohlcv, to_rows
from adapters.timescale import TimescaleAdapter
from config import INTERVAL_TO_MS, settings


def _parse_symbols(raw: str) -> List[str]:
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _parse_intervals(raw: str) -> List[str]:
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _iter_fetch(exchange: str, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000) -> Iterable[list]:
    step = INTERVAL_TO_MS.get(interval)
    if not step:
        return []
    cur = start_ms
    while cur < end_ms:
        candles = fetch_ohlcv(exchange, symbol, interval, since_ms=cur, limit=limit) or []
        if not candles:
            break
        yield candles
        last_ts = candles[-1][0]
        if last_ts is None:
            break
        if isinstance(last_ts, str):
            last_ts = int(float(last_ts))
        cur = last_ts + step
        if len(candles) < limit:
            break


def _refresh_cagg(ts: TimescaleAdapter, interval: str, start_ts: datetime, end_ts: datetime) -> None:
    view = f"{ts.schema}.candles_{interval}"
    with ts.connection() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CALL refresh_continuous_aggregate(%s, %s, %s)", (view, start_ts, end_ts))


def repair_symbol_interval(ts: TimescaleAdapter, ccxt_exchange: str, db_exchange: str, symbol: str, interval: str, lookback_days: int, apply: bool) -> dict:
    end_ms = _now_ms()
    start_ms = end_ms - int(timedelta(days=lookback_days).total_seconds() * 1000)
    repair_interval = "1m" if interval != "1m" else interval

    total_rows = 0
    inserted = 0
    for batch in _iter_fetch(ccxt_exchange, symbol, repair_interval, start_ms, end_ms):
        # Binance 原生返回为字符串，先归一化时间戳
        norm_batch = []
        for c in batch:
            if not c:
                continue
            if isinstance(c[0], str):
                cc = list(c)
                cc[0] = int(float(cc[0]))
                norm_batch.append(cc)
            else:
                norm_batch.append(c)
        rows = to_rows(db_exchange, symbol, norm_batch, source="ccxt_repair")
        # 限定时间窗
        rows = [r for r in rows if start_ms <= int(r["bucket_ts"].timestamp() * 1000) <= end_ms]
        if not rows:
            continue
        total_rows += len(rows)
        if apply:
            inserted += ts.upsert_candles(repair_interval, rows)

    if apply and interval != "1m":
        _refresh_cagg(ts, interval, datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
                      datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc))

    return {
        "symbol": symbol,
        "interval": interval,
        "repair_interval": repair_interval,
        "lookback_days": lookback_days,
        "fetched_rows": total_rows,
        "upserted_rows": inserted if apply else 0,
        "mode": "apply" if apply else "dry-run",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Timescale K线字段修复（默认只读）")
    parser.add_argument("--symbols", required=True, help="交易对列表(逗号分隔, 如 BNBUSDT,BTCUSDT)")
    parser.add_argument("--intervals", default="1h", help="周期列表(逗号分隔, 默认 1h)")
    parser.add_argument("--lookback", type=int, default=7, help="回溯天数")
    parser.add_argument("--apply", action="store_true", help="写入 Timescale（不加则只读）")
    args = parser.parse_args()

    ccxt_exchange = settings.ccxt_exchange
    db_exchange = settings.db_exchange
    symbols = _parse_symbols(args.symbols)
    intervals = _parse_intervals(args.intervals)

    ts = TimescaleAdapter()
    try:
        results = []
        for sym in symbols:
            for interval in intervals:
                results.append(repair_symbol_interval(ts, ccxt_exchange, db_exchange, sym, interval, args.lookback, args.apply))
        for r in results:
            print(r)
    finally:
        ts.close()


if __name__ == "__main__":
    main()
