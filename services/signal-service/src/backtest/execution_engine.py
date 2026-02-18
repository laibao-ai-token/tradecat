"""Execution engine for M1 backtest (next-open fills, fee/slippage)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import AggregationConfig, Bar, EquityPoint, ExecutionConfig, Position, RiskConfig, Trade


@dataclass
class ExecutionResult:
    trades: list[Trade]
    equity_curve: list[EquityPoint]
    final_equity: float


def _apply_slippage(raw_price: float, side: str, slippage_bps: float) -> float:
    rate = max(0.0, float(slippage_bps)) / 10_000.0
    if side == "BUY":
        return raw_price * (1.0 + rate)
    return raw_price * (1.0 - rate)


def _position_unrealized(pos: Position, mark_price: float) -> float:
    if pos.side == "LONG":
        return (mark_price - pos.entry_price) * pos.qty
    return (pos.entry_price - mark_price) * pos.qty


def _build_bar_indices(bars_by_symbol: dict[str, list[Bar]]) -> tuple[dict[str, dict[datetime, Bar]], dict[str, dict[datetime, Bar]], list[datetime]]:
    bar_by_ts: dict[str, dict[datetime, Bar]] = {}
    next_bar_by_ts: dict[str, dict[datetime, Bar]] = {}
    timeline_set: set[datetime] = set()

    for symbol, bars in bars_by_symbol.items():
        if not bars:
            bar_by_ts[symbol] = {}
            next_bar_by_ts[symbol] = {}
            continue

        sorted_bars = sorted(bars, key=lambda x: x.timestamp)
        cur_map: dict[datetime, Bar] = {}
        next_map: dict[datetime, Bar] = {}
        for idx, bar in enumerate(sorted_bars):
            cur_map[bar.timestamp] = bar
            timeline_set.add(bar.timestamp)
            if idx + 1 < len(sorted_bars):
                next_map[bar.timestamp] = sorted_bars[idx + 1]
        bar_by_ts[symbol] = cur_map
        next_bar_by_ts[symbol] = next_map

    timeline = sorted(timeline_set)
    return bar_by_ts, next_bar_by_ts, timeline


def run_execution(
    bars_by_symbol: dict[str, list[Bar]],
    score_map: dict[str, dict[datetime, int]],
    execution: ExecutionConfig,
    risk: RiskConfig,
    aggregation: AggregationConfig,
) -> ExecutionResult:
    """Run simple position simulation using aggregated scores."""

    bar_by_ts, next_bar_by_ts, timeline = _build_bar_indices(bars_by_symbol)

    equity = float(risk.initial_equity)
    positions: dict[str, Position] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    curve: list[EquityPoint] = []

    fee_rate = max(0.0, float(execution.fee_bps)) / 10_000.0
    leverage = max(1.0, float(risk.leverage))
    pos_pct = max(0.0, min(1.0, float(risk.position_size_pct)))
    allow_long = bool(getattr(execution, "allow_long", True))
    allow_short = bool(getattr(execution, "allow_short", True))
    min_hold_minutes = max(0, int(getattr(execution, "min_hold_minutes", 0) or 0))
    neutral_confirm_minutes = max(1, int(getattr(execution, "neutral_confirm_minutes", 1) or 1))
    neutral_streak: dict[str, int] = {}

    def open_position(symbol: str, side: str, ts: datetime, next_bar: Bar, score: int) -> None:
        nonlocal equity
        raw = float(next_bar.open)
        entry_side = "BUY" if side == "LONG" else "SELL"
        entry_price = _apply_slippage(raw, entry_side, execution.slippage_bps)

        notional = max(0.0, equity) * pos_pct * leverage
        if notional <= 0 or entry_price <= 0:
            return

        qty = notional / entry_price
        entry_fee = notional * fee_rate
        equity -= entry_fee
        positions[symbol] = Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_ts=next_bar.timestamp,
            entry_price=entry_price,
            entry_fee=entry_fee,
            entry_score=score,
        )
        neutral_streak[symbol] = 0

    def close_position(symbol: str, ts: datetime, raw_exit_price: float, score: int, reason: str) -> None:
        nonlocal equity
        pos = positions.get(symbol)
        if pos is None:
            return

        exit_side = "SELL" if pos.side == "LONG" else "BUY"
        exit_price = _apply_slippage(raw_exit_price, exit_side, execution.slippage_bps)

        if pos.side == "LONG":
            pnl_gross = (exit_price - pos.entry_price) * pos.qty
        else:
            pnl_gross = (pos.entry_price - exit_price) * pos.qty

        exit_fee = (pos.qty * exit_price) * fee_rate
        pnl_net = pnl_gross - pos.entry_fee - exit_fee

        # Entry fee has been deducted when opening, so settle gross minus exit fee now.
        equity += pnl_gross - exit_fee

        trades.append(
            Trade(
                symbol=symbol,
                side=pos.side,
                entry_ts=pos.entry_ts,
                exit_ts=ts,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                qty=pos.qty,
                entry_fee=pos.entry_fee,
                exit_fee=exit_fee,
                pnl_gross=pnl_gross,
                pnl_net=pnl_net,
                entry_score=pos.entry_score,
                exit_score=score,
                reason=reason,
            )
        )
        positions.pop(symbol, None)
        neutral_streak[symbol] = 0

    def can_neutral_close(symbol: str, next_ts: datetime) -> bool:
        if min_hold_minutes <= 0:
            return True
        pos = positions.get(symbol)
        if pos is None:
            return False
        held = (next_ts - pos.entry_ts).total_seconds() / 60.0
        return held >= float(min_hold_minutes)

    for ts in timeline:
        for symbol in sorted(bar_by_ts):
            current_bar = bar_by_ts[symbol].get(ts)
            if current_bar is None:
                continue

            last_close[symbol] = current_bar.close
            next_bar = next_bar_by_ts[symbol].get(ts)
            score_raw = score_map.get(symbol, {}).get(ts)
            has_signal = score_raw is not None
            score = int(score_raw) if has_signal else 0
            pos = positions.get(symbol)

            if pos is None:
                # Event-driven: only open when we have a signal at this timestamp.
                if not has_signal or next_bar is None:
                    continue
                if score >= aggregation.long_open_threshold and allow_long:
                    open_position(symbol, "LONG", ts, next_bar, score)
                elif score <= -aggregation.short_open_threshold and allow_short:
                    open_position(symbol, "SHORT", ts, next_bar, score)
                continue

            # Only react (close/reverse) on new signal buckets; otherwise hold.
            if not has_signal or next_bar is None:
                continue

            if pos.side == "LONG":
                if score <= -aggregation.short_open_threshold:
                    if allow_short:
                        close_position(symbol, next_bar.timestamp, next_bar.open, score, reason="reverse_to_short")
                        open_position(symbol, "SHORT", ts, next_bar, score)
                    else:
                        close_position(symbol, next_bar.timestamp, next_bar.open, score, reason="exit_on_opposite")
                    neutral_streak[symbol] = 0
                elif abs(score) < aggregation.close_threshold:
                    if not can_neutral_close(symbol, next_bar.timestamp):
                        continue
                    neutral_streak[symbol] = neutral_streak.get(symbol, 0) + 1
                    if neutral_streak[symbol] >= neutral_confirm_minutes:
                        close_position(symbol, next_bar.timestamp, next_bar.open, score, reason="neutral_close")
                else:
                    neutral_streak[symbol] = 0
            else:
                if score >= aggregation.long_open_threshold:
                    if allow_long:
                        close_position(symbol, next_bar.timestamp, next_bar.open, score, reason="reverse_to_long")
                        open_position(symbol, "LONG", ts, next_bar, score)
                    else:
                        close_position(symbol, next_bar.timestamp, next_bar.open, score, reason="exit_on_opposite")
                    neutral_streak[symbol] = 0
                elif abs(score) < aggregation.close_threshold:
                    if not can_neutral_close(symbol, next_bar.timestamp):
                        continue
                    neutral_streak[symbol] = neutral_streak.get(symbol, 0) + 1
                    if neutral_streak[symbol] >= neutral_confirm_minutes:
                        close_position(symbol, next_bar.timestamp, next_bar.open, score, reason="neutral_close")
                else:
                    neutral_streak[symbol] = 0

        mark_equity = equity
        for symbol, pos in positions.items():
            mark_price = last_close.get(symbol)
            if mark_price is None:
                continue
            mark_equity += _position_unrealized(pos, mark_price)
        curve.append(EquityPoint(timestamp=ts, equity=mark_equity))

    # Force-close remaining positions at the last known close for each symbol.
    for symbol, pos in list(positions.items()):
        bars = bars_by_symbol.get(symbol, [])
        if not bars:
            continue
        last_bar = max(bars, key=lambda x: x.timestamp)
        close_position(symbol, last_bar.timestamp, float(last_bar.close), 0, reason="eod_close")

    final_ts = timeline[-1] if timeline else datetime.utcnow()
    curve.append(EquityPoint(timestamp=final_ts, equity=equity))

    # Keep monotonic order and coalesce duplicate timestamps with latest equity.
    dedup: dict[datetime, EquityPoint] = {point.timestamp: point for point in curve}
    curve_sorted = [dedup[ts] for ts in sorted(dedup)]

    return ExecutionResult(trades=trades, equity_curve=curve_sorted, final_equity=equity)
