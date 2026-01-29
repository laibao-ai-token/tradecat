"""验证样本数据：生成 JSONL 并输出验收报告。"""

from __future__ import annotations

import json
import csv
import zipfile
import os
import sys
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import settings
from pipeline.json_sink import append_jsonl, json_path
from runtime.errors import DatacatError, safe_main
from runtime.logging_utils import setup_logging

from collectors.binance.um_futures.all.backfill.pull.rest.klines.ccxt import fetch_ohlcv, to_rows
from collectors.binance.um_futures.all.backfill.pull.rest.metrics.http import MetricsRestBackfiller
from collectors.binance.um_futures.all.backfill.pull.file.klines.http_zip import ZipBackfiller as ZipKlines
from collectors.binance.um_futures.all.backfill.pull.file.metrics.http_zip import ZipBackfiller as ZipMetrics


@dataclass
class CountResult:
    expected: int
    actual: int

    @property
    def error_pct(self) -> float:
        if self.expected <= 0:
            return 100.0 if self.actual else 0.0
        return abs(self.actual - self.expected) / self.expected * 100.0


def _parse_list(val: str) -> List[str]:
    return [item.strip().upper() for item in val.split(",") if item.strip()]


def _parse_dt(val: object) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None
    return None


def _date_range(start: date, end: date) -> List[date]:
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _expected_count(start: datetime, end: datetime, interval_minutes: int) -> int:
    if end <= start:
        return 0
    start_aligned = start.replace(second=0, microsecond=0)
    end_aligned = end.replace(second=0, microsecond=0)
    delta = end_aligned - start_aligned
    minutes = int(delta.total_seconds() // 60)
    return max(minutes // interval_minutes, 0)


def _is_number_like(val: object) -> bool:
    if val is None:
        return True
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, str):
        try:
            float(val)
            return True
        except ValueError:
            return False
    return False


def _load_rows(path: Path, time_key: str) -> List[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_dt(row.get(time_key))
            if ts:
                row[time_key] = ts.replace(tzinfo=None)
            rows.append(row)
    return rows


def detect_zip_samples(base_dir: Path, symbols: Sequence[str]) -> bool:
    if not base_dir.exists():
        return False
    sym_set = {s.upper() for s in symbols}
    for path in base_dir.glob("*.zip"):
        name = path.name.upper()
        if not any(name.startswith(sym) for sym in sym_set):
            continue
        try:
            with zipfile.ZipFile(path) as zf:
                for member in zf.namelist():
                    if not member.endswith(".csv"):
                        continue
                    with zf.open(member) as f:
                        reader = csv.reader(line.decode() for line in f)
                        for _ in reader:
                            return True
        except Exception:
            continue
    return False


def _filter_rows(rows: Iterable[dict], symbols: Sequence[str], start: datetime, end: datetime, time_key: str) -> List[dict]:
    sym_set = set(symbols)
    filtered = []
    for row in rows:
        sym = row.get("symbol")
        ts = row.get(time_key)
        if sym in sym_set and isinstance(ts, datetime) and start <= ts < end:
            filtered.append(row)
    return filtered


def collect_metrics_rest(symbols: Sequence[str], start_day: date, end_day: date,
                         realtime_start: datetime, now: datetime) -> None:
    class _NullTS:
        def upsert_metrics(self, *_args, **_kwargs):
            return 0
    filler = MetricsRestBackfiller(_NullTS())

    for day in _date_range(start_day, end_day):
        for sym in symbols:
            rows = filler._fetch_day(sym, day)
            if rows:
                append_jsonl(json_path("metrics_5m"), rows, dedup_keys=("exchange", "symbol", "create_time"))

    # 采集实时窗口（今天与可能跨日）
    rt_days = _date_range(realtime_start.date(), now.date())
    for day in rt_days:
        for sym in symbols:
            rows = filler._fetch_day(sym, day)
            if not rows:
                continue
            filtered = [r for r in rows if r.get("create_time") and realtime_start <= r["create_time"] < now]
            if filtered:
                append_jsonl(json_path("metrics_5m"), filtered, dedup_keys=("exchange", "symbol", "create_time"))


def collect_metrics_zip(symbols: Sequence[str], start_day: date, end_day: date) -> None:
    class _NullTS:
        def upsert_metrics(self, *_args, **_kwargs):
            return 0
    filler = ZipMetrics(_NullTS(), workers=2)
    month_groups: Dict[Tuple[str, str], List[date]] = {}
    for day in _date_range(start_day, end_day):
        for sym in symbols:
            key = (sym.upper(), day.strftime("%Y-%m"))
            month_groups.setdefault(key, []).append(day)
    for (sym, month), days in month_groups.items():
        filler._download_metrics_month(sym, month, days)


def collect_klines_ccxt(symbols: Sequence[str], start: datetime, end: datetime, interval: str = "1m") -> None:
    interval_ms = 60_000
    for sym in symbols:
        since_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(end.replace(tzinfo=timezone.utc).timestamp() * 1000)
        while since_ms < end_ms:
            candles = fetch_ohlcv(settings.ccxt_exchange, sym, interval, since_ms, limit=1000)
            if not candles:
                break
            rows = to_rows(settings.db_exchange, sym, candles, source="ccxt_gap")
            filtered = [r for r in rows if start <= r["bucket_ts"].replace(tzinfo=None) < end]
            if filtered:
                append_jsonl(json_path(f"candles_{interval}"), filtered, dedup_keys=("exchange", "symbol", "bucket_ts"))
            last_ts = candles[-1][0] if candles else None
            if not last_ts:
                break
            since_ms = last_ts + interval_ms


def collect_klines_zip(symbols: Sequence[str], start_day: date, end_day: date, interval: str = "1m") -> None:
    class _NullTS:
        def upsert_candles(self, *_args, **_kwargs):
            return 0
    filler = ZipKlines(_NullTS(), workers=2)
    month_groups: Dict[Tuple[str, str], List[date]] = {}
    for day in _date_range(start_day, end_day):
        for sym in symbols:
            key = (sym.upper(), day.strftime("%Y-%m"))
            month_groups.setdefault(key, []).append(day)
    for (sym, month), days in month_groups.items():
        filler._download_kline_month(sym, month, days, interval)


def analyze_rows(rows: List[dict], symbols: Sequence[str], start: datetime, end: datetime,
                 time_key: str, interval_minutes: int) -> Tuple[Dict[str, CountResult], CountResult]:
    filtered = _filter_rows(rows, symbols, start, end, time_key)
    expected = _expected_count(start, end, interval_minutes)
    per_symbol: Dict[str, CountResult] = {}
    total_actual = 0
    for sym in symbols:
        count = sum(1 for r in filtered if r.get("symbol") == sym)
        per_symbol[sym] = CountResult(expected, count)
        total_actual += count
    total_expected = expected * len(symbols)
    total = CountResult(total_expected, total_actual)
    return per_symbol, total


def check_fields(rows: List[dict], required: Sequence[str], numeric_fields: Sequence[str]) -> Tuple[bool, List[str]]:
    if not rows:
        return False, ["无数据"]
    row = rows[0]
    missing = [f for f in required if f not in row]
    if missing:
        return False, missing
    type_errors = []
    for f in numeric_fields:
        if not _is_number_like(row.get(f)):
            type_errors.append(f)
    if type_errors:
        return False, type_errors
    return True, []


def max_time_offset(rows: List[dict], time_key: str, interval_minutes: int) -> int:
    if not rows:
        return 0
    offsets = []
    for row in rows:
        ts = row.get(time_key)
        if not isinstance(ts, datetime):
            continue
        floor = ts.replace(second=0, microsecond=0)
        if interval_minutes > 1:
            minute = floor.minute - (floor.minute % interval_minutes)
            floor = floor.replace(minute=minute)
        offsets.append(int(abs((ts - floor).total_seconds())))
    return max(offsets) if offsets else 0


def render_report(path: Path, *, symbols: Sequence[str], now: datetime,
                  realtime_start: datetime, backfill_start: date, backfill_end: date,
                  candle_rows: List[dict], metrics_rows: List[dict],
                  zip_candle_ok: bool, zip_metrics_ok: bool) -> None:
    rt_candles = _filter_rows(candle_rows, symbols, realtime_start, now, "bucket_ts")
    rt_metrics = _filter_rows(metrics_rows, symbols, realtime_start, now, "create_time")

    bf_start_dt = datetime.combine(backfill_start, datetime.min.time())
    bf_end_dt = datetime.combine(backfill_end + timedelta(days=1), datetime.min.time())
    bf_candles = _filter_rows(candle_rows, symbols, bf_start_dt, bf_end_dt, "bucket_ts")
    bf_metrics = _filter_rows(metrics_rows, symbols, bf_start_dt, bf_end_dt, "create_time")

    per_symbol_rt_c, total_rt_c = analyze_rows(candle_rows, symbols, realtime_start, now, "bucket_ts", 1)
    per_symbol_rt_m, total_rt_m = analyze_rows(metrics_rows, symbols, realtime_start, now, "create_time", 5)

    per_symbol_bf_c, total_bf_c = analyze_rows(candle_rows, symbols, bf_start_dt, bf_end_dt, "bucket_ts", 1)
    per_symbol_bf_m, total_bf_m = analyze_rows(metrics_rows, symbols, bf_start_dt, bf_end_dt, "create_time", 5)

    candle_fields = ["symbol", "bucket_ts", "open", "high", "low", "close", "volume", "source", "is_closed"]
    candle_numeric = ["open", "high", "low", "close", "volume"]
    metrics_fields = [
        "symbol", "create_time", "sum_open_interest", "sum_open_interest_value",
        "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
        "count_long_short_ratio", "sum_taker_long_short_vol_ratio", "source", "is_closed",
    ]
    metrics_numeric = [
        "sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
    ]

    candle_ok, candle_err = check_fields(rt_candles or bf_candles, candle_fields, candle_numeric)
    metrics_ok, metrics_err = check_fields(rt_metrics or bf_metrics, metrics_fields, metrics_numeric)

    candle_offset = max_time_offset(rt_candles + bf_candles, "bucket_ts", 1)
    metrics_offset = max_time_offset(rt_metrics + bf_metrics, "create_time", 5)

    candle_sources = sorted({r.get("source") for r in candle_rows if r.get("source")})
    metrics_sources = sorted({r.get("source") for r in metrics_rows if r.get("source")})
    if zip_candle_ok and "binance_zip" not in candle_sources:
        candle_sources.append("binance_zip")
    if zip_metrics_ok and "binance_zip" not in metrics_sources:
        metrics_sources.append("binance_zip")

    rt_ok = total_rt_c.error_pct <= 1.0 and total_rt_m.error_pct <= 1.0
    bf_ok = total_bf_c.error_pct <= 0.5 and total_bf_m.error_pct <= 0.5
    time_ok = candle_offset <= 60 and metrics_offset <= 300
    field_ok = candle_ok and metrics_ok

    overall_ok = rt_ok and bf_ok and time_ok and field_ok

    git_sha = "unknown"
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT)).decode().strip()
    except Exception:
        pass

    proxy = os.getenv("DATACAT_HTTP_PROXY") or os.getenv("HTTP_PROXY") or ""

    lines = []
    lines.append("# Datacat Service 验收报告")
    lines.append("")
    lines.append("## 1. 元信息")
    lines.append("")
    lines.append(f"- 日期：{now.date().isoformat()}")
    lines.append(f"- 版本：git {git_sha}")
    lines.append("- 运行人：auto")
    lines.append(f"- 环境：local")
    lines.append(f"- 代理：{proxy or '无'}")
    lines.append("")
    lines.append("## 2. 样本说明")
    lines.append("")
    lines.append(f"- 符号：{', '.join(symbols)}")
    lines.append(f"- 实时窗口：{realtime_start.strftime('%Y-%m-%d %H:%M')} ~ {now.strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append(f"- 回填窗口：{backfill_start.isoformat()} ~ {backfill_end.isoformat()} (UTC 自然日)")
    lines.append("- 数据类型：K线 1m、Metrics 5m")
    lines.append("")
    lines.append("## 3. 对照指标")
    lines.append("")
    lines.append("### 3.1 字段一致性")
    lines.append("")
    lines.append(f"- K线字段：{'✅' if candle_ok else '❌'}" + ("" if candle_ok else f" 缺失/类型异常: {', '.join(candle_err)}"))
    lines.append(f"- Metrics 字段：{'✅' if metrics_ok else '❌'}" + ("" if metrics_ok else f" 缺失/类型异常: {', '.join(metrics_err)}"))
    lines.append("")
    lines.append("### 3.2 行数一致性")
    lines.append("")
    lines.append("- 实时窗口：误差 <= 1%")
    lines.append(f"  - K线期望行数：{total_rt_c.expected}")
    lines.append(f"  - K线实际行数：{total_rt_c.actual}")
    lines.append(f"  - K线误差：{total_rt_c.error_pct:.2f}%")
    lines.append(f"  - Metrics期望行数：{total_rt_m.expected}")
    lines.append(f"  - Metrics实际行数：{total_rt_m.actual}")
    lines.append(f"  - Metrics误差：{total_rt_m.error_pct:.2f}%")
    lines.append("")
    lines.append("- 回填窗口：误差 <= 0.5%")
    lines.append(f"  - K线期望行数：{total_bf_c.expected}")
    lines.append(f"  - K线实际行数：{total_bf_c.actual}")
    lines.append(f"  - K线误差：{total_bf_c.error_pct:.2f}%")
    lines.append(f"  - Metrics期望行数：{total_bf_m.expected}")
    lines.append(f"  - Metrics实际行数：{total_bf_m.actual}")
    lines.append(f"  - Metrics误差：{total_bf_m.error_pct:.2f}%")
    lines.append("")
    lines.append("### 3.3 时间对齐")
    lines.append("")
    lines.append("- K线：bucket_ts 对齐到 1m")
    lines.append("- Metrics：create_time 对齐到 5m")
    lines.append(f"- 最大偏移：K线 {candle_offset}s / Metrics {metrics_offset}s")
    lines.append("")
    lines.append("### 3.4 来源一致性")
    lines.append("")
    lines.append(f"- K线来源：{', '.join(candle_sources) if candle_sources else '无'}")
    lines.append(f"- Metrics来源：{', '.join(metrics_sources) if metrics_sources else '无'}")
    lines.append("")
    lines.append("## 4. 偏差分析")
    lines.append("")
    if overall_ok:
        lines.append("- 主要偏差：未发现")
        lines.append("- 原因分析：数据齐全且时间对齐")
        lines.append("- 影响范围：无")
    else:
        lines.append("- 主要偏差：行数或字段未达标")
        lines.append("- 原因分析：采集窗口不足或源数据缺失")
        lines.append("- 影响范围：样本窗口")
    lines.append("")
    lines.append("## 5. 结论")
    lines.append("")
    lines.append(f"- {'✅' if overall_ok else '❌'} 验收{'通过' if overall_ok else '未通过'}")
    lines.append("- 结论说明：" + ("满足当前阈值" if overall_ok else "需补齐采集窗口或排查缺失原因"))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    setup_logging(level=settings.log_level, fmt=settings.log_format, component="validation", log_file=settings.log_file)

    if settings.output_mode != "json":
        raise DatacatError("必须设置 DATACAT_OUTPUT_MODE=json 才能进行验证")

    symbols_raw = os.getenv("DATACAT_VALIDATION_SYMBOLS") or os.getenv("SYMBOLS_VALIDATION")
    symbols = _parse_list(symbols_raw) if symbols_raw else ["BTCUSDT", "ETHUSDT"]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    realtime_start = now - timedelta(hours=3)

    backfill_end = (now.date() - timedelta(days=1))
    backfill_start = backfill_end - timedelta(days=2)

    collect_metrics_rest(symbols, backfill_start, backfill_end, realtime_start, now)
    collect_metrics_zip(symbols, backfill_start, backfill_end)

    collect_klines_ccxt(symbols, realtime_start, now, "1m")
    collect_klines_ccxt(symbols, datetime.combine(backfill_start, datetime.min.time()),
                        datetime.combine(backfill_end + timedelta(days=1), datetime.min.time()), "1m")
    collect_klines_zip(symbols, backfill_start, backfill_end, "1m")

    candle_rows = _load_rows(json_path("candles_1m"), "bucket_ts")
    metrics_rows = _load_rows(json_path("metrics_5m"), "create_time")

    report_path = ROOT / "tasks" / "validation-report.md"
    zip_candle_ok = detect_zip_samples(settings.data_dir / "downloads" / "klines", symbols)
    zip_metrics_ok = detect_zip_samples(settings.data_dir / "downloads" / "metrics", symbols)
    render_report(report_path, symbols=symbols, now=now, realtime_start=realtime_start,
                  backfill_start=backfill_start, backfill_end=backfill_end,
                  candle_rows=candle_rows, metrics_rows=metrics_rows,
                  zip_candle_ok=zip_candle_ok, zip_metrics_ok=zip_metrics_ok)


if __name__ == "__main__":
    sys.exit(safe_main(main, component="validation"))
