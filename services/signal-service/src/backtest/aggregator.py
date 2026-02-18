"""Signal aggregation helpers for backtest."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .data_loader import floor_minute
from .models import SignalEvent


def _timeframe_to_minutes(timeframe: str) -> int:
    tf = str(timeframe or "").strip().lower().replace(" ", "")
    if not tf:
        return 1
    # Common forms: 1m/5m/15m, 1h/4h, 1d.
    try:
        if tf.endswith("m"):
            return max(1, int(tf[:-1]))
        if tf.endswith("h"):
            return max(1, int(tf[:-1]) * 60)
        if tf.endswith("d"):
            return max(1, int(tf[:-1]) * 60 * 24)
    except Exception:
        return 1
    return 1


def aggregate_signal_scores(signals: list[SignalEvent], *, timeframe: str = "1m") -> dict[str, dict[datetime, int]]:
    """Aggregate BUY/SELL strengths into net scores by minute.

    For timeframes > 1m, we forward-fill the bucket score across the timeframe window
    so execution logic does not immediately treat the next minute as neutral.
    """

    out: dict[str, dict[datetime, int]] = defaultdict(dict)

    for event in signals:
        symbol = event.symbol.upper().strip()
        if not symbol:
            continue

        bucket = floor_minute(event.timestamp)
        side = event.direction.upper().strip()
        delta = int(event.strength)
        if side == "BUY":
            pass
        elif side == "SELL":
            delta = -delta
        else:
            continue

        current = out[symbol].get(bucket, 0)
        out[symbol][bucket] = current + delta

    minutes = _timeframe_to_minutes(timeframe)
    if minutes <= 1:
        return dict(out)

    expanded: dict[str, dict[datetime, int]] = {}
    for symbol, buckets in out.items():
        if not buckets:
            continue
        times = sorted(buckets)
        filled: dict[datetime, int] = {}
        for idx, ts in enumerate(times):
            score = int(buckets[ts])
            end = ts + timedelta(minutes=minutes)
            if idx + 1 < len(times):
                end = min(end, times[idx + 1])
            cur = ts
            while cur < end:
                filled[cur] = score
                cur += timedelta(minutes=1)
        expanded[symbol] = filled

    return expanded
