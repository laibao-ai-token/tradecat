from __future__ import annotations

import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

def _install_collector_import_stubs() -> None:
    """为 collectors 模块安装最小依赖桩，避免测试依赖外部组件。"""
    config_mod = types.ModuleType("config")
    config_mod.INTERVAL_TO_MS = {"1m": 60_000}
    config_mod.settings = SimpleNamespace(
        db_exchange="binance_futures_um",
        http_proxy=None,
        ccxt_exchange="binance",
        data_dir=Path("."),
    )
    sys.modules["config"] = config_mod

    adapters_pkg = types.ModuleType("adapters")
    sys.modules["adapters"] = adapters_pkg

    ccxt_mod = types.ModuleType("adapters.ccxt")
    ccxt_mod.fetch_ohlcv = lambda *args, **kwargs: []
    ccxt_mod.load_symbols = lambda *args, **kwargs: []
    ccxt_mod.to_rows = lambda *args, **kwargs: []
    sys.modules["adapters.ccxt"] = ccxt_mod

    class _NoopMetrics:
        def inc(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def set(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def __str__(self) -> str:
            return "metrics(noop)"

    class _NoopTimer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def __enter__(self) -> "_NoopTimer":
            return self

        def __exit__(self, *args: Any) -> bool:
            del args
            return False

    adapters_metrics_mod = types.ModuleType("adapters.metrics")
    adapters_metrics_mod.Timer = _NoopTimer
    adapters_metrics_mod.metrics = _NoopMetrics()
    sys.modules["adapters.metrics"] = adapters_metrics_mod

    rate_limiter_mod = types.ModuleType("adapters.rate_limiter")
    rate_limiter_mod.acquire = lambda *args, **kwargs: None
    rate_limiter_mod.release = lambda *args, **kwargs: None
    rate_limiter_mod.set_ban = lambda *args, **kwargs: None
    rate_limiter_mod.parse_ban = lambda *args, **kwargs: 0.0
    sys.modules["adapters.rate_limiter"] = rate_limiter_mod

    class _StubTimescaleAdapter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def close(self) -> None:
            return None

    timescale_mod = types.ModuleType("adapters.timescale")
    timescale_mod.TimescaleAdapter = _StubTimescaleAdapter
    sys.modules["adapters.timescale"] = timescale_mod


_install_collector_import_stubs()

import src.collectors.backfill as backfill_mod
import src.collectors.metrics as metrics_mod


class _FakeTimescale:
    def __init__(self) -> None:
        self.saved_metrics: list[dict[str, Any]] = []
        self.saved_candles: list[dict[str, Any]] = []
        self.last_interval: str | None = None

    def upsert_metrics(self, rows: list[dict[str, Any]]) -> int:
        self.saved_metrics = list(rows)
        return len(rows)

    def upsert_candles(self, interval: str, rows: list[dict[str, Any]]) -> int:
        self.last_interval = interval
        self.saved_candles = list(rows)
        return len(rows)


@pytest.mark.integration
def test_metrics_collector_collect_parse_save_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ts = _FakeTimescale()
    monkeypatch.setattr(metrics_mod, "TimescaleAdapter", lambda: fake_ts)
    collector = metrics_mod.MetricsCollector(workers=1)

    ts_ms = 1_700_000_123_456

    def _fake_get(url: str, params: dict[str, Any]) -> list[dict[str, str]]:
        del params
        if "openInterestHist" in url:
            return [{"timestamp": ts_ms, "sumOpenInterest": "10.5", "sumOpenInterestValue": "20.6"}]
        if "topLongShortPositionRatio" in url:
            return [{"longShortRatio": "1.11"}]
        if "topLongShortAccountRatio" in url:
            return [{"longShortRatio": "1.22"}]
        if "globalLongShortAccountRatio" in url:
            return [{"longShortRatio": "1.33"}]
        if "takerlongshortRatio" in url:
            return [{"buySellRatio": "1.44"}]
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(collector, "_get", _fake_get)

    written = collector.run_once(["BTCUSDT"])
    assert written == 1
    assert len(fake_ts.saved_metrics) == 1

    row = fake_ts.saved_metrics[0]
    expected_dt = datetime.fromtimestamp(((ts_ms // 300000) * 300000) / 1000, tz=timezone.utc).replace(tzinfo=None)

    assert row["symbol"] == "BTCUSDT"
    assert row["create_time"] == expected_dt
    assert row["sum_open_interest"] == Decimal("10.5")
    assert row["sum_open_interest_value"] == Decimal("20.6")
    assert row["sum_toptrader_long_short_ratio"] == Decimal("1.11")
    assert row["count_toptrader_long_short_ratio"] == Decimal("1.22")
    assert row["count_long_short_ratio"] == Decimal("1.33")
    assert row["sum_taker_long_short_vol_ratio"] == Decimal("1.44")
    assert row["source"] == "binance_api"
    assert row["is_closed"] is True


def _write_zip_csv(zip_path: Path, csv_name: str, rows: list[list[str]]) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        content = []
        for row in rows:
            content.append(",".join(row))
        zf.writestr(csv_name, "\n".join(content) + "\n")


@pytest.mark.integration
def test_zip_backfiller_import_kline_zip_parses_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_ts = _FakeTimescale()
    monkeypatch.setattr(backfill_mod.settings, "data_dir", tmp_path)
    backfiller = backfill_mod.ZipBackfiller(fake_ts, workers=1)

    zip_path = tmp_path / "BTCUSDT-1m.zip"
    ts1 = int(datetime(2026, 3, 3, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
    ts2 = int(datetime(2026, 3, 4, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
    _write_zip_csv(
        zip_path,
        "BTCUSDT-1m.csv",
        [
            [str(ts1), "1", "2", "0.5", "1.5", "100", "0", "200", "300", "50", "80"],
            [str(ts2), "2", "3", "1.2", "2.8", "110", "0", "210", "310", "55", "85"],
        ],
    )

    inserted = backfiller._import_kline_zip(zip_path, "btcusdt", "1m", filter_date=date(2026, 3, 3))
    assert inserted == 1
    assert fake_ts.last_interval == "1m"
    assert len(fake_ts.saved_candles) == 1

    row = fake_ts.saved_candles[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["source"] == "binance_zip"
    assert row["open"] == 1.0
    assert row["high"] == 2.0
    assert row["low"] == 0.5
    assert row["close"] == 1.5
    assert row["volume"] == 100.0
    assert row["quote_volume"] == 200.0
    assert row["trade_count"] == 300
    assert row["taker_buy_volume"] == 50.0
    assert row["taker_buy_quote_volume"] == 80.0
    assert row["bucket_ts"].date() == date(2026, 3, 3)


@pytest.mark.integration
def test_zip_backfiller_import_metrics_zip_aligns_time_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_ts = _FakeTimescale()
    monkeypatch.setattr(backfill_mod.settings, "data_dir", tmp_path)
    backfiller = backfill_mod.ZipBackfiller(fake_ts, workers=1)

    zip_path = tmp_path / "BTCUSDT-metrics.zip"
    _write_zip_csv(
        zip_path,
        "BTCUSDT-metrics.csv",
        [
            ["2026-03-03T00:07:59Z", "BTCUSDT", "10.1", "20.2", "1.1", "1.2", "1.3", "1.4"],
            ["bad-ts", "BTCUSDT", "oops", "oops"],
        ],
    )

    inserted = backfiller._import_metrics_zip(zip_path, "btcusdt")
    assert inserted == 1
    assert len(fake_ts.saved_metrics) == 1

    row = fake_ts.saved_metrics[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["create_time"] == datetime(2026, 3, 3, 0, 5)
    assert row["sum_open_interest"] == Decimal("10.1")
    assert row["sum_open_interest_value"] == Decimal("20.2")
    assert row["sum_toptrader_long_short_ratio"] == Decimal("1.1")
    assert row["count_toptrader_long_short_ratio"] == Decimal("1.2")
    assert row["count_long_short_ratio"] == Decimal("1.3")
    assert row["sum_taker_long_short_vol_ratio"] == Decimal("1.4")
    assert row["source"] == "binance_zip"
    assert row["is_closed"] is True
