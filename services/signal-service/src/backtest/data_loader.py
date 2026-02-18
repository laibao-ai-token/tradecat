"""Data loaders for backtest inputs (signal_history + candles_1m)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import Bar, DateRange, SignalEvent

logger = logging.getLogger(__name__)


def normalize_symbol(symbol: str) -> str:
    """Normalize symbols for resilient matching."""
    return "".join(ch for ch in (symbol or "").upper() if ch.isalnum())


def parse_timestamp(raw: object) -> datetime | None:
    """Parse timestamps emitted by signal-history and PostgreSQL."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        text = text.replace("T", " ").replace("Z", "+00:00")

        tried = [text]
        # sqlite history often has microseconds; keep direct fromisoformat first.
        if len(text) >= 19 and text[10] == " ":
            tried.append(text[:19])

        dt = None
        for candidate in dict.fromkeys(tried):
            try:
                dt = datetime.fromisoformat(candidate)
                break
            except ValueError:
                continue
        if dt is None:
            return None

    if dt.tzinfo is None:
        # Service stores naive local-like timestamps; treat as UTC for deterministic backtests.
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_minute(dt: datetime) -> datetime:
    """Align a datetime to minute bucket."""
    return dt.replace(second=0, microsecond=0)


def resolve_range(date_range: DateRange, *, default_days: int = 90, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Resolve config date range; fallback to recent `default_days`."""

    now_dt = now or datetime.now(tz=timezone.utc)
    end = parse_timestamp(date_range.end) if date_range.end else now_dt
    if end is None:
        end = now_dt

    start = parse_timestamp(date_range.start) if date_range.start else None
    if start is None:
        start = end - timedelta(days=max(1, int(default_days)))

    if start >= end:
        raise ValueError(f"Invalid date range: start({start.isoformat()}) >= end({end.isoformat()})")

    return start, end


def load_signals_from_sqlite(
    db_path: str,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    *,
    timeframe: str = "1m",
) -> list[SignalEvent]:
    """Load historical signals from sqlite signal_history table."""

    symbol_set = {normalize_symbol(s) for s in symbols}
    tf_norm = (timeframe or "").strip().lower()
    events: list[SignalEvent] = []

    query = """
        SELECT id, timestamp, symbol, direction, strength, signal_type, timeframe, source, price
        FROM signal_history
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC, id ASC
    """

    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, (start.isoformat(), end.isoformat())).fetchall()

    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        if normalize_symbol(symbol) not in symbol_set:
            continue

        row_tf = str(row["timeframe"] or "").strip().lower()
        if tf_norm and row_tf and row_tf != tf_norm:
            continue

        ts = parse_timestamp(row["timestamp"])
        if ts is None:
            continue

        direction = str(row["direction"] or "").strip().upper()
        if direction not in {"BUY", "SELL"}:
            continue

        try:
            strength = int(row["strength"])
        except Exception:
            continue

        price = None
        if row["price"] is not None:
            try:
                price = float(row["price"])
            except Exception:
                price = None

        events.append(
            SignalEvent(
                event_id=int(row["id"]),
                timestamp=ts,
                symbol=symbol,
                direction=direction,
                strength=strength,
                signal_type=str(row["signal_type"] or ""),
                timeframe=str(row["timeframe"] or ""),
                source=str(row["source"] or "sqlite"),
                price=price,
            )
        )

    logger.info("Loaded %d signal rows from %s", len(events), db_path)
    return events


def load_candles_from_pg(
    database_url: str,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[Bar]]:
    """Load 1m candles from TimescaleDB and group by symbol."""

    import psycopg

    symbol_list = [str(s).upper().strip() for s in symbols if str(s).strip()]
    out: dict[str, list[Bar]] = {s: [] for s in symbol_list}

    if not symbol_list:
        return out

    query = """
        WITH ranked AS (
            SELECT
                symbol,
                bucket_ts,
                open,
                high,
                low,
                close,
                COALESCE(volume, 0) AS volume,
                ROW_NUMBER() OVER (PARTITION BY symbol, bucket_ts ORDER BY updated_at DESC NULLS LAST) AS rn
            FROM market_data.candles_1m
            WHERE symbol = ANY(%(symbols)s)
              AND bucket_ts >= %(start)s
              AND bucket_ts <= %(end)s
        )
        SELECT symbol, bucket_ts, open, high, low, close, volume
        FROM ranked
        WHERE rn = 1
        ORDER BY symbol ASC, bucket_ts ASC
    """

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                {
                    "symbols": symbol_list,
                    "start": start,
                    "end": end,
                },
            )
            rows = cur.fetchall()

    for symbol, bucket_ts, c_open, c_high, c_low, c_close, volume in rows:
        sym = str(symbol).upper()
        ts = parse_timestamp(bucket_ts)
        if ts is None:
            continue
        bar = Bar(
            symbol=sym,
            timestamp=ts,
            open=float(c_open),
            high=float(c_high),
            low=float(c_low),
            close=float(c_close),
            volume=float(volume or 0.0),
        )
        out.setdefault(sym, []).append(bar)

    for sym in symbol_list:
        out.setdefault(sym, [])

    total = sum(len(v) for v in out.values())
    logger.info("Loaded %d candle rows from PG", total)
    return out
