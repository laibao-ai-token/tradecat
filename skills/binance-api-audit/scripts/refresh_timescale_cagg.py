#!/usr/bin/env python3
"""
Timescale 连续聚合刷新（默认只读，显式 --apply 才执行）
- 用于修复旧的聚合未刷新问题
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import List

from pathlib import Path
import sys

DATA_SERVICE_SRC = Path("/home/lenovo/.projects/tradecat/services/data-service/src")
if str(DATA_SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(DATA_SERVICE_SRC))

from adapters.timescale import TimescaleAdapter
from config import settings


def _parse_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _normalize_view(schema: str, name: str) -> str:
    name = name.strip()
    if name.startswith(f"{schema}."):
        return name
    if name.startswith("candles_") or name.startswith("metrics_"):
        return f"{schema}.{name}"
    # 允许传 interval，如 1h/4h/1d
    if name in {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}:
        return f"{schema}.candles_{name}"
    return f"{schema}.{name}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Timescale 连续聚合刷新（默认只读）")
    parser.add_argument("--views", required=True, help="视图列表(逗号分隔)，支持 candles_1h 或 1h")
    parser.add_argument("--lookback-days", type=int, default=30, help="回溯天数")
    parser.add_argument("--apply", action="store_true", help="执行刷新（不加则只读）")
    args = parser.parse_args()

    schema = settings.db_schema
    views = [_normalize_view(schema, v) for v in _parse_list(args.views)]
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=args.lookback_days)

    ts = TimescaleAdapter()
    try:
        for view in views:
            if args.apply:
                with ts.connection() as conn:
                    conn.autocommit = True
                    with conn.cursor() as cur:
                        cur.execute("CALL refresh_continuous_aggregate(%s, %s, %s)", (view, start_ts, end_ts))
                print({"view": view, "start": start_ts.isoformat(), "end": end_ts.isoformat(), "mode": "apply"})
            else:
                print({"view": view, "start": start_ts.isoformat(), "end": end_ts.isoformat(), "mode": "dry-run"})
    finally:
        ts.close()


if __name__ == "__main__":
    main()
