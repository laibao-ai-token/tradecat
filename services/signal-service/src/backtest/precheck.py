"""Backtest input coverage precheck helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import get_database_url, get_history_db_path
from .data_loader import resolve_range
from .models import BacktestConfig


@dataclass(frozen=True)
class SymbolCoverage:
    """Window coverage summary per symbol."""

    symbol: str
    signal_count: int = 0
    signal_min_ts: str = ""
    signal_max_ts: str = ""
    candle_count: int = 0
    candle_min_ts: str = ""
    candle_max_ts: str = ""


@dataclass(frozen=True)
class BacktestCoverageReport:
    """Merged coverage summary used by CLI/TUI diagnostics."""

    start: str
    end: str
    timeframe: str
    symbols: list[str]
    signal_count: int
    signal_days: int
    signal_min_ts: str
    signal_max_ts: str
    candle_count: int
    candle_min_ts: str
    candle_max_ts: str
    expected_candle_count: int
    candle_coverage_pct: float
    symbol_rows: list[SymbolCoverage] = field(default_factory=list)


def _normalize_symbols(raw_symbols: list[str]) -> list[str]:
    out: list[str] = []
    for symbol in raw_symbols:
        sym = str(symbol or "").upper().strip()
        if sym:
            out.append(sym)
    return list(dict.fromkeys(out))


def _fmt_ts(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value).strip()


def _load_signal_coverage_from_sqlite(
    db_path: Path,
    symbols: list[str],
    start_iso: str,
    end_iso: str,
    timeframe: str,
) -> dict[str, Any]:
    if not db_path.exists() or not symbols:
        return {
            "total_count": 0,
            "day_count": 0,
            "min_ts": "",
            "max_ts": "",
            "by_symbol": {},
        }

    placeholders = ",".join("?" for _ in symbols)
    tf = str(timeframe or "").strip().lower()

    where_sql = [
        "timestamp >= ?",
        "timestamp <= ?",
        f"upper(symbol) in ({placeholders})",
    ]
    params: list[Any] = [start_iso, end_iso, *symbols]

    if tf:
        where_sql.append("lower(coalesce(timeframe,'')) = ?")
        params.append(tf)

    where_clause = " and ".join(where_sql)

    q_total = f"""
        select count(*), min(timestamp), max(timestamp)
        from signal_history
        where {where_clause}
    """
    q_days = f"""
        select count(distinct substr(timestamp, 1, 10))
        from signal_history
        where {where_clause}
    """
    q_symbol = f"""
        select upper(symbol), count(*), min(timestamp), max(timestamp)
        from signal_history
        where {where_clause}
        group by upper(symbol)
        order by upper(symbol)
    """

    with sqlite3.connect(db_path, timeout=10) as conn:
        cur = conn.cursor()
        total_count, min_ts, max_ts = cur.execute(q_total, params).fetchone()
        (day_count,) = cur.execute(q_days, params).fetchone()
        symbol_rows = cur.execute(q_symbol, params).fetchall()

    by_symbol: dict[str, dict[str, Any]] = {}
    for sym, count, s_min, s_max in symbol_rows:
        by_symbol[str(sym)] = {
            "count": int(count or 0),
            "min_ts": _fmt_ts(s_min),
            "max_ts": _fmt_ts(s_max),
        }

    return {
        "total_count": int(total_count or 0),
        "day_count": int(day_count or 0),
        "min_ts": _fmt_ts(min_ts),
        "max_ts": _fmt_ts(max_ts),
        "by_symbol": by_symbol,
    }


def _load_candle_coverage_from_pg(
    database_url: str,
    symbols: list[str],
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, Any]:
    if not symbols:
        return {
            "total_count": 0,
            "min_ts": "",
            "max_ts": "",
            "by_symbol": {},
        }

    import psycopg

    q_total = """
        with ranked as (
            select
                symbol,
                bucket_ts,
                row_number() over (partition by symbol, bucket_ts order by updated_at desc nulls last) as rn
            from market_data.candles_1m
            where symbol = any(%(symbols)s)
              and bucket_ts >= %(start)s
              and bucket_ts <= %(end)s
        )
        select count(*), min(bucket_ts), max(bucket_ts)
        from ranked
        where rn = 1
    """

    q_symbol = """
        with ranked as (
            select
                symbol,
                bucket_ts,
                row_number() over (partition by symbol, bucket_ts order by updated_at desc nulls last) as rn
            from market_data.candles_1m
            where symbol = any(%(symbols)s)
              and bucket_ts >= %(start)s
              and bucket_ts <= %(end)s
        )
        select symbol, count(*), min(bucket_ts), max(bucket_ts)
        from ranked
        where rn = 1
        group by symbol
        order by symbol
    """

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(q_total, {"symbols": symbols, "start": start_dt, "end": end_dt})
            total_count, min_ts, max_ts = cur.fetchone()

            cur.execute(q_symbol, {"symbols": symbols, "start": start_dt, "end": end_dt})
            symbol_rows = cur.fetchall()

    by_symbol: dict[str, dict[str, Any]] = {}
    for sym, count, s_min, s_max in symbol_rows:
        by_symbol[str(sym)] = {
            "count": int(count or 0),
            "min_ts": _fmt_ts(s_min),
            "max_ts": _fmt_ts(s_max),
        }

    return {
        "total_count": int(total_count or 0),
        "min_ts": _fmt_ts(min_ts),
        "max_ts": _fmt_ts(max_ts),
        "by_symbol": by_symbol,
    }


def compute_coverage_report(
    config: BacktestConfig,
    *,
    history_db_path: Path | None = None,
    database_url: str | None = None,
) -> BacktestCoverageReport:
    """Compute coverage summary for the configured backtest window."""

    start_dt, end_dt = resolve_range(config.date_range)
    symbols = _normalize_symbols(config.symbols)

    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()

    signal_info = _load_signal_coverage_from_sqlite(
        history_db_path or get_history_db_path(),
        symbols,
        start_iso,
        end_iso,
        config.timeframe,
    )

    candle_info = _load_candle_coverage_from_pg(
        database_url or get_database_url(),
        symbols,
        start_dt,
        end_dt,
    )

    minutes = max(0, int((end_dt - start_dt).total_seconds() // 60) + 1)
    expected_candle_count = minutes * len(symbols)
    candle_count = int(candle_info["total_count"])
    candle_coverage_pct = (candle_count / expected_candle_count * 100.0) if expected_candle_count > 0 else 0.0

    symbol_rows: list[SymbolCoverage] = []
    signal_by_symbol = signal_info.get("by_symbol") or {}
    candle_by_symbol = candle_info.get("by_symbol") or {}

    for sym in symbols:
        s = signal_by_symbol.get(sym) or {}
        c = candle_by_symbol.get(sym) or {}
        symbol_rows.append(
            SymbolCoverage(
                symbol=sym,
                signal_count=int(s.get("count") or 0),
                signal_min_ts=str(s.get("min_ts") or ""),
                signal_max_ts=str(s.get("max_ts") or ""),
                candle_count=int(c.get("count") or 0),
                candle_min_ts=str(c.get("min_ts") or ""),
                candle_max_ts=str(c.get("max_ts") or ""),
            )
        )

    return BacktestCoverageReport(
        start=start_iso,
        end=end_iso,
        timeframe=str(config.timeframe or "").strip(),
        symbols=symbols,
        signal_count=int(signal_info["total_count"]),
        signal_days=int(signal_info["day_count"]),
        signal_min_ts=str(signal_info["min_ts"]),
        signal_max_ts=str(signal_info["max_ts"]),
        candle_count=candle_count,
        candle_min_ts=str(candle_info["min_ts"]),
        candle_max_ts=str(candle_info["max_ts"]),
        expected_candle_count=expected_candle_count,
        candle_coverage_pct=float(candle_coverage_pct),
        symbol_rows=symbol_rows,
    )


def format_coverage_lines(report: BacktestCoverageReport) -> list[str]:
    """Render human-readable precheck lines for CLI logging."""

    lines = [
        f"window={report.start} -> {report.end} tf={report.timeframe} symbols={len(report.symbols)}",
        (
            "signals="
            f"{report.signal_count} days={report.signal_days} "
            f"range={report.signal_min_ts or '--'} -> {report.signal_max_ts or '--'}"
        ),
        (
            "candles="
            f"{report.candle_count} expected~={report.expected_candle_count} "
            f"coverage={report.candle_coverage_pct:.2f}% "
            f"range={report.candle_min_ts or '--'} -> {report.candle_max_ts or '--'}"
        ),
    ]

    for row in report.symbol_rows:
        lines.append(
            f"{row.symbol}: signals={row.signal_count} candles={row.candle_count} "
            f"sig_range={row.signal_min_ts or '--'} -> {row.signal_max_ts or '--'}"
        )

    return lines
