"""Backtest input coverage precheck helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import get_database_url, get_history_db_path
from .data_loader import floor_minute, resolve_range
from .models import BacktestConfig, Bar, SignalEvent


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


@dataclass(frozen=True)
class InputQualitySymbol:
    """Per-symbol input-quality diagnostics persisted with each run."""

    symbol: str
    signal_count: int = 0
    aggregated_signal_bucket_count: int = 0
    candle_count: int = 0
    expected_candle_count: int = 0
    candle_coverage_pct: float = 0.0
    missing_candle_count: int = 0
    gap_count: int = 0
    largest_gap_minutes: int = 0
    no_next_open_bucket_count: int = 0
    dropped_signal_count: int = 0
    quality_score: float = 0.0
    quality_status: str = "unknown"


@dataclass(frozen=True)
class InputQualityReport:
    """Run-level input-quality artifact."""

    run_id: str
    mode: str
    start: str
    end: str
    timeframe: str
    generated_at: str
    signal_count: int
    signal_days: int = 0
    aggregated_signal_bucket_count: int = 0
    candle_count: int = 0
    expected_candle_count: int = 0
    candle_coverage_pct: float = 0.0
    no_next_open_bucket_count: int = 0
    dropped_signal_count: int = 0
    quality_score: float = 0.0
    quality_status: str = "unknown"
    score_status: str = "unknown"
    gate_status: str = "not_evaluated"
    gate_failures: list[str] = field(default_factory=list)
    gate_thresholds: dict[str, int | float] = field(default_factory=dict)
    quality_breakdown: dict[str, float | int] = field(default_factory=dict)
    symbol_rows: list[InputQualitySymbol] = field(default_factory=list)


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


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(sep=" ")


def _ratio_pct(numerator: int | float, denominator: int | float) -> float:
    den = max(float(denominator), 0.0)
    if den <= 0:
        return 0.0
    return float(max(float(numerator), 0.0) / den * 100.0)


def _clamp_quality_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _quality_status(score: float) -> str:
    if float(score) >= 90.0:
        return "pass"
    if float(score) >= 75.0:
        return "warn"
    return "fail"


def _calc_quality_score_components(
    *,
    candle_coverage_pct: float,
    expected_candle_count: int,
    missing_candle_count: int,
    gap_count: int,
    largest_gap_minutes: int,
    aggregated_signal_bucket_count: int,
    no_next_open_bucket_count: int,
    signal_count: int,
    dropped_signal_count: int,
) -> tuple[float, str, dict[str, float | int]]:
    coverage_score = _clamp_quality_score(candle_coverage_pct)
    missing_candle_ratio_pct = _ratio_pct(missing_candle_count, max(expected_candle_count, 1))
    no_next_open_ratio_pct = _ratio_pct(no_next_open_bucket_count, max(aggregated_signal_bucket_count, 1))
    dropped_signal_ratio_pct = _ratio_pct(dropped_signal_count, max(signal_count, 1))

    missing_candle_penalty = min(20.0, missing_candle_ratio_pct * 0.20)
    gap_penalty = min(15.0, float(max(0, gap_count)) * 1.5 + float(max(0, largest_gap_minutes - 1)) * 0.5)
    no_next_open_penalty = min(25.0, no_next_open_ratio_pct * 0.25)
    dropped_signal_penalty = min(15.0, dropped_signal_ratio_pct * 0.15)

    quality_score = _clamp_quality_score(
        coverage_score
        - missing_candle_penalty
        - gap_penalty
        - no_next_open_penalty
        - dropped_signal_penalty
    )
    status = _quality_status(quality_score)
    breakdown: dict[str, float | int] = {
        "coverage_score": round(coverage_score, 2),
        "missing_candle_penalty": round(missing_candle_penalty, 2),
        "gap_penalty": round(gap_penalty, 2),
        "no_next_open_penalty": round(no_next_open_penalty, 2),
        "dropped_signal_penalty": round(dropped_signal_penalty, 2),
        "missing_candle_ratio_pct": round(missing_candle_ratio_pct, 2),
        "no_next_open_ratio_pct": round(no_next_open_ratio_pct, 2),
        "dropped_signal_ratio_pct": round(dropped_signal_ratio_pct, 2),
        "gap_count": int(gap_count),
        "largest_gap_minutes": int(largest_gap_minutes),
        "quality_score": round(quality_score, 2),
    }
    return float(quality_score), status, breakdown


def _count_signal_days(signals: list[SignalEvent]) -> int:
    days: set[str] = set()
    for event in signals:
        try:
            days.add(event.timestamp.astimezone(timezone.utc).date().isoformat())
        except Exception:
            days.add(str(event.timestamp.date()))
    return len(days)


def build_coverage_guard_thresholds(
    *,
    min_signal_days: int,
    min_signal_count: int,
    min_candle_coverage_pct: float,
) -> dict[str, int | float]:
    """Normalize coverage guard thresholds for JSON/report payloads."""

    return {
        "min_signal_days": max(0, int(min_signal_days)),
        "min_signal_count": max(0, int(min_signal_count)),
        "min_candle_coverage_pct": max(0.0, float(min_candle_coverage_pct)),
    }


def collect_coverage_guard_failures(
    *,
    mode: str,
    signal_days: int,
    signal_count: int,
    candle_count: int,
    expected_candle_count: int,
    candle_coverage_pct: float,
    min_signal_days: int,
    min_signal_count: int,
    min_candle_coverage_pct: float,
) -> list[str]:
    """Evaluate the shared precheck guard against coverage-style counters."""

    failures: list[str] = []

    if int(candle_count) <= 0:
        failures.append("no candle rows in selected window")

    pct_threshold = max(0.0, float(min_candle_coverage_pct))
    if int(expected_candle_count) > 0 and float(candle_coverage_pct) < pct_threshold:
        failures.append(
            "candle coverage too low: "
            f"{float(candle_coverage_pct):.2f}% < {pct_threshold:.2f}%"
        )

    if str(mode or "").strip().lower() == "history_signal":
        day_threshold = max(0, int(min_signal_days))
        if day_threshold > 0 and int(signal_days) < day_threshold:
            failures.append(
                "signal day coverage too low: "
                f"{int(signal_days)} < {day_threshold}"
            )

        count_threshold = max(0, int(min_signal_count))
        if count_threshold > 0 and int(signal_count) < count_threshold:
            failures.append(
                "signal count too low: "
                f"{int(signal_count)} < {count_threshold}"
            )

    return failures


def _merge_quality_status(score_status: str, gate_status: str) -> str:
    score = str(score_status or "unknown").strip().lower() or "unknown"
    gate = str(gate_status or "not_evaluated").strip().lower() or "not_evaluated"

    if gate == "fail":
        return "fail"
    if score in {"pass", "warn", "fail"}:
        return score
    if gate == "pass":
        return "pass"
    return "unknown"


def _minute_span(start_dt: datetime, end_dt: datetime) -> int:
    return max(0, int((end_dt - start_dt).total_seconds() // 60) + 1)


def _calc_gap_stats(bars: list[Bar]) -> tuple[int, int, int]:
    if len(bars) < 2:
        return 0, 0, 0

    ordered = sorted(bars, key=lambda x: x.timestamp)
    gap_count = 0
    missing_candle_count = 0
    largest_gap_minutes = 0

    for prev, cur in zip(ordered, ordered[1:]):
        delta_minutes = int((cur.timestamp - prev.timestamp).total_seconds() // 60)
        if delta_minutes <= 1:
            continue
        missing = delta_minutes - 1
        gap_count += 1
        missing_candle_count += missing
        largest_gap_minutes = max(largest_gap_minutes, missing)

    return gap_count, missing_candle_count, largest_gap_minutes


def _build_next_open_keys(bars: list[Bar]) -> set[datetime]:
    ordered = sorted(bars, key=lambda x: x.timestamp)
    executable: set[datetime] = set()
    for current, nxt in zip(ordered, ordered[1:]):
        if nxt.timestamp - current.timestamp == timedelta(minutes=1):
            executable.add(current.timestamp)
    return executable


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

    params = {
        "symbols": symbols,
        "start": start_dt,
        "end": end_dt,
    }

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            total_count, min_ts, max_ts = cur.execute(q_total, params).fetchone()
            symbol_rows = cur.execute(q_symbol, params).fetchall()

    by_symbol: dict[str, dict[str, Any]] = {}
    for sym, count, c_min, c_max in symbol_rows:
        by_symbol[str(sym).upper().strip()] = {
            "count": int(count or 0),
            "min_ts": _fmt_ts(c_min),
            "max_ts": _fmt_ts(c_max),
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

    minutes = _minute_span(start_dt, end_dt)
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


def build_input_quality_report(
    config: BacktestConfig,
    *,
    run_id: str,
    mode: str,
    signals: list[SignalEvent],
    bars_by_symbol: dict[str, list[Bar]],
    score_map: dict[str, dict[datetime, int]] | None = None,
    signal_days: int | None = None,
    gate_failures: list[str] | None = None,
    gate_thresholds: dict[str, int | float] | None = None,
) -> InputQualityReport:
    """Build input-quality diagnostics from loaded bars and signals."""

    from .aggregator import aggregate_signal_scores

    start_dt, end_dt = resolve_range(config.date_range)
    start_iso = start_dt.isoformat(sep=" ")
    end_iso = end_dt.isoformat(sep=" ")
    symbols = _normalize_symbols(config.symbols)
    minutes = _minute_span(start_dt, end_dt)
    score_map = score_map or aggregate_signal_scores(signals, timeframe=config.timeframe)

    symbol_rows: list[InputQualitySymbol] = []
    total_signal_count = 0
    total_aggregated_bucket_count = 0
    total_candle_count = 0
    total_expected_candle_count = minutes * len(symbols)
    total_no_next_open_bucket_count = 0
    total_dropped_signal_count = 0
    total_missing_candle_count = 0
    total_gap_count = 0
    largest_gap_minutes_overall = 0

    for symbol in symbols:
        symbol_signals = [ev for ev in signals if str(getattr(ev, "symbol", "")).upper().strip() == symbol]
        valid_symbol_signals = [
            ev
            for ev in symbol_signals
            if str(getattr(ev, "direction", "")).upper().strip() in {"BUY", "SELL"}
        ]
        bars = sorted(bars_by_symbol.get(symbol, []), key=lambda x: x.timestamp)
        candle_count = len(bars)
        expected_candle_count = minutes
        candle_coverage_pct = (candle_count / expected_candle_count * 100.0) if expected_candle_count > 0 else 0.0
        gap_count, missing_candle_count, largest_gap_minutes = _calc_gap_stats(bars)
        next_open_keys = _build_next_open_keys(bars)
        symbol_score_map = score_map.get(symbol, {})
        aggregated_bucket_count = len(symbol_score_map)
        no_next_open_bucket_count = sum(1 for ts in symbol_score_map if ts not in next_open_keys)
        invalid_signal_count = max(0, len(symbol_signals) - len(valid_symbol_signals))
        dropped_signal_count = invalid_signal_count + sum(
            1 for ev in valid_symbol_signals if floor_minute(ev.timestamp) not in next_open_keys
        )

        total_signal_count += len(symbol_signals)
        total_aggregated_bucket_count += aggregated_bucket_count
        total_candle_count += candle_count
        total_no_next_open_bucket_count += no_next_open_bucket_count
        total_dropped_signal_count += dropped_signal_count
        total_missing_candle_count += missing_candle_count
        total_gap_count += gap_count
        largest_gap_minutes_overall = max(largest_gap_minutes_overall, largest_gap_minutes)

        symbol_quality_score, symbol_quality_status, _symbol_breakdown = _calc_quality_score_components(
            candle_coverage_pct=candle_coverage_pct,
            expected_candle_count=expected_candle_count,
            missing_candle_count=missing_candle_count,
            gap_count=gap_count,
            largest_gap_minutes=largest_gap_minutes,
            aggregated_signal_bucket_count=aggregated_bucket_count,
            no_next_open_bucket_count=no_next_open_bucket_count,
            signal_count=len(symbol_signals),
            dropped_signal_count=dropped_signal_count,
        )

        symbol_rows.append(
            InputQualitySymbol(
                symbol=symbol,
                signal_count=len(symbol_signals),
                aggregated_signal_bucket_count=aggregated_bucket_count,
                candle_count=candle_count,
                expected_candle_count=expected_candle_count,
                candle_coverage_pct=float(candle_coverage_pct),
                missing_candle_count=missing_candle_count,
                gap_count=gap_count,
                largest_gap_minutes=largest_gap_minutes,
                no_next_open_bucket_count=no_next_open_bucket_count,
                dropped_signal_count=dropped_signal_count,
                quality_score=float(symbol_quality_score),
                quality_status=str(symbol_quality_status),
            )
        )

    overall_coverage_pct = (
        total_candle_count / total_expected_candle_count * 100.0 if total_expected_candle_count > 0 else 0.0
    )
    quality_score, quality_status, quality_breakdown = _calc_quality_score_components(
        candle_coverage_pct=overall_coverage_pct,
        expected_candle_count=total_expected_candle_count,
        missing_candle_count=total_missing_candle_count,
        gap_count=total_gap_count,
        largest_gap_minutes=largest_gap_minutes_overall,
        aggregated_signal_bucket_count=total_aggregated_bucket_count,
        no_next_open_bucket_count=total_no_next_open_bucket_count,
        signal_count=total_signal_count,
        dropped_signal_count=total_dropped_signal_count,
    )
    resolved_signal_days = int(signal_days) if signal_days is not None else _count_signal_days(signals)
    resolved_gate_thresholds = dict(gate_thresholds or {})
    resolved_gate_failures = [str(item).strip() for item in (gate_failures or []) if str(item).strip()]
    gate_status = "not_evaluated"
    overall_quality_status = str(quality_status)
    if resolved_gate_thresholds:
        gate_status = "fail" if resolved_gate_failures else "pass"
        overall_quality_status = _merge_quality_status(str(quality_status), gate_status)

    return InputQualityReport(
        run_id=str(run_id or ""),
        mode=str(mode or ""),
        start=start_iso,
        end=end_iso,
        timeframe=str(config.timeframe or "").strip(),
        generated_at=_utc_now_iso(),
        signal_count=total_signal_count,
        signal_days=resolved_signal_days,
        aggregated_signal_bucket_count=total_aggregated_bucket_count,
        candle_count=total_candle_count,
        expected_candle_count=total_expected_candle_count,
        candle_coverage_pct=float(overall_coverage_pct),
        no_next_open_bucket_count=total_no_next_open_bucket_count,
        dropped_signal_count=total_dropped_signal_count,
        quality_score=float(quality_score),
        quality_status=str(overall_quality_status),
        score_status=str(quality_status),
        gate_status=gate_status,
        gate_failures=resolved_gate_failures,
        gate_thresholds=resolved_gate_thresholds,
        quality_breakdown=quality_breakdown,
        symbol_rows=symbol_rows,
    )


def input_quality_to_payload(report: InputQualityReport) -> dict[str, Any]:
    """Convert input-quality dataclass to JSON-ready payload."""

    return asdict(report)


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
