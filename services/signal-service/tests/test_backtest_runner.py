"""Backtest M1 tests."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.backtest.aggregator import aggregate_signal_scores
from src.backtest.config_loader import load_config
from src.backtest.execution_engine import run_execution
from src.backtest.offline_replay import replay_signals_from_bars
from src.backtest.models import (
    AggregationConfig,
    BacktestConfig,
    Bar,
    DateRange,
    ExecutionConfig,
    RiskConfig,
    SignalEvent,
)
from src.backtest.runner import run_backtest


def test_load_config_with_json_text_and_overrides(tmp_path: Path) -> None:
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        """
        {
          "symbols": ["btc_usdt", "eth-usdt"],
          "risk": {
            "initial_equity": 2000,
            "position_size_pct": 0.5,
            "maintenance_margin_ratio": 0.01,
            "liquidation_fee_bps": 25,
            "liquidation_buffer_bps": 12
          },
          "execution": {
            "fee_bps": 8,
            "slippage_bps": 2,
            "slippage_model": "layered",
            "slippage_max_bps": 9,
            "slippage_volatility_weight": 0.7,
            "slippage_volume_weight": 0.5,
            "slippage_session_weight": 0.2,
            "slippage_volume_window": 12,
            "max_bar_participation_rate": 0.25,
            "min_order_notional": 10,
            "impact_bps_per_bar_participation": 80,
            "funding_rate_bps_per_8h": 1.5
          }
        }
        """,
        encoding="utf-8",
    )

    cfg = load_config(
        cfg_file,
        symbols="BTCUSDT,SOLUSDT",
        start="2026-01-01 00:00:00",
        end="2026-01-02 00:00:00",
        fee_bps=3,
        slippage_bps=1,
        initial_equity=3500,
        leverage=3,
        position_size_pct=0.4,
        wf_train_days=21,
        wf_test_days=7,
        wf_step_days=7,
        long_open_threshold=90,
        short_open_threshold=80,
        close_threshold=30,
    )

    assert cfg.symbols == ["BTCUSDT", "SOLUSDT"]
    assert cfg.risk.initial_equity == 3500
    assert cfg.risk.leverage == 3
    assert cfg.risk.position_size_pct == 0.4
    assert cfg.risk.maintenance_margin_ratio == 0.01
    assert cfg.risk.liquidation_fee_bps == 25
    assert cfg.risk.liquidation_buffer_bps == 12
    assert cfg.execution.fee_bps == 3
    assert cfg.execution.maker_fee_bps == 3
    assert cfg.execution.taker_fee_bps == 3
    assert cfg.execution.funding_rate_bps_per_8h == 1.5
    assert cfg.execution.slippage_bps == 1
    assert cfg.execution.slippage_model == "layered"
    assert cfg.execution.slippage_max_bps == 9
    assert cfg.execution.slippage_volatility_weight == 0.7
    assert cfg.execution.slippage_volume_weight == 0.5
    assert cfg.execution.slippage_session_weight == 0.2
    assert cfg.execution.slippage_volume_window == 12
    assert cfg.execution.max_bar_participation_rate == 0.25
    assert cfg.execution.min_order_notional == 10
    assert cfg.execution.impact_bps_per_bar_participation == 80
    assert cfg.walk_forward.train_days == 21
    assert cfg.walk_forward.test_days == 7
    assert cfg.walk_forward.step_days == 7
    assert cfg.aggregation.long_open_threshold == 90
    assert cfg.aggregation.short_open_threshold == 80
    assert cfg.aggregation.close_threshold == 30
    assert cfg.date_range.start.startswith("2026-01-01")


def test_run_execution_minimal_long_then_close() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 101, 103, 100, 102, 1),
            Bar("BTCUSDT", t2, 103, 105, 102, 104, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: 80, t1: 0}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(entry="next_open", slippage_bps=0, fee_bps=0),
        risk=RiskConfig(leverage=1, initial_equity=10_000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "LONG"
    assert trade.entry_price == 101
    assert trade.exit_price == 103
    assert trade.trading_fee == pytest.approx(0.0)
    assert trade.funding_fee == pytest.approx(0.0)
    assert result.final_equity > 10_000


def test_run_backtest_writes_artifacts_and_done_state(tmp_path: Path, monkeypatch) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)

    signals = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=100,
        )
    ]
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 101, 102, 100, 101.5, 1),
            Bar("BTCUSDT", t2, 102, 103, 101, 102.5, 1),
        ]
    }

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_history_db_path", lambda: tmp_path / "signal_history.db")
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", lambda *args, **kwargs: signals)
    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", lambda *args, **kwargs: bars)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:03:00"),
        execution=ExecutionConfig(slippage_bps=0, fee_bps=0),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    out = run_backtest(cfg, run_id="unit-run")

    assert out.output_dir.exists()
    assert (out.output_dir / "trades.csv").exists()
    assert (out.output_dir / "equity_curve.csv").exists()
    assert (out.output_dir / "metrics.json").exists()
    assert (out.output_dir / "input_quality.json").exists()
    assert (out.output_dir / "report.md").exists()
    assert (tmp_path / "artifacts" / "backtest" / "latest").exists()
    assert out.metrics.avg_holding_minutes >= 0
    assert out.metrics.gross_pnl > 0
    assert out.metrics.trading_fee == pytest.approx(0.0)
    assert out.metrics.funding_fee == pytest.approx(0.0)
    assert out.metrics.net_pnl == pytest.approx(out.metrics.gross_pnl)
    assert len(out.metrics.symbol_contributions) == 1
    assert out.metrics.symbol_contributions[0].symbol == "BTCUSDT"
    assert out.metrics.buy_hold_return_pct > 0
    assert out.metrics.buy_hold_final_equity > out.metrics.initial_equity

    quality_payload = json.loads((out.output_dir / "input_quality.json").read_text(encoding="utf-8"))
    assert quality_payload["run_id"] == "unit-run"
    assert quality_payload["signal_count"] == 1
    assert quality_payload["quality_score"] >= 0

    state_payload = json.loads((tmp_path / "artifacts" / "backtest" / "run_state.json").read_text(encoding="utf-8"))
    assert state_payload["status"] == "done"
    assert state_payload["stage"] == "done"
    assert state_payload["run_id"] == "unit-run"
    assert state_payload["latest_run_id"] == "unit-run"
    assert state_payload["error"] is None

    scores = aggregate_signal_scores(signals)
    assert scores["BTCUSDT"][t0] == 80


def test_run_backtest_error_marks_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_history_db_path", lambda: tmp_path / "signal_history.db")
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")

    def _raise_loader(*args, **kwargs):
        raise RuntimeError("load boom")

    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", _raise_loader)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:03:00"),
    )

    with pytest.raises(RuntimeError, match="load boom"):
        run_backtest(cfg, run_id="unit-run-error")

    state_payload = json.loads((tmp_path / "artifacts" / "backtest" / "run_state.json").read_text(encoding="utf-8"))
    assert state_payload["status"] == "error"
    assert state_payload["run_id"] == "unit-run-error"
    assert state_payload["stage"] == "loading_signals"
    assert "RuntimeError" in (state_payload.get("error") or "")


def test_replay_signals_from_bars_generates_buy_and_sell() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t0 + timedelta(minutes=1), 100, 102, 100, 101.5, 100),
            Bar("BTCUSDT", t0 + timedelta(minutes=2), 101.5, 101.6, 99.5, 99.9, 320),
        ]
    }

    events = replay_signals_from_bars(bars, timeframe="1m")

    assert events
    assert any(ev.direction == "BUY" for ev in events)
    assert any(ev.direction == "SELL" for ev in events)
    assert all(ev.source == "offline_replay" for ev in events)




def test_run_backtest_offline_rule_replay_uses_sqlite_rules(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t0 + timedelta(minutes=1), 100, 102, 99.9, 101.8, 120),
            Bar("BTCUSDT", t0 + timedelta(minutes=2), 101.8, 102.2, 100.4, 100.5, 350),
            Bar("BTCUSDT", t0 + timedelta(minutes=3), 100.5, 100.8, 98.8, 99.0, 420),
        ]
    }

    replay_events = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="rule_demo",
            timeframe="1m",
            source="offline_rule_replay",
            price=100.0,
        )
    ]

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.get_sqlite_path", lambda: tmp_path / "market_data.db")
    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", lambda *args, **kwargs: bars)

    def _fake_rule_replay(*args, **kwargs):
        return replay_events, SimpleNamespace(
            table_count=1,
            row_count=3,
            signal_count=1,
            rule_counters={
                "rule_demo": {
                    "evaluated": 10,
                    "timeframe_filtered": 1,
                    "volume_filtered": 0,
                    "condition_failed": 8,
                    "cooldown_blocked": 0,
                    "triggered": 1,
                }
            },
        )

    monkeypatch.setattr("src.backtest.runner.replay_signals_from_rules", _fake_rule_replay)

    def _should_not_call(*args, **kwargs):
        raise AssertionError("history signal loader should not be called in offline_rule_replay mode")

    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", _should_not_call)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:04:00"),
        execution=ExecutionConfig(slippage_bps=0, fee_bps=0),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    out = run_backtest(cfg, mode="offline_rule_replay", run_id="unit-offline-rule")

    assert out.output_dir.exists()
    assert out.metrics.mode == "offline_rule_replay"
    assert out.metrics.signal_count == 1

    replay_diag = json.loads((out.output_dir / "rule_replay_diagnostics.json").read_text(encoding="utf-8"))
    assert replay_diag["signal_count"] == 1
    assert replay_diag["rule_counters"]["rule_demo"]["triggered"] == 1
    assert "rule_timeframe_profiles" in replay_diag

    state_payload = json.loads((tmp_path / "artifacts" / "backtest" / "run_state.json").read_text(encoding="utf-8"))
    assert state_payload["status"] == "done"
    assert state_payload["mode"] == "offline_rule_replay"
    assert state_payload["run_id"] == "unit-offline-rule"

def test_run_backtest_offline_replay_uses_candles_only(tmp_path: Path, monkeypatch) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t0 + timedelta(minutes=1), 100, 102, 99.9, 101.8, 120),
            Bar("BTCUSDT", t0 + timedelta(minutes=2), 101.8, 102.2, 100.4, 100.5, 350),
            Bar("BTCUSDT", t0 + timedelta(minutes=3), 100.5, 100.8, 98.8, 99.0, 420),
        ]
    }

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", lambda *args, **kwargs: bars)

    def _should_not_call(*args, **kwargs):
        raise AssertionError("history signal loader should not be called in offline_replay mode")

    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", _should_not_call)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:04:00"),
        execution=ExecutionConfig(slippage_bps=0, fee_bps=0),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    out = run_backtest(cfg, mode="offline_replay", run_id="unit-offline")

    assert out.output_dir.exists()
    assert out.metrics.mode == "offline_replay"
    assert out.metrics.signal_count > 0

    state_payload = json.loads((tmp_path / "artifacts" / "backtest" / "run_state.json").read_text(encoding="utf-8"))
    assert state_payload["status"] == "done"
    assert state_payload["mode"] == "offline_replay"
    assert state_payload["run_id"] == "unit-offline"

def test_run_execution_liquidates_long_position() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 102, 50, 60, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: 80}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(entry="next_open", slippage_bps=0, fee_bps=0),
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=0,
        ),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.reason == "liquidation"
    assert trade.exit_kind == "liquidation"
    assert trade.exit_price_source == "binance_usdm_liquidation_threshold"
    assert trade.liquidation_price == pytest.approx(50.5)
    assert trade.exit_price == pytest.approx(50.5)
    assert trade.liquidation_fee == pytest.approx(0.0)
    assert result.final_equity == pytest.approx(10.0)


def test_run_execution_liquidates_short_position() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 151, 99, 140, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: -80}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(entry="next_open", slippage_bps=0, fee_bps=0),
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=0,
        ),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "SHORT"
    assert trade.reason == "liquidation"
    assert trade.exit_kind == "liquidation"
    assert trade.exit_price_source == "binance_usdm_liquidation_threshold"
    assert trade.liquidation_price == pytest.approx(149.5)
    assert trade.exit_price == pytest.approx(149.5)
    assert result.final_equity == pytest.approx(10.0)


def test_run_execution_long_liquidation_buffer_boundary() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 101, 50.75, 80, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: 80}}
    aggregation = AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20)
    execution = ExecutionConfig(entry="next_open", slippage_bps=0, fee_bps=0)

    without_buffer = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=execution,
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=0,
        ),
        aggregation=aggregation,
    )
    with_buffer = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=execution,
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=25,
        ),
        aggregation=aggregation,
    )

    assert len(without_buffer.trades) == 1
    assert without_buffer.trades[0].reason == "eod_close"
    assert without_buffer.trades[0].exit_kind == "normal_close"
    assert without_buffer.trades[0].exit_price_source == "bar_close"
    assert without_buffer.trades[0].liquidation_price == pytest.approx(0.0)
    assert without_buffer.final_equity == pytest.approx(600.0)

    assert len(with_buffer.trades) == 1
    assert with_buffer.trades[0].reason == "liquidation"
    assert with_buffer.trades[0].exit_kind == "liquidation"
    assert with_buffer.trades[0].exit_price_source == "binance_usdm_liquidation_threshold"
    assert with_buffer.trades[0].liquidation_price == pytest.approx(50.75)
    assert with_buffer.trades[0].exit_price == pytest.approx(50.75)
    assert with_buffer.final_equity == pytest.approx(15.0)


def test_run_execution_short_liquidation_buffer_boundary() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 149.25, 99, 120, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: -80}}
    aggregation = AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20)
    execution = ExecutionConfig(entry="next_open", slippage_bps=0, fee_bps=0)

    without_buffer = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=execution,
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=0,
        ),
        aggregation=aggregation,
    )
    with_buffer = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=execution,
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=25,
        ),
        aggregation=aggregation,
    )

    assert len(without_buffer.trades) == 1
    assert without_buffer.trades[0].reason == "eod_close"
    assert without_buffer.trades[0].exit_kind == "normal_close"
    assert without_buffer.trades[0].exit_price_source == "bar_close"
    assert without_buffer.trades[0].liquidation_price == pytest.approx(0.0)
    assert without_buffer.final_equity == pytest.approx(600.0)

    assert len(with_buffer.trades) == 1
    assert with_buffer.trades[0].side == "SHORT"
    assert with_buffer.trades[0].reason == "liquidation"
    assert with_buffer.trades[0].exit_kind == "liquidation"
    assert with_buffer.trades[0].exit_price_source == "binance_usdm_liquidation_threshold"
    assert with_buffer.trades[0].liquidation_price == pytest.approx(149.25)
    assert with_buffer.trades[0].exit_price == pytest.approx(149.25)
    assert with_buffer.final_equity == pytest.approx(15.0)


def test_run_execution_long_gap_liquidation_caps_at_bankruptcy_and_flattens_curve() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)
    t3 = t2 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t2, 40, 42, 39, 41, 1),
            Bar("BTCUSDT", t3, 80, 82, 79, 81, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: 80}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(entry="next_open", slippage_bps=0, fee_bps=0),
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=0,
        ),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.reason == "liquidation"
    assert trade.exit_price_source == "bar_open_gap_bankruptcy_cap"
    assert trade.liquidation_price == pytest.approx(50.5)
    assert trade.exit_price == pytest.approx(50.0)
    assert result.final_equity == pytest.approx(0.0)
    assert [point.equity for point in result.equity_curve] == [1000.0, 1000.0, 0.0, 0.0]
    assert [point.timestamp for point in result.equity_curve] == [t0, t1, t2, t3]


def test_run_execution_short_gap_liquidation_caps_at_bankruptcy_and_flattens_curve() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)
    t3 = t2 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t2, 160, 162, 159, 161, 1),
            Bar("BTCUSDT", t3, 120, 121, 119, 120, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: -80}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(entry="next_open", slippage_bps=0, fee_bps=0),
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=0,
        ),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "SHORT"
    assert trade.reason == "liquidation"
    assert trade.exit_price_source == "bar_open_gap_bankruptcy_cap"
    assert trade.liquidation_price == pytest.approx(149.5)
    assert trade.exit_price == pytest.approx(150.0)
    assert result.final_equity == pytest.approx(0.0)
    assert [point.equity for point in result.equity_curve] == [1000.0, 1000.0, 0.0, 0.0]
    assert [point.timestamp for point in result.equity_curve] == [t0, t1, t2, t3]


def test_run_backtest_writes_liquidation_fields_to_trades_csv(tmp_path: Path, monkeypatch) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    signals = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=100,
        )
    ]
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 102, 50, 60, 1),
        ]
    }

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_history_db_path", lambda: tmp_path / "signal_history.db")
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", lambda *args, **kwargs: signals)
    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", lambda *args, **kwargs: bars)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:02:00"),
        execution=ExecutionConfig(slippage_bps=0, fee_bps=0),
        risk=RiskConfig(
            leverage=2,
            initial_equity=1000,
            position_size_pct=1.0,
            maintenance_margin_ratio=0.005,
            liquidation_fee_bps=0,
            liquidation_buffer_bps=0,
        ),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    out = run_backtest(cfg, run_id="unit-liquidation")

    with (out.output_dir / "trades.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 1
    row = rows[0]
    assert row["reason"] == "liquidation"
    assert row["exit_kind"] == "liquidation"
    assert row["exit_price_source"] == "binance_usdm_liquidation_threshold"
    assert row["liquidation_price"] == "50.50000000"
    assert row["liquidation_fee"] == "0.00000000"

def test_run_execution_applies_taker_and_funding_costs_for_long() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=8)
    t2 = t1 + timedelta(hours=8)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 99, 100, 98, 99, 1),
            Bar("BTCUSDT", t1, 100, 105, 99, 104, 1),
            Bar("BTCUSDT", t2, 110, 111, 109, 110, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: 80, t1: 0}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(
            entry="next_open",
            slippage_bps=0,
            fee_bps=0,
            taker_fee_bps=10,
            funding_rate_bps_per_8h=20,
        ),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    trade = result.trades[0]
    assert trade.pnl_gross == pytest.approx(100.0)
    assert trade.trading_fee == pytest.approx(2.1)
    assert trade.funding_fee == pytest.approx(2.0)
    assert trade.pnl_net == pytest.approx(95.9)
    assert result.final_equity == pytest.approx(1095.9)


def test_run_execution_short_receives_positive_funding() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=8)
    t2 = t1 + timedelta(hours=8)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 99, 100, 98, 99, 1),
            Bar("BTCUSDT", t1, 100, 101, 95, 96, 1),
            Bar("BTCUSDT", t2, 90, 91, 89, 90, 1),
        ]
    }
    scores = {"BTCUSDT": {t0: -80, t1: 0}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(
            entry="next_open",
            slippage_bps=0,
            fee_bps=0,
            taker_fee_bps=10,
            funding_rate_bps_per_8h=20,
        ),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    trade = result.trades[0]
    assert trade.side == "SHORT"
    assert trade.pnl_gross == pytest.approx(100.0)
    assert trade.trading_fee == pytest.approx(1.9)
    assert trade.funding_fee == pytest.approx(-2.0)
    assert trade.pnl_net == pytest.approx(100.1)
    assert result.final_equity == pytest.approx(1100.1)


def test_run_backtest_writes_cost_breakdown_fields(tmp_path: Path, monkeypatch) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=8)
    t2 = t1 + timedelta(hours=8)

    signals = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=99,
        ),
        SignalEvent(
            event_id=2,
            timestamp=t1,
            symbol="BTCUSDT",
            direction="BUY",
            strength=0,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=104,
        ),
    ]
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 99, 100, 98, 99, 1),
            Bar("BTCUSDT", t1, 100, 105, 99, 104, 1),
            Bar("BTCUSDT", t2, 110, 111, 109, 110, 1),
        ]
    }

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_history_db_path", lambda: tmp_path / "signal_history.db")
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", lambda *args, **kwargs: signals)
    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", lambda *args, **kwargs: bars)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 16:01:00"),
        execution=ExecutionConfig(slippage_bps=0, fee_bps=0, taker_fee_bps=10, funding_rate_bps_per_8h=20),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    out = run_backtest(cfg, run_id="unit-cost-breakdown")
    metrics_payload = json.loads((out.output_dir / "metrics.json").read_text(encoding="utf-8"))

    assert metrics_payload["gross_pnl"] == pytest.approx(100.0)
    assert metrics_payload["trading_fee"] == pytest.approx(2.1)
    assert metrics_payload["funding_fee"] == pytest.approx(2.0)
    assert metrics_payload["net_pnl"] == pytest.approx(95.9)
    assert metrics_payload["total_cost_impact"] == pytest.approx(4.1)
    assert metrics_payload["funding_credit"] == pytest.approx(0.0)
    assert metrics_payload["cost_drag_pct_of_initial"] == pytest.approx(0.41)
    assert metrics_payload["cost_erosion_pct_of_gross"] == pytest.approx(4.1)
    assert metrics_payload["gross_to_net_retention_pct"] == pytest.approx(95.9)
    assert metrics_payload["cost_status"] == "signal_driven"
    assert "signal-driven" in metrics_payload["cost_summary"]

    report_md = (out.output_dir / "report.md").read_text(encoding="utf-8")
    assert "Cost Status" in report_md
    assert "Cost Summary" in report_md
    assert "Gross→Net Retention" in report_md

    with (out.output_dir / "trades.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["trading_fee"] == "2.10000000"
    assert rows[0]["funding_fee"] == "2.00000000"



def test_run_backtest_ephemeral_failure_does_not_write_state(tmp_path: Path, monkeypatch) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    signals = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=100,
        )
    ]

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_history_db_path", lambda: tmp_path / "signal_history.db")
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", lambda *args, **kwargs: signals)

    def _boom(*args, **kwargs):
        raise RuntimeError("pg unavailable")

    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", _boom)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:03:00"),
        execution=ExecutionConfig(slippage_bps=0, fee_bps=0),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    with pytest.raises(RuntimeError, match="pg unavailable"):
        run_backtest(cfg, run_id="unit-ephemeral-fail", ephemeral=True)

    backtest_root = tmp_path / "artifacts" / "backtest"
    assert not (backtest_root / "run_state.json").exists()
    assert not (backtest_root / "latest").exists()
    assert not (backtest_root / "unit-ephemeral-fail").exists()


def test_run_execution_layered_slippage_increases_cost_under_thin_volatile_conditions() -> None:
    t_prev2 = datetime(2026, 1, 1, 0, 58, tzinfo=timezone.utc)
    t_prev1 = t_prev2 + timedelta(minutes=1)
    t0 = t_prev1 + timedelta(minutes=1)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t_prev2, 100, 100.5, 99.5, 100, 1000),
            Bar("BTCUSDT", t_prev1, 100, 100.4, 99.8, 100, 1100),
            Bar("BTCUSDT", t0, 100, 108, 92, 100, 100),
            Bar("BTCUSDT", t1, 101, 103, 100, 102, 120),
            Bar("BTCUSDT", t2, 103, 104, 102, 103, 150),
        ]
    }
    scores = {"BTCUSDT": {t0: 80, t1: 0}}

    fixed = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(entry="next_open", slippage_bps=3, fee_bps=0),
        risk=RiskConfig(leverage=1, initial_equity=10_000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )
    layered = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(
            entry="next_open",
            slippage_bps=3,
            fee_bps=0,
            slippage_model="layered",
            slippage_max_bps=9,
            slippage_volatility_weight=0.8,
            slippage_volume_weight=0.6,
            slippage_session_weight=0.3,
            slippage_volume_window=3,
        ),
        risk=RiskConfig(leverage=1, initial_equity=10_000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    fixed_trade = fixed.trades[0]
    layered_trade = layered.trades[0]

    assert layered_trade.slippage_model == "layered"
    assert layered_trade.entry_slippage_bps > fixed_trade.entry_slippage_bps
    assert layered_trade.exit_slippage_bps > fixed_trade.exit_slippage_bps
    assert layered_trade.entry_slippage_bps == pytest.approx(9.0)
    assert layered_trade.entry_slippage_cost > fixed_trade.entry_slippage_cost
    assert layered_trade.exit_slippage_cost > fixed_trade.exit_slippage_cost
    assert layered.final_equity < fixed.final_equity


def test_run_backtest_layered_slippage_writes_metrics_and_trade_fields(tmp_path: Path, monkeypatch) -> None:
    t_prev2 = datetime(2026, 1, 1, 0, 58, tzinfo=timezone.utc)
    t_prev1 = t_prev2 + timedelta(minutes=1)
    t0 = t_prev1 + timedelta(minutes=1)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)

    signals = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=100,
        ),
        SignalEvent(
            event_id=2,
            timestamp=t1,
            symbol="BTCUSDT",
            direction="BUY",
            strength=0,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=102,
        ),
    ]
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t_prev2, 100, 100.5, 99.5, 100, 1000),
            Bar("BTCUSDT", t_prev1, 100, 100.4, 99.8, 100, 1100),
            Bar("BTCUSDT", t0, 100, 108, 92, 100, 100),
            Bar("BTCUSDT", t1, 101, 103, 100, 102, 120),
            Bar("BTCUSDT", t2, 103, 104, 102, 103, 150),
        ]
    }

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_history_db_path", lambda: tmp_path / "signal_history.db")
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", lambda *args, **kwargs: signals)
    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", lambda *args, **kwargs: bars)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:58:00", end="2026-01-01 01:03:00"),
        execution=ExecutionConfig(
            slippage_bps=3,
            fee_bps=0,
            slippage_model="layered",
            slippage_max_bps=9,
            slippage_volatility_weight=0.8,
            slippage_volume_weight=0.6,
            slippage_session_weight=0.3,
            slippage_volume_window=3,
        ),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    out = run_backtest(cfg, run_id="unit-layered-slip")
    metrics_payload = json.loads((out.output_dir / "metrics.json").read_text(encoding="utf-8"))

    assert metrics_payload["slippage_cost"] > 0
    assert metrics_payload["slippage_cost_pct_of_initial"] > 0
    assert "slip=3.0bps(layered cap=9.0" in metrics_payload["strategy_summary"]

    report_md = (out.output_dir / "report.md").read_text(encoding="utf-8")
    assert "Embedded Slippage Cost" in report_md

    with (out.output_dir / "trades.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["slippage_model"] == "layered"
    assert float(rows[0]["entry_slippage_bps"]) > 3.0
    assert float(rows[0]["exit_slippage_bps"]) > 3.0
    assert float(rows[0]["entry_slippage_cost"]) > 0.0
    assert float(rows[0]["exit_slippage_cost"]) > 0.0


def test_run_execution_partial_entry_respects_bar_capacity() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t1, 100, 102, 99, 101, 20),
            Bar("BTCUSDT", t2, 102, 103, 101, 102, 100),
        ]
    }
    scores = {"BTCUSDT": {t0: 80, t1: 0}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(
            entry="next_open",
            slippage_bps=0,
            fee_bps=0,
            max_bar_participation_rate=0.2,
            min_order_notional=5,
            impact_bps_per_bar_participation=50,
        ),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.partial_fill is True
    assert trade.constraint_flags == "entry_capped,impact"
    assert trade.qty == pytest.approx(4.0)
    assert trade.entry_requested_qty == pytest.approx(10.0)
    assert trade.entry_fill_ratio == pytest.approx(0.4)
    assert trade.entry_capacity_notional == pytest.approx(400.0)
    assert trade.entry_impact_bps == pytest.approx(10.0)
    assert trade.entry_impact_cost > 0.0


def test_run_execution_partial_exit_splits_trade_when_close_capacity_thin() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)
    t3 = t2 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t1, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t2, 102, 103, 101, 102, 30),
            Bar("BTCUSDT", t3, 103, 104, 102, 103, 100),
        ]
    }
    scores = {"BTCUSDT": {t0: 80, t1: 0, t2: 0}}

    result = run_execution(
        bars_by_symbol=bars,
        score_map=scores,
        execution=ExecutionConfig(
            entry="next_open",
            slippage_bps=0,
            fee_bps=0,
            max_bar_participation_rate=0.1,
            min_order_notional=5,
            impact_bps_per_bar_participation=100,
        ),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    assert len(result.trades) == 2
    first, second = result.trades
    assert first.partial_fill is True
    assert first.constraint_flags == "exit_capped,impact"
    assert first.qty == pytest.approx(3.0)
    assert first.exit_requested_qty == pytest.approx(10.0)
    assert first.exit_fill_ratio == pytest.approx(0.3)
    assert first.exit_capacity_notional == pytest.approx(306.0)
    assert first.exit_impact_bps == pytest.approx(10.0)
    assert second.qty == pytest.approx(7.0)
    assert second.partial_fill is False


def test_run_backtest_execution_constraints_write_impact_and_partial_fields(tmp_path: Path, monkeypatch) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)
    t3 = t2 + timedelta(minutes=1)

    signals = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=100,
        ),
        SignalEvent(
            event_id=2,
            timestamp=t1,
            symbol="BTCUSDT",
            direction="BUY",
            strength=0,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=100,
        ),
        SignalEvent(
            event_id=3,
            timestamp=t2,
            symbol="BTCUSDT",
            direction="BUY",
            strength=0,
            signal_type="test",
            timeframe="1m",
            source="sqlite",
            price=102,
        ),
    ]
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t1, 100, 101, 99, 100, 100),
            Bar("BTCUSDT", t2, 102, 103, 101, 102, 30),
            Bar("BTCUSDT", t3, 103, 104, 102, 103, 100),
        ]
    }

    monkeypatch.setattr("src.backtest.runner.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.backtest.runner.get_history_db_path", lambda: tmp_path / "signal_history.db")
    monkeypatch.setattr("src.backtest.runner.get_database_url", lambda: "postgresql://unused")
    monkeypatch.setattr("src.backtest.runner.load_signals_from_sqlite", lambda *args, **kwargs: signals)
    monkeypatch.setattr("src.backtest.runner.load_candles_from_pg", lambda *args, **kwargs: bars)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:04:00"),
        execution=ExecutionConfig(
            slippage_bps=0,
            fee_bps=0,
            max_bar_participation_rate=0.1,
            min_order_notional=5,
            impact_bps_per_bar_participation=100,
        ),
        risk=RiskConfig(leverage=1, initial_equity=1000, position_size_pct=1.0),
        aggregation=AggregationConfig(long_open_threshold=70, short_open_threshold=70, close_threshold=20),
    )

    out = run_backtest(cfg, run_id="unit-execution-constraints")
    metrics_payload = json.loads((out.output_dir / "metrics.json").read_text(encoding="utf-8"))

    assert metrics_payload["impact_cost"] > 0
    assert metrics_payload["impact_cost_pct_of_initial"] > 0
    assert metrics_payload["partial_fill_trade_count"] == 1
    assert "part<=10.0%" in metrics_payload["strategy_summary"]

    report_md = (out.output_dir / "report.md").read_text(encoding="utf-8")
    assert "Embedded Impact Cost" in report_md
    assert "Partial Fill Trades" in report_md

    with (out.output_dir / "trades.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["partial_fill"] == "true"
    assert rows[0]["constraint_flags"] == "exit_capped,impact"
    assert float(rows[0]["exit_fill_ratio"]) == pytest.approx(0.3)
    assert float(rows[0]["exit_impact_cost"]) > 0.0
