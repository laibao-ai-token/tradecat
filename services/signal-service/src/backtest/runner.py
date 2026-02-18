"""Backtest runner orchestration (M1 skeleton)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import REPO_ROOT, get_database_url, get_history_db_path, get_sqlite_path
from .aggregator import aggregate_signal_scores
from .data_loader import load_candles_from_pg, load_signals_from_sqlite, resolve_range
from .execution_engine import run_execution
from .models import BacktestConfig, Bar, Metrics, SignalEvent
from .offline_replay import replay_signals_from_bars
from .rule_replay import replay_signals_from_rules
from .reporter import build_metrics, write_artifacts
from .retention import cleanup_old_runs, update_latest_link
from .state import mark_done, mark_error, mark_running

logger = logging.getLogger(__name__)


@dataclass
class RunnerResult:
    run_id: str
    output_dir: Path
    latest_dir: Path
    metrics: Metrics


def _make_run_id(now: datetime | None = None) -> str:
    dt = now or datetime.now(tz=timezone.utc)
    return dt.strftime("%Y%m%d-%H%M%S")


def _to_bar_symbols(config_symbols: list[str]) -> list[str]:
    out: list[str] = []
    for symbol in config_symbols:
        sym = str(symbol).upper().strip()
        if sym:
            out.append(sym)
    return list(dict.fromkeys(out))


def _safe_state_update(action: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.warning("Backtest state update failed (%s): %s", action, exc)


def _as_int(value: object) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _normalize_tf_list(raw: object) -> list[str]:
    out: list[str] = []
    if isinstance(raw, (list, tuple, set)):
        values = raw
    elif raw is None:
        values = []
    else:
        values = [raw]

    for item in values:
        text = str(item or "").strip().lower()
        if text:
            out.append(text)
    return sorted(set(out))


def _write_rule_replay_diagnostics(output_dir: Path, replay_stats: object) -> None:
    counters_raw = getattr(replay_stats, "rule_counters", {})
    counters_map = counters_raw if isinstance(counters_raw, dict) else {}

    payload_rows: dict[str, dict[str, int | float]] = {}
    for rule_name, counter in counters_map.items():
        if isinstance(counter, dict):
            evaluated = _as_int(counter.get("evaluated"))
            timeframe_filtered = _as_int(counter.get("timeframe_filtered"))
            volume_filtered = _as_int(counter.get("volume_filtered"))
            condition_failed = _as_int(counter.get("condition_failed"))
            cooldown_blocked = _as_int(counter.get("cooldown_blocked"))
            triggered = _as_int(counter.get("triggered"))
        else:
            evaluated = _as_int(getattr(counter, "evaluated", 0))
            timeframe_filtered = _as_int(getattr(counter, "timeframe_filtered", 0))
            volume_filtered = _as_int(getattr(counter, "volume_filtered", 0))
            condition_failed = _as_int(getattr(counter, "condition_failed", 0))
            cooldown_blocked = _as_int(getattr(counter, "cooldown_blocked", 0))
            triggered = _as_int(getattr(counter, "triggered", 0))

        trigger_rate_pct = float(triggered / evaluated * 100.0) if evaluated > 0 else 0.0
        payload_rows[str(rule_name)] = {
            "evaluated": evaluated,
            "timeframe_filtered": timeframe_filtered,
            "volume_filtered": volume_filtered,
            "condition_failed": condition_failed,
            "cooldown_blocked": cooldown_blocked,
            "triggered": triggered,
            "trigger_rate_pct": trigger_rate_pct,
        }

    profiles_raw = getattr(replay_stats, "rule_timeframe_profiles", {})
    profiles_map = profiles_raw if isinstance(profiles_raw, dict) else {}
    payload_profiles: dict[str, dict[str, object]] = {}
    for rule_name, profile in profiles_map.items():
        if isinstance(profile, dict):
            configured = _normalize_tf_list(profile.get("configured_timeframes"))
            observed = _normalize_tf_list(profile.get("observed_timeframes"))
            overlap = _normalize_tf_list(profile.get("overlap_timeframes"))
        else:
            configured = _normalize_tf_list(getattr(profile, "configured_timeframes", []))
            observed = _normalize_tf_list(getattr(profile, "observed_timeframes", []))
            overlap = _normalize_tf_list(getattr(profile, "overlap_timeframes", []))

        if not overlap:
            overlap = sorted(set(configured) & set(observed))

        payload_profiles[str(rule_name)] = {
            "configured_timeframes": configured,
            "observed_timeframes": observed,
            "overlap_timeframes": overlap,
            "has_overlap": bool(overlap),
        }

    payload = {
        "table_count": _as_int(getattr(replay_stats, "table_count", 0)),
        "row_count": _as_int(getattr(replay_stats, "row_count", 0)),
        "signal_count": _as_int(getattr(replay_stats, "signal_count", 0)),
        "rule_counters": payload_rows,
        "rule_timeframe_profiles": payload_profiles,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "rule_replay_diagnostics.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _normalize_mode(raw_mode: str) -> str:
    mode = str(raw_mode or "history_signal").strip().lower()
    alias = {
        "offline_129": "offline_rule_replay",
        "offline_129_replay": "offline_rule_replay",
        "rule_replay": "offline_rule_replay",
    }
    return alias.get(mode, mode)


def _state_stage_for_mode(mode: str) -> str:
    if mode == "history_signal":
        return "loading_signals"
    if mode == "offline_rule_replay":
        return "loading_indicator_tables"
    return "loading_candles"


def _state_message_for_mode(mode: str) -> str:
    if mode == "history_signal":
        return "loading signals from sqlite"
    if mode == "offline_rule_replay":
        return "loading sqlite tables for rule replay"
    return "loading candles from pg for offline replay"


def _load_inputs_history(
    config: BacktestConfig,
    *,
    symbols: list[str],
    start: datetime,
    end: datetime,
    state_path: Path,
    run_id: str,
    mode: str,
) -> tuple[list[SignalEvent], dict[str, list[Bar]], int, str, object | None]:
    signals = load_signals_from_sqlite(
        str(get_history_db_path()),
        symbols=symbols,
        start=start,
        end=end,
        timeframe=config.timeframe,
    )

    current_stage = "loading_candles"
    _safe_state_update(
        "running",
        mark_running,
        state_path,
        run_id=run_id,
        mode=mode,
        stage=current_stage,
        message=f"loading candles from pg symbols={len(symbols)}",
    )

    bars_by_symbol = load_candles_from_pg(
        get_database_url(),
        symbols=symbols,
        start=start,
        end=end,
    )

    bar_count = sum(len(v) for v in bars_by_symbol.values())
    return signals, bars_by_symbol, bar_count, current_stage, None


def _load_inputs_offline_rule_replay(
    config: BacktestConfig,
    *,
    symbols: list[str],
    start: datetime,
    end: datetime,
    state_path: Path,
    run_id: str,
    mode: str,
) -> tuple[list[SignalEvent], dict[str, list[Bar]], int, str, object | None]:
    replay_path = str(get_sqlite_path())
    signals, replay_stats = replay_signals_from_rules(
        replay_path,
        symbols=symbols,
        start=start,
        end=end,
        preferred_timeframe=config.timeframe,
    )

    current_stage = "loading_candles"
    _safe_state_update(
        "running",
        mark_running,
        state_path,
        run_id=run_id,
        mode=mode,
        stage=current_stage,
        message=(
            f"rule replay done tables={replay_stats.table_count} "
            f"rows={replay_stats.row_count} signals={replay_stats.signal_count}; loading candles"
        ),
    )

    bars_by_symbol = load_candles_from_pg(
        get_database_url(),
        symbols=symbols,
        start=start,
        end=end,
    )

    bar_count = sum(len(v) for v in bars_by_symbol.values())
    return signals, bars_by_symbol, bar_count, current_stage, replay_stats


def _load_inputs_offline_replay(
    config: BacktestConfig,
    *,
    symbols: list[str],
    start: datetime,
    end: datetime,
    state_path: Path,
    run_id: str,
    mode: str,
) -> tuple[list[SignalEvent], dict[str, list[Bar]], int, str, object | None]:
    bars_by_symbol = load_candles_from_pg(
        get_database_url(),
        symbols=symbols,
        start=start,
        end=end,
    )

    bar_count = sum(len(v) for v in bars_by_symbol.values())

    current_stage = "replaying_signals"
    _safe_state_update(
        "running",
        mark_running,
        state_path,
        run_id=run_id,
        mode=mode,
        stage=current_stage,
        message=f"replaying signals from bars={bar_count}",
    )

    signals = replay_signals_from_bars(
        bars_by_symbol,
        timeframe=config.timeframe,
    )
    logger.info("Offline replay generated %d synthetic signals", len(signals))
    return signals, bars_by_symbol, bar_count, current_stage, None


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


def run_backtest(
    config: BacktestConfig,
    *,
    mode: str = "history_signal",
    run_id: str | None = None,
    output_dir: Path | None = None,
) -> RunnerResult:
    """Run one backtest and write artifacts to output_dir (or artifacts/backtest/<run_id>)."""

    rid = (run_id or "").strip() or _make_run_id()
    backtest_root = Path(REPO_ROOT) / "artifacts" / "backtest"
    state_path = backtest_root / "run_state.json"
    mode = _normalize_mode(mode)
    current_stage = _state_stage_for_mode(mode)

    _safe_state_update(
        "running",
        mark_running,
        state_path,
        run_id=rid,
        mode=mode,
        stage=current_stage,
        message=_state_message_for_mode(mode),
    )

    try:
        if mode not in {"history_signal", "offline_replay", "offline_rule_replay"}:
            current_stage = "error"
            raise ValueError(f"Unsupported mode: {mode}")

        start, end = resolve_range(config.date_range)
        symbols = _to_bar_symbols(config.symbols)
        if not symbols:
            raise ValueError("No symbols configured for backtest")

        replay_stats: object | None = None
        if mode == "history_signal":
            signals, bars_by_symbol, bar_count, current_stage, replay_stats = _load_inputs_history(
                config,
                symbols=symbols,
                start=start,
                end=end,
                state_path=state_path,
                run_id=rid,
                mode=mode,
            )
        elif mode == "offline_rule_replay":
            signals, bars_by_symbol, bar_count, current_stage, replay_stats = _load_inputs_offline_rule_replay(
                config,
                symbols=symbols,
                start=start,
                end=end,
                state_path=state_path,
                run_id=rid,
                mode=mode,
            )
        else:
            signals, bars_by_symbol, bar_count, current_stage, replay_stats = _load_inputs_offline_replay(
                config,
                symbols=symbols,
                start=start,
                end=end,
                state_path=state_path,
                run_id=rid,
                mode=mode,
            )

        if bar_count == 0:
            raise RuntimeError("No candle data loaded from market_data.candles_1m")

        score_map = aggregate_signal_scores(signals, timeframe=config.timeframe)

        current_stage = "executing"
        _safe_state_update(
            "running",
            mark_running,
            state_path,
            run_id=rid,
            mode=mode,
            stage=current_stage,
            message=f"executing with bars={bar_count} signals={len(signals)}",
        )

        result = run_execution(
            bars_by_symbol=bars_by_symbol,
            score_map=score_map,
            execution=config.execution,
            risk=config.risk,
            aggregation=config.aggregation,
        )

        resolved_output_dir = Path(output_dir) if output_dir is not None else (backtest_root / rid)

        current_stage = "writing"
        _safe_state_update(
            "running",
            mark_running,
            state_path,
            run_id=rid,
            mode=mode,
            stage=current_stage,
            message=f"writing artifacts trades={len(result.trades)}",
        )

        metrics = build_metrics(
            run_id=rid,
            mode=mode,
            start=start,
            end=end,
            symbols=symbols,
            timeframe=config.timeframe,
            initial_equity=config.risk.initial_equity,
            final_equity=result.final_equity,
            trades=result.trades,
            curve=result.equity_curve,
            signal_count=len(signals),
            bar_count=bar_count,
            bars_by_symbol=bars_by_symbol,
            signals=signals,
            strategy_label=config.strategy_label,
            strategy_config_path=config.strategy_config_path,
            strategy_summary=_strategy_summary(config),
        )

        write_artifacts(resolved_output_dir, result.trades, result.equity_curve, metrics)
        if mode == "offline_rule_replay" and replay_stats is not None:
            _write_rule_replay_diagnostics(resolved_output_dir, replay_stats)

        current_stage = "retention"
        _safe_state_update(
            "running",
            mark_running,
            state_path,
            run_id=rid,
            mode=mode,
            stage=current_stage,
            message="updating latest pointer and retention",
        )

        latest_dir = update_latest_link(backtest_root, resolved_output_dir)
        removed = cleanup_old_runs(backtest_root, config.retention.keep_runs)

        _safe_state_update(
            "done",
            mark_done,
            state_path,
            run_id=rid,
            mode=mode,
            latest_run_id=rid,
            message=f"completed trades={len(result.trades)} return={metrics.total_return_pct:+.2f}%",
        )

        logger.info(
            "Backtest completed run_id=%s trades=%d final_equity=%.2f",
            rid,
            len(result.trades),
            result.final_equity,
        )
        if removed:
            logger.info("Retention removed %d old runs", len(removed))

        return RunnerResult(
            run_id=rid,
            output_dir=resolved_output_dir,
            latest_dir=latest_dir,
            metrics=metrics,
        )

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        _safe_state_update(
            "error",
            mark_error,
            state_path,
            run_id=rid,
            mode=mode,
            stage=current_stage,
            error=err,
            message="backtest failed",
        )
        raise
