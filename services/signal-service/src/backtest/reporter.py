"""Artifact writers and metric calculators for backtest outputs."""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from ..rules import format_signal_display_key
from .models import Bar, EquityPoint, Metrics, SignalEvent, SymbolContribution, Trade
from .precheck import InputQualityReport, input_quality_to_payload
from .retention import collect_recent_comparable_metrics


def _fmt_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat(sep=" ")
    return dt.astimezone(timezone.utc).isoformat(sep=" ")


def _fmt_side(side: str) -> str:
    """Display label for trade direction in human-facing reports."""
    s = str(side or "").strip().upper()
    if s == "LONG":
        return "做多"
    if s == "SHORT":
        return "做空"
    return s or "--"


def _calc_max_drawdown_pct(curve: list[EquityPoint]) -> float:
    if not curve:
        return 0.0

    peak = curve[0].equity
    max_dd = 0.0
    for point in curve:
        peak = max(peak, point.equity)
        if peak <= 0:
            continue
        dd = (peak - point.equity) / peak
        max_dd = max(max_dd, dd)
    return max_dd * 100.0


def _calc_sharpe(curve: list[EquityPoint]) -> float:
    if len(curve) < 3:
        return 0.0

    rets: list[float] = []
    prev = curve[0].equity
    for point in curve[1:]:
        if prev > 0:
            rets.append((point.equity - prev) / prev)
        prev = point.equity

    if len(rets) < 2:
        return 0.0

    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    std = math.sqrt(max(var, 0.0))
    if std <= 1e-12:
        return 0.0

    # Minute returns annualization factor.
    annual_factor = math.sqrt(365.0 * 24.0 * 60.0)
    return (mean / std) * annual_factor


def _holding_minutes(trade: Trade) -> float:
    return max(0.0, (trade.exit_ts - trade.entry_ts).total_seconds() / 60.0)


def _calc_avg_holding_minutes(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    return sum(_holding_minutes(t) for t in trades) / len(trades)


def _build_symbol_contributions(trades: list[Trade]) -> list[SymbolContribution]:
    if not trades:
        return []

    stats: dict[str, dict[str, float]] = {}
    for trade in trades:
        symbol = str(trade.symbol or "").upper().strip()
        if not symbol:
            continue

        cur = stats.setdefault(
            symbol,
            {
                "pnl_net": 0.0,
                "trade_count": 0.0,
                "wins": 0.0,
                "holding_minutes": 0.0,
            },
        )
        cur["pnl_net"] += float(trade.pnl_net)
        cur["trade_count"] += 1.0
        if trade.pnl_net > 0:
            cur["wins"] += 1.0
        cur["holding_minutes"] += _holding_minutes(trade)

    rows: list[SymbolContribution] = []
    for symbol, cur in stats.items():
        trade_count = int(cur["trade_count"])
        win_rate_pct = (cur["wins"] / trade_count * 100.0) if trade_count > 0 else 0.0
        avg_holding_minutes = (cur["holding_minutes"] / trade_count) if trade_count > 0 else 0.0
        rows.append(
            SymbolContribution(
                symbol=symbol,
                pnl_net=float(cur["pnl_net"]),
                trade_count=trade_count,
                win_rate_pct=float(win_rate_pct),
                avg_holding_minutes=float(avg_holding_minutes),
            )
        )

    # Keep deterministic output for reporting/TUI: best pnl first, then symbol.
    rows.sort(key=lambda x: (-x.pnl_net, x.symbol))
    return rows


def _sorted_counter(counter: dict[str, int]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _build_signal_profile(signals: list[SignalEvent] | None) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    if not signals:
        return {}, {}, {}

    signal_type_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    timeframe_counts: dict[str, int] = {}

    for ev in signals:
        raw_signal_type = str(ev.rule_id or ev.signal_type or "UNKNOWN").strip() or "UNKNOWN"
        signal_type = format_signal_display_key(raw_signal_type) or raw_signal_type
        direction = str(ev.direction or "UNKNOWN").upper().strip() or "UNKNOWN"
        timeframe = str(ev.timeframe or "UNKNOWN").strip() or "UNKNOWN"

        signal_type_counts[signal_type] = signal_type_counts.get(signal_type, 0) + 1
        direction_counts[direction] = direction_counts.get(direction, 0) + 1
        timeframe_counts[timeframe] = timeframe_counts.get(timeframe, 0) + 1

    return (
        _sorted_counter(signal_type_counts),
        _sorted_counter(direction_counts),
        _sorted_counter(timeframe_counts),
    )


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(var, 0.0))


def _prepare_baseline_symbol_data(bars_by_symbol: dict[str, list[Bar]] | None) -> list[dict[str, object]]:
    if not bars_by_symbol:
        return []

    prepared: list[dict[str, object]] = []
    for symbol, bars in sorted(bars_by_symbol.items()):
        ordered = [bar for bar in sorted(bars, key=lambda x: x.timestamp) if float(bar.close) > 0]
        if len(ordered) < 2:
            continue

        first_close = float(ordered[0].close)
        last_close = float(ordered[-1].close)
        if first_close <= 0:
            continue

        close_map = {bar.timestamp: float(bar.close) for bar in ordered}
        timestamps = [bar.timestamp for bar in ordered]
        step_returns: list[float] = []
        prev_close = first_close
        for bar in ordered[1:]:
            close = float(bar.close)
            if prev_close > 0:
                step_returns.append((close - prev_close) / prev_close)
            prev_close = close

        prepared.append(
            {
                "symbol": symbol,
                "total_return": float((last_close - first_close) / first_close),
                "volatility": float(_stddev(step_returns)),
                "close_map": close_map,
                "timestamps": timestamps,
            }
        )

    return prepared


def _calc_buy_hold_baseline(
    prepared_rows: list[dict[str, object]],
    initial_equity: float,
) -> tuple[float, float]:
    initial = float(initial_equity)
    if initial <= 0:
        return 0.0, 0.0
    if not prepared_rows:
        return initial, 0.0

    avg_return = sum(float(row["total_return"]) for row in prepared_rows) / len(prepared_rows)
    final_equity = initial * (1.0 + avg_return)
    return float(final_equity), float(avg_return * 100.0)


def _calc_risk_parity_baseline(
    prepared_rows: list[dict[str, object]],
    initial_equity: float,
) -> tuple[float, float]:
    initial = float(initial_equity)
    if initial <= 0:
        return 0.0, 0.0
    if not prepared_rows:
        return initial, 0.0

    inv_vols = [1.0 / max(float(row["volatility"]), 1e-6) for row in prepared_rows]
    weight_sum = sum(inv_vols)
    if weight_sum <= 0:
        return _calc_buy_hold_baseline(prepared_rows, initial_equity)

    weighted_return = sum(
        (inv_vol / weight_sum) * float(row["total_return"]) for inv_vol, row in zip(inv_vols, prepared_rows)
    )
    final_equity = initial * (1.0 + weighted_return)
    return float(final_equity), float(weighted_return * 100.0)


def _calc_simple_momentum_baseline(
    prepared_rows: list[dict[str, object]],
    initial_equity: float,
    *,
    lookback_bars: int = 60,
) -> tuple[float, float]:
    initial = float(initial_equity)
    if initial <= 0:
        return 0.0, 0.0
    if not prepared_rows:
        return initial, 0.0

    timeline = sorted(
        {timestamp for row in prepared_rows for timestamp in (row.get("timestamps") or [])}
    )
    if len(timeline) < 2:
        return initial, 0.0

    lookback = min(max(1, int(lookback_bars)), len(timeline) - 1)
    equity = initial

    for idx in range(1, len(timeline)):
        prev_ts = timeline[idx - 1]
        current_ts = timeline[idx]
        lookback_ts = timeline[max(0, idx - lookback)]
        active_returns: list[float] = []

        for row in prepared_rows:
            close_map = row.get("close_map")
            if not isinstance(close_map, dict):
                continue
            prev_close = close_map.get(prev_ts)
            current_close = close_map.get(current_ts)
            lookback_close = close_map.get(lookback_ts)
            if prev_close is None or current_close is None or lookback_close is None:
                continue
            prev_close = float(prev_close)
            current_close = float(current_close)
            lookback_close = float(lookback_close)
            if prev_close <= 0 or lookback_close <= 0:
                continue
            momentum = (prev_close - lookback_close) / lookback_close
            if momentum > 0:
                active_returns.append((current_close - prev_close) / prev_close)

        if active_returns:
            equity *= 1.0 + (sum(active_returns) / len(active_returns))

    return float(equity), float((equity / initial - 1.0) * 100.0)


def _resolve_best_baseline(
    *,
    buy_hold_return_pct: float,
    risk_parity_return_pct: float,
    momentum_return_pct: float,
) -> tuple[str, float]:
    baselines = [
        ("buy_hold", float(buy_hold_return_pct)),
        ("risk_parity", float(risk_parity_return_pct)),
        ("momentum", float(momentum_return_pct)),
    ]
    return max(baselines, key=lambda item: item[1])



def _safe_pct(value: float, denominator: float) -> float:
    den = abs(float(denominator))
    if den <= 1e-12:
        return 0.0
    return float(value / den * 100.0)


def _build_cost_profile(
    *,
    initial_equity: float,
    gross_pnl: float,
    trading_fee: float,
    funding_fee: float,
    net_pnl: float,
) -> dict[str, float | str]:
    total_cost_impact = float(trading_fee + funding_fee)
    funding_credit = float(max(-funding_fee, 0.0))
    cost_drag_pct_of_initial = _safe_pct(total_cost_impact, float(initial_equity)) if float(initial_equity) > 0 else 0.0
    cost_erosion_pct_of_gross = _safe_pct(total_cost_impact, gross_pnl) if abs(float(gross_pnl)) > 1e-12 else 0.0
    if abs(float(gross_pnl)) <= 1e-12:
        gross_to_net_retention_pct = 0.0
    elif gross_pnl < 0 and net_pnl < 0:
        gross_to_net_retention_pct = _safe_pct(abs(net_pnl), gross_pnl)
    else:
        gross_to_net_retention_pct = _safe_pct(net_pnl, gross_pnl)

    if gross_pnl > 0:
        if net_pnl <= 0:
            status = "cost_flip_loss"
            summary = "Gross profit exists, but fees/funding flip the outcome to a net loss."
        elif total_cost_impact < 0:
            status = "funding_tailwind"
            summary = "Signal edge is amplified by favorable funding."
        elif gross_to_net_retention_pct >= 80.0:
            status = "signal_driven"
            summary = "Most gross profit survives costs; PnL is mainly signal-driven."
        elif gross_to_net_retention_pct >= 50.0:
            status = "cost_eroded"
            summary = "Signal edge survives, but costs eat a meaningful share of gross profit."
        else:
            status = "cost_heavy"
            summary = "Signal edge is heavily eroded by costs."
    elif gross_pnl < 0:
        if total_cost_impact < 0:
            status = "loss_offset_by_funding"
            summary = "Strategy loses before cost, but favorable funding offsets part of the loss."
        else:
            status = "signal_loss"
            summary = "Strategy loses before cost, and costs worsen the final result."
    else:
        if total_cost_impact < 0:
            status = "funding_tailwind_flat"
            summary = "Strategy is flat before costs; favorable funding creates a net gain."
        elif total_cost_impact > 0:
            status = "flat_before_cost"
            summary = "Strategy is flat before costs; fees/funding create the net loss."
        else:
            status = "flat"
            summary = "Strategy is flat before and after costs."

    return {
        "total_cost_impact": float(total_cost_impact),
        "funding_credit": float(funding_credit),
        "cost_drag_pct_of_initial": float(cost_drag_pct_of_initial),
        "cost_erosion_pct_of_gross": float(cost_erosion_pct_of_gross),
        "gross_to_net_retention_pct": float(gross_to_net_retention_pct),
        "cost_status": str(status),
        "cost_summary": str(summary),
    }


def build_metrics(
    *,
    run_id: str,
    mode: str,
    start: datetime,
    end: datetime,
    symbols: list[str],
    timeframe: str,
    initial_equity: float,
    final_equity: float,
    trades: list[Trade],
    curve: list[EquityPoint],
    signal_count: int,
    bar_count: int,
    bars_by_symbol: dict[str, list[Bar]] | None = None,
    signals: list[SignalEvent] | None = None,
    strategy_label: str = "",
    strategy_config_path: str = "",
    strategy_summary: str = "",
    strategy_context: dict[str, object] | None = None,
) -> Metrics:
    """Build summarized metrics from raw outputs."""

    total_return_pct = ((final_equity / initial_equity) - 1.0) * 100.0 if initial_equity > 0 else 0.0
    max_dd = _calc_max_drawdown_pct(curve)
    sharpe = _calc_sharpe(curve)
    gross_pnl = sum(float(t.pnl_gross) for t in trades)
    trading_fee = sum(float(getattr(t, "trading_fee", t.entry_fee + t.exit_fee + getattr(t, "liquidation_fee", 0.0))) for t in trades)
    funding_fee = sum(float(getattr(t, "funding_fee", 0.0)) for t in trades)
    slippage_cost = sum(
        float(getattr(t, "entry_slippage_cost", 0.0)) + float(getattr(t, "exit_slippage_cost", 0.0)) for t in trades
    )
    impact_cost = sum(
        float(getattr(t, "entry_impact_cost", 0.0)) + float(getattr(t, "exit_impact_cost", 0.0)) for t in trades
    )
    partial_fill_trade_count = sum(1 for t in trades if bool(getattr(t, "partial_fill", False)))
    net_pnl = sum(float(t.pnl_net) for t in trades)
    cost_profile = _build_cost_profile(
        initial_equity=float(initial_equity),
        gross_pnl=float(gross_pnl),
        trading_fee=float(trading_fee),
        funding_fee=float(funding_fee),
        net_pnl=float(net_pnl),
    )

    wins = [t for t in trades if t.pnl_net > 0]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0

    gain = sum(t.pnl_net for t in trades if t.pnl_net > 0)
    loss = -sum(t.pnl_net for t in trades if t.pnl_net < 0)
    profit_factor = (gain / loss) if loss > 0 else (999.0 if gain > 0 else 0.0)

    avg_holding_minutes = _calc_avg_holding_minutes(trades)
    symbol_contributions = _build_symbol_contributions(trades)
    signal_type_counts, direction_counts, timeframe_counts = _build_signal_profile(signals)
    prepared_baselines = _prepare_baseline_symbol_data(bars_by_symbol)
    buy_hold_final_equity, buy_hold_return_pct = _calc_buy_hold_baseline(prepared_baselines, initial_equity)
    risk_parity_final_equity, risk_parity_return_pct = _calc_risk_parity_baseline(prepared_baselines, initial_equity)
    momentum_final_equity, momentum_return_pct = _calc_simple_momentum_baseline(prepared_baselines, initial_equity)
    excess_return_pct = total_return_pct - buy_hold_return_pct
    excess_return_vs_risk_parity_pct = total_return_pct - risk_parity_return_pct
    excess_return_vs_momentum_pct = total_return_pct - momentum_return_pct
    best_baseline_name, best_baseline_return_pct = _resolve_best_baseline(
        buy_hold_return_pct=buy_hold_return_pct,
        risk_parity_return_pct=risk_parity_return_pct,
        momentum_return_pct=momentum_return_pct,
    )

    return Metrics(
        run_id=run_id,
        mode=mode,
        start=_fmt_ts(start),
        end=_fmt_ts(end),
        symbols=list(symbols),
        timeframe=timeframe,
        initial_equity=float(initial_equity),
        final_equity=float(final_equity),
        total_return_pct=float(total_return_pct),
        max_drawdown_pct=float(max_dd),
        sharpe=float(sharpe),
        trade_count=len(trades),
        win_rate_pct=float(win_rate),
        profit_factor=float(profit_factor),
        avg_holding_minutes=float(avg_holding_minutes),
        signal_count=int(signal_count),
        bar_count=int(bar_count),
        gross_pnl=float(gross_pnl),
        trading_fee=float(trading_fee),
        funding_fee=float(funding_fee),
        net_pnl=float(net_pnl),
        total_cost_impact=float(cost_profile["total_cost_impact"]),
        funding_credit=float(cost_profile["funding_credit"]),
        cost_drag_pct_of_initial=float(cost_profile["cost_drag_pct_of_initial"]),
        cost_erosion_pct_of_gross=float(cost_profile["cost_erosion_pct_of_gross"]),
        gross_to_net_retention_pct=float(cost_profile["gross_to_net_retention_pct"]),
        cost_status=str(cost_profile["cost_status"]),
        cost_summary=str(cost_profile["cost_summary"]),
        slippage_cost=float(slippage_cost),
        slippage_cost_pct_of_initial=(float(slippage_cost) / float(initial_equity) * 100.0) if initial_equity > 0 else 0.0,
        impact_cost=float(impact_cost),
        impact_cost_pct_of_initial=(float(impact_cost) / float(initial_equity) * 100.0) if initial_equity > 0 else 0.0,
        partial_fill_trade_count=int(partial_fill_trade_count),
        buy_hold_final_equity=float(buy_hold_final_equity),
        buy_hold_return_pct=float(buy_hold_return_pct),
        risk_parity_final_equity=float(risk_parity_final_equity),
        risk_parity_return_pct=float(risk_parity_return_pct),
        momentum_final_equity=float(momentum_final_equity),
        momentum_return_pct=float(momentum_return_pct),
        excess_return_pct=float(excess_return_pct),
        excess_return_vs_risk_parity_pct=float(excess_return_vs_risk_parity_pct),
        excess_return_vs_momentum_pct=float(excess_return_vs_momentum_pct),
        best_baseline_name=str(best_baseline_name),
        best_baseline_return_pct=float(best_baseline_return_pct),
        symbol_contributions=symbol_contributions,
        signal_type_counts=signal_type_counts,
        direction_counts=direction_counts,
        timeframe_counts=timeframe_counts,
        strategy_label=str(strategy_label or ""),
        strategy_config_path=str(strategy_config_path or ""),
        strategy_summary=str(strategy_summary or ""),
        strategy_context=dict(strategy_context or {}),
    )


def _write_trades_csv(path: Path, trades: list[Trade]) -> None:
    fieldnames = [
        "symbol",
        "side",
        "entry_ts",
        "exit_ts",
        "entry_price",
        "exit_price",
        "qty",
        "partial_fill",
        "constraint_flags",
        "entry_requested_qty",
        "exit_requested_qty",
        "entry_fill_ratio",
        "exit_fill_ratio",
        "entry_capacity_notional",
        "exit_capacity_notional",
        "slippage_model",
        "entry_slippage_bps",
        "exit_slippage_bps",
        "entry_slippage_cost",
        "exit_slippage_cost",
        "entry_impact_bps",
        "exit_impact_bps",
        "entry_impact_cost",
        "exit_impact_cost",
        "entry_fee",
        "exit_fee",
        "liquidation_fee",
        "liquidation_price",
        "trading_fee",
        "funding_fee",
        "pnl_gross",
        "pnl_net",
        "entry_score",
        "exit_score",
        "reason",
        "exit_kind",
        "exit_price_source",
    ]

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_ts": _fmt_ts(t.entry_ts),
                    "exit_ts": _fmt_ts(t.exit_ts),
                    "entry_price": f"{t.entry_price:.8f}",
                    "exit_price": f"{t.exit_price:.8f}",
                    "qty": f"{t.qty:.8f}",
                    "partial_fill": str(bool(getattr(t, "partial_fill", False))).lower(),
                    "constraint_flags": str(getattr(t, "constraint_flags", "") or ""),
                    "entry_requested_qty": f"{float(getattr(t, 'entry_requested_qty', 0.0)):.8f}",
                    "exit_requested_qty": f"{float(getattr(t, 'exit_requested_qty', 0.0)):.8f}",
                    "entry_fill_ratio": f"{float(getattr(t, 'entry_fill_ratio', 1.0)):.6f}",
                    "exit_fill_ratio": f"{float(getattr(t, 'exit_fill_ratio', 1.0)):.6f}",
                    "entry_capacity_notional": f"{float(getattr(t, 'entry_capacity_notional', 0.0)):.8f}",
                    "exit_capacity_notional": f"{float(getattr(t, 'exit_capacity_notional', 0.0)):.8f}",
                    "slippage_model": str(getattr(t, "slippage_model", "") or "fixed"),
                    "entry_slippage_bps": f"{float(getattr(t, 'entry_slippage_bps', 0.0)):.4f}",
                    "exit_slippage_bps": f"{float(getattr(t, 'exit_slippage_bps', 0.0)):.4f}",
                    "entry_slippage_cost": f"{float(getattr(t, 'entry_slippage_cost', 0.0)):.8f}",
                    "exit_slippage_cost": f"{float(getattr(t, 'exit_slippage_cost', 0.0)):.8f}",
                    "entry_impact_bps": f"{float(getattr(t, 'entry_impact_bps', 0.0)):.4f}",
                    "exit_impact_bps": f"{float(getattr(t, 'exit_impact_bps', 0.0)):.4f}",
                    "entry_impact_cost": f"{float(getattr(t, 'entry_impact_cost', 0.0)):.8f}",
                    "exit_impact_cost": f"{float(getattr(t, 'exit_impact_cost', 0.0)):.8f}",
                    "entry_fee": f"{t.entry_fee:.8f}",
                    "exit_fee": f"{t.exit_fee:.8f}",
                    "liquidation_fee": f"{t.liquidation_fee:.8f}",
                    "liquidation_price": f"{t.liquidation_price:.8f}" if t.liquidation_price > 0 else "",
                    "trading_fee": f"{t.trading_fee:.8f}",
                    "funding_fee": f"{t.funding_fee:.8f}",
                    "pnl_gross": f"{t.pnl_gross:.8f}",
                    "pnl_net": f"{t.pnl_net:.8f}",
                    "entry_score": t.entry_score,
                    "exit_score": t.exit_score,
                    "reason": t.reason,
                    "exit_kind": t.exit_kind,
                    "exit_price_source": t.exit_price_source,
                }
            )


def _write_curve_csv(path: Path, curve: list[EquityPoint]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamp", "equity"])
        writer.writeheader()
        for point in curve:
            writer.writerow({"timestamp": _fmt_ts(point.timestamp), "equity": f"{point.equity:.8f}"})


def _write_metrics_json(path: Path, metrics: Metrics) -> None:
    payload = {
        "run_id": metrics.run_id,
        "mode": metrics.mode,
        "start": metrics.start,
        "end": metrics.end,
        "symbols": metrics.symbols,
        "timeframe": metrics.timeframe,
        "initial_equity": metrics.initial_equity,
        "final_equity": metrics.final_equity,
        "total_return_pct": metrics.total_return_pct,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "sharpe": metrics.sharpe,
        "trade_count": metrics.trade_count,
        "win_rate_pct": metrics.win_rate_pct,
        "profit_factor": metrics.profit_factor,
        "avg_holding_minutes": metrics.avg_holding_minutes,
        "signal_count": metrics.signal_count,
        "bar_count": metrics.bar_count,
        "gross_pnl": metrics.gross_pnl,
        "trading_fee": metrics.trading_fee,
        "funding_fee": metrics.funding_fee,
        "net_pnl": metrics.net_pnl,
        "total_cost_impact": metrics.total_cost_impact,
        "funding_credit": metrics.funding_credit,
        "cost_drag_pct_of_initial": metrics.cost_drag_pct_of_initial,
        "cost_erosion_pct_of_gross": metrics.cost_erosion_pct_of_gross,
        "gross_to_net_retention_pct": metrics.gross_to_net_retention_pct,
        "cost_status": metrics.cost_status,
        "cost_summary": metrics.cost_summary,
        "slippage_cost": metrics.slippage_cost,
        "slippage_cost_pct_of_initial": metrics.slippage_cost_pct_of_initial,
        "impact_cost": metrics.impact_cost,
        "impact_cost_pct_of_initial": metrics.impact_cost_pct_of_initial,
        "partial_fill_trade_count": metrics.partial_fill_trade_count,
        "buy_hold_final_equity": metrics.buy_hold_final_equity,
        "buy_hold_return_pct": metrics.buy_hold_return_pct,
        "risk_parity_final_equity": metrics.risk_parity_final_equity,
        "risk_parity_return_pct": metrics.risk_parity_return_pct,
        "momentum_final_equity": metrics.momentum_final_equity,
        "momentum_return_pct": metrics.momentum_return_pct,
        "excess_return_pct": metrics.excess_return_pct,
        "excess_return_vs_risk_parity_pct": metrics.excess_return_vs_risk_parity_pct,
        "excess_return_vs_momentum_pct": metrics.excess_return_vs_momentum_pct,
        "best_baseline_name": metrics.best_baseline_name,
        "best_baseline_return_pct": metrics.best_baseline_return_pct,
        "signal_type_counts": metrics.signal_type_counts,
        "direction_counts": metrics.direction_counts,
        "timeframe_counts": metrics.timeframe_counts,
        "strategy_label": metrics.strategy_label,
        "strategy_config_path": metrics.strategy_config_path,
        "strategy_summary": metrics.strategy_summary,
        "strategy_context": metrics.strategy_context,
        "symbol_contributions": [
            {
                "symbol": row.symbol,
                "pnl_net": row.pnl_net,
                "trade_count": row.trade_count,
                "win_rate_pct": row.win_rate_pct,
                "avg_holding_minutes": row.avg_holding_minutes,
            }
            for row in metrics.symbol_contributions
        ],
        "generated_at": _fmt_ts(datetime.now(tz=timezone.utc)),
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def _coerce_float(raw: object) -> float:
    try:
        return float(raw)
    except Exception:
        return 0.0


def _coerce_int(raw: object) -> int:
    try:
        return int(raw)
    except Exception:
        return 0


def _relative_delta_pct(current: float, baseline: float) -> float:
    base = abs(float(baseline))
    if base <= 1e-12:
        return 0.0 if abs(float(current)) <= 1e-12 else 100.0
    return float((float(current) - float(baseline)) / base * 100.0)


def _append_stability_warning(
    warnings: list[dict[str, object]],
    *,
    kind: str,
    severity: str,
    message: str,
    current: float | int,
    baseline: float | int,
) -> None:
    warnings.append(
        {
            "kind": kind,
            "severity": severity,
            "message": message,
            "current": float(current) if isinstance(current, float) or isinstance(baseline, float) else int(current),
            "baseline": float(baseline) if isinstance(current, float) or isinstance(baseline, float) else int(baseline),
        }
    )


def _build_stability_report(
    output_dir: Path,
    metrics: Metrics,
    *,
    backtest_root: Path | None = None,
    lookback: int = 5,
) -> dict[str, object]:
    history_root = Path(backtest_root) if backtest_root is not None else output_dir.parent
    history_rows = collect_recent_comparable_metrics(
        history_root,
        current_metrics=metrics,
        exclude_run_dir=output_dir,
        limit=lookback,
    )

    current_payload = {
        "run_id": metrics.run_id,
        "total_return_pct": float(metrics.total_return_pct),
        "max_drawdown_pct": float(metrics.max_drawdown_pct),
        "sharpe": float(metrics.sharpe),
        "win_rate_pct": float(metrics.win_rate_pct),
        "excess_return_pct": float(metrics.excess_return_pct),
        "trade_count": int(metrics.trade_count),
        "signal_count": int(metrics.signal_count),
    }

    if not history_rows:
        return {
            "run_id": metrics.run_id,
            "generated_at": _fmt_ts(datetime.now(tz=timezone.utc)),
            "mode": metrics.mode,
            "start": metrics.start,
            "end": metrics.end,
            "symbols": list(metrics.symbols),
            "timeframe": metrics.timeframe,
            "lookback_runs": int(lookback),
            "comparable_run_count": 0,
            "stability_status": "insufficient_history",
            "stability_summary": "Need at least one prior comparable run to measure cross-run stability.",
            "current": current_payload,
            "baseline": {},
            "drift": {},
            "warnings": [],
            "recent_runs": [],
        }

    baseline = {
        "total_return_pct": _median([_coerce_float(row.get("total_return_pct")) for row in history_rows]),
        "max_drawdown_pct": _median([_coerce_float(row.get("max_drawdown_pct")) for row in history_rows]),
        "sharpe": _median([_coerce_float(row.get("sharpe")) for row in history_rows]),
        "win_rate_pct": _median([_coerce_float(row.get("win_rate_pct")) for row in history_rows]),
        "excess_return_pct": _median([_coerce_float(row.get("excess_return_pct")) for row in history_rows]),
        "trade_count": _median([_coerce_int(row.get("trade_count")) for row in history_rows]),
        "signal_count": _median([_coerce_int(row.get("signal_count")) for row in history_rows]),
    }

    drift = {
        "total_return_pct_delta": float(current_payload["total_return_pct"] - baseline["total_return_pct"]),
        "max_drawdown_pct_delta": float(current_payload["max_drawdown_pct"] - baseline["max_drawdown_pct"]),
        "sharpe_delta": float(current_payload["sharpe"] - baseline["sharpe"]),
        "win_rate_pct_delta": float(current_payload["win_rate_pct"] - baseline["win_rate_pct"]),
        "excess_return_pct_delta": float(current_payload["excess_return_pct"] - baseline["excess_return_pct"]),
        "trade_count_delta_pct": float(_relative_delta_pct(current_payload["trade_count"], baseline["trade_count"])),
        "signal_count_delta_pct": float(_relative_delta_pct(current_payload["signal_count"], baseline["signal_count"])),
    }

    warnings: list[dict[str, object]] = []
    return_drop_threshold = max(5.0, abs(float(baseline["total_return_pct"])) * 0.5)
    excess_drop_threshold = max(5.0, abs(float(baseline["excess_return_pct"])) * 0.5)
    sharpe_drop_threshold = max(0.5, abs(float(baseline["sharpe"])) * 0.5)
    drawdown_expand_threshold = max(3.0, abs(float(baseline["max_drawdown_pct"])) * 0.5)

    if float(drift["total_return_pct_delta"]) <= -return_drop_threshold:
        _append_stability_warning(
            warnings,
            kind="return_collapse",
            severity="error",
            message="Current total return is materially below the recent comparable baseline.",
            current=float(current_payload["total_return_pct"]),
            baseline=float(baseline["total_return_pct"]),
        )
    if float(drift["excess_return_pct_delta"]) <= -excess_drop_threshold:
        _append_stability_warning(
            warnings,
            kind="excess_return_collapse",
            severity="error",
            message="Excess return vs buy-and-hold collapsed versus recent comparable runs.",
            current=float(current_payload["excess_return_pct"]),
            baseline=float(baseline["excess_return_pct"]),
        )
    if float(drift["sharpe_delta"]) <= -sharpe_drop_threshold:
        _append_stability_warning(
            warnings,
            kind="sharpe_collapse",
            severity="warn",
            message="Risk-adjusted return is materially weaker than recent comparable runs.",
            current=float(current_payload["sharpe"]),
            baseline=float(baseline["sharpe"]),
        )
    if float(drift["max_drawdown_pct_delta"]) >= drawdown_expand_threshold:
        _append_stability_warning(
            warnings,
            kind="drawdown_expansion",
            severity="warn",
            message="Drawdown expanded materially versus recent comparable runs.",
            current=float(current_payload["max_drawdown_pct"]),
            baseline=float(baseline["max_drawdown_pct"]),
        )
    if abs(float(drift["win_rate_pct_delta"])) >= 10.0:
        _append_stability_warning(
            warnings,
            kind="win_rate_drift",
            severity="warn",
            message="Win-rate drift exceeds 10 percentage points versus baseline.",
            current=float(current_payload["win_rate_pct"]),
            baseline=float(baseline["win_rate_pct"]),
        )
    if abs(float(drift["trade_count_delta_pct"])) >= 50.0:
        _append_stability_warning(
            warnings,
            kind="trade_count_drift",
            severity="warn",
            message="Trade-count drift exceeds 50% versus baseline.",
            current=int(current_payload["trade_count"]),
            baseline=float(baseline["trade_count"]),
        )
    if abs(float(drift["signal_count_delta_pct"])) >= 50.0:
        _append_stability_warning(
            warnings,
            kind="signal_count_drift",
            severity="warn",
            message="Signal-count drift exceeds 50% versus baseline.",
            current=int(current_payload["signal_count"]),
            baseline=float(baseline["signal_count"]),
        )

    error_count = sum(1 for row in warnings if str(row.get("severity")) == "error")
    warn_count = sum(1 for row in warnings if str(row.get("severity")) == "warn")
    if error_count > 0:
        status = "critical"
        summary = "Performance collapsed versus recent comparable runs; overfit risk is high."
    elif warn_count > 0:
        status = "warn"
        summary = "Cross-run drift is visible; review stability warnings before trusting this result."
    else:
        status = "stable"
        summary = "Recent comparable runs look stable; no major drift was detected."

    recent_runs = [
        {
            "run_id": str(row.get("run_id") or ""),
            "artifact_dir": str(row.get("artifact_dir") or ""),
            "generated_at": str(row.get("generated_at") or ""),
            "total_return_pct": _coerce_float(row.get("total_return_pct")),
            "max_drawdown_pct": _coerce_float(row.get("max_drawdown_pct")),
            "sharpe": _coerce_float(row.get("sharpe")),
            "win_rate_pct": _coerce_float(row.get("win_rate_pct")),
            "excess_return_pct": _coerce_float(row.get("excess_return_pct")),
            "trade_count": _coerce_int(row.get("trade_count")),
            "signal_count": _coerce_int(row.get("signal_count")),
        }
        for row in history_rows
    ]

    return {
        "run_id": metrics.run_id,
        "generated_at": _fmt_ts(datetime.now(tz=timezone.utc)),
        "mode": metrics.mode,
        "start": metrics.start,
        "end": metrics.end,
        "symbols": list(metrics.symbols),
        "timeframe": metrics.timeframe,
        "lookback_runs": int(lookback),
        "comparable_run_count": len(history_rows),
        "stability_status": status,
        "stability_summary": summary,
        "current": current_payload,
        "baseline": baseline,
        "drift": drift,
        "warnings": warnings,
        "recent_runs": recent_runs,
    }


def _render_stability_markdown(report: dict[str, object]) -> str:
    status = str(report.get("stability_status") or "unknown").upper()
    summary = str(report.get("stability_summary") or "")
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    drift = report.get("drift") if isinstance(report.get("drift"), dict) else {}
    recent_runs = report.get("recent_runs") if isinstance(report.get("recent_runs"), list) else []
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []

    lines = [
        "# Stability Report",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- status: `{status}`",
        f"- summary: `{summary}`",
        f"- comparable_runs: `{int(report.get('comparable_run_count') or 0)}`",
        "",
    ]

    if baseline:
        current = report.get("current") if isinstance(report.get("current"), dict) else {}
        lines.extend(
            [
                "## Baseline vs Current",
                "",
                f"- Return: current `{float(current.get('total_return_pct', 0.0)):+.2f}%` vs baseline `{float(baseline.get('total_return_pct', 0.0)):+.2f}%`",
                f"- Excess Return: current `{float(current.get('excess_return_pct', 0.0)):+.2f}%` vs baseline `{float(baseline.get('excess_return_pct', 0.0)):+.2f}%`",
                f"- Max Drawdown: current `{float(current.get('max_drawdown_pct', 0.0)):.2f}%` vs baseline `{float(baseline.get('max_drawdown_pct', 0.0)):.2f}%`",
                f"- Sharpe: current `{float(current.get('sharpe', 0.0)):.2f}` vs baseline `{float(baseline.get('sharpe', 0.0)):.2f}`",
                f"- Win Rate: current `{float(current.get('win_rate_pct', 0.0)):.2f}%` vs baseline `{float(baseline.get('win_rate_pct', 0.0)):.2f}%`",
                "",
                "## Drift",
                "",
                f"- Return Delta: `{float(drift.get('total_return_pct_delta', 0.0)):+.2f}%`",
                f"- Excess Return Delta: `{float(drift.get('excess_return_pct_delta', 0.0)):+.2f}%`",
                f"- Drawdown Delta: `{float(drift.get('max_drawdown_pct_delta', 0.0)):+.2f}%`",
                f"- Sharpe Delta: `{float(drift.get('sharpe_delta', 0.0)):+.2f}`",
                f"- Win Rate Delta: `{float(drift.get('win_rate_pct_delta', 0.0)):+.2f}%`",
                f"- Trade Count Delta: `{float(drift.get('trade_count_delta_pct', 0.0)):+.2f}%`",
                f"- Signal Count Delta: `{float(drift.get('signal_count_delta_pct', 0.0)):+.2f}%`",
                "",
            ]
        )

    lines.extend(["## Warnings", ""])
    if warnings:
        for row in warnings:
            lines.append(
                f"- `{str(row.get('severity') or 'warn').upper()}` `{row.get('kind')}`: {row.get('message')} "
                f"(current=`{row.get('current')}`, baseline=`{row.get('baseline')}`)"
            )
    else:
        lines.append("- none")
    lines.append("")

    lines.extend(["## Recent Comparable Runs", "", "| run_id | return | max_dd | sharpe | win_rate | excess | dir |", "|---|---:|---:|---:|---:|---:|---|",])
    if recent_runs:
        for row in recent_runs:
            lines.append(
                "| "
                f"{row.get('run_id') or '--'} | {float(row.get('total_return_pct', 0.0)):+.2f}% | "
                f"{float(row.get('max_drawdown_pct', 0.0)):.2f}% | {float(row.get('sharpe', 0.0)):.2f} | "
                f"{float(row.get('win_rate_pct', 0.0)):.2f}% | {float(row.get('excess_return_pct', 0.0)):+.2f}% | "
                f"{row.get('artifact_dir') or '--'} |"
            )
    else:
        lines.append('| -- | -- | -- | -- | -- | -- | -- |')
    lines.append('')
    return "\n".join(lines)


def _render_markdown_report(
    metrics: Metrics,
    trades: list[Trade],
    input_quality: InputQualityReport | None = None,
    stability_report: dict[str, object] | None = None,
) -> str:
    recent = sorted(trades, key=lambda x: x.exit_ts, reverse=True)[:10]
    execution_ctx = metrics.strategy_context.get("execution", {}) if isinstance(metrics.strategy_context, dict) else {}
    entry_mode = str(execution_ctx.get("entry") or "next_open").strip().lower()
    fee_assumption = "Current execution path prices fills with taker fees."
    if entry_mode == "next_open":
        fee_assumption = "Current next_open execution prices fills with taker fees; maker_fee_bps is reserved for future passive fills."
    lines = [
        "# Backtest Report",
        "",
        f"- run_id: `{metrics.run_id}`",
        f"- mode: `{metrics.mode}`",
        f"- range: `{metrics.start}` -> `{metrics.end}`",
        f"- symbols: `{', '.join(metrics.symbols)}`",
        f"- timeframe: `{metrics.timeframe}`",
        "",
        "## Metrics",
        "",
        f"- Initial Equity: `{metrics.initial_equity:.2f}`",
        f"- Final Equity: `{metrics.final_equity:.2f}`",
        f"- Total Return: `{metrics.total_return_pct:+.2f}%`",
        f"- Max Drawdown: `{metrics.max_drawdown_pct:.2f}%`",
        f"- Sharpe: `{metrics.sharpe:.2f}`",
        f"- Trade Count: `{metrics.trade_count}`",
        f"- Win Rate: `{metrics.win_rate_pct:.2f}%`",
        f"- Profit Factor: `{metrics.profit_factor:.2f}`",
        f"- Avg Holding: `{metrics.avg_holding_minutes:.2f} min`",
        f"- Signal Count: `{metrics.signal_count}`",
        f"- Bar Count: `{metrics.bar_count}`",
        f"- Gross PnL: `{metrics.gross_pnl:+.4f}`",
        f"- Trading Fee: `{metrics.trading_fee:.4f}`",
        f"- Funding Fee: `{metrics.funding_fee:+.4f}`",
        f"- Net PnL: `{metrics.net_pnl:+.4f}`",
        f"- Total Cost Impact: `{metrics.total_cost_impact:+.4f}`",
        f"- Funding Credit: `{metrics.funding_credit:+.4f}`",
        f"- Gross→Net Retention: `{metrics.gross_to_net_retention_pct:+.2f}%`",
        f"- Cost Erosion vs Gross: `{metrics.cost_erosion_pct_of_gross:+.2f}%`",
        f"- Cost Drag vs Initial Equity: `{metrics.cost_drag_pct_of_initial:+.2f}%`",
        f"- Cost Status: `{metrics.cost_status}`",
        f"- Cost Summary: `{metrics.cost_summary}`",
        f"- Fee Assumption: `{fee_assumption}`",
        f"- Embedded Slippage Cost: `{metrics.slippage_cost:+.4f}`",
        f"- Slippage Cost vs Initial Equity: `{metrics.slippage_cost_pct_of_initial:+.2f}%`",
        f"- Embedded Impact Cost: `{metrics.impact_cost:+.4f}`",
        f"- Impact Cost vs Initial Equity: `{metrics.impact_cost_pct_of_initial:+.2f}%`",
        f"- Partial Fill Trades: `{metrics.partial_fill_trade_count}`",
        f"- Buy & Hold Return: `{metrics.buy_hold_return_pct:+.2f}%`",
        f"- Risk Parity Return: `{metrics.risk_parity_return_pct:+.2f}%`",
        f"- Simple Momentum Return: `{metrics.momentum_return_pct:+.2f}%`",
        f"- Excess Return vs Buy & Hold: `{metrics.excess_return_pct:+.2f}%`",
        f"- Excess Return vs Risk Parity: `{metrics.excess_return_vs_risk_parity_pct:+.2f}%`",
        f"- Excess Return vs Momentum: `{metrics.excess_return_vs_momentum_pct:+.2f}%`",
        f"- Strongest Baseline: `{metrics.best_baseline_name}` `{metrics.best_baseline_return_pct:+.2f}%`",
        "",
    ]

    if input_quality is not None:
        breakdown = input_quality.quality_breakdown or {}
        gate_thresholds = input_quality.gate_thresholds or {}
        gate_threshold_text = ""
        if gate_thresholds:
            gate_threshold_text = (
                f"signal_days>={int(gate_thresholds.get('min_signal_days', 0))} | "
                f"signal_count>={int(gate_thresholds.get('min_signal_count', 0))} | "
                f"candle_coverage>={float(gate_thresholds.get('min_candle_coverage_pct', 0.0)):.2f}%"
            )
        gate_failures_text = "; ".join(input_quality.gate_failures) if input_quality.gate_failures else "none"
        lines.extend(
            [
                "## Input Quality",
                "",
                f"- Quality Score: `{input_quality.quality_score:.2f}`",
                f"- Quality Status: `{str(input_quality.quality_status or '--').upper()}`",
                f"- Score Status: `{str(input_quality.score_status or '--').upper()}`",
                f"- Gate Status: `{str(input_quality.gate_status or '--').upper()}`",
                f"- Signal Days: `{input_quality.signal_days}`",
                f"- Signal Count: `{input_quality.signal_count}`",
                f"- Candle Coverage: `{input_quality.candle_coverage_pct:.2f}%`",
                f"- Aggregated Signal Buckets: `{input_quality.aggregated_signal_bucket_count}`",
                f"- No Next Open Buckets: `{input_quality.no_next_open_bucket_count}`",
                f"- Dropped Signals: `{input_quality.dropped_signal_count}`",
                (
                    "- Penalties: "
                    f"missing=`{float(breakdown.get('missing_candle_penalty', 0.0)):.2f}` | "
                    f"gaps=`{float(breakdown.get('gap_penalty', 0.0)):.2f}` | "
                    f"no_next_open=`{float(breakdown.get('no_next_open_penalty', 0.0)):.2f}` | "
                    f"dropped=`{float(breakdown.get('dropped_signal_penalty', 0.0)):.2f}`"
                ),
                f"- Gate Thresholds: `{gate_threshold_text or '--'}`",
                f"- Gate Failures: `{gate_failures_text}`",
                "",
                "| symbol | score | status | coverage | gaps | missing_candles | no_next_open | dropped |",
                "|---|---:|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in input_quality.symbol_rows:
            lines.append(
                "| "
                f"{row.symbol} | {row.quality_score:.2f} | {str(row.quality_status or '--').upper()} | "
                f"{row.candle_coverage_pct:.2f}% | {row.gap_count} | {row.missing_candle_count} | "
                f"{row.no_next_open_bucket_count} | {row.dropped_signal_count} |"
            )
        if not input_quality.symbol_rows:
            lines.append("| -- | -- | -- | -- | -- | -- | -- | -- |")
        lines.append("")

    if stability_report is not None:
        lines.extend(
            [
                "## Stability",
                "",
                f"- Stability Status: `{str(stability_report.get('stability_status') or '--').upper()}`",
                f"- Stability Summary: `{str(stability_report.get('stability_summary') or '')}`",
                f"- Comparable Runs: `{int(stability_report.get('comparable_run_count') or 0)}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Signal Profile",
            "",
            f"- Direction Mix: `{metrics.direction_counts}`",
            f"- Timeframe Mix: `{metrics.timeframe_counts}`",
            "",
            "## Symbol Contributions",
            "",
            "| symbol | pnl_net | trades | win_rate | avg_hold_min |",
            "|---|---:|---:|---:|---:|",
        ]
    )

    for row in metrics.symbol_contributions:
        lines.append(
            "| "
            f"{row.symbol} | {row.pnl_net:+.4f} | {row.trade_count} | "
            f"{row.win_rate_pct:.2f}% | {row.avg_holding_minutes:.2f} |"
        )

    if not metrics.symbol_contributions:
        lines.append("| -- | -- | -- | -- | -- |")

    lines.extend(
        [
            "",
            "## Recent Trades",
            "",
            "| exit_ts | symbol | side | pnl_net | reason |",
            "|---|---|---:|---:|---|",
        ]
    )

    for t in recent:
        lines.append(f"| {_fmt_ts(t.exit_ts)} | {t.symbol} | {_fmt_side(t.side)} | {t.pnl_net:+.4f} | {t.reason} |")

    if not recent:
        lines.append("| -- | -- | -- | -- | -- |")

    lines.append("")
    return "\n".join(lines)


def _write_input_quality_json(path: Path, report: InputQualityReport) -> None:
    path.write_text(json.dumps(input_quality_to_payload(report), ensure_ascii=True, indent=2), encoding="utf-8")


def write_artifacts(
    output_dir: Path,
    trades: list[Trade],
    curve: list[EquityPoint],
    metrics: Metrics,
    input_quality: InputQualityReport | None = None,
    backtest_root: Path | None = None,
) -> None:
    """Write run artifacts under output_dir."""

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_trades_csv(output_dir / "trades.csv", trades)
    _write_curve_csv(output_dir / "equity_curve.csv", curve)
    _write_metrics_json(output_dir / "metrics.json", metrics)
    stability_report = _build_stability_report(output_dir, metrics, backtest_root=backtest_root)
    (output_dir / "stability_report.json").write_text(
        json.dumps(stability_report, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    (output_dir / "stability_report.md").write_text(
        _render_stability_markdown(stability_report),
        encoding="utf-8",
    )
    if input_quality is not None:
        _write_input_quality_json(output_dir / "input_quality.json", input_quality)
    (output_dir / "report.md").write_text(
        _render_markdown_report(metrics, trades, input_quality=input_quality, stability_report=stability_report),
        encoding="utf-8",
    )
