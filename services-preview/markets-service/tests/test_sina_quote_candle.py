from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from providers.sina.candle import _parse_hk_point, _parse_us_point


def test_sina_parse_us_point_smoke():
    value = (
        "苹果,263.4150,1.52,2026-02-02 23:53:37,3.9350,260.0300,265.3700,259.2050,"
        "288.6200,168.4300,20214536,59505086,3871654176763,7.93,33.220000,0.00,0.92,"
        "0.26,0.00,14697925998,63,0.0000,0.00,0.00,,Feb 02 10:53AM EST,259.4800,0,1,2026,"
        "5310497832.0000,0.0000,0.0000,0.0000,0.0000,259.4800"
    )
    point = _parse_us_point(value, symbol="AAPL", exchange="us")
    assert point is not None
    assert point.market == "us_stock"
    assert point.symbol == "AAPL"
    assert point.last == Decimal("263.4150")
    assert point.cum_volume == Decimal("20214536")
    assert point.ts_utc == datetime(2026, 2, 2, 15, 53, 37, tzinfo=timezone.utc)


def test_sina_parse_hk_point_smoke():
    value = (
        "TENCENT,腾讯控股,598.000,606.000,604.500,590.500,597.500,-8.500,-1.403,"
        "601.00000,601.00000,15379832498,25774632,0.000,0.000,683.000,390.789,2026/02/02,16:06"
    )
    point = _parse_hk_point(value, symbol="00700", exchange="hkex")
    assert point is not None
    assert point.market == "hk_stock"
    assert point.symbol == "00700"
    assert point.last == Decimal("597.500")
    assert point.cum_volume == Decimal("25774632")
    assert point.ts_utc == datetime(2026, 2, 2, 8, 6, 0, tzinfo=timezone.utc)

