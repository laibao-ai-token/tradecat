"""Artifact writers and metric calculators for backtest outputs."""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .models import Bar, EquityPoint, Metrics, SignalEvent, SymbolContribution, Trade


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
        signal_type = str(ev.signal_type or "UNKNOWN").strip() or "UNKNOWN"
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


def _calc_buy_hold_baseline(
    bars_by_symbol: dict[str, list[Bar]] | None,
    initial_equity: float,
) -> tuple[float, float]:
    """Estimate equal-weight buy-and-hold baseline over selected symbols."""

    initial = float(initial_equity)
    if initial <= 0:
        return 0.0, 0.0
    if not bars_by_symbol:
        return initial, 0.0

    symbol_returns: list[float] = []
    for bars in bars_by_symbol.values():
        if not bars:
            continue
        ordered = sorted(bars, key=lambda x: x.timestamp)
        first_close = float(ordered[0].close)
        last_close = float(ordered[-1].close)
        if first_close <= 0:
            continue
        symbol_returns.append((last_close - first_close) / first_close)

    if not symbol_returns:
        return initial, 0.0

    avg_return = sum(symbol_returns) / len(symbol_returns)
    final_equity = initial * (1.0 + avg_return)
    return float(final_equity), float(avg_return * 100.0)


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
) -> Metrics:
    """Build summarized metrics from raw outputs."""

    total_return_pct = ((final_equity / initial_equity) - 1.0) * 100.0 if initial_equity > 0 else 0.0
    max_dd = _calc_max_drawdown_pct(curve)
    sharpe = _calc_sharpe(curve)

    wins = [t for t in trades if t.pnl_net > 0]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0

    gain = sum(t.pnl_net for t in trades if t.pnl_net > 0)
    loss = -sum(t.pnl_net for t in trades if t.pnl_net < 0)
    profit_factor = (gain / loss) if loss > 0 else (999.0 if gain > 0 else 0.0)

    avg_holding_minutes = _calc_avg_holding_minutes(trades)
    symbol_contributions = _build_symbol_contributions(trades)
    signal_type_counts, direction_counts, timeframe_counts = _build_signal_profile(signals)
    buy_hold_final_equity, buy_hold_return_pct = _calc_buy_hold_baseline(bars_by_symbol, initial_equity)
    excess_return_pct = total_return_pct - buy_hold_return_pct

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
        buy_hold_final_equity=float(buy_hold_final_equity),
        buy_hold_return_pct=float(buy_hold_return_pct),
        excess_return_pct=float(excess_return_pct),
        symbol_contributions=symbol_contributions,
        signal_type_counts=signal_type_counts,
        direction_counts=direction_counts,
        timeframe_counts=timeframe_counts,
        strategy_label=str(strategy_label or ""),
        strategy_config_path=str(strategy_config_path or ""),
        strategy_summary=str(strategy_summary or ""),
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
        "entry_fee",
        "exit_fee",
        "pnl_gross",
        "pnl_net",
        "entry_score",
        "exit_score",
        "reason",
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
                    "entry_fee": f"{t.entry_fee:.8f}",
                    "exit_fee": f"{t.exit_fee:.8f}",
                    "pnl_gross": f"{t.pnl_gross:.8f}",
                    "pnl_net": f"{t.pnl_net:.8f}",
                    "entry_score": t.entry_score,
                    "exit_score": t.exit_score,
                    "reason": t.reason,
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
        "buy_hold_final_equity": metrics.buy_hold_final_equity,
        "buy_hold_return_pct": metrics.buy_hold_return_pct,
        "excess_return_pct": metrics.excess_return_pct,
        "signal_type_counts": metrics.signal_type_counts,
        "direction_counts": metrics.direction_counts,
        "timeframe_counts": metrics.timeframe_counts,
        "strategy_label": metrics.strategy_label,
        "strategy_config_path": metrics.strategy_config_path,
        "strategy_summary": metrics.strategy_summary,
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


def _render_markdown_report(metrics: Metrics, trades: list[Trade]) -> str:
    recent = sorted(trades, key=lambda x: x.exit_ts, reverse=True)[:10]
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
        f"- Buy & Hold Return: `{metrics.buy_hold_return_pct:+.2f}%`",
        f"- Excess Return vs Buy & Hold: `{metrics.excess_return_pct:+.2f}%`",
        "",
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


def write_artifacts(output_dir: Path, trades: list[Trade], curve: list[EquityPoint], metrics: Metrics) -> None:
    """Write run artifacts under output_dir."""

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_trades_csv(output_dir / "trades.csv", trades)
    _write_curve_csv(output_dir / "equity_curve.csv", curve)
    _write_metrics_json(output_dir / "metrics.json", metrics)
    (output_dir / "report.md").write_text(_render_markdown_report(metrics, trades), encoding="utf-8")
