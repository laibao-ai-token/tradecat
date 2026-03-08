"""Execution engine for M1 backtest (next-open fills, fee/slippage)."""

from __future__ import annotations

from collections import deque
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


def _split_fill_prices(raw_price: float, side: str, slippage_bps: float, impact_bps: float) -> tuple[float, float]:
    slipped_price = _apply_slippage(raw_price, side, slippage_bps)
    final_price = _apply_slippage(slipped_price, side, impact_bps) if float(impact_bps) > 0 else slipped_price
    return slipped_price, final_price


def _slippage_model(execution: ExecutionConfig) -> str:
    return str(getattr(execution, "slippage_model", "fixed") or "fixed").strip().lower()


def _rolling_median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def _session_slippage_penalty(ts: datetime) -> float:
    hour = int(ts.hour)
    if 0 <= hour < 6:
        return 0.50
    if 6 <= hour < 12:
        return 0.20
    if 12 <= hour < 18:
        return 0.0
    return 0.10


def _resolve_slippage_bps(
    execution: ExecutionConfig,
    *,
    context_bar: Bar | None,
    fill_ts: datetime,
    volume_baseline: float | None,
) -> float:
    base_bps = max(0.0, float(getattr(execution, "slippage_bps", 0.0) or 0.0))
    if _slippage_model(execution) != "layered":
        return base_bps

    if context_bar is None:
        return base_bps

    ref_price = max(1e-9, abs(float(context_bar.open)) or abs(float(context_bar.close)) or 1.0)
    bar_range_ratio = max(0.0, (float(context_bar.high) - float(context_bar.low)) / ref_price)
    volatility_score = min(2.0, bar_range_ratio / 0.01)

    current_volume = max(0.0, float(getattr(context_bar, "volume", 0.0) or 0.0))
    if current_volume <= 0:
        low_liquidity_score = 2.0
    else:
        baseline = max(0.0, float(volume_baseline or 0.0))
        low_liquidity_score = min(2.0, max(0.0, (baseline / current_volume) - 1.0)) if baseline > 0 else 0.0

    session_score = _session_slippage_penalty(fill_ts)
    vol_weight = max(0.0, float(getattr(execution, "slippage_volatility_weight", 0.0) or 0.0))
    volume_weight = max(0.0, float(getattr(execution, "slippage_volume_weight", 0.0) or 0.0))
    session_weight = max(0.0, float(getattr(execution, "slippage_session_weight", 0.0) or 0.0))
    cap_bps = getattr(execution, "slippage_max_bps", None)
    resolved_cap = max(base_bps, float(cap_bps)) if cap_bps is not None else max(base_bps, base_bps * 3.0)

    layered_bps = base_bps * (
        1.0
        + (vol_weight * volatility_score)
        + (volume_weight * low_liquidity_score)
        + (session_weight * session_score)
    )
    return min(resolved_cap, max(base_bps, layered_bps))


def _calc_execution_cost(reference_price: float, filled_price: float, side: str, qty: float) -> float:
    quantity = max(0.0, float(qty))
    base = float(reference_price)
    filled = float(filled_price)
    if side == "BUY":
        return max(0.0, filled - base) * quantity
    return max(0.0, base - filled) * quantity


def _build_volume_baselines(
    bars_by_symbol: dict[str, list[Bar]],
    window: int,
) -> dict[str, dict[datetime, float]]:
    resolved_window = max(1, int(window))
    out: dict[str, dict[datetime, float]] = {}
    for symbol, bars in bars_by_symbol.items():
        history: deque[float] = deque(maxlen=resolved_window)
        symbol_map: dict[datetime, float] = {}
        for bar in sorted(bars, key=lambda row: row.timestamp):
            current_volume = max(0.0, float(bar.volume))
            baseline = _rolling_median(list(history)) if history else current_volume
            symbol_map[bar.timestamp] = float(baseline)
            history.append(current_volume)
        out[symbol] = symbol_map
    return out


def _max_bar_participation_rate(execution: ExecutionConfig) -> float:
    return max(0.0, min(1.0, float(getattr(execution, "max_bar_participation_rate", 1.0) or 0.0)))


def _min_order_notional(execution: ExecutionConfig) -> float:
    return max(0.0, float(getattr(execution, "min_order_notional", 0.0) or 0.0))


def _impact_bps_per_bar_participation(execution: ExecutionConfig) -> float:
    return max(0.0, float(getattr(execution, "impact_bps_per_bar_participation", 0.0) or 0.0))


def _execution_constraints_enabled(execution: ExecutionConfig) -> bool:
    return (
        float(getattr(execution, "max_bar_participation_rate", 1.0) or 0.0) < 0.999999
        or float(getattr(execution, "min_order_notional", 0.0) or 0.0) > 0.0
        or float(getattr(execution, "impact_bps_per_bar_participation", 0.0) or 0.0) > 0.0
    )


def _capacity_key(symbol: str, fill_bar: Bar) -> tuple[str, datetime]:
    return symbol, fill_bar.timestamp


def _fill_capacity_state(
    consumed_notional: dict[tuple[str, datetime], float],
    symbol: str,
    fill_bar: Bar | None,
    raw_price: float,
    execution: ExecutionConfig,
) -> tuple[float, float, float]:
    if fill_bar is None or raw_price <= 0:
        return 0.0, 0.0, 0.0
    total_bar_notional = max(0.0, float(fill_bar.volume)) * float(raw_price)
    capacity_notional = total_bar_notional * _max_bar_participation_rate(execution)
    key = _capacity_key(symbol, fill_bar)
    used_notional = max(0.0, float(consumed_notional.get(key, 0.0)))
    available_notional = max(0.0, capacity_notional - used_notional)
    return capacity_notional, available_notional, total_bar_notional


def _consume_fill_notional(
    consumed_notional: dict[tuple[str, datetime], float],
    symbol: str,
    fill_bar: Bar | None,
    fill_notional: float,
) -> None:
    if fill_bar is None or fill_notional <= 0:
        return
    key = _capacity_key(symbol, fill_bar)
    consumed_notional[key] = max(0.0, float(consumed_notional.get(key, 0.0))) + float(fill_notional)


def _position_unrealized(pos: Position, mark_price: float) -> float:
    if pos.side == "LONG":
        return (mark_price - pos.entry_price) * pos.qty
    return (pos.entry_price - mark_price) * pos.qty


def _maker_fee_rate(execution: ExecutionConfig) -> float:
    raw = execution.maker_fee_bps if execution.maker_fee_bps is not None else execution.fee_bps
    return max(0.0, float(raw)) / 10_000.0


def _taker_fee_rate(execution: ExecutionConfig) -> float:
    raw = execution.taker_fee_bps if execution.taker_fee_bps is not None else execution.fee_bps
    return max(0.0, float(raw)) / 10_000.0


def _calc_funding_fee(pos: Position, exit_ts: datetime, execution: ExecutionConfig) -> float:
    hours_held = max(0.0, (exit_ts - pos.entry_ts).total_seconds() / 3600.0)
    if hours_held <= 0:
        return 0.0

    funding_rate = float(getattr(execution, "funding_rate_bps_per_8h", 0.0) or 0.0) / 10_000.0
    if funding_rate == 0.0:
        return 0.0

    periods = hours_held / 8.0
    side_sign = 1.0 if pos.side == "LONG" else -1.0
    return float(pos.entry_notional * funding_rate * periods * side_sign)


def _calc_liquidation_price(entry_price: float, side: str, leverage: float, risk: RiskConfig) -> float:
    """Estimate first-pass liquidation threshold for a linear perpetual position."""

    if entry_price <= 0:
        return 0.0

    lev = max(1.0, float(leverage))
    mmr = max(0.0, float(getattr(risk, "maintenance_margin_ratio", 0.0) or 0.0))
    buffer_rate = max(0.0, float(getattr(risk, "liquidation_buffer_bps", 0.0) or 0.0)) / 10_000.0

    if side == "LONG":
        threshold = entry_price * (1.0 - (1.0 / lev) + mmr + buffer_rate)
    else:
        threshold = entry_price * (1.0 + (1.0 / lev) - mmr - buffer_rate)

    return max(0.0, float(threshold))


def _calc_bankruptcy_price(entry_price: float, side: str, leverage: float) -> float:
    if entry_price <= 0:
        return 0.0
    lev = max(1.0, float(leverage))
    if side == "LONG":
        return max(0.0, float(entry_price) * (1.0 - (1.0 / lev)))
    return max(0.0, float(entry_price) * (1.0 + (1.0 / lev)))


def _position_leverage(pos: Position) -> float:
    if pos.initial_margin <= 0:
        return 1.0
    return max(1.0, float(pos.entry_notional) / float(pos.initial_margin))


def _price_epsilon(price: float) -> float:
    return max(1e-9, abs(float(price)) * 1e-12)


def _bar_triggers_liquidation(pos: Position, bar: Bar, liquidation_price: float) -> bool:
    if liquidation_price <= 0:
        return False
    eps = _price_epsilon(liquidation_price)
    if pos.side == "LONG":
        return float(bar.low) <= liquidation_price + eps
    return float(bar.high) >= liquidation_price - eps


def _resolve_liquidation_fill(pos: Position, bar: Bar, liquidation_price: float) -> tuple[float, str]:
    """Choose a conservative fill price using only OHLC data."""

    bar_open = float(bar.open)
    eps = _price_epsilon(liquidation_price)
    bankruptcy_price = _calc_bankruptcy_price(pos.entry_price, pos.side, _position_leverage(pos))
    bankruptcy_eps = _price_epsilon(bankruptcy_price) if bankruptcy_price > 0 else 0.0
    if pos.side == "LONG":
        if bar_open > 0 and bar_open <= liquidation_price + eps:
            capped = max(bar_open, bankruptcy_price) if bankruptcy_price > 0 else bar_open
            if bankruptcy_price > 0 and capped > bar_open + bankruptcy_eps:
                return capped, "bar_open_gap_bankruptcy_cap"
            return capped, "bar_open_gap_liquidation"
    else:
        if bar_open > 0 and bar_open >= liquidation_price - eps:
            capped = min(bar_open, bankruptcy_price) if bankruptcy_price > 0 else bar_open
            if bankruptcy_price > 0 and capped < bar_open - bankruptcy_eps:
                return capped, "bar_open_gap_bankruptcy_cap"
            return capped, "bar_open_gap_liquidation"
    return liquidation_price, "binance_usdm_liquidation_threshold"


def _resolve_exit_kind(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if r == "liquidation":
        return "liquidation"
    if r.startswith("reverse_"):
        return "reverse"
    if r == "neutral_close":
        return "neutral_close"
    return "normal_close"


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
    volume_baselines = _build_volume_baselines(
        bars_by_symbol,
        max(1, int(getattr(execution, "slippage_volume_window", 20) or 20)),
    )

    equity = float(risk.initial_equity)
    positions: dict[str, Position] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    curve: list[EquityPoint] = []
    consumed_notional: dict[tuple[str, datetime], float] = {}

    taker_fee_rate = _taker_fee_rate(execution)
    liquidation_fee_rate = max(0.0, float(getattr(risk, "liquidation_fee_bps", 0.0) or 0.0)) / 10_000.0
    leverage = max(1.0, float(risk.leverage))
    pos_pct = max(0.0, min(1.0, float(risk.position_size_pct)))
    allow_long = bool(getattr(execution, "allow_long", True))
    allow_short = bool(getattr(execution, "allow_short", True))
    min_hold_minutes = max(0, int(getattr(execution, "min_hold_minutes", 0) or 0))
    neutral_confirm_minutes = max(1, int(getattr(execution, "neutral_confirm_minutes", 1) or 1))
    neutral_streak: dict[str, int] = {}
    resolved_slippage_model = _slippage_model(execution)
    min_order_notional = _min_order_notional(execution)
    impact_coeff = _impact_bps_per_bar_participation(execution)
    constraints_enabled = _execution_constraints_enabled(execution)

    def open_position(
        symbol: str,
        side: str,
        ts: datetime,
        next_bar: Bar,
        score: int,
        *,
        context_bar: Bar,
        volume_baseline: float,
    ) -> bool:
        nonlocal equity
        raw = float(next_bar.open)
        if raw <= 0:
            return False

        desired_notional = max(0.0, equity) * pos_pct * leverage
        if desired_notional <= 0:
            return False

        capacity_notional = 0.0
        total_bar_notional = 0.0
        fill_notional_raw = desired_notional
        if constraints_enabled:
            capacity_notional, available_notional, total_bar_notional = _fill_capacity_state(
                consumed_notional,
                symbol,
                next_bar,
                raw,
                execution,
            )
            fill_notional_raw = min(desired_notional, available_notional) if capacity_notional > 0 else desired_notional
            if fill_notional_raw <= 0 or fill_notional_raw < min_order_notional:
                return False
            _consume_fill_notional(consumed_notional, symbol, next_bar, fill_notional_raw)

        requested_qty = desired_notional / raw
        qty = fill_notional_raw / raw
        entry_side = "BUY" if side == "LONG" else "SELL"
        entry_slippage_bps = _resolve_slippage_bps(
            execution,
            context_bar=context_bar,
            fill_ts=next_bar.timestamp,
            volume_baseline=volume_baseline,
        )
        participation_rate = (fill_notional_raw / total_bar_notional) if (constraints_enabled and total_bar_notional > 0) else 0.0
        entry_impact_bps = impact_coeff * participation_rate if constraints_enabled else 0.0
        slipped_entry_price, entry_price = _split_fill_prices(raw, entry_side, entry_slippage_bps, entry_impact_bps)

        actual_entry_notional = qty * entry_price
        entry_fee = actual_entry_notional * taker_fee_rate
        entry_slippage_cost = _calc_execution_cost(raw, slipped_entry_price, entry_side, qty)
        entry_impact_cost = _calc_execution_cost(slipped_entry_price, entry_price, entry_side, qty)
        equity -= entry_fee
        positions[symbol] = Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_ts=next_bar.timestamp,
            entry_price=entry_price,
            entry_fee=entry_fee,
            entry_score=score,
            entry_notional=actual_entry_notional,
            initial_margin=(actual_entry_notional / leverage) if leverage > 0 else actual_entry_notional,
            liquidation_price=_calc_liquidation_price(entry_price, side, leverage, risk),
            entry_slippage_bps=float(entry_slippage_bps),
            entry_slippage_cost=float(entry_slippage_cost),
            entry_requested_qty=float(requested_qty),
            entry_fill_ratio=(float(qty) / float(requested_qty)) if requested_qty > 0 else 0.0,
            entry_capacity_notional=float(capacity_notional),
            entry_impact_bps=float(entry_impact_bps),
            entry_impact_cost=float(entry_impact_cost),
        )
        neutral_streak[symbol] = 0
        return True

    def close_position(
        symbol: str,
        ts: datetime,
        raw_exit_price: float,
        score: int,
        reason: str,
        *,
        exit_kind: str | None = None,
        exit_price_source: str = "next_open",
        liquidation_price: float = 0.0,
        apply_slippage: bool = True,
        liquidation_fee_rate_override: float | None = None,
        fill_bar: Bar | None = None,
        context_bar: Bar | None = None,
        volume_baseline: float = 0.0,
        enforce_constraints: bool = True,
    ) -> bool:
        nonlocal equity
        pos = positions.get(symbol)
        if pos is None:
            return True

        raw_price = float(raw_exit_price)
        if raw_price <= 0:
            return False

        requested_qty = float(pos.qty)
        fill_qty = float(pos.qty)
        exit_capacity_notional = 0.0
        participation_rate = 0.0
        exit_fill_ratio = 1.0

        if enforce_constraints and constraints_enabled and fill_bar is not None:
            desired_notional_raw = requested_qty * raw_price
            capacity_notional, available_notional, total_bar_notional = _fill_capacity_state(
                consumed_notional,
                symbol,
                fill_bar,
                raw_price,
                execution,
            )
            fill_notional_raw = min(desired_notional_raw, available_notional) if capacity_notional > 0 else desired_notional_raw
            if fill_notional_raw <= 0 or fill_notional_raw < min_order_notional:
                return False
            _consume_fill_notional(consumed_notional, symbol, fill_bar, fill_notional_raw)
            fill_qty = min(requested_qty, fill_notional_raw / raw_price)
            exit_capacity_notional = float(capacity_notional)
            participation_rate = (fill_notional_raw / total_bar_notional) if total_bar_notional > 0 else 0.0
            exit_fill_ratio = (float(fill_qty) / float(requested_qty)) if requested_qty > 0 else 1.0

        if fill_qty <= 0:
            return False

        exit_side = "SELL" if pos.side == "LONG" else "BUY"
        exit_slippage_bps = (
            _resolve_slippage_bps(
                execution,
                context_bar=context_bar,
                fill_ts=ts,
                volume_baseline=volume_baseline,
            )
            if apply_slippage
            else 0.0
        )
        exit_impact_bps = (impact_coeff * participation_rate) if (apply_slippage and enforce_constraints and constraints_enabled) else 0.0
        if apply_slippage:
            slipped_exit_price, exit_price = _split_fill_prices(raw_price, exit_side, exit_slippage_bps, exit_impact_bps)
        else:
            slipped_exit_price = raw_price
            exit_price = raw_price

        ratio = min(1.0, max(0.0, float(fill_qty) / float(pos.qty))) if pos.qty > 0 else 1.0
        allocated_entry_fee = float(pos.entry_fee) * ratio
        allocated_entry_slippage_cost = float(pos.entry_slippage_cost) * ratio
        allocated_entry_impact_cost = float(pos.entry_impact_cost) * ratio
        funding_fee = _calc_funding_fee(pos, ts, execution) * ratio

        if pos.side == "LONG":
            pnl_gross = (exit_price - pos.entry_price) * fill_qty
        else:
            pnl_gross = (pos.entry_price - exit_price) * fill_qty

        exit_notional = fill_qty * exit_price
        exit_fee = exit_notional * taker_fee_rate
        liquidation_fee = exit_notional * (
            liquidation_fee_rate if liquidation_fee_rate_override is None else max(0.0, liquidation_fee_rate_override)
        )
        exit_slippage_cost = _calc_execution_cost(raw_price, slipped_exit_price, exit_side, fill_qty) if apply_slippage else 0.0
        exit_impact_cost = _calc_execution_cost(slipped_exit_price, exit_price, exit_side, fill_qty) if apply_slippage else 0.0
        trading_fee = allocated_entry_fee + exit_fee + liquidation_fee
        pnl_net = pnl_gross - trading_fee - funding_fee

        equity += pnl_gross - exit_fee - liquidation_fee - funding_fee

        flags: list[str] = []
        if float(pos.entry_fill_ratio) < 0.999999:
            flags.append("entry_capped")
        if exit_fill_ratio < 0.999999:
            flags.append("exit_capped")
        if float(pos.entry_impact_bps) > 0 or float(exit_impact_bps) > 0:
            flags.append("impact")

        trades.append(
            Trade(
                symbol=symbol,
                side=pos.side,
                entry_ts=pos.entry_ts,
                exit_ts=ts,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                qty=fill_qty,
                entry_fee=allocated_entry_fee,
                exit_fee=exit_fee,
                pnl_gross=pnl_gross,
                pnl_net=pnl_net,
                entry_score=pos.entry_score,
                exit_score=score,
                reason=reason,
                exit_kind=str(exit_kind or _resolve_exit_kind(reason)),
                exit_price_source=str(exit_price_source or "--"),
                liquidation_price=float(liquidation_price or 0.0),
                liquidation_fee=float(liquidation_fee),
                funding_fee=float(funding_fee),
                trading_fee=float(trading_fee),
                slippage_model=resolved_slippage_model,
                partial_fill=(float(pos.entry_fill_ratio) < 0.999999) or (float(exit_fill_ratio) < 0.999999),
                constraint_flags=",".join(flags),
                entry_requested_qty=float(pos.entry_requested_qty or 0.0),
                exit_requested_qty=float(requested_qty),
                entry_fill_ratio=float(pos.entry_fill_ratio),
                exit_fill_ratio=float(exit_fill_ratio),
                entry_capacity_notional=float(pos.entry_capacity_notional),
                exit_capacity_notional=float(exit_capacity_notional),
                entry_slippage_bps=float(pos.entry_slippage_bps),
                exit_slippage_bps=float(exit_slippage_bps),
                entry_slippage_cost=float(allocated_entry_slippage_cost),
                exit_slippage_cost=float(exit_slippage_cost),
                entry_impact_bps=float(pos.entry_impact_bps),
                exit_impact_bps=float(exit_impact_bps),
                entry_impact_cost=float(allocated_entry_impact_cost),
                exit_impact_cost=float(exit_impact_cost),
            )
        )

        if ratio >= 1.0 - 1e-12:
            positions.pop(symbol, None)
        else:
            pos.qty = float(pos.qty) - float(fill_qty)
            pos.entry_fee = max(0.0, float(pos.entry_fee) - float(allocated_entry_fee))
            pos.entry_notional = max(0.0, float(pos.entry_notional) * (1.0 - ratio))
            pos.initial_margin = max(0.0, float(pos.initial_margin) * (1.0 - ratio))
            pos.entry_slippage_cost = max(0.0, float(pos.entry_slippage_cost) - float(allocated_entry_slippage_cost))
            pos.entry_impact_cost = max(0.0, float(pos.entry_impact_cost) - float(allocated_entry_impact_cost))

        neutral_streak[symbol] = 0
        return ratio >= 1.0 - 1e-12

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
            volume_baseline = float(volume_baselines.get(symbol, {}).get(ts, max(0.0, float(current_bar.volume))))

            if pos is not None and _bar_triggers_liquidation(pos, current_bar, pos.liquidation_price):
                fill_price, price_source = _resolve_liquidation_fill(pos, current_bar, pos.liquidation_price)
                close_position(
                    symbol,
                    current_bar.timestamp,
                    fill_price,
                    score,
                    reason="liquidation",
                    exit_kind="liquidation",
                    exit_price_source=price_source,
                    liquidation_price=pos.liquidation_price,
                    apply_slippage=False,
                    fill_bar=current_bar,
                    context_bar=current_bar,
                    enforce_constraints=False,
                )
                pos = None

            if pos is None:
                if not has_signal or next_bar is None:
                    continue
                if score >= aggregation.long_open_threshold and allow_long:
                    open_position(
                        symbol,
                        "LONG",
                        ts,
                        next_bar,
                        score,
                        context_bar=current_bar,
                        volume_baseline=volume_baseline,
                    )
                elif score <= -aggregation.short_open_threshold and allow_short:
                    open_position(
                        symbol,
                        "SHORT",
                        ts,
                        next_bar,
                        score,
                        context_bar=current_bar,
                        volume_baseline=volume_baseline,
                    )
                continue

            if not has_signal or next_bar is None:
                continue

            if pos.side == "LONG":
                if score <= -aggregation.short_open_threshold:
                    fully_closed = False
                    if allow_short:
                        fully_closed = close_position(
                            symbol,
                            next_bar.timestamp,
                            next_bar.open,
                            score,
                            reason="reverse_to_short",
                            fill_bar=next_bar,
                            context_bar=current_bar,
                            volume_baseline=volume_baseline,
                        )
                        if fully_closed:
                            open_position(
                                symbol,
                                "SHORT",
                                ts,
                                next_bar,
                                score,
                                context_bar=current_bar,
                                volume_baseline=volume_baseline,
                            )
                    else:
                        close_position(
                            symbol,
                            next_bar.timestamp,
                            next_bar.open,
                            score,
                            reason="exit_on_opposite",
                            fill_bar=next_bar,
                            context_bar=current_bar,
                            volume_baseline=volume_baseline,
                        )
                    neutral_streak[symbol] = 0
                elif abs(score) < aggregation.close_threshold:
                    if not can_neutral_close(symbol, next_bar.timestamp):
                        continue
                    neutral_streak[symbol] = neutral_streak.get(symbol, 0) + 1
                    if neutral_streak[symbol] >= neutral_confirm_minutes:
                        close_position(
                            symbol,
                            next_bar.timestamp,
                            next_bar.open,
                            score,
                            reason="neutral_close",
                            fill_bar=next_bar,
                            context_bar=current_bar,
                            volume_baseline=volume_baseline,
                        )
                else:
                    neutral_streak[symbol] = 0
            else:
                if score >= aggregation.long_open_threshold:
                    fully_closed = False
                    if allow_long:
                        fully_closed = close_position(
                            symbol,
                            next_bar.timestamp,
                            next_bar.open,
                            score,
                            reason="reverse_to_long",
                            fill_bar=next_bar,
                            context_bar=current_bar,
                            volume_baseline=volume_baseline,
                        )
                        if fully_closed:
                            open_position(
                                symbol,
                                "LONG",
                                ts,
                                next_bar,
                                score,
                                context_bar=current_bar,
                                volume_baseline=volume_baseline,
                            )
                    else:
                        close_position(
                            symbol,
                            next_bar.timestamp,
                            next_bar.open,
                            score,
                            reason="exit_on_opposite",
                            fill_bar=next_bar,
                            context_bar=current_bar,
                            volume_baseline=volume_baseline,
                        )
                    neutral_streak[symbol] = 0
                elif abs(score) < aggregation.close_threshold:
                    if not can_neutral_close(symbol, next_bar.timestamp):
                        continue
                    neutral_streak[symbol] = neutral_streak.get(symbol, 0) + 1
                    if neutral_streak[symbol] >= neutral_confirm_minutes:
                        close_position(
                            symbol,
                            next_bar.timestamp,
                            next_bar.open,
                            score,
                            reason="neutral_close",
                            fill_bar=next_bar,
                            context_bar=current_bar,
                            volume_baseline=volume_baseline,
                        )
                else:
                    neutral_streak[symbol] = 0

        mark_equity = equity
        for symbol, pos in positions.items():
            mark_price = last_close.get(symbol)
            if mark_price is None:
                continue
            mark_equity += _position_unrealized(pos, mark_price)
        curve.append(EquityPoint(timestamp=ts, equity=mark_equity))

    for symbol, pos in list(positions.items()):
        bars = bars_by_symbol.get(symbol, [])
        if not bars:
            continue
        last_bar = max(bars, key=lambda x: x.timestamp)
        close_position(
            symbol,
            last_bar.timestamp,
            float(last_bar.close),
            0,
            reason="eod_close",
            exit_kind="normal_close",
            exit_price_source="bar_close",
            fill_bar=last_bar,
            context_bar=last_bar,
            volume_baseline=float(volume_baselines.get(symbol, {}).get(last_bar.timestamp, max(0.0, float(last_bar.volume)))),
            enforce_constraints=False,
        )

    final_ts = timeline[-1] if timeline else datetime.utcnow()
    curve.append(EquityPoint(timestamp=final_ts, equity=equity))

    dedup: dict[datetime, EquityPoint] = {point.timestamp: point for point in curve}
    curve_sorted = [dedup[ts] for ts in sorted(dedup)]

    return ExecutionResult(trades=trades, equity_curve=curve_sorted, final_equity=equity)
