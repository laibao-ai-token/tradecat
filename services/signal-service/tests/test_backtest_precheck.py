"""Backtest precheck coverage tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.backtest.models import BacktestConfig, DateRange
from src.backtest.precheck import build_input_quality_report, compute_coverage_report, format_coverage_lines


def test_compute_coverage_report_with_stubbed_sources(monkeypatch, tmp_path: Path) -> None:
    cfg = BacktestConfig(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-02-01 00:00:00", end="2026-02-01 00:09:00"),
    )

    def _fake_signal(*args, **kwargs):
        return {
            "total_count": 8,
            "day_count": 1,
            "min_ts": "2026-02-01T00:00:10",
            "max_ts": "2026-02-01T00:08:59",
            "by_symbol": {
                "BTCUSDT": {"count": 5, "min_ts": "2026-02-01T00:00:10", "max_ts": "2026-02-01T00:08:59"},
                "ETHUSDT": {"count": 3, "min_ts": "2026-02-01T00:01:10", "max_ts": "2026-02-01T00:07:59"},
            },
        }

    def _fake_candle(*args, **kwargs):
        return {
            "total_count": 20,
            "min_ts": "2026-02-01 00:00:00+00:00",
            "max_ts": "2026-02-01 00:09:00+00:00",
            "by_symbol": {
                "BTCUSDT": {"count": 10, "min_ts": "2026-02-01 00:00:00+00:00", "max_ts": "2026-02-01 00:09:00+00:00"},
                "ETHUSDT": {"count": 10, "min_ts": "2026-02-01 00:00:00+00:00", "max_ts": "2026-02-01 00:09:00+00:00"},
            },
        }

    monkeypatch.setattr("src.backtest.precheck._load_signal_coverage_from_sqlite", _fake_signal)
    monkeypatch.setattr("src.backtest.precheck._load_candle_coverage_from_pg", _fake_candle)

    out = compute_coverage_report(cfg, history_db_path=tmp_path / "x.db", database_url="postgresql://unused")
    assert out.signal_count == 8
    assert out.signal_days == 1
    assert out.candle_count == 20
    assert out.expected_candle_count == 20
    assert out.candle_coverage_pct == 100.0
    assert len(out.symbol_rows) == 2
    assert out.symbol_rows[0].symbol == "BTCUSDT"


def test_format_coverage_lines_contains_core_fields() -> None:
    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-02-01 00:00:00", end="2026-02-01 00:02:00"),
    )

    def _fake_signal(*args, **kwargs):
        return {
            "total_count": 2,
            "day_count": 1,
            "min_ts": "2026-02-01T00:00:10",
            "max_ts": "2026-02-01T00:01:10",
            "by_symbol": {"BTCUSDT": {"count": 2, "min_ts": "2026-02-01T00:00:10", "max_ts": "2026-02-01T00:01:10"}},
        }

    def _fake_candle(*args, **kwargs):
        return {
            "total_count": 3,
            "min_ts": datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc),
            "max_ts": datetime(2026, 2, 1, 0, 2, tzinfo=timezone.utc),
            "by_symbol": {"BTCUSDT": {"count": 3, "min_ts": "2026-02-01", "max_ts": "2026-02-01"}},
        }

    from src.backtest import precheck as precheck_mod

    orig_signal = precheck_mod._load_signal_coverage_from_sqlite
    orig_candle = precheck_mod._load_candle_coverage_from_pg
    try:
        precheck_mod._load_signal_coverage_from_sqlite = _fake_signal
        precheck_mod._load_candle_coverage_from_pg = _fake_candle
        report = compute_coverage_report(cfg)
    finally:
        precheck_mod._load_signal_coverage_from_sqlite = orig_signal
        precheck_mod._load_candle_coverage_from_pg = orig_candle

    lines = format_coverage_lines(report)
    joined = "\n".join(lines)
    assert "window=" in joined
    assert "signals=2" in joined
    assert "candles=3" in joined
    assert "BTCUSDT" in joined


def test_collect_precheck_failures_history_mode_enforces_signal_thresholds() -> None:
    from src.backtest.__main__ import _collect_precheck_failures
    from src.backtest.precheck import BacktestCoverageReport

    report = BacktestCoverageReport(
        start="2026-02-01T00:00:00+00:00",
        end="2026-02-02T00:00:00+00:00",
        timeframe="1m",
        symbols=["BTCUSDT"],
        signal_count=40,
        signal_days=2,
        signal_min_ts="2026-02-01T00:00:00+00:00",
        signal_max_ts="2026-02-01T10:00:00+00:00",
        candle_count=1000,
        candle_min_ts="2026-02-01T00:00:00+00:00",
        candle_max_ts="2026-02-02T00:00:00+00:00",
        expected_candle_count=1440,
        candle_coverage_pct=69.44,
        symbol_rows=[],
    )

    failures = _collect_precheck_failures(
        report,
        mode="history_signal",
        min_signal_days=7,
        min_signal_count=200,
        min_candle_coverage_pct=95.0,
    )

    assert any("signal day coverage too low" in item for item in failures)
    assert any("signal count too low" in item for item in failures)
    assert any("candle coverage too low" in item for item in failures)


def test_collect_precheck_failures_offline_mode_ignores_signal_thresholds() -> None:
    from src.backtest.__main__ import _collect_precheck_failures
    from src.backtest.precheck import BacktestCoverageReport

    report = BacktestCoverageReport(
        start="2026-02-01T00:00:00+00:00",
        end="2026-02-02T00:00:00+00:00",
        timeframe="1m",
        symbols=["BTCUSDT"],
        signal_count=0,
        signal_days=0,
        signal_min_ts="",
        signal_max_ts="",
        candle_count=1400,
        candle_min_ts="2026-02-01T00:00:00+00:00",
        candle_max_ts="2026-02-02T00:00:00+00:00",
        expected_candle_count=1440,
        candle_coverage_pct=97.22,
        symbol_rows=[],
    )

    failures = _collect_precheck_failures(
        report,
        mode="offline_replay",
        min_signal_days=7,
        min_signal_count=200,
        min_candle_coverage_pct=95.0,
    )

    assert failures == []


def test_build_input_quality_report_tracks_gaps_and_no_next_open() -> None:
    from src.backtest.models import BacktestConfig, Bar, DateRange, SignalEvent

    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t3 = t0 + timedelta(minutes=3)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:03:00"),
    )
    signals = [
        SignalEvent(1, t0, "BTCUSDT", "BUY", 80, "test", "1m", "sqlite", 100.0),
        SignalEvent(2, t3, "BTCUSDT", "BUY", 90, "test", "1m", "sqlite", 101.0),
    ]
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 102, 99, 101, 1),
            Bar("BTCUSDT", t3, 101, 103, 100, 102, 1),
        ]
    }

    report = build_input_quality_report(
        cfg,
        run_id="iq-unit",
        mode="history_signal",
        signals=signals,
        bars_by_symbol=bars,
    )

    assert report.signal_count == 2
    assert report.aggregated_signal_bucket_count == 2
    assert report.candle_count == 3
    assert report.expected_candle_count == 4
    assert report.no_next_open_bucket_count == 1
    assert report.dropped_signal_count == 1
    assert report.quality_score == pytest.approx(48.5)
    assert report.quality_status == "fail"
    assert report.quality_breakdown["missing_candle_penalty"] == pytest.approx(5.0)
    assert report.quality_breakdown["gap_penalty"] == pytest.approx(1.5)
    assert report.quality_breakdown["no_next_open_penalty"] == pytest.approx(12.5)
    assert report.quality_breakdown["dropped_signal_penalty"] == pytest.approx(7.5)
    assert report.symbol_rows[0].gap_count == 1
    assert report.symbol_rows[0].missing_candle_count == 1
    assert report.symbol_rows[0].no_next_open_bucket_count == 1
    assert report.symbol_rows[0].dropped_signal_count == 1
    assert report.symbol_rows[0].quality_score == pytest.approx(48.5)
    assert report.symbol_rows[0].quality_status == "fail"


def test_build_input_quality_report_treats_gap_before_later_bar_as_missing_next_open() -> None:
    from src.backtest.models import BacktestConfig, Bar, DateRange, SignalEvent

    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=1)
    t3 = t0 + timedelta(minutes=3)

    cfg = BacktestConfig(
        symbols=["BTCUSDT"],
        timeframe="1m",
        date_range=DateRange(start="2026-01-01 00:00:00", end="2026-01-01 00:03:00"),
    )
    signals = [
        SignalEvent(1, t1, "BTCUSDT", "BUY", 80, "test", "1m", "sqlite", 100.0),
    ]
    bars = {
        "BTCUSDT": [
            Bar("BTCUSDT", t0, 100, 101, 99, 100, 1),
            Bar("BTCUSDT", t1, 100, 102, 99, 101, 1),
            Bar("BTCUSDT", t3, 101, 103, 100, 102, 1),
        ]
    }

    report = build_input_quality_report(
        cfg,
        run_id="iq-gap-unit",
        mode="history_signal",
        signals=signals,
        bars_by_symbol=bars,
    )

    assert report.aggregated_signal_bucket_count == 1
    assert report.no_next_open_bucket_count == 1
    assert report.dropped_signal_count == 1
    assert report.symbol_rows[0].no_next_open_bucket_count == 1
    assert report.symbol_rows[0].dropped_signal_count == 1
