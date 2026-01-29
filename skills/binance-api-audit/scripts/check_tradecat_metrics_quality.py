#!/usr/bin/env python3
"""
Tradecat 指标数据质量检查（只读）
- 默认读取 Telegram 服务 SQLite 基础数据表（直接字段，不做推算）
- 可选通过 API 拉取 futures/metrics
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen, Request


INTERVAL_SECONDS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
    "1M": 2592000,
}

INTERVAL_TABLE = {
    "5m": "market_data.binance_futures_metrics_5m",
    "15m": "market_data.binance_futures_metrics_15m_last",
    "1h": "market_data.binance_futures_metrics_1h_last",
    "4h": "market_data.binance_futures_metrics_4h_last",
    "1d": "market_data.binance_futures_metrics_1d_last",
    "1w": "market_data.binance_futures_metrics_1w_last",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def auto_start_tradecat(project_root: str) -> None:
    # ==================== 自动启动 Tradecat API ====================
    subprocess.run(
        ["bash", "-lc", f"cd {project_root}/tradecat/services-preview/api-service && ./scripts/start.sh start"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    req = Request(url, headers={"User-Agent": "binance-api-audit"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _sqlite_quality(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.sqlite_path)
    if not db_path.exists():
        raise SystemExit(f"SQLite 不存在: {db_path}")

    table = args.sqlite_table
    where = []
    params: list[Any] = []
    input_symbol = args.symbol
    input_interval = args.interval
    resolved_symbol = input_symbol
    resolved_interval = input_interval

    def _build_where(sym: str | None, interval: str | None) -> tuple[str, list[Any]]:
        clause = []
        p: list[Any] = []
        if sym:
            clause.append("交易对 = ?")
            p.append(sym)
        if interval:
            clause.append("周期 = ?")
            p.append(interval)
        return (f"WHERE {' AND '.join(clause)}" if clause else "", p)

    where_sql, params = _build_where(resolved_symbol, resolved_interval)

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()

    def _count(expr: str) -> int:
        cur.execute(f'SELECT COUNT(*) FROM "{table}" {where_sql} AND {expr}' if where_sql else f'SELECT COUNT(*) FROM "{table}" WHERE {expr}', params)
        return int(cur.fetchone()[0])

    cur.execute(f'SELECT COUNT(*) FROM "{table}" {where_sql}', params)
    total = int(cur.fetchone()[0])

    if total == 0 and args.auto_resolve and input_symbol:
        # ==================== 自动解析交易对与周期 ====================
        pattern = f"{input_symbol.upper()}%"
        cur.execute(
            f'SELECT 交易对, COUNT(*) AS c FROM "{table}" WHERE 交易对 LIKE ? GROUP BY 交易对 ORDER BY c DESC LIMIT 1',
            (pattern,),
        )
        row = cur.fetchone()
        if row:
            resolved_symbol = row[0]
        if resolved_symbol:
            cur.execute(
                f'SELECT 周期, COUNT(*) AS c FROM "{table}" WHERE 交易对 = ? GROUP BY 周期 ORDER BY c DESC LIMIT 1',
                (resolved_symbol,),
            )
            row = cur.fetchone()
            if row:
                resolved_interval = row[0]
        where_sql, params = _build_where(resolved_symbol, resolved_interval)
        cur.execute(f'SELECT COUNT(*) FROM "{table}" {where_sql}', params)
        total = int(cur.fetchone()[0])

    fields = ["成交额", "主动买卖比", "主动买额", "主动卖出额"]
    counts = {}
    for field in fields:
        counts[f"{field}_null"] = _count(f'"{field}" IS NULL')
        counts[f"{field}_zero"] = _count(f'"{field}" = 0')

    missing_count = counts.get("主动买卖比_null", 0) + counts.get("主动买卖比_zero", 0)
    missing_ratio = round(missing_count / total, 4) if total else None

    cur.execute(
        f'SELECT 交易对, 周期, 数据时间, 成交额, 主动买卖比, 主动买额, 主动卖出额 '
        f'FROM "{table}" {where_sql} ORDER BY 数据时间 DESC LIMIT 1',
        params,
    )
    latest = cur.fetchone()
    con.close()

    return {
        "ts": _now_iso(),
        "source": {
            "mode": "sqlite",
            "db_path": str(db_path),
            "table": table,
        },
        "filters": {
            "symbol": input_symbol,
            "interval": input_interval,
        },
        "resolved_filters": {
            "symbol": resolved_symbol,
            "interval": resolved_interval,
        },
        "records": total,
        "missing_count": missing_count,
        "missing_ratio": missing_ratio,
        "ratio_missing": missing_ratio,
        "field_stats": counts,
        "latest_row": {
            "交易对": latest[0],
            "周期": latest[1],
            "数据时间": latest[2],
            "成交额": latest[3],
            "主动买卖比": latest[4],
            "主动买额": latest[5],
            "主动卖出额": latest[6],
        } if latest else None,
        "gap_anomalies": None,
        "max_gap_seconds": None,
    }


def _api_quality(args: argparse.Namespace) -> dict[str, Any]:
    interval = args.interval
    if interval not in INTERVAL_SECONDS:
        raise SystemExit(f"不支持的 interval: {interval}")

    if args.auto_start:
        auto_start_tradecat(args.project_root)

    query = urlencode({"symbol": args.symbol, "interval": interval, "limit": args.limit})
    url = f"{args.base_url}/api/futures/metrics?{query}"
    payload = fetch_json(url, timeout=args.timeout)

    data = payload.get("data") or []
    expected_gap = INTERVAL_SECONDS[interval]

    null_open_interest = 0
    zero_open_interest = 0
    null_long_short = 0
    zero_long_short = 0
    null_taker_long_short = 0
    zero_taker_long_short = 0
    missing_count = 0
    gaps = 0
    max_gap = 0

    last_ts: datetime | None = None
    for row in data:
        open_interest = row.get("openInterest")
        long_short = row.get("longShortRatio")
        taker_long_short = row.get("takerLongShortRatio")

        if open_interest is None:
            null_open_interest += 1
        elif float(open_interest) == 0:
            zero_open_interest += 1

        if long_short is None:
            null_long_short += 1
        elif float(long_short) == 0:
            zero_long_short += 1

        if taker_long_short is None:
            null_taker_long_short += 1
        elif float(taker_long_short) == 0:
            zero_taker_long_short += 1

        if taker_long_short is None or float(taker_long_short) == 0:
            missing_count += 1

        ts = parse_ts(row.get("bucket_ts") or row.get("bucket") or row.get("ts"))
        if ts and last_ts:
            gap = abs((last_ts - ts).total_seconds())
            if gap > expected_gap * 1.5:
                gaps += 1
                if gap > max_gap:
                    max_gap = int(gap)
        if ts:
            last_ts = ts

    total = len(data)
    missing_ratio = round(missing_count / total, 4) if total else None
    return {
        "ts": _now_iso(),
        "source": {
            "mode": "api",
            "endpoint": "/api/futures/metrics",
            "base_url": args.base_url,
            "interval": interval,
            "table_hint": INTERVAL_TABLE.get(interval, "<unknown>"),
        },
        "symbol": args.symbol,
        "limit": args.limit,
        "records": total,
        "null_open_interest": null_open_interest,
        "zero_open_interest": zero_open_interest,
        "null_long_short_ratio": null_long_short,
        "zero_long_short_ratio": zero_long_short,
        "null_taker_long_short_ratio": null_taker_long_short,
        "zero_taker_long_short_ratio": zero_taker_long_short,
        "missing_count": missing_count,
        "missing_ratio": missing_ratio,
        "ratio_missing": missing_ratio,
        "gap_anomalies": gaps,
        "max_gap_seconds": max_gap,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Tradecat 指标数据质量检查（只读）")
    parser.add_argument("--symbol", default="BTC", help="币种（默认 BTC）")
    parser.add_argument("--interval", default="1h", help="周期（5m/15m/1h/4h/1d/1w）")
    parser.add_argument("--limit", type=int, default=200, help="拉取条数（仅 API 模式）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088", help="Tradecat API 基地址")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP 超时秒")
    parser.add_argument("--auto-start", action="store_true", help="自动启动 Tradecat API")
    parser.add_argument("--project-root", default="/home/lenovo/.projects", help="项目根目录")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.add_argument("--source", choices=["auto", "sqlite", "api"], default="auto", help="数据源选择")
    parser.add_argument(
        "--sqlite-path",
        default="/home/lenovo/.projects/tradecat/libs/database/services/telegram-service/market_data.db",
        help="SQLite 路径（默认 telegram-service/market_data.db）",
    )
    parser.add_argument("--sqlite-table", default="基础数据同步器.py", help="SQLite 表名（默认 基础数据同步器.py）")
    parser.add_argument("--no-auto-resolve", action="store_true", help="关闭自动解析交易对与周期")
    args = parser.parse_args()

    mode = args.source
    if mode == "auto":
        mode = "sqlite" if Path(args.sqlite_path).exists() else "api"

    args.auto_resolve = not args.no_auto_resolve
    summary = _sqlite_quality(args) if mode == "sqlite" else _api_quality(args)

    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    print("SUMMARY")
    for k, v in summary.items():
        print(f"- {k}: {v}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
