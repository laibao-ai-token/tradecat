"""Backtest M1 tests."""

from __future__ import annotations

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
          "risk": {"initial_equity": 2000, "position_size_pct": 0.5},
          "execution": {"fee_bps": 8, "slippage_bps": 2}
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
    assert cfg.execution.fee_bps == 3
    assert cfg.execution.slippage_bps == 1
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
    assert (out.output_dir / "report.md").exists()
    assert (tmp_path / "artifacts" / "backtest" / "latest").exists()
    assert out.metrics.avg_holding_minutes >= 0
    assert len(out.metrics.symbol_contributions) == 1
    assert out.metrics.symbol_contributions[0].symbol == "BTCUSDT"
    assert out.metrics.buy_hold_return_pct > 0
    assert out.metrics.buy_hold_final_equity > out.metrics.initial_equity

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
