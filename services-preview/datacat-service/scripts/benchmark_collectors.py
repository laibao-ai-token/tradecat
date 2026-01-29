"""采集器基准测试（轻量版）。"""

from __future__ import annotations

import os
import sys
import time
import platform
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import settings
from runtime.errors import safe_main
from runtime.logging_utils import setup_logging

from collectors.binance.um_futures.all.realtime.pull.rest.metrics.http import MetricsCollector
from collectors.binance.um_futures.all.backfill.pull.rest.klines.ccxt import fetch_ohlcv


def _parse_list(val: str) -> List[str]:
    return [item.strip().upper() for item in val.split(",") if item.strip()]


def bench_metrics(symbols: List[str], workers: int = 4, rounds: int = 3) -> dict:
    collector = MetricsCollector(workers=workers)
    try:
        total_rows = 0
        start = time.time()
        for _ in range(rounds):
            rows = collector.collect(symbols)
            total_rows += len(rows)
        elapsed = time.time() - start
        return {
            "rows": total_rows,
            "seconds": elapsed,
            "rows_per_sec": (total_rows / elapsed) if elapsed > 0 else 0.0,
        }
    finally:
        collector.close()


def bench_klines(symbol: str, limit: int = 1000) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=limit)
    since_ms = int(start.timestamp() * 1000)
    start_t = time.time()
    candles = fetch_ohlcv(settings.ccxt_exchange, symbol, "1m", since_ms, limit=limit)
    elapsed = time.time() - start_t
    rows = len(candles) if candles else 0
    return {
        "rows": rows,
        "seconds": elapsed,
        "rows_per_sec": (rows / elapsed) if elapsed > 0 else 0.0,
    }


def main() -> None:
    setup_logging(level=settings.log_level, fmt=settings.log_format, component="benchmark", log_file=settings.log_file)

    symbols_raw = os.getenv("DATACAT_BENCH_SYMBOLS") or "BTCUSDT,ETHUSDT"
    symbols = _parse_list(symbols_raw)
    if not symbols:
        symbols = ["BTCUSDT"]

    metrics_result = bench_metrics(symbols, workers=4, rounds=3)
    klines_result = bench_klines(symbols[0], limit=1000)

    report_path = ROOT / "tasks" / "benchmark-report.md"
    now = datetime.now(timezone.utc)

    lines = []
    lines.append("# Datacat Service 基准测试报告")
    lines.append("")
    lines.append(f"- 日期：{now.date().isoformat()}")
    lines.append(f"- 环境：{platform.platform()}")
    lines.append(f"- Python：{platform.python_version()}")
    lines.append(f"- 采样符号：{', '.join(symbols)}")
    lines.append("")
    lines.append("## Metrics（REST 并发采集）")
    lines.append("")
    lines.append(f"- 行数：{metrics_result['rows']}")
    lines.append(f"- 耗时：{metrics_result['seconds']:.2f}s")
    lines.append(f"- 吞吐：{metrics_result['rows_per_sec']:.2f} rows/s")
    lines.append("")
    lines.append("## Klines（CCXT 单次拉取）")
    lines.append("")
    lines.append(f"- 行数：{klines_result['rows']}")
    lines.append(f"- 耗时：{klines_result['seconds']:.2f}s")
    lines.append(f"- 吞吐：{klines_result['rows_per_sec']:.2f} rows/s")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(safe_main(main, component="benchmark"))
