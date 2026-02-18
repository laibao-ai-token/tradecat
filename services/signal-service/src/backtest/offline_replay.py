"""Offline signal replay helpers used to patch history_signal coverage gaps.

The goal is not to mimic all online rules yet; this module only builds a
stable, deterministic signal stream from candles so long windows can be
backtested when `signal_history` coverage is sparse.
"""

from __future__ import annotations

from typing import Iterable

from .models import Bar, SignalEvent


def _clamp_strength(value: float, low: int = 50, high: int = 95) -> int:
    return int(max(low, min(high, round(value))))


def _iter_symbol_bars(bars: Iterable[Bar]) -> list[Bar]:
    out = [bar for bar in bars]
    out.sort(key=lambda x: x.timestamp)
    return out


def replay_signals_from_bars(
    bars_by_symbol: dict[str, list[Bar]],
    *,
    timeframe: str = "1m",
    start_event_id: int = 1,
    min_signal_gap_bars: int = 3,
) -> list[SignalEvent]:
    """Generate pseudo signals from historical bars.

    Rules are intentionally simple and deterministic:
    - momentum jump up/down
    - close breakout above previous high / below previous low
    - directional volume spike confirmation
    """

    tf = str(timeframe or "").strip() or "1m"
    events: list[SignalEvent] = []
    event_id = max(1, int(start_event_id))

    gap = max(1, int(min_signal_gap_bars))

    for symbol in sorted(bars_by_symbol.keys()):
        bars = _iter_symbol_bars(bars_by_symbol.get(symbol) or [])
        if len(bars) < 2:
            continue

        last_emit_idx = -10_000
        last_emit_direction = ""

        for i in range(1, len(bars)):
            prev = bars[i - 1]
            curr = bars[i]
            if prev.close <= 0 or prev.high <= 0 or prev.low <= 0:
                continue

            change_pct = (curr.close - prev.close) / prev.close * 100.0
            breakout_pct = (curr.close - prev.high) / prev.high * 100.0
            breakdown_pct = (prev.low - curr.close) / prev.low * 100.0

            candidates: list[tuple[str, int, str]] = []

            if change_pct >= 0.12:
                candidates.append(
                    (
                        "BUY",
                        _clamp_strength(55.0 + change_pct * 120.0, low=55),
                        "replay_momentum_up",
                    )
                )
            elif change_pct <= -0.12:
                candidates.append(
                    (
                        "SELL",
                        _clamp_strength(55.0 + abs(change_pct) * 120.0, low=55),
                        "replay_momentum_down",
                    )
                )

            if breakout_pct >= 0.05:
                candidates.append(
                    (
                        "BUY",
                        _clamp_strength(60.0 + breakout_pct * 180.0, low=60),
                        "replay_breakout_up",
                    )
                )
            elif breakdown_pct >= 0.05:
                candidates.append(
                    (
                        "SELL",
                        _clamp_strength(60.0 + breakdown_pct * 180.0, low=60),
                        "replay_breakdown_down",
                    )
                )

            if prev.volume > 0:
                vol_ratio = curr.volume / prev.volume
                if vol_ratio >= 2.8 and change_pct >= 0.03:
                    candidates.append(
                        (
                            "BUY",
                            _clamp_strength(58.0 + vol_ratio * 8.0, low=58),
                            "replay_volume_follow_up",
                        )
                    )
                elif vol_ratio >= 2.8 and change_pct <= -0.03:
                    candidates.append(
                        (
                            "SELL",
                            _clamp_strength(58.0 + vol_ratio * 8.0, low=58),
                            "replay_volume_follow_down",
                        )
                    )

            if not candidates:
                continue

            direction, strength, signal_type = max(candidates, key=lambda x: x[1])
            bars_since_last = i - last_emit_idx
            if bars_since_last < gap and direction == last_emit_direction:
                continue
            if bars_since_last < max(1, gap // 2) and direction != last_emit_direction and strength < 80:
                continue

            events.append(
                SignalEvent(
                    event_id=event_id,
                    timestamp=curr.timestamp,
                    symbol=symbol,
                    direction=direction,
                    strength=int(strength),
                    signal_type=signal_type,
                    timeframe=tf,
                    source="offline_replay",
                    price=curr.close,
                )
            )
            event_id += 1
            last_emit_idx = i
            last_emit_direction = direction

    events.sort(key=lambda ev: (ev.timestamp, ev.symbol, ev.event_id))
    return events
