from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from providers.tencent.candle import _parse_point


def test_tencent_parse_hk_point_smoke():
    # Trimmed but contains the critical fields (price/volume/time).
    value = (
        "100~小米集团-W~01810~34.760~35.060~34.820~105208839.0~0~0~34.760~0~0~0~0~0~0~0~0~0~34.760~0~0~0~0~0~0~0~0~0~"
        "105208839.0~2026/02/03 15:05:11~-0.300~-0.86~35.280~34.360~34.760~105208839.0~3658261605.580~HKD~1~30"
    )
    pt = _parse_point("hk01810", value, market="hk_stock", symbol="01810", exchange="hkex")
    assert pt is not None
    assert pt.market == "hk_stock"
    assert pt.symbol == "01810"
    assert pt.exchange == "hkex"
    assert pt.last == Decimal("34.760")
    assert pt.cum_volume == Decimal("105208839.0")
    assert pt.ts_utc == datetime(2026, 2, 3, 7, 5, 11, tzinfo=timezone.utc)


def test_tencent_parse_cn_point_smoke():
    value = (
        "1~寒武纪-U~688256~1128.00~1242.00~1255.00~22614455~10590769~12023686~~20260203152016~-114.00~-9.18~CNY~0"
    )
    pt = _parse_point("sh688256", value, market="cn_stock", symbol="688256", exchange="sse")
    assert pt is not None
    assert pt.market == "cn_stock"
    assert pt.symbol == "688256"
    assert pt.exchange == "sse"
    assert pt.last == Decimal("1128.00")
    assert pt.cum_volume == Decimal("22614455")
    assert pt.ts_utc == datetime(2026, 2, 3, 7, 20, 16, tzinfo=timezone.utc)


def test_tencent_parse_us_point_smoke():
    value = (
        "200~苹果~AAPL.OQ~270.01~259.48~260.03~73913425~0~0~~2026-02-02 16:00:03~USD~73913425"
    )
    pt = _parse_point("usAAPL", value, market="us_stock", symbol="AAPL", exchange="us")
    assert pt is not None
    assert pt.market == "us_stock"
    assert pt.symbol == "AAPL"
    assert pt.exchange == "us"
    assert pt.last == Decimal("270.01")
    assert pt.cum_volume == Decimal("73913425")
    assert pt.ts_utc == datetime(2026, 2, 2, 21, 0, 3, tzinfo=timezone.utc)

