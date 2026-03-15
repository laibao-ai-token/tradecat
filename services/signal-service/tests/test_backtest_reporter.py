"""Reporter metric tests for baseline comparison fields."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.backtest.models import Bar, EquityPoint, SignalEvent, Trade
from src.backtest.precheck import InputQualityReport, InputQualitySymbol
from src.backtest.reporter import build_metrics, write_artifacts


def test_build_metrics_includes_multi_baselines_and_excess() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(minutes=1)
    t3 = t2 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 105, 106, 104, 105, 1),
            Bar("BTCUSDT", t2, 110, 111, 109, 110, 1),
            Bar("BTCUSDT", t3, 115, 116, 114, 115, 1),
        ],
        "ETHUSDT": [
            Bar("ETHUSDT", t0, 100, 101, 99, 100, 1),
            Bar("ETHUSDT", t1, 70, 71, 69, 70, 1),
            Bar("ETHUSDT", t2, 80, 81, 79, 80, 1),
            Bar("ETHUSDT", t3, 85, 86, 84, 85, 1),
        ],
    }

    metrics = build_metrics(
        run_id="unit-benchmark",
        mode="history_signal",
        start=t0,
        end=t3,
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        initial_equity=1000,
        final_equity=900,
        trades=[],
        curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t3, equity=900)],
        signal_count=0,
        bar_count=8,
        bars_by_symbol=bars,
    )

    assert round(metrics.buy_hold_return_pct, 6) == 0.0
    assert round(metrics.buy_hold_final_equity, 6) == 1000.0
    assert metrics.risk_parity_return_pct > metrics.buy_hold_return_pct
    assert metrics.momentum_return_pct > metrics.buy_hold_return_pct
    assert round(metrics.total_return_pct, 6) == -10.0
    assert round(metrics.excess_return_pct, 6) == -10.0
    assert metrics.excess_return_vs_risk_parity_pct < metrics.excess_return_pct
    assert metrics.excess_return_vs_momentum_pct < metrics.excess_return_pct
    assert metrics.best_baseline_name in {"risk_parity", "momentum"}
    assert metrics.best_baseline_return_pct >= metrics.buy_hold_return_pct


def test_build_metrics_tracks_signal_profile() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    signals = [
        SignalEvent(
            event_id=1,
            timestamp=t0,
            symbol="BTCUSDT",
            direction="BUY",
            strength=80,
            signal_type="ema_cross",
            timeframe="1m",
            source="history",
            price=100.0,
        ),
        SignalEvent(
            event_id=2,
            timestamp=t1,
            symbol="BTCUSDT",
            direction="SELL",
            strength=85,
            signal_type="ema_cross",
            timeframe="5m",
            source="history",
            price=99.0,
        ),
        SignalEvent(
            event_id=3,
            timestamp=t1,
            symbol="ETHUSDT",
            direction="BUY",
            strength=75,
            signal_type="rsi_rebound",
            timeframe="1m",
            source="history",
            price=50.0,
        ),
    ]

    metrics = build_metrics(
        run_id="unit-profile",
        mode="history_signal",
        start=t0,
        end=t1,
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        initial_equity=1000,
        final_equity=1000,
        trades=[],
        curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=1000)],
        signal_count=len(signals),
        bar_count=4,
        bars_by_symbol=None,
        signals=signals,
    )

    assert metrics.signal_type_counts == {"ema_cross": 2, "rsi_rebound": 1}
    assert metrics.direction_counts == {"BUY": 2, "SELL": 1}
    assert metrics.timeframe_counts == {"1m": 2, "5m": 1}


def test_build_metrics_reports_positive_retention_for_loss_offsets() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=10)

    trades = [
        Trade(
            symbol="BTCUSDT",
            side="LONG",
            entry_ts=t0,
            exit_ts=t1,
            entry_price=100.0,
            exit_price=99.0,
            qty=10.0,
            entry_fee=0.0,
            exit_fee=0.0,
            pnl_gross=-10.0,
            pnl_net=-8.0,
            entry_score=80,
            exit_score=10,
            reason="normal_close",
            funding_fee=-2.0,
            trading_fee=0.0,
        )
    ]

    metrics = build_metrics(
        run_id="unit-loss-retention",
        mode="history_signal",
        start=t0,
        end=t1,
        symbols=["BTCUSDT"],
        timeframe="1m",
        initial_equity=1000,
        final_equity=992,
        trades=trades,
        curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=992)],
        signal_count=1,
        bar_count=2,
        bars_by_symbol=None,
    )

    assert metrics.cost_status == "loss_offset_by_funding"
    assert metrics.gross_to_net_retention_pct == 80.0



def test_write_artifacts_persists_baseline_fields(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    metrics = build_metrics(
        run_id="unit-json",
        mode="history_signal",
        start=t0,
        end=t1,
        symbols=["BTCUSDT"],
        timeframe="1m",
        initial_equity=1000,
        final_equity=1100,
        trades=[],
        curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=1100)],
        signal_count=1,
        bar_count=2,
        bars_by_symbol={
            "BTCUSDT": [
                Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
                Bar("BTCUSDT", t1, 110, 111, 109, 110, 1),
            ]
        },
    )

    input_quality = InputQualityReport(
        run_id="unit-json",
        mode="history_signal",
        start=t0.isoformat(sep=" "),
        end=t1.isoformat(sep=" "),
        timeframe="1m",
        generated_at=t1.isoformat(sep=" "),
        signal_count=1,
        aggregated_signal_bucket_count=1,
        candle_count=2,
        expected_candle_count=2,
        candle_coverage_pct=100.0,
        no_next_open_bucket_count=0,
        dropped_signal_count=0,
        quality_score=100.0,
        quality_status="pass",
        quality_breakdown={
            "coverage_score": 100.0,
            "missing_candle_penalty": 0.0,
            "gap_penalty": 0.0,
            "no_next_open_penalty": 0.0,
            "dropped_signal_penalty": 0.0,
            "quality_score": 100.0,
        },
        symbol_rows=[
            InputQualitySymbol(
                symbol="BTCUSDT",
                signal_count=1,
                aggregated_signal_bucket_count=1,
                candle_count=2,
                expected_candle_count=2,
                candle_coverage_pct=100.0,
                quality_score=100.0,
                quality_status="pass",
            )
        ],
    )

    write_artifacts(tmp_path, trades=[], curve=[], metrics=metrics, input_quality=input_quality)

    payload = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert "buy_hold_return_pct" in payload
    assert "risk_parity_return_pct" in payload
    assert "momentum_return_pct" in payload
    assert "excess_return_pct" in payload
    assert "excess_return_vs_risk_parity_pct" in payload
    assert "excess_return_vs_momentum_pct" in payload
    assert "best_baseline_name" in payload
    assert "signal_type_counts" in payload
    assert "direction_counts" in payload
    assert "timeframe_counts" in payload
    assert "cost_status" in payload
    assert "cost_summary" in payload
    assert payload["buy_hold_return_pct"] == metrics.buy_hold_return_pct
    assert payload["risk_parity_return_pct"] == metrics.risk_parity_return_pct
    assert payload["momentum_return_pct"] == metrics.momentum_return_pct
    assert payload["excess_return_pct"] == metrics.excess_return_pct
    assert payload["excess_return_vs_risk_parity_pct"] == metrics.excess_return_vs_risk_parity_pct
    assert payload["excess_return_vs_momentum_pct"] == metrics.excess_return_vs_momentum_pct

    input_quality_payload = json.loads((tmp_path / "input_quality.json").read_text(encoding="utf-8"))
    assert input_quality_payload["quality_score"] == 100.0
    assert input_quality_payload["quality_status"] == "pass"
    assert input_quality_payload["quality_breakdown"]["coverage_score"] == 100.0
    assert input_quality_payload["symbol_rows"][0]["quality_status"] == "pass"
    report_md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## Input Quality" in report_md
    assert "Quality Status" in report_md
    assert "Penalties" in report_md
    assert "Risk Parity Return" in report_md
    assert "Simple Momentum Return" in report_md
    assert "## Stability" in report_md

    stability_payload = json.loads((tmp_path / "stability_report.json").read_text(encoding="utf-8"))
    assert stability_payload["stability_status"] == "insufficient_history"
    assert stability_payload["comparable_run_count"] == 0
    stability_md = (tmp_path / "stability_report.md").read_text(encoding="utf-8")
    assert "Stability Report" in stability_md
    assert "INSUFFICIENT_HISTORY" in stability_md


def test_write_artifacts_generates_cross_run_stability_report(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    backtest_root = tmp_path / "artifacts" / "backtest"

    def _mk_run(run_id: str, final_equity: float, *, sharpe: float, win_rate: float, signal_count: int, trade_count: int) -> None:
        metrics = build_metrics(
            run_id=run_id,
            mode="history_signal",
            start=t0,
            end=t1,
            symbols=["BTCUSDT"],
            timeframe="1m",
            initial_equity=1000,
            final_equity=final_equity,
            trades=[],
            curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=final_equity)],
            signal_count=signal_count,
            bar_count=2,
            bars_by_symbol={
                "BTCUSDT": [
                    Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
                    Bar("BTCUSDT", t1, 110, 111, 109, 110, 1),
                ]
            },
            strategy_label="demo",
            strategy_config_path="demo.yaml",
            strategy_summary="demo-strategy",
        )
        metrics.sharpe = sharpe
        metrics.win_rate_pct = win_rate
        metrics.trade_count = trade_count
        write_artifacts(backtest_root / run_id, trades=[], curve=[], metrics=metrics, backtest_root=backtest_root)

    _mk_run("stable-a", 1120, sharpe=1.6, win_rate=62.0, signal_count=100, trade_count=12)
    _mk_run("stable-b", 1100, sharpe=1.4, win_rate=58.0, signal_count=96, trade_count=11)

    current_metrics = build_metrics(
        run_id="unstable-now",
        mode="history_signal",
        start=t0,
        end=t1,
        symbols=["BTCUSDT"],
        timeframe="1m",
        initial_equity=1000,
        final_equity=920,
        trades=[],
        curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=920)],
        signal_count=20,
        bar_count=2,
        bars_by_symbol={
            "BTCUSDT": [
                Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
                Bar("BTCUSDT", t1, 110, 111, 109, 110, 1),
            ]
        },
        strategy_label="demo",
        strategy_config_path="demo.yaml",
        strategy_summary="demo-strategy",
    )
    current_metrics.sharpe = 0.1
    current_metrics.win_rate_pct = 35.0
    current_metrics.trade_count = 3

    out_dir = backtest_root / "unstable-now"
    write_artifacts(out_dir, trades=[], curve=[], metrics=current_metrics, backtest_root=backtest_root)

    stability_payload = json.loads((out_dir / "stability_report.json").read_text(encoding="utf-8"))
    assert stability_payload["comparable_run_count"] == 2
    assert stability_payload["stability_status"] == "critical"
    assert round(stability_payload["baseline"]["total_return_pct"], 2) == 11.0
    warning_kinds = {row["kind"] for row in stability_payload["warnings"]}
    assert "return_collapse" in warning_kinds
    assert "excess_return_collapse" in warning_kinds
    assert "trade_count_drift" in warning_kinds
    assert "signal_count_drift" in warning_kinds

    stability_md = (out_dir / "stability_report.md").read_text(encoding="utf-8")
    assert "Stability Report" in stability_md
    assert "CRITICAL" in stability_md
    assert "return_collapse" in stability_md


def test_write_artifacts_stability_ignores_runs_with_different_strategy_context(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    backtest_root = tmp_path / "artifacts" / "backtest"

    def _mk_run(run_id: str, final_equity: float, *, leverage: float) -> None:
        metrics = build_metrics(
            run_id=run_id,
            mode="history_signal",
            start=t0,
            end=t1,
            symbols=["BTCUSDT"],
            timeframe="1m",
            initial_equity=1000,
            final_equity=final_equity,
            trades=[],
            curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=final_equity)],
            signal_count=100,
            bar_count=2,
            bars_by_symbol={
                "BTCUSDT": [
                    Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
                    Bar("BTCUSDT", t1, 110, 111, 109, 110, 1),
                ]
            },
            strategy_label="demo",
            strategy_config_path="demo.yaml",
            strategy_summary="demo-strategy",
            strategy_context={
                "aggregation": {"long_open_threshold": 70, "short_open_threshold": 70, "close_threshold": 20},
                "execution": {"slippage_bps": 3.0, "maker_fee_bps": 4.0, "taker_fee_bps": 4.0},
                "risk": {"initial_equity": 1000.0, "leverage": leverage, "position_size_pct": 0.25},
            },
        )
        write_artifacts(backtest_root / run_id, trades=[], curve=[], metrics=metrics, backtest_root=backtest_root)

    _mk_run("stable-same", 1120, leverage=2.0)
    _mk_run("stable-other-leverage", 1110, leverage=3.0)

    current_metrics = build_metrics(
        run_id="stable-current",
        mode="history_signal",
        start=t0,
        end=t1,
        symbols=["BTCUSDT"],
        timeframe="1m",
        initial_equity=1000,
        final_equity=1090,
        trades=[],
        curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=1090)],
        signal_count=90,
        bar_count=2,
        bars_by_symbol={
            "BTCUSDT": [
                Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
                Bar("BTCUSDT", t1, 110, 111, 109, 110, 1),
            ]
        },
        strategy_label="demo",
        strategy_config_path="demo.yaml",
        strategy_summary="demo-strategy",
        strategy_context={
            "aggregation": {"long_open_threshold": 70, "short_open_threshold": 70, "close_threshold": 20},
            "execution": {"slippage_bps": 3.0, "maker_fee_bps": 4.0, "taker_fee_bps": 4.0},
            "risk": {"initial_equity": 1000.0, "leverage": 2.0, "position_size_pct": 0.25},
        },
    )

    out_dir = backtest_root / "stable-current"
    write_artifacts(out_dir, trades=[], curve=[], metrics=current_metrics, backtest_root=backtest_root)

    stability_payload = json.loads((out_dir / "stability_report.json").read_text(encoding="utf-8"))
    assert stability_payload["comparable_run_count"] == 1
    assert round(stability_payload["baseline"]["total_return_pct"], 2) == 12.0
