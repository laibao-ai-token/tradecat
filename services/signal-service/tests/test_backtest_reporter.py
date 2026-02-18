"""Reporter metric tests for baseline comparison fields."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.backtest.models import Bar, EquityPoint, SignalEvent
from src.backtest.reporter import build_metrics, write_artifacts


def test_build_metrics_includes_buy_hold_and_excess() -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)

    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 110, 111, 109, 110, 1),
        ],
        "ETHUSDT": [
            Bar("ETHUSDT", t0, 50, 51, 49, 50, 1),
            Bar("ETHUSDT", t1, 45, 46, 44, 45, 1),
        ],
    }

    metrics = build_metrics(
        run_id="unit-bh",
        mode="history_signal",
        start=t0,
        end=t1,
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        initial_equity=1000,
        final_equity=900,
        trades=[],
        curve=[EquityPoint(timestamp=t0, equity=1000), EquityPoint(timestamp=t1, equity=900)],
        signal_count=0,
        bar_count=4,
        bars_by_symbol=bars,
    )

    # +10% and -10% average to 0%, so buy-and-hold baseline keeps initial equity.
    assert round(metrics.buy_hold_return_pct, 6) == 0.0
    assert round(metrics.buy_hold_final_equity, 6) == 1000.0
    assert round(metrics.total_return_pct, 6) == -10.0
    assert round(metrics.excess_return_pct, 6) == -10.0


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

    write_artifacts(tmp_path, trades=[], curve=[], metrics=metrics)

    payload = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert "buy_hold_return_pct" in payload
    assert "excess_return_pct" in payload
    assert "signal_type_counts" in payload
    assert "direction_counts" in payload
    assert "timeframe_counts" in payload
    assert payload["buy_hold_return_pct"] == metrics.buy_hold_return_pct
    assert payload["excess_return_pct"] == metrics.excess_return_pct
