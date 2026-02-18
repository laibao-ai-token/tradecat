from __future__ import annotations

from datetime import datetime, timezone

from providers.alltick.candle import AllTickCandleFetcher


def test_alltick_transform_data_smoke():
    f = AllTickCandleFetcher()

    raw = [
        {
            "timestamp": "1700000000",
            "open_price": "1.0",
            "high_price": "2.0",
            "low_price": "0.5",
            "close_price": "1.5",
            "volume": "100",
            "turnover": "123.45",
            "_market": "us_stock",
            "_symbol": "AAPL",
            "_interval": "1m",
            "_code": "AAPL.US",
        }
    ]

    candles = f.transform_data(raw)
    assert len(candles) == 1

    c = candles[0]
    assert c.market == "us_stock"
    assert c.symbol == "AAPL"
    assert c.interval == "1m"
    assert c.source == "alltick"
    assert c.timestamp == datetime.fromtimestamp(1700000000, tz=timezone.utc)
    assert str(c.open) == "1.0"
    assert str(c.close) == "1.5"

