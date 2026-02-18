"""Tests for comparison artifacts between history and rule replay modes."""

from __future__ import annotations

import json
from pathlib import Path

from src.backtest.comparison import build_comparison_summary, write_comparison_artifacts
from src.backtest.models import Metrics
from src.backtest.runner import RunnerResult


def _mk_metrics(
    run_id: str,
    mode: str,
    ret: float,
    dd: float,
    trades: int,
    excess: float,
    *,
    signal_count: int = 100,
    signal_type_counts: dict[str, int] | None = None,
    direction_counts: dict[str, int] | None = None,
    timeframe_counts: dict[str, int] | None = None,
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
        signal_type_counts=signal_type_counts or {},
        direction_counts=direction_counts or {},
        timeframe_counts=timeframe_counts or {},
    )


def test_write_comparison_artifacts(tmp_path: Path) -> None:
    history = RunnerResult(
        run_id="cmp-001-history",
        output_dir=tmp_path / "history",
        latest_dir=tmp_path / "latest",
        metrics=_mk_metrics(
            "cmp-001-history",
            "history_signal",
            ret=-2.0,
            dd=8.0,
            trades=20,
            excess=3.0,
            signal_count=100,
            signal_type_counts={"rule_a": 70, "rule_b": 20, "rule_c": 10, "rule_d": 5},
            direction_counts={"BUY": 60, "SELL": 40},
            timeframe_counts={"1m": 80, "5m": 20},
        ),
    )
    rule = RunnerResult(
        run_id="cmp-001-rules",
        output_dir=tmp_path / "rules",
        latest_dir=tmp_path / "latest",
        metrics=_mk_metrics(
            "cmp-001-rules",
            "offline_rule_replay",
            ret=1.5,
            dd=4.5,
            trades=12,
            excess=6.2,
            signal_count=140,
            signal_type_counts={"rule_x": 50, "rule_a": 30, "rule_b": 40},
            direction_counts={"BUY": 120, "SELL": 20},
            timeframe_counts={"1m": 100, "15m": 40},
        ),
    )

    summary = build_comparison_summary("cmp-001", history, rule)
    out_dir = write_comparison_artifacts(tmp_path, summary)

    assert out_dir.exists()
    assert (out_dir / "comparison.json").exists()
    assert (out_dir / "comparison.md").exists()

    payload = json.loads((out_dir / "comparison.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == "cmp-001"
    assert payload["history_run_id"] == "cmp-001-history"
    assert payload["rule_run_id"] == "cmp-001-rules"
    assert payload["delta_return_pct"] == 3.5
    assert payload["delta_trade_count"] == -8
    assert payload["history_signal_count"] == 100
    assert payload["rule_signal_count"] == 140
    assert payload["delta_signal_count"] == 40

    assert payload["history_signal_type_counts"]["rule_a"] == 70
    assert payload["rule_signal_type_counts"]["rule_x"] == 50

    signal_deltas = {row["key"]: row["delta"] for row in payload["signal_type_delta_top"]}
    assert signal_deltas["rule_x"] == 50
    assert signal_deltas["rule_a"] == -40

    assert round(payload["history_direction_mix"]["buy_ratio_pct"], 2) == 60.00
    assert round(payload["rule_direction_mix"]["buy_ratio_pct"], 2) == 85.71
    assert round(payload["delta_buy_ratio_pct"], 2) == 25.71

    overlap = payload["rule_overlap"]
    assert overlap["history_rule_types"] == 4
    assert overlap["rule_rule_types"] == 3
    assert overlap["shared_rule_types"] == 2
    assert round(overlap["jaccard_pct"], 2) == 40.00
    assert round(overlap["history_coverage_pct"], 2) == 50.00
    assert round(overlap["rule_overlap_pct"], 2) == 66.67

    missing = {row["key"]: row["delta"] for row in payload["missing_history_rules_top"]}
    assert missing["rule_c"] == -10
    assert missing["rule_d"] == -5

    created = {row["key"]: row["delta"] for row in payload["new_rule_types_top"]}
    assert created["rule_x"] == 50

    assert payload["timeframe_overlap"] == ["1m"]
    assert payload["history_timeframe_profile"]["dominant"] == "1m"
    assert payload["rule_timeframe_profile"]["dominant"] == "1m"
    assert payload["missing_history_rules_diagnostics"] == []

    md_text = (out_dir / "comparison.md").read_text(encoding="utf-8")
    assert "Rule Alignment" in md_text
    assert "Missing in Rule Replay" in md_text
    assert "New in Rule Replay" in md_text
    assert "Top Signal-Type Delta" in md_text
    assert "Timeframe Delta" in md_text
    assert "Direction Delta" in md_text


def test_write_comparison_artifacts_with_rule_diagnostics(tmp_path: Path) -> None:
    history = RunnerResult(
        run_id="cmp-002-history",
        output_dir=tmp_path / "history",
        latest_dir=tmp_path / "latest",
        metrics=_mk_metrics(
            "cmp-002-history",
            "history_signal",
            ret=-1.0,
            dd=6.0,
            trades=10,
            excess=1.0,
            signal_count=60,
            signal_type_counts={"rule_missing": 30, "rule_shared": 30},
            direction_counts={"BUY": 40, "SELL": 20},
            timeframe_counts={"1m": 60},
        ),
    )
    rule_output = tmp_path / "rules"
    rule_output.mkdir(parents=True, exist_ok=True)
    (rule_output / "rule_replay_diagnostics.json").write_text(
        json.dumps(
            {
                "table_count": 1,
                "row_count": 10,
                "signal_count": 5,
                "rule_counters": {
                    "rule_missing": {
                        "evaluated": 120,
                        "timeframe_filtered": 20,
                        "volume_filtered": 5,
                        "condition_failed": 80,
                        "cooldown_blocked": 10,
                        "triggered": 5,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    rule = RunnerResult(
        run_id="cmp-002-rules",
        output_dir=rule_output,
        latest_dir=tmp_path / "latest",
        metrics=_mk_metrics(
            "cmp-002-rules",
            "offline_rule_replay",
            ret=-0.2,
            dd=3.0,
            trades=8,
            excess=1.8,
            signal_count=35,
            signal_type_counts={"rule_shared": 35},
            direction_counts={"BUY": 20, "SELL": 15},
            timeframe_counts={"1m": 35},
        ),
    )

    summary = build_comparison_summary("cmp-002", history, rule)
    out_dir = write_comparison_artifacts(tmp_path, summary, rule_run_dir=rule_output)

    payload = json.loads((out_dir / "comparison.json").read_text(encoding="utf-8"))
    rows = payload["missing_history_rules_diagnostics"]
    assert len(rows) == 1
    row = rows[0]
    assert row["key"] == "rule_missing"
    assert row["evaluated"] == 120
    assert row["condition_failed"] == 80
    assert row["primary_block_reason"] == "condition_failed"
    assert round(row["trigger_rate_pct"], 2) == 4.17

    md_text = (out_dir / "comparison.md").read_text(encoding="utf-8")
    assert "Missing Rule Diagnostics" in md_text


def test_write_comparison_artifacts_marks_timeframe_no_data_reason(tmp_path: Path) -> None:
    history = RunnerResult(
        run_id="cmp-003-history",
        output_dir=tmp_path / "history",
        latest_dir=tmp_path / "latest",
        metrics=_mk_metrics(
            "cmp-003-history",
            "history_signal",
            ret=-1.0,
            dd=6.0,
            trades=10,
            excess=1.0,
            signal_count=40,
            signal_type_counts={"rule_missing": 40},
            direction_counts={"BUY": 20, "SELL": 20},
            timeframe_counts={"1m": 40},
        ),
    )

    rule_output = tmp_path / "rules"
    rule_output.mkdir(parents=True, exist_ok=True)
    (rule_output / "rule_replay_diagnostics.json").write_text(
        json.dumps(
            {
                "table_count": 1,
                "row_count": 10,
                "signal_count": 0,
                "rule_counters": {
                    "rule_missing": {
                        "evaluated": 100,
                        "timeframe_filtered": 100,
                        "volume_filtered": 0,
                        "condition_failed": 0,
                        "cooldown_blocked": 0,
                        "triggered": 0,
                    }
                },
                "rule_timeframe_profiles": {
                    "rule_missing": {
                        "configured_timeframes": ["1m"],
                        "observed_timeframes": ["1h", "4h"],
                        "overlap_timeframes": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    rule = RunnerResult(
        run_id="cmp-003-rules",
        output_dir=rule_output,
        latest_dir=tmp_path / "latest",
        metrics=_mk_metrics(
            "cmp-003-rules",
            "offline_rule_replay",
            ret=-0.2,
            dd=3.0,
            trades=8,
            excess=1.8,
            signal_count=0,
            signal_type_counts={},
            direction_counts={},
            timeframe_counts={},
        ),
    )

    summary = build_comparison_summary("cmp-003", history, rule)
    out_dir = write_comparison_artifacts(tmp_path, summary, rule_run_dir=rule_output)

    payload = json.loads((out_dir / "comparison.json").read_text(encoding="utf-8"))
    rows = payload["missing_history_rules_diagnostics"]
    assert len(rows) == 1
    row = rows[0]
    assert row["primary_block_reason"] == "timeframe_no_data"
    assert row["configured_timeframes"] == ["1m"]
    assert row["observed_timeframes"] == ["1h", "4h"]
    assert row["overlap_timeframes"] == []
