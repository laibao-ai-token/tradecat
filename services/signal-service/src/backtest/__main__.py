"""CLI entrypoint for signal-service backtest."""

from __future__ import annotations

import os
import argparse
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Keep backtests reproducible unless the caller explicitly opts into a
# rule-timeframe override in the current process environment.
if "SIGNAL_RULE_TIMEFRAMES" not in os.environ:
    os.environ["SIGNAL_RULE_TIMEFRAMES"] = ""

from ..config import REPO_ROOT
from .comparison import build_comparison_summary, write_comparison_artifacts
from .config_loader import load_config
from .data_loader import floor_minute, parse_timestamp, resolve_range
from .models import BacktestConfig, DateRange
from .precheck import (
    BacktestCoverageReport,
    build_coverage_guard_thresholds,
    collect_coverage_guard_failures,
    compute_coverage_report,
    format_coverage_lines,
)
from .runner import run_backtest
from .state import mark_done, mark_error, mark_running
from .walkforward import run_walk_forward

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)


_ALIGNMENT_RISK_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_ALIGNMENT_GATE_EXIT_CODE = 2


def _coerce_optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _normalize_alignment_risk_level(raw: object) -> str:
    level = str(raw or "").strip().lower()
    if level in _ALIGNMENT_RISK_LEVEL_ORDER:
        return level
    return "unknown"


def _load_alignment_payload(compare_dir: Path) -> dict[str, object]:
    payload_path = Path(compare_dir) / "comparison.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"comparison payload must be a JSON object: {payload_path}")
    return payload


def _collect_alignment_gate_failures(
    payload: dict[str, object],
    *,
    min_alignment_score: float | None = None,
    max_alignment_risk_level: str | None = None,
) -> list[str]:
    failures: list[str] = []

    score = _coerce_optional_float(payload.get("alignment_score"))
    risk_level = _normalize_alignment_risk_level(payload.get("alignment_risk_level"))
    status = str(payload.get("alignment_status") or "--").strip().lower() or "--"

    if min_alignment_score is not None:
        threshold = float(min_alignment_score)
        if score is None:
            failures.append("alignment_score is missing; cannot evaluate --alignment-min-score gate")
        elif score < threshold:
            failures.append(f"alignment score gate failed: {score:.2f} < {threshold:.2f}")

    if max_alignment_risk_level is not None:
        max_level = _normalize_alignment_risk_level(max_alignment_risk_level)
        if max_level == "unknown":
            failures.append(f"invalid max alignment risk level: {max_alignment_risk_level}")
        elif risk_level == "unknown":
            failures.append("alignment_risk_level is missing; cannot evaluate --alignment-max-risk-level gate")
        elif _ALIGNMENT_RISK_LEVEL_ORDER[risk_level] > _ALIGNMENT_RISK_LEVEL_ORDER[max_level]:
            failures.append(
                f"alignment risk gate failed: level={risk_level} exceeds max={max_level} (status={status})"
            )

    return failures


def _alignment_gate_requested(*, min_alignment_score: float | None, max_alignment_risk_level: str | None) -> bool:
    return min_alignment_score is not None or max_alignment_risk_level is not None


def _collect_precheck_failures(
    coverage: BacktestCoverageReport,
    *,
    mode: str,
    min_signal_days: int,
    min_signal_count: int,
    min_candle_coverage_pct: float,
) -> list[str]:
    return collect_coverage_guard_failures(
        mode=mode,
        signal_days=coverage.signal_days,
        signal_count=coverage.signal_count,
        candle_count=coverage.candle_count,
        expected_candle_count=coverage.expected_candle_count,
        candle_coverage_pct=coverage.candle_coverage_pct,
        min_signal_days=min_signal_days,
        min_signal_count=min_signal_count,
        min_candle_coverage_pct=min_candle_coverage_pct,
    )




def _normalize_mode(raw_mode: str) -> str:
    mode = str(raw_mode or "history_signal").strip().lower()
    alias = {
        "offline_129_replay": "offline_rule_replay",
    }
    return alias.get(mode, mode)


def _shrink_compare_cfg_to_history_window(cfg: BacktestConfig, coverage: BacktestCoverageReport) -> BacktestConfig:
    """Clamp compare-mode runs to the actual history-signal overlap window when available."""

    signal_start = parse_timestamp(coverage.signal_min_ts)
    signal_end = parse_timestamp(coverage.signal_max_ts)
    if signal_start is None or signal_end is None:
        return cfg

    requested_start, requested_end = resolve_range(cfg.date_range)
    overlap_start = max(requested_start, floor_minute(signal_start))
    overlap_end = min(requested_end, floor_minute(signal_end) + timedelta(minutes=1))
    if overlap_end <= overlap_start:
        return cfg

    start_text = overlap_start.isoformat(sep=" ")
    end_text = overlap_end.isoformat(sep=" ")
    if start_text == str(cfg.date_range.start or "").strip() and end_text == str(cfg.date_range.end or "").strip():
        return cfg

    return replace(cfg, date_range=DateRange(start=start_text, end=end_text))


def main() -> int:
    parser = argparse.ArgumentParser(description="Signal Service Backtest (M1 minimal closed loop)")
    parser.add_argument(
        "--config",
        default="src/backtest/strategies/default.crypto.yaml",
        help="Config file path (JSON/YAML)",
    )
    parser.add_argument("--start", default="", help="Override start time, e.g. 2026-01-01 00:00:00")
    parser.add_argument("--end", default="", help="Override end time, e.g. 2026-02-01 00:00:00")
    parser.add_argument("--symbols", default="", help="Override symbols, comma-separated")
    parser.add_argument("--run-id", default="", help="Optional run_id")
    parser.add_argument(
        "--mode",
        default="history_signal",
        choices=[
            "history_signal",
            "offline_replay",
            "offline_rule_replay",
            "offline_129_replay",
            "compare_history_rule",
        ],
        help="Run mode",
    )
    parser.add_argument("--fee-bps", type=float, default=None, help="Override execution.fee_bps")
    parser.add_argument("--slippage-bps", type=float, default=None, help="Override execution.slippage_bps")
    parser.add_argument(
        "--allow-long",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override execution.allow_long",
    )
    parser.add_argument(
        "--allow-short",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override execution.allow_short",
    )
    parser.add_argument("--min-hold-minutes", type=int, default=None, help="Override execution.min_hold_minutes")
    parser.add_argument(
        "--neutral-confirm-minutes",
        type=int,
        default=None,
        help="Override execution.neutral_confirm_minutes (>=1)",
    )
    parser.add_argument("--initial-equity", type=float, default=None, help="Override risk.initial_equity")
    parser.add_argument("--leverage", type=float, default=None, help="Override risk.leverage")
    parser.add_argument("--position-size-pct", type=float, default=None, help="Override risk.position_size_pct")
    parser.add_argument("--wf-train-days", type=int, default=None, help="Override walk_forward.train_days")
    parser.add_argument("--wf-test-days", type=int, default=None, help="Override walk_forward.test_days")
    parser.add_argument("--wf-step-days", type=int, default=None, help="Override walk_forward.step_days")
    parser.add_argument("--long-threshold", type=int, default=None, help="Override aggregation.long_open_threshold")
    parser.add_argument("--short-threshold", type=int, default=None, help="Override aggregation.short_open_threshold")
    parser.add_argument("--close-threshold", type=int, default=None, help="Override aggregation.close_threshold")
    parser.add_argument("--check-only", action="store_true", help="Only run data coverage precheck and exit")
    parser.add_argument(
        "--min-signal-days",
        type=int,
        default=7,
        help="Precheck guard for history_signal mode; set 0 to disable",
    )
    parser.add_argument(
        "--min-signal-count",
        type=int,
        default=200,
        help="Precheck guard for history_signal mode; set 0 to disable",
    )
    parser.add_argument(
        "--min-candle-coverage-pct",
        type=float,
        default=95.0,
        help="Minimum candle coverage percentage required by precheck",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass precheck threshold failures and continue",
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run rolling walk-forward tests (train/test windows) and output summary",
    )
    parser.add_argument(
        "--walk-forward-max-folds",
        type=int,
        default=0,
        help="Optional cap for walk-forward folds (0 means no cap)",
    )
    parser.add_argument(
        "--walk-forward-auto-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto fallback to offline_replay for thin history-signal folds",
    )
    parser.add_argument(
        "--alignment-min-score",
        type=float,
        default=None,
        help="Compare-mode gate: fail when alignment_score is below this threshold",
    )
    parser.add_argument(
        "--alignment-max-risk-level",
        choices=["low", "medium", "high", "critical"],
        default=None,
        help="Compare-mode gate: fail when alignment_risk_level is above this threshold",
    )
    args = parser.parse_args()

    config_path: Path | None
    if args.config:
        config_path = Path(str(args.config)).expanduser().resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
    else:
        config_path = None

    cfg = load_config(
        config_path,
        start=args.start or None,
        end=args.end or None,
        symbols=args.symbols or None,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        allow_long=args.allow_long,
        allow_short=args.allow_short,
        min_hold_minutes=args.min_hold_minutes,
        neutral_confirm_minutes=args.neutral_confirm_minutes,
        initial_equity=args.initial_equity,
        leverage=args.leverage,
        position_size_pct=args.position_size_pct,
        wf_train_days=args.wf_train_days,
        wf_test_days=args.wf_test_days,
        wf_step_days=args.wf_step_days,
        long_open_threshold=args.long_threshold,
        short_open_threshold=args.short_threshold,
        close_threshold=args.close_threshold,
    )

    mode = _normalize_mode(args.mode)

    if _alignment_gate_requested(
        min_alignment_score=args.alignment_min_score,
        max_alignment_risk_level=args.alignment_max_risk_level,
    ) and mode != "compare_history_rule":
        raise ValueError(
            "--alignment-min-score/--alignment-max-risk-level only work with --mode compare_history_rule"
        )

    coverage = compute_coverage_report(cfg)
    for line in format_coverage_lines(coverage):
        logger.info("precheck: %s", line)

    input_quality_gate_thresholds = build_coverage_guard_thresholds(
        min_signal_days=args.min_signal_days,
        min_signal_count=args.min_signal_count,
        min_candle_coverage_pct=args.min_candle_coverage_pct,
    )
    failures = _collect_precheck_failures(
        coverage,
        mode=mode,
        min_signal_days=args.min_signal_days,
        min_signal_count=args.min_signal_count,
        min_candle_coverage_pct=args.min_candle_coverage_pct,
    )

    if mode == "history_signal" and coverage.signal_count <= 0:
        logger.warning("Precheck: no signal rows in selected window; consider --mode offline_replay")
    if mode == "compare_history_rule" and coverage.signal_count <= 0:
        logger.info("Precheck: history leg may be sparse; compare mode will still execute both legs")
    if mode == "offline_replay" and coverage.signal_count <= 0:
        logger.info("Precheck: signal rows are empty, but offline_replay can still run from candles")
    if mode == "offline_rule_replay":
        logger.info("Precheck: offline_rule_replay uses sqlite indicator tables instead of signal_history")

    if failures:
        for msg in failures:
            logger.error("precheck guard: %s", msg)
        if not args.force:
            raise RuntimeError(
                "Precheck failed. Tune window/thresholds, or use --mode offline_replay/offline_rule_replay, "
                "or pass --force to continue anyway"
            )
        logger.warning("--force enabled: continuing despite %d precheck guard failures", len(failures))

    if args.check_only:
        logger.info("check-only done")
        return 0

    run_id = (str(args.run_id).strip() or None)
    backtest_root = Path(REPO_ROOT) / "artifacts" / "backtest"
    session_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    session_dir = backtest_root / session_id


    if args.walk_forward and mode == "compare_history_rule":
        raise ValueError("--walk-forward cannot be combined with --mode compare_history_rule")

    if mode == "compare_history_rule":
        base_run_id = run_id or datetime.now(tz=timezone.utc).strftime("cmp-%Y%m%d-%H%M%S")
        state_path = backtest_root / "run_state.json"
        compare_cfg = _shrink_compare_cfg_to_history_window(cfg, coverage)
        if compare_cfg is not cfg:
            logger.info(
                "compare window narrowed to history overlap: %s -> %s",
                compare_cfg.date_range.start,
                compare_cfg.date_range.end,
            )

        mark_running(
            state_path,
            run_id=base_run_id,
            mode=mode,
            stage="compare_modes",
            message="running history and rule replay backtests",
        )

        try:
            history_result = run_backtest(
                compare_cfg,
                mode="history_signal",
                run_id=f"{base_run_id}-history",
                output_dir=session_dir / f"{base_run_id}-history",
            )
            rule_result = run_backtest(
                compare_cfg,
                mode="offline_rule_replay",
                run_id=f"{base_run_id}-rules",
                output_dir=session_dir / f"{base_run_id}-rules",
            )
            summary = build_comparison_summary(base_run_id, history_result, rule_result)
            compare_dir = write_comparison_artifacts(
                session_dir,
                summary,
                rule_run_dir=rule_result.output_dir,
            )
        except Exception as exc:
            mark_error(
                state_path,
                run_id=base_run_id,
                mode=mode,
                stage="compare_modes",
                error=f"{type(exc).__name__}: {exc}",
                message="compare mode failed",
            )
            raise

        gate_failures = _collect_alignment_gate_failures(
            _load_alignment_payload(compare_dir),
            min_alignment_score=args.alignment_min_score,
            max_alignment_risk_level=args.alignment_max_risk_level,
        )
        gate_failed = bool(gate_failures)
        if gate_failed:
            for msg in gate_failures:
                logger.error("alignment gate: %s", msg)

        done_message = (
            f"compare done history={history_result.metrics.total_return_pct:+.2f}% "
            f"rule={rule_result.metrics.total_return_pct:+.2f}%"
        )
        if gate_failed:
            done_message = f"{done_message} | alignment_gate=failed"

        if gate_failed:
            mark_error(
                state_path,
                run_id=base_run_id,
                mode=mode,
                stage="compare_modes",
                error="; ".join(gate_failures),
                message=done_message,
                latest_run_id=rule_result.run_id,
            )
        else:
            mark_done(
                state_path,
                run_id=base_run_id,
                mode=mode,
                latest_run_id=rule_result.run_id,
                message=done_message,
            )

        logger.info("compare run_id=%s", base_run_id)
        logger.info("history run=%s return=%+.2f%%", history_result.run_id, history_result.metrics.total_return_pct)
        logger.info("rule run=%s return=%+.2f%%", rule_result.run_id, rule_result.metrics.total_return_pct)
        logger.info("session=%s", session_dir)
        logger.info("comparison output=%s", compare_dir)
        if gate_failed:
            logger.error("alignment gate blocked compare run with exit code %d", _ALIGNMENT_GATE_EXIT_CODE)
            return _ALIGNMENT_GATE_EXIT_CODE
        return 0

    if args.walk_forward:
        wf_run_id = run_id or f"wf-{mode}"
        state_path = Path(REPO_ROOT) / "artifacts" / "backtest" / "run_state.json"

        mark_running(
            state_path,
            run_id=wf_run_id,
            mode=mode,
            stage="walk_forward",
            message="walk-forward executing folds",
        )

        try:
            summary = run_walk_forward(
                cfg,
                mode=mode,
                run_id=wf_run_id,
                output_dir=session_dir,
                max_folds=max(0, int(args.walk_forward_max_folds)),
                auto_fallback=bool(args.walk_forward_auto_fallback),
                min_signal_days=max(0, int(args.min_signal_days)),
                min_signal_count=max(0, int(args.min_signal_count)),
            )
        except Exception as exc:
            mark_error(
                state_path,
                run_id=wf_run_id,
                mode=mode,
                stage="walk_forward",
                error=f"{type(exc).__name__}: {exc}",
                message="walk-forward failed",
            )
            raise

        mark_done(
            state_path,
            run_id=wf_run_id,
            mode=mode,
            latest_run_id=wf_run_id,
            message=(
                f"walk-forward done folds={summary.fold_count} "
                f"avg={summary.avg_return_pct:+.2f}% "
                f"excess={summary.avg_excess_return_pct:+.2f}%"
            ),
        )

        logger.info("walk-forward run_id=%s", summary.run_id)
        logger.info("walk-forward output=%s", summary.output_dir)
        logger.info(
            "walk-forward folds=%d avg_return=%+.2f%% median=%+.2f%% "
            "positive_rate=%.2f%% avg_excess=%+.2f%% history=%d replay=%d fallback=%d",
            summary.fold_count,
            summary.avg_return_pct,
            summary.median_return_pct,
            summary.positive_fold_rate_pct,
            summary.avg_excess_return_pct,
            summary.history_fold_count,
            summary.replay_fold_count,
            summary.fallback_fold_count,
        )
        return 0

    result = run_backtest(
        cfg,
        mode=mode,
        run_id=run_id,
        output_dir=session_dir,
        input_quality_signal_days=coverage.signal_days,
        input_quality_gate_failures=failures,
        input_quality_gate_thresholds=input_quality_gate_thresholds,
    )

    logger.info("run_id=%s", result.run_id)
    logger.info("output=%s", result.output_dir)
    logger.info("latest=%s", result.latest_dir)
    logger.info(
        "return=%+.2f%% max_dd=%.2f%% sharpe=%.2f trades=%d",
        result.metrics.total_return_pct,
        result.metrics.max_drawdown_pct,
        result.metrics.sharpe,
        result.metrics.trade_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
