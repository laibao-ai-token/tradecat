"""Walk-forward helpers for rolling out-of-sample evaluation."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
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
    buy_hold_return_pct: float
    risk_parity_return_pct: float
    momentum_return_pct: float
    excess_return_vs_risk_parity_pct: float
    excess_return_vs_momentum_pct: float
    signal_count: int
    signal_days: int
    fallback_reason: str = ""
    selected_params: dict[str, object] = field(default_factory=dict)


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


def _avg(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _compound_growth_multiplier(returns: list[float]) -> float:
    growth = 1.0
    for value in returns:
        growth *= max(0.0, 1.0 + float(value) / 100.0)
    return float(growth)


def _compound_return_pct(returns: list[float]) -> float:
    return float((_compound_growth_multiplier(returns) - 1.0) * 100.0)


def _final_equity_after_returns(initial_equity: float, returns: list[float]) -> float:
    return float(max(0.0, float(initial_equity)) * _compound_growth_multiplier(returns))


def _build_baseline_rollup(rows: list[WalkForwardFoldResult]) -> dict[str, float | str]:
    buy_hold_returns = [float(row.buy_hold_return_pct) for row in rows]
    risk_parity_returns = [float(row.risk_parity_return_pct) for row in rows]
    momentum_returns = [float(row.momentum_return_pct) for row in rows]

    avg_buy_hold = _avg(buy_hold_returns)
    avg_risk_parity = _avg(risk_parity_returns)
    avg_momentum = _avg(momentum_returns)
    buy_hold_compounded = _compound_return_pct(buy_hold_returns)
    risk_parity_compounded = _compound_return_pct(risk_parity_returns)
    momentum_compounded = _compound_return_pct(momentum_returns)

    best_baseline_name, best_baseline_return_pct = max(
        (
            ("buy_hold", buy_hold_compounded),
            ("risk_parity", risk_parity_compounded),
            ("momentum", momentum_compounded),
        ),
        key=lambda item: item[1],
    )
    return {
        "avg_buy_hold_return_pct": float(avg_buy_hold),
        "avg_risk_parity_return_pct": float(avg_risk_parity),
        "avg_momentum_return_pct": float(avg_momentum),
        "buy_hold_compounded_return_pct": float(buy_hold_compounded),
        "risk_parity_compounded_return_pct": float(risk_parity_compounded),
        "momentum_compounded_return_pct": float(momentum_compounded),
        "best_baseline_name": str(best_baseline_name),
        "best_baseline_return_pct": float(best_baseline_return_pct),
    }


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


def _clone_config_for_window(config: BacktestConfig, start_dt: datetime, end_dt: datetime) -> BacktestConfig:
    return BacktestConfig(
        market=config.market,
        symbols=list(config.symbols),
        timeframe=config.timeframe,
        strategy_label=config.strategy_label,
        strategy_config_path=config.strategy_config_path,
        date_range=DateRange(start=_fmt_ts(start_dt), end=_fmt_ts(end_dt)),
        execution=config.execution,
        risk=config.risk,
        aggregation=config.aggregation,
        walk_forward=config.walk_forward,
        retention=config.retention,
    )


def _clone_config_for_test_window(config: BacktestConfig, test_start: datetime, test_end: datetime) -> BacktestConfig:
    return _clone_config_for_window(config, test_start, test_end)


def _clone_config_with_aggregation(config: BacktestConfig, aggregation: AggregationConfig) -> BacktestConfig:
    return BacktestConfig(
        market=config.market,
        symbols=list(config.symbols),
        timeframe=config.timeframe,
        strategy_label=config.strategy_label,
        strategy_config_path=config.strategy_config_path,
        date_range=config.date_range,
        execution=config.execution,
        risk=config.risk,
        aggregation=aggregation,
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
    maker_fee = float(ex.maker_fee_bps if ex.maker_fee_bps is not None else ex.fee_bps)
    taker_fee = float(ex.taker_fee_bps if ex.taker_fee_bps is not None else ex.fee_bps)
    funding_rate = float(getattr(ex, "funding_rate_bps_per_8h", 0.0) or 0.0)
    slippage_model = str(getattr(ex, "slippage_model", "fixed") or "fixed").strip().lower()
    slippage_text = f"slip={float(ex.slippage_bps):.1f}bps"
    if slippage_model == "layered":
        cap_bps = getattr(ex, "slippage_max_bps", None)
        cap_text = float(cap_bps) if cap_bps is not None else max(float(ex.slippage_bps), float(ex.slippage_bps) * 3.0)
        slippage_text = (
            f"slip={float(ex.slippage_bps):.1f}bps(layered cap={cap_text:.1f} "
            f"vol={float(getattr(ex, 'slippage_volatility_weight', 0.0) or 0.0):.2f} "
            f"liq={float(getattr(ex, 'slippage_volume_weight', 0.0) or 0.0):.2f} "
            f"session={float(getattr(ex, 'slippage_session_weight', 0.0) or 0.0):.2f})"
        )
    participation = float(getattr(ex, "max_bar_participation_rate", 1.0) or 0.0) * 100.0
    min_order_notional = float(getattr(ex, "min_order_notional", 0.0) or 0.0)
    impact_bps = float(getattr(ex, "impact_bps_per_bar_participation", 0.0) or 0.0)
    return (
        f"side={_strategy_side_text(config)} "
        f"L/S/C={int(ag.long_open_threshold)}/{int(ag.short_open_threshold)}/{int(ag.close_threshold)} "
        f"maker={maker_fee:.1f}bps taker={taker_fee:.1f}bps funding={funding_rate:+.2f}bps/8h "
        f"{slippage_text} hold>={int(ex.min_hold_minutes)}m neutral={int(ex.neutral_confirm_minutes)}m "
        f"part<={participation:.1f}% min_notional={min_order_notional:.2f} impact={impact_bps:.1f}bps/100%bar"
    )


def _build_selected_params(config: BacktestConfig, *, selection_source: str) -> dict[str, object]:
    ag = config.aggregation
    ex = config.execution
    risk = config.risk
    maker_fee = float(ex.maker_fee_bps if ex.maker_fee_bps is not None else ex.fee_bps)
    taker_fee = float(ex.taker_fee_bps if ex.taker_fee_bps is not None else ex.fee_bps)
    return {
        "selection_source": str(selection_source or "base_config"),
        "strategy_side": _strategy_side_text(config),
        "aggregation": {
            "long_open_threshold": int(ag.long_open_threshold),
            "short_open_threshold": int(ag.short_open_threshold),
            "close_threshold": int(ag.close_threshold),
        },
        "execution": {
            "allow_long": bool(ex.allow_long),
            "allow_short": bool(ex.allow_short),
            "slippage_bps": float(ex.slippage_bps),
            "slippage_model": str(getattr(ex, "slippage_model", "fixed") or "fixed"),
            "slippage_max_bps": (
                float(getattr(ex, "slippage_max_bps", 0.0))
                if getattr(ex, "slippage_max_bps", None) is not None
                else None
            ),
            "slippage_volatility_weight": float(getattr(ex, "slippage_volatility_weight", 0.0) or 0.0),
            "slippage_volume_weight": float(getattr(ex, "slippage_volume_weight", 0.0) or 0.0),
            "slippage_session_weight": float(getattr(ex, "slippage_session_weight", 0.0) or 0.0),
            "slippage_volume_window": int(getattr(ex, "slippage_volume_window", 20) or 20),
            "max_bar_participation_rate": float(getattr(ex, "max_bar_participation_rate", 1.0) or 0.0),
            "min_order_notional": float(getattr(ex, "min_order_notional", 0.0) or 0.0),
            "impact_bps_per_bar_participation": float(getattr(ex, "impact_bps_per_bar_participation", 0.0) or 0.0),
            "fee_bps": float(ex.fee_bps),
            "maker_fee_bps": maker_fee,
            "taker_fee_bps": taker_fee,
            "funding_rate_bps_per_8h": float(getattr(ex, "funding_rate_bps_per_8h", 0.0) or 0.0),
            "min_hold_minutes": int(ex.min_hold_minutes),
            "neutral_confirm_minutes": int(ex.neutral_confirm_minutes),
        },
        "risk": {
            "initial_equity": float(risk.initial_equity),
            "leverage": float(risk.leverage),
            "position_size_pct": float(risk.position_size_pct),
            "maintenance_margin_ratio": float(risk.maintenance_margin_ratio),
            "liquidation_fee_bps": float(risk.liquidation_fee_bps),
            "liquidation_buffer_bps": float(risk.liquidation_buffer_bps),
        },
        "strategy_summary": _strategy_summary(config),
    }


def _aggregation_signature(aggregation: AggregationConfig) -> tuple[int, int, int]:
    return (
        int(aggregation.long_open_threshold),
        int(aggregation.short_open_threshold),
        int(aggregation.close_threshold),
    )


def _aggregation_distance(base: AggregationConfig, candidate: AggregationConfig) -> int:
    return int(
        abs(int(base.long_open_threshold) - int(candidate.long_open_threshold))
        + abs(int(base.short_open_threshold) - int(candidate.short_open_threshold))
        + abs(int(base.close_threshold) - int(candidate.close_threshold))
    )


def _build_train_window_candidates(config: BacktestConfig) -> list[tuple[str, AggregationConfig]]:
    base = config.aggregation
    raw = [
        (
            "base",
            AggregationConfig(
                long_open_threshold=int(base.long_open_threshold),
                short_open_threshold=int(base.short_open_threshold),
                close_threshold=int(base.close_threshold),
            ),
        ),
        (
            "aggressive",
            AggregationConfig(
                long_open_threshold=max(70, int(round(float(base.long_open_threshold) * 0.85))),
                short_open_threshold=max(70, int(round(float(base.short_open_threshold) * 0.85))),
                close_threshold=max(10, int(round(float(base.close_threshold) * 0.85))),
            ),
        ),
        (
            "conservative",
            AggregationConfig(
                long_open_threshold=max(int(base.long_open_threshold) + 5, int(round(float(base.long_open_threshold) * 1.15))),
                short_open_threshold=max(int(base.short_open_threshold) + 5, int(round(float(base.short_open_threshold) * 1.15))),
                close_threshold=max(int(base.close_threshold) + 2, int(round(float(base.close_threshold) * 1.15))),
            ),
        ),
    ]

    out: list[tuple[str, AggregationConfig]] = []
    seen: set[tuple[int, int, int]] = set()
    for name, aggregation in raw:
        sig = _aggregation_signature(aggregation)
        if sig in seen:
            continue
        seen.add(sig)
        out.append((name, aggregation))
    return out


def _candidate_rank(
    metrics: Metrics,
    *,
    base_aggregation: AggregationConfig,
    candidate_aggregation: AggregationConfig,
    candidate_name: str,
) -> tuple[float, float, float, float, float, float, int, int]:
    return (
        1.0 if int(metrics.trade_count) > 0 else 0.0,
        float(metrics.excess_return_pct),
        float(metrics.total_return_pct),
        float(metrics.sharpe),
        -float(metrics.max_drawdown_pct),
        float(metrics.win_rate_pct),
        -_aggregation_distance(base_aggregation, candidate_aggregation),
        1 if candidate_name == "base" else 0,
    )


def _select_train_window_params(
    train_cfg: BacktestConfig,
    test_cfg: BacktestConfig,
    *,
    mode: str,
    fold_run_id: str,
    base_selection_source: str,
) -> tuple[BacktestConfig, dict[str, object]]:
    candidates = _build_train_window_candidates(train_cfg)
    errors: list[str] = []
    evaluations: list[tuple[tuple[float, float, float, float, float, float, int, int], str, AggregationConfig, Metrics]] = []

    for candidate_name, aggregation in candidates:
        candidate_train_cfg = _clone_config_with_aggregation(train_cfg, aggregation)
        try:
            result = run_backtest(
                candidate_train_cfg,
                mode=mode,
                run_id=f"{fold_run_id}-train-{candidate_name}",
                ephemeral=True,
            )
        except Exception as exc:
            errors.append(f"{candidate_name}:{type(exc).__name__}")
            continue

        evaluations.append(
            (
                _candidate_rank(
                    result.metrics,
                    base_aggregation=train_cfg.aggregation,
                    candidate_aggregation=aggregation,
                    candidate_name=candidate_name,
                ),
                candidate_name,
                aggregation,
                result.metrics,
            )
        )

    if not evaluations:
        selected_cfg = test_cfg
        selected_params = _build_selected_params(selected_cfg, selection_source=base_selection_source)
        selected_params["train_window"] = {"start": str(train_cfg.date_range.start), "end": str(train_cfg.date_range.end)}
        selected_params["train_eval_mode"] = str(mode)
        selected_params["candidate_count"] = len(candidates)
        if errors:
            selected_params["selection_errors"] = errors
        return selected_cfg, selected_params

    evaluations.sort(key=lambda item: item[0], reverse=True)
    _, candidate_name, aggregation, metrics = evaluations[0]
    selected_cfg = _clone_config_with_aggregation(test_cfg, aggregation)
    selected_params = _build_selected_params(selected_cfg, selection_source="train_window_search")
    selected_params["base_selection_source"] = str(base_selection_source or "base_config")
    selected_params["candidate_name"] = candidate_name
    selected_params["candidate_count"] = len(candidates)
    selected_params["train_eval_mode"] = str(mode)
    selected_params["train_window"] = {"start": str(train_cfg.date_range.start), "end": str(train_cfg.date_range.end)}
    selected_params["train_score"] = {
        "excess_return_pct": float(metrics.excess_return_pct),
        "total_return_pct": float(metrics.total_return_pct),
        "sharpe": float(metrics.sharpe),
        "max_drawdown_pct": float(metrics.max_drawdown_pct),
        "win_rate_pct": float(metrics.win_rate_pct),
        "trade_count": int(metrics.trade_count),
        "signal_count": int(metrics.signal_count),
    }
    selected_params["candidate_scores"] = [
        {
            "candidate_name": name,
            "aggregation": {
                "long_open_threshold": int(agg.long_open_threshold),
                "short_open_threshold": int(agg.short_open_threshold),
                "close_threshold": int(agg.close_threshold),
            },
            "excess_return_pct": float(item_metrics.excess_return_pct),
            "total_return_pct": float(item_metrics.total_return_pct),
            "sharpe": float(item_metrics.sharpe),
            "max_drawdown_pct": float(item_metrics.max_drawdown_pct),
            "trade_count": int(item_metrics.trade_count),
        }
        for _, name, agg, item_metrics in evaluations
    ]
    if errors:
        selected_params["selection_errors"] = errors
    return selected_cfg, selected_params


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
        "buy_hold_return_pct",
        "risk_parity_return_pct",
        "momentum_return_pct",
        "excess_return_vs_risk_parity_pct",
        "excess_return_vs_momentum_pct",
        "signal_count",
        "signal_days",
        "fallback_reason",
        "selected_params_json",
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
                    "buy_hold_return_pct": f"{row.buy_hold_return_pct:.8f}",
                    "risk_parity_return_pct": f"{row.risk_parity_return_pct:.8f}",
                    "momentum_return_pct": f"{row.momentum_return_pct:.8f}",
                    "excess_return_vs_risk_parity_pct": f"{row.excess_return_vs_risk_parity_pct:.8f}",
                    "excess_return_vs_momentum_pct": f"{row.excess_return_vs_momentum_pct:.8f}",
                    "signal_count": row.signal_count,
                    "signal_days": row.signal_days,
                    "fallback_reason": row.fallback_reason,
                    "selected_params_json": json.dumps(row.selected_params, ensure_ascii=True, sort_keys=True),
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
    rollup = _build_baseline_rollup(rows)
    avg_excess_vs_risk_parity = _avg([float(row.excess_return_vs_risk_parity_pct) for row in rows])
    avg_excess_vs_momentum = _avg([float(row.excess_return_vs_momentum_pct) for row in rows])
    compounded_return_pct = _compound_return_pct([float(row.total_return_pct) for row in rows])
    compounded_excess_vs_risk_parity = compounded_return_pct - float(rollup["risk_parity_compounded_return_pct"])
    compounded_excess_vs_momentum = compounded_return_pct - float(rollup["momentum_compounded_return_pct"])
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
        "compounded_return_pct": float(compounded_return_pct),
        "avg_buy_hold_return_pct": float(rollup["avg_buy_hold_return_pct"]),
        "avg_risk_parity_return_pct": float(rollup["avg_risk_parity_return_pct"]),
        "avg_momentum_return_pct": float(rollup["avg_momentum_return_pct"]),
        "buy_hold_compounded_return_pct": float(rollup["buy_hold_compounded_return_pct"]),
        "risk_parity_compounded_return_pct": float(rollup["risk_parity_compounded_return_pct"]),
        "momentum_compounded_return_pct": float(rollup["momentum_compounded_return_pct"]),
        "avg_excess_return_vs_risk_parity_pct": float(avg_excess_vs_risk_parity),
        "avg_excess_return_vs_momentum_pct": float(avg_excess_vs_momentum),
        "compounded_excess_return_vs_risk_parity_pct": float(compounded_excess_vs_risk_parity),
        "compounded_excess_return_vs_momentum_pct": float(compounded_excess_vs_momentum),
        "best_baseline_name": str(rollup["best_baseline_name"]),
        "best_baseline_return_pct": float(rollup["best_baseline_return_pct"]),
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
                "buy_hold_return_pct": row.buy_hold_return_pct,
                "risk_parity_return_pct": row.risk_parity_return_pct,
                "momentum_return_pct": row.momentum_return_pct,
                "excess_return_vs_risk_parity_pct": row.excess_return_vs_risk_parity_pct,
                "excess_return_vs_momentum_pct": row.excess_return_vs_momentum_pct,
                "signal_count": row.signal_count,
                "signal_days": row.signal_days,
                "fallback_reason": row.fallback_reason,
                "selected_params": row.selected_params,
            }
            for row in rows
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_summary_metrics_json(path: Path, config: BacktestConfig, summary: WalkForwardSummary, rows: list[WalkForwardFoldResult]) -> None:
    initial_equity = float(config.risk.initial_equity)
    strategy_returns = [float(row.total_return_pct) for row in rows]
    rollup = _build_baseline_rollup(rows)
    compounded_return_pct = _compound_return_pct(strategy_returns)
    compounded_buy_hold = float(rollup["buy_hold_compounded_return_pct"])
    compounded_risk_parity = float(rollup["risk_parity_compounded_return_pct"])
    compounded_momentum = float(rollup["momentum_compounded_return_pct"])
    compounded_excess_vs_risk_parity = compounded_return_pct - compounded_risk_parity
    compounded_excess_vs_momentum = compounded_return_pct - compounded_momentum

    if rows:
        start_txt = rows[0].test_start
        end_txt = rows[-1].test_end
        avg_sharpe = _avg([float(row.sharpe) for row in rows])
        avg_win_rate = _avg([float(row.win_rate_pct) for row in rows])
        avg_excess_vs_risk_parity = _avg([float(row.excess_return_vs_risk_parity_pct) for row in rows])
        avg_excess_vs_momentum = _avg([float(row.excess_return_vs_momentum_pct) for row in rows])
        trade_count = int(sum(row.trade_count for row in rows))
        signal_count = int(sum(row.signal_count for row in rows))
        final_equity = _final_equity_after_returns(initial_equity, strategy_returns)
        buy_hold_final = _final_equity_after_returns(initial_equity, [float(row.buy_hold_return_pct) for row in rows])
        risk_parity_final = _final_equity_after_returns(initial_equity, [float(row.risk_parity_return_pct) for row in rows])
        momentum_final = _final_equity_after_returns(initial_equity, [float(row.momentum_return_pct) for row in rows])
    else:
        start_txt = ""
        end_txt = ""
        avg_sharpe = 0.0
        avg_win_rate = 0.0
        avg_excess_vs_risk_parity = 0.0
        avg_excess_vs_momentum = 0.0
        trade_count = 0
        signal_count = 0
        final_equity = initial_equity
        buy_hold_final = initial_equity
        risk_parity_final = initial_equity
        momentum_final = initial_equity

    payload = {
        "run_id": summary.run_id,
        "mode": "walk_forward",
        "start": start_txt,
        "end": end_txt,
        "symbols": list(config.symbols),
        "timeframe": config.timeframe,
        "initial_equity": initial_equity,
        "final_equity": float(final_equity),
        "total_return_pct": float(compounded_return_pct),
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
        "buy_hold_return_pct": float(compounded_buy_hold),
        "risk_parity_final_equity": float(risk_parity_final),
        "risk_parity_return_pct": float(compounded_risk_parity),
        "momentum_final_equity": float(momentum_final),
        "momentum_return_pct": float(compounded_momentum),
        "excess_return_pct": float(compounded_return_pct - compounded_buy_hold),
        "excess_return_vs_risk_parity_pct": float(compounded_excess_vs_risk_parity),
        "excess_return_vs_momentum_pct": float(compounded_excess_vs_momentum),
        "best_baseline_name": str(rollup["best_baseline_name"]),
        "best_baseline_return_pct": float(rollup["best_baseline_return_pct"]),
        "symbol_contributions": [],
        "walk_forward_summary": {
            "fold_count": int(summary.fold_count),
            "history_fold_count": int(summary.history_fold_count),
            "replay_fold_count": int(summary.replay_fold_count),
            "fallback_fold_count": int(summary.fallback_fold_count),
            "avg_return_pct": float(summary.avg_return_pct),
            "avg_max_drawdown_pct": float(summary.avg_max_drawdown_pct),
            "avg_excess_return_pct": float(summary.avg_excess_return_pct),
            "compounded_return_pct": float(compounded_return_pct),
            "avg_buy_hold_return_pct": float(rollup["avg_buy_hold_return_pct"]),
            "avg_risk_parity_return_pct": float(rollup["avg_risk_parity_return_pct"]),
            "avg_momentum_return_pct": float(rollup["avg_momentum_return_pct"]),
            "buy_hold_compounded_return_pct": float(compounded_buy_hold),
            "risk_parity_compounded_return_pct": float(compounded_risk_parity),
            "momentum_compounded_return_pct": float(compounded_momentum),
            "avg_excess_return_vs_risk_parity_pct": float(avg_excess_vs_risk_parity),
            "avg_excess_return_vs_momentum_pct": float(avg_excess_vs_momentum),
            "compounded_excess_return_vs_risk_parity_pct": float(compounded_excess_vs_risk_parity),
            "compounded_excess_return_vs_momentum_pct": float(compounded_excess_vs_momentum),
            "best_baseline_name": str(rollup["best_baseline_name"]),
            "best_baseline_return_pct": float(rollup["best_baseline_return_pct"]),
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
                    "selected_params": row.selected_params,
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
    selected_params: dict[str, object],
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
        buy_hold_return_pct=float(metrics.buy_hold_return_pct),
        risk_parity_return_pct=float(metrics.risk_parity_return_pct),
        momentum_return_pct=float(metrics.momentum_return_pct),
        excess_return_vs_risk_parity_pct=float(metrics.excess_return_vs_risk_parity_pct),
        excess_return_vs_momentum_pct=float(metrics.excess_return_vs_momentum_pct),
        signal_count=int(signal_count),
        signal_days=int(signal_days),
        fallback_reason=str(fallback_reason or ""),
        selected_params=dict(selected_params or {}),
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
    select_train_params: bool = True,
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
        train_cfg = _clone_config_for_window(config, window.train_start, window.train_end)
        selection_source = "base_config"
        if effective_mode == "offline_replay" and fallback_reason:
            run_cfg = _adapt_config_for_offline_replay(fold_cfg)
            train_cfg = _adapt_config_for_offline_replay(train_cfg)
            fallback_reason = f"{fallback_reason}; replay_threshold=70%"
            selection_source = "offline_replay_fallback"

        if select_train_params:
            run_cfg, selected_params = _select_train_window_params(
                train_cfg,
                run_cfg,
                mode=effective_mode,
                fold_run_id=fold_run_id,
                base_selection_source=selection_source,
            )
        else:
            selected_params = _build_selected_params(run_cfg, selection_source=selection_source)

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
                selected_params=selected_params,
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
