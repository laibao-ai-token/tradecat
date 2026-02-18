from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from providers.nasdaq.candle import NasdaqCandleFetcher


def test_nasdaq_transform_pseudo_ohlc_and_tz():
    fetcher = NasdaqCandleFetcher()

    raw = [
        {
            "datetime": "2026-02-02 09:30:00",  # America/New_York (EST)
            "price": 100,
            "volume": 10,
            "_market": "us_stock",
            "_symbol": "AAPL",
            "_interval": "1m",
            "_exchange": "NASDAQ-GS",
            "_limit": 1000,
        },
        {
            "datetime": "2026-02-02 09:31:00",
            "price": 101,
            "volume": 5,
            "_market": "us_stock",
            "_symbol": "AAPL",
            "_interval": "1m",
            "_exchange": "NASDAQ-GS",
            "_limit": 1000,
        },
    ]

    candles = fetcher.transform_data(raw)
    assert len(candles) == 2

    assert candles[0].timestamp == datetime(2026, 2, 2, 14, 30, tzinfo=timezone.utc)
    assert candles[0].open == Decimal("100")
    assert candles[0].high == Decimal("100")
    assert candles[0].low == Decimal("100")
    assert candles[0].close == Decimal("100")

    assert candles[1].open == Decimal("100")
    assert candles[1].high == Decimal("101")
    assert candles[1].low == Decimal("100")
    assert candles[1].close == Decimal("101")


def test_nasdaq_transform_respects_limit():
    fetcher = NasdaqCandleFetcher()

    raw = [
        {
            "datetime": "2026-02-02 09:30:00",
            "price": 100,
            "volume": 10,
            "_market": "us_stock",
            "_symbol": "AAPL",
            "_interval": "1m",
            "_exchange": "NASDAQ-GS",
            "_limit": 1,
        },
        {
            "datetime": "2026-02-02 09:31:00",
            "price": 101,
            "volume": 5,
            "_market": "us_stock",
            "_symbol": "AAPL",
            "_interval": "1m",
            "_exchange": "NASDAQ-GS",
            "_limit": 1,
        },
    ]

    candles = fetcher.transform_data(raw)
    assert len(candles) == 1
    assert candles[0].close == Decimal("101")

