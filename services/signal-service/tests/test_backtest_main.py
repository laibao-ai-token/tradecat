"""Tests for backtest CLI gating behavior."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.backtest.models import BacktestConfig, DateRange, Metrics
from src.backtest.precheck import BacktestCoverageReport
from src.backtest.runner import RunnerResult
from src.backtest.state import read_state


def _mk_metrics(
    run_id: str,
    mode: str,
    ret: float,
    dd: float,
    trades: int,
    excess: float,
    *,
    signal_count: int,
    signal_type_counts: dict[str, int],
    direction_counts: dict[str, int],
    timeframe_counts: dict[str, int],
) -> Metrics:
    return Metrics(
        run_id=run_id,
        mode=mode,
        start="2026-01-01 00:00:00+00:00",
        end="2026-01-10 00:00:00+00:00",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        initial_equity=10_000.0,
        final_equity=10_000.0 * (1.0 + ret / 100.0),
        total_return_pct=ret,
        max_drawdown_pct=dd,
        sharpe=1.23,
        trade_count=trades,
        win_rate_pct=50.0,
        profit_factor=1.2,
        avg_holding_minutes=10.0,
        signal_count=signal_count,
        bar_count=1000,
        buy_hold_final_equity=9_500.0,
        buy_hold_return_pct=-5.0,
        excess_return_pct=excess,
        symbol_contributions=[],
        signal_type_counts=signal_type_counts,
        direction_counts=direction_counts,
        timeframe_counts=timeframe_counts,
    )


def _mk_coverage() -> BacktestCoverageReport:
    return BacktestCoverageReport(
        start="2026-01-01 00:00:00+00:00",
        end="2026-01-10 00:00:00+00:00",
        timeframe="1m",
        symbols=["BTCUSDT", "ETHUSDT"],
        signal_count=120,
        signal_days=9,
        signal_min_ts="2026-01-01 00:00:00+00:00",
        signal_max_ts="2026-01-09 23:59:00+00:00",
        candle_count=1000,
        candle_min_ts="2026-01-01 00:00:00+00:00",
        candle_max_ts="2026-01-09 23:59:00+00:00",
        expected_candle_count=1000,
        candle_coverage_pct=100.0,
        symbol_rows=[],
    )


def test_collect_alignment_gate_failures_for_score_and_risk() -> None:
    from src.backtest.__main__ import _collect_alignment_gate_failures

    payload = {
        "alignment_score": 48.6,
        "alignment_status": "fail",
        "alignment_risk_level": "critical",
    }

    failures = _collect_alignment_gate_failures(
        payload,
        min_alignment_score=70.0,
        max_alignment_risk_level="medium",
    )

    assert len(failures) == 2
    assert "alignment score gate failed" in failures[0]
    assert "alignment risk gate failed" in failures[1]


def test_shrink_compare_cfg_to_history_window() -> None:
    from src.backtest.__main__ import _shrink_compare_cfg_to_history_window

    cfg = BacktestConfig(
        date_range=DateRange(
            start="2026-01-01 00:00:00+00:00",
            end="2026-01-10 00:00:00+00:00",
        )
    )
    coverage = BacktestCoverageReport(
        start="2026-01-01 00:00:00+00:00",
        end="2026-01-10 00:00:00+00:00",
        timeframe="1m",
        symbols=["BTCUSDT"],
        signal_count=10,
        signal_days=3,
        signal_min_ts="2026-01-04 12:03:12+00:00",
        signal_max_ts="2026-01-07 01:02:03+00:00",
        candle_count=100,
        candle_min_ts="2026-01-01 00:00:00+00:00",
        candle_max_ts="2026-01-10 00:00:00+00:00",
        expected_candle_count=100,
        candle_coverage_pct=100.0,
        symbol_rows=[],
    )

    shrunk = _shrink_compare_cfg_to_history_window(cfg, coverage)

    assert shrunk.date_range.start == "2026-01-04 12:03:00+00:00"
    assert shrunk.date_range.end == "2026-01-07 01:03:00+00:00"


def test_main_compare_mode_returns_gate_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.backtest import __main__ as main_mod

    cfg_path = tmp_path / "demo.yaml"
    cfg_path.write_text("market: crypto\n", encoding="utf-8")

    monkeypatch.setattr(main_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(main_mod, "load_config", lambda *args, **kwargs: BacktestConfig())
    monkeypatch.setattr(main_mod, "compute_coverage_report", lambda cfg: _mk_coverage())
    monkeypatch.setattr(main_mod, "format_coverage_lines", lambda coverage: [])

    def _fake_run_backtest(cfg, *, mode: str, run_id: str | None, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        if mode == "history_signal":
            metrics = _mk_metrics(
                run_id or "cmp-ci-history",
                mode,
                ret=-2.0,
                dd=8.0,
                trades=20,
                excess=3.0,
                signal_count=100,
                signal_type_counts={"rule_a": 70, "rule_b": 20, "rule_c": 10, "rule_d": 5},
                direction_counts={"BUY": 60, "SELL": 40},
                timeframe_counts={"1m": 80, "5m": 20},
            )
        else:
            metrics = _mk_metrics(
                run_id or "cmp-ci-rules",
                mode,
                ret=1.5,
                dd=4.5,
                trades=12,
                excess=6.2,
                signal_count=140,
                signal_type_counts={"rule_x": 50, "rule_a": 30, "rule_b": 40},
                direction_counts={"BUY": 120, "SELL": 20},
                timeframe_counts={"1m": 100, "15m": 40},
            )
        return RunnerResult(
            run_id=metrics.run_id,
            output_dir=output_dir,
            latest_dir=tmp_path / "artifacts" / "backtest" / "latest",
            metrics=metrics,
        )

    monkeypatch.setattr(main_mod, "run_backtest", _fake_run_backtest)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest",
            "--config",
            str(cfg_path),
            "--mode",
            "compare_history_rule",
            "--run-id",
            "cmp-ci",
            "--alignment-min-score",
            "70",
            "--alignment-max-risk-level",
            "medium",
        ],
    )

    rc = main_mod.main()

    assert rc == main_mod._ALIGNMENT_GATE_EXIT_CODE
    state = read_state(tmp_path / "artifacts" / "backtest" / "run_state.json")
    assert state.status == "error"
    assert state.mode == "compare_history_rule"
    assert state.latest_run_id == "cmp-ci-rules"
    assert "alignment_gate=failed" in state.message
    assert "alignment score gate failed" in (state.error or "")

    compare_path = next((tmp_path / "artifacts" / "backtest").glob("*/cmp-ci-compare/comparison.json"))
    payload = json.loads(compare_path.read_text(encoding="utf-8"))
    assert payload["alignment_risk_level"] == "critical"
    assert payload["alignment_score"] < 70.0


def test_main_rejects_alignment_gate_outside_compare_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.backtest import __main__ as main_mod

    cfg_path = tmp_path / "demo.yaml"
    cfg_path.write_text("market: crypto\n", encoding="utf-8")

    monkeypatch.setattr(main_mod, "load_config", lambda *args, **kwargs: BacktestConfig())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest",
            "--config",
            str(cfg_path),
            "--mode",
            "history_signal",
            "--alignment-min-score",
            "70",
        ],
    )

    with pytest.raises(ValueError, match="only work with --mode compare_history_rule"):
        main_mod.main()
