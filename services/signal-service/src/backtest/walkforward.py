"""Walk-forward helpers for rolling out-of-sample evaluation."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import REPO_ROOT
from .data_loader import resolve_range
from .models import AggregationConfig, BacktestConfig, DateRange, Metrics
from .precheck import compute_coverage_report
from .retention import cleanup_old_runs, update_latest_link
from .runner import run_backtest


@dataclass(frozen=True)
class WalkForwardWindow:
    fold: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass(frozen=True)
class WalkForwardFoldResult:
    fold: int
    run_id: str
    mode: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    trade_count: int
    win_rate_pct: float
    excess_return_pct: float
    signal_count: int
    signal_days: int
    fallback_reason: str = ""


@dataclass(frozen=True)
class WalkForwardSummary:
    run_id: str
    mode: str
    fold_count: int
    avg_return_pct: float
    median_return_pct: float
    min_return_pct: float
    max_return_pct: float
    positive_fold_rate_pct: float
    avg_max_drawdown_pct: float
    avg_excess_return_pct: float
    history_fold_count: int
    replay_fold_count: int
    fallback_fold_count: int
    output_dir: Path


def _fmt_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat(sep=" ")
    return dt.astimezone(timezone.utc).isoformat(sep=" ")


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    n = len(arr)
    mid = n // 2
    if n % 2 == 1:
        return float(arr[mid])
    return float((arr[mid - 1] + arr[mid]) / 2.0)


def build_walk_forward_windows(
    start: datetime,
    end: datetime,
    *,
    train_days: int,
    test_days: int,
    step_days: int,
    max_folds: int = 0,
) -> list[WalkForwardWindow]:
    """Build rolling train/test windows.

    Note: in current M2 implementation only test windows are executed; train
    windows are emitted for observability and future parameter tuning.
    """

    train_span = max(1, int(train_days))
    test_span = max(1, int(test_days))
    step_span = max(1, int(step_days))
    cap = max(0, int(max_folds))

    windows: list[WalkForwardWindow] = []
    cursor = start
    fold = 1

    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_span)
        test_start = train_end
        test_end = test_start + timedelta(days=test_span)

        if test_start >= end:
            break
        if test_end > end:
            test_end = end
        if test_end <= test_start:
            break

        windows.append(
            WalkForwardWindow(
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )

        if cap > 0 and len(windows) >= cap:
            break

        cursor = cursor + timedelta(days=step_span)
        fold += 1

    return windows


def _clone_config_for_test_window(config: BacktestConfig, test_start: datetime, test_end: datetime) -> BacktestConfig:
    return BacktestConfig(
        market=config.market,
        symbols=list(config.symbols),
        timeframe=config.timeframe,
        strategy_label=config.strategy_label,
        strategy_config_path=config.strategy_config_path,
        date_range=DateRange(start=_fmt_ts(test_start), end=_fmt_ts(test_end)),
        execution=config.execution,
        risk=config.risk,
        aggregation=config.aggregation,
        walk_forward=config.walk_forward,
        retention=config.retention,
    )


def _adapt_config_for_offline_replay(config: BacktestConfig) -> BacktestConfig:
    """Lower thresholds for replay fallback to avoid all-flat folds."""

    ag = config.aggregation
    replay_ag = AggregationConfig(
        long_open_threshold=max(70, int(ag.long_open_threshold * 0.7)),
        short_open_threshold=max(70, int(ag.short_open_threshold * 0.7)),
        close_threshold=max(ag.close_threshold, 15),
    )
    return BacktestConfig(
        market=config.market,
        symbols=list(config.symbols),
        timeframe=config.timeframe,
        strategy_label=config.strategy_label,
        strategy_config_path=config.strategy_config_path,
        date_range=config.date_range,
        execution=config.execution,
        risk=config.risk,
        aggregation=replay_ag,
        walk_forward=config.walk_forward,
        retention=config.retention,
    )


def _strategy_side_text(config: BacktestConfig) -> str:
    allow_long = bool(config.execution.allow_long)
    allow_short = bool(config.execution.allow_short)
    if allow_long and allow_short:
        return "long_short"
    if allow_short and not allow_long:
        return "short_only"
    if allow_long and not allow_short:
        return "long_only"
    return "disabled"


def _strategy_summary(config: BacktestConfig) -> str:
    ag = config.aggregation
    ex = config.execution
    return (
        f"side={_strategy_side_text(config)} "
        f"L/S/C={int(ag.long_open_threshold)}/{int(ag.short_open_threshold)}/{int(ag.close_threshold)} "
        f"fee={float(ex.fee_bps):.1f}bps slip={float(ex.slippage_bps):.1f}bps "
        f"hold>={int(ex.min_hold_minutes)}m neutral={int(ex.neutral_confirm_minutes)}m"
    )


def _select_fold_mode(
    requested_mode: str,
    fold_cfg: BacktestConfig,
    *,
    auto_fallback: bool,
    min_signal_days: int,
    min_signal_count: int,
) -> tuple[str, int, int, str]:
    """Pick fold execution mode; fallback to offline replay when history coverage is thin."""

    mode = str(requested_mode or "history_signal").strip().lower()
    if mode != "history_signal" or not auto_fallback:
        return mode, 0, 0, ""

    day_th = max(0, int(min_signal_days))
    count_th = max(0, int(min_signal_count))
    if day_th <= 0 and count_th <= 0:
        return "history_signal", 0, 0, ""

    coverage = compute_coverage_report(fold_cfg)
    days = int(coverage.signal_days)
    count = int(coverage.signal_count)

    fail_days = day_th > 0 and days < day_th
    fail_count = count_th > 0 and count < count_th
    if fail_days or fail_count:
        parts: list[str] = []
        if fail_days:
            parts.append(f"signal_days {days}<{day_th}")
        if fail_count:
            parts.append(f"signal_count {count}<{count_th}")
        return "offline_replay", count, days, "; ".join(parts)

    return "history_signal", count, days, ""


def _write_fold_csv(path: Path, rows: list[WalkForwardFoldResult]) -> None:
    fieldnames = [
        "fold",
        "run_id",
        "mode",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe",
        "trade_count",
        "win_rate_pct",
        "excess_return_pct",
        "signal_count",
        "signal_days",
        "fallback_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "fold": row.fold,
                    "run_id": row.run_id,
                    "mode": row.mode,
                    "train_start": row.train_start,
                    "train_end": row.train_end,
                    "test_start": row.test_start,
                    "test_end": row.test_end,
                    "total_return_pct": f"{row.total_return_pct:.8f}",
                    "max_drawdown_pct": f"{row.max_drawdown_pct:.8f}",
                    "sharpe": f"{row.sharpe:.8f}",
                    "trade_count": row.trade_count,
                    "win_rate_pct": f"{row.win_rate_pct:.8f}",
                    "excess_return_pct": f"{row.excess_return_pct:.8f}",
                    "signal_count": row.signal_count,
                    "signal_days": row.signal_days,
                    "fallback_reason": row.fallback_reason,
                }
            )


def _summary_from_folds(run_id: str, mode: str, output_dir: Path, rows: list[WalkForwardFoldResult]) -> WalkForwardSummary:
    if not rows:
        return WalkForwardSummary(
            run_id=run_id,
            mode=mode,
            fold_count=0,
            avg_return_pct=0.0,
            median_return_pct=0.0,
            min_return_pct=0.0,
            max_return_pct=0.0,
            positive_fold_rate_pct=0.0,
            avg_max_drawdown_pct=0.0,
            avg_excess_return_pct=0.0,
            history_fold_count=0,
            replay_fold_count=0,
            fallback_fold_count=0,
            output_dir=output_dir,
        )

    returns = [row.total_return_pct for row in rows]
    max_dds = [row.max_drawdown_pct for row in rows]
    excess = [row.excess_return_pct for row in rows]
    pos = sum(1 for v in returns if v > 0)

    history_count = sum(1 for row in rows if row.mode == "history_signal")
    replay_count = sum(1 for row in rows if row.mode == "offline_replay")
    fallback_count = sum(1 for row in rows if row.fallback_reason)

    return WalkForwardSummary(
        run_id=run_id,
        mode=mode,
        fold_count=len(rows),
        avg_return_pct=float(sum(returns) / len(returns)),
        median_return_pct=float(_median(returns)),
        min_return_pct=float(min(returns)),
        max_return_pct=float(max(returns)),
        positive_fold_rate_pct=float(pos / len(rows) * 100.0),
        avg_max_drawdown_pct=float(sum(max_dds) / len(max_dds)),
        avg_excess_return_pct=float(sum(excess) / len(excess)),
        history_fold_count=history_count,
        replay_fold_count=replay_count,
        fallback_fold_count=fallback_count,
        output_dir=output_dir,
    )


def _write_summary_json(path: Path, summary: WalkForwardSummary, rows: list[WalkForwardFoldResult]) -> None:
    payload = {
        "run_id": summary.run_id,
        "mode": summary.mode,
        "fold_count": summary.fold_count,
        "avg_return_pct": summary.avg_return_pct,
        "median_return_pct": summary.median_return_pct,
        "min_return_pct": summary.min_return_pct,
        "max_return_pct": summary.max_return_pct,
        "positive_fold_rate_pct": summary.positive_fold_rate_pct,
        "avg_max_drawdown_pct": summary.avg_max_drawdown_pct,
        "avg_excess_return_pct": summary.avg_excess_return_pct,
        "history_fold_count": summary.history_fold_count,
        "replay_fold_count": summary.replay_fold_count,
        "fallback_fold_count": summary.fallback_fold_count,
        "folds": [
            {
                "fold": row.fold,
                "run_id": row.run_id,
                "mode": row.mode,
                "train_start": row.train_start,
                "train_end": row.train_end,
                "test_start": row.test_start,
                "test_end": row.test_end,
                "total_return_pct": row.total_return_pct,
                "max_drawdown_pct": row.max_drawdown_pct,
                "sharpe": row.sharpe,
                "trade_count": row.trade_count,
                "win_rate_pct": row.win_rate_pct,
                "excess_return_pct": row.excess_return_pct,
                "signal_count": row.signal_count,
                "signal_days": row.signal_days,
                "fallback_reason": row.fallback_reason,
            }
            for row in rows
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_summary_metrics_json(path: Path, config: BacktestConfig, summary: WalkForwardSummary, rows: list[WalkForwardFoldResult]) -> None:
    if rows:
        start_txt = rows[0].test_start
        end_txt = rows[-1].test_end
        avg_sharpe = float(sum(row.sharpe for row in rows) / len(rows))
        avg_win_rate = float(sum(row.win_rate_pct for row in rows) / len(rows))
        avg_buy_hold = float(sum(row.total_return_pct - row.excess_return_pct for row in rows) / len(rows))
        trade_count = int(sum(row.trade_count for row in rows))
        signal_count = int(sum(row.signal_count for row in rows))
        final_equity = float(config.risk.initial_equity) * (1.0 + float(summary.avg_return_pct) / 100.0)
        buy_hold_final = float(config.risk.initial_equity) * (1.0 + float(avg_buy_hold) / 100.0)
    else:
        start_txt = ""
        end_txt = ""
        avg_sharpe = 0.0
        avg_win_rate = 0.0
        avg_buy_hold = 0.0
        trade_count = 0
        signal_count = 0
        final_equity = float(config.risk.initial_equity)
        buy_hold_final = float(config.risk.initial_equity)

    payload = {
        "run_id": summary.run_id,
        "mode": "walk_forward",
        "start": start_txt,
        "end": end_txt,
        "symbols": list(config.symbols),
        "timeframe": config.timeframe,
        "initial_equity": float(config.risk.initial_equity),
        "final_equity": float(final_equity),
        "total_return_pct": float(summary.avg_return_pct),
        "max_drawdown_pct": float(summary.avg_max_drawdown_pct),
        "sharpe": float(avg_sharpe),
        "trade_count": trade_count,
        "win_rate_pct": float(avg_win_rate),
        "profit_factor": 0.0,
        "avg_holding_minutes": 0.0,
        "signal_count": signal_count,
        "bar_count": 0,
        "strategy_label": str(config.strategy_label or ""),
        "strategy_config_path": str(config.strategy_config_path or ""),
        "strategy_summary": _strategy_summary(config),
        "buy_hold_final_equity": float(buy_hold_final),
        "buy_hold_return_pct": float(avg_buy_hold),
        "excess_return_pct": float(summary.avg_excess_return_pct),
        "symbol_contributions": [],
        "walk_forward_summary": {
            "fold_count": int(summary.fold_count),
            "history_fold_count": int(summary.history_fold_count),
            "replay_fold_count": int(summary.replay_fold_count),
            "fallback_fold_count": int(summary.fallback_fold_count),
            "avg_return_pct": float(summary.avg_return_pct),
            "avg_max_drawdown_pct": float(summary.avg_max_drawdown_pct),
            "avg_excess_return_pct": float(summary.avg_excess_return_pct),
            "positive_fold_rate_pct": float(summary.positive_fold_rate_pct),
            "folds": [
                {
                    "fold": int(row.fold),
                    "run_id": row.run_id,
                    "mode": row.mode,
                    "test_start": row.test_start,
                    "test_end": row.test_end,
                    "total_return_pct": float(row.total_return_pct),
                    "max_drawdown_pct": float(row.max_drawdown_pct),
                    "trade_count": int(row.trade_count),
                }
                for row in rows
            ],
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_summary_equity_curve(path: Path, config: BacktestConfig, rows: list[WalkForwardFoldResult]) -> None:
    initial = max(0.0, float(config.risk.initial_equity))

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamp", "equity"])
        writer.writeheader()
        if not rows:
            return

        equity = initial
        writer.writerow({"timestamp": rows[0].test_start, "equity": f"{equity:.8f}"})
        for row in rows:
            equity *= 1.0 + float(row.total_return_pct) / 100.0
            writer.writerow({"timestamp": row.test_end, "equity": f"{equity:.8f}"})


def _to_fold_row(
    window: WalkForwardWindow,
    metrics: Metrics,
    *,
    signal_count: int,
    signal_days: int,
    fallback_reason: str,
) -> WalkForwardFoldResult:
    return WalkForwardFoldResult(
        fold=window.fold,
        run_id=str(metrics.run_id),
        mode=str(metrics.mode),
        train_start=_fmt_ts(window.train_start),
        train_end=_fmt_ts(window.train_end),
        test_start=_fmt_ts(window.test_start),
        test_end=_fmt_ts(window.test_end),
        total_return_pct=float(metrics.total_return_pct),
        max_drawdown_pct=float(metrics.max_drawdown_pct),
        sharpe=float(metrics.sharpe),
        trade_count=int(metrics.trade_count),
        win_rate_pct=float(metrics.win_rate_pct),
        excess_return_pct=float(metrics.excess_return_pct),
        signal_count=int(signal_count),
        signal_days=int(signal_days),
        fallback_reason=str(fallback_reason or ""),
    )


def run_walk_forward(
    config: BacktestConfig,
    *,
    mode: str,
    run_id: str,
    output_dir: Path | None = None,
    max_folds: int = 0,
    auto_fallback: bool = True,
    min_signal_days: int = 0,
    min_signal_count: int = 0,
) -> WalkForwardSummary:
    """Run walk-forward test windows and write summary artifacts."""

    start, end = resolve_range(config.date_range)
    wf = config.walk_forward

    windows = build_walk_forward_windows(
        start,
        end,
        train_days=wf.train_days,
        test_days=wf.test_days,
        step_days=wf.step_days,
        max_folds=max_folds,
    )

    resolved_output_dir = Path(output_dir) if output_dir is not None else (Path(REPO_ROOT) / "artifacts" / "backtest" / run_id)
    rows: list[WalkForwardFoldResult] = []
    for window in windows:
        fold_cfg = _clone_config_for_test_window(config, window.test_start, window.test_end)
        effective_mode, sig_count, sig_days, fallback_reason = _select_fold_mode(
            mode,
            fold_cfg,
            auto_fallback=auto_fallback,
            min_signal_days=min_signal_days,
            min_signal_count=min_signal_count,
        )
        fold_run_id = f"{run_id}-wf{window.fold:02d}"
        run_cfg = fold_cfg
        if effective_mode == "offline_replay" and fallback_reason:
            run_cfg = _adapt_config_for_offline_replay(fold_cfg)
            fallback_reason = f"{fallback_reason}; replay_threshold=70%"

        res = run_backtest(
            run_cfg,
            mode=effective_mode,
            run_id=fold_run_id,
            output_dir=resolved_output_dir / fold_run_id,
        )
        rows.append(
            _to_fold_row(
                window,
                res.metrics,
                signal_count=sig_count,
                signal_days=sig_days,
                fallback_reason=fallback_reason,
            )
        )

    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    summary = _summary_from_folds(run_id, mode, resolved_output_dir, rows)
    _write_fold_csv(resolved_output_dir / "walk_forward_folds.csv", rows)
    _write_summary_json(resolved_output_dir / "walk_forward_summary.json", summary, rows)

    # Keep TUI/automation compatible with standard backtest artifact names.
    _write_summary_metrics_json(resolved_output_dir / "metrics.json", config, summary, rows)
    _write_summary_equity_curve(resolved_output_dir / "equity_curve.csv", config, rows)

    backtest_root = Path(REPO_ROOT) / "artifacts" / "backtest"
    update_latest_link(backtest_root, resolved_output_dir)
    cleanup_old_runs(backtest_root, config.retention.keep_runs)

    return summary
