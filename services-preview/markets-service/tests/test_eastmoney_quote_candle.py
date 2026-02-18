from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from providers.eastmoney.candle import _parse_quote_hk


def test_eastmoney_parse_quote_hk_price_scale_and_ts():
    data = {
        "f43": 34720,  # 34.720
        "f47": 117377688,
        "f57": "01810",
        "f58": "小米集团-W",
        "f86": 1770104810,  # 2026-02-03T07:46:50Z
    }
    pt = _parse_quote_hk(data, symbol="01810")
    assert pt is not None
    assert pt.market == "hk_stock"
    assert pt.exchange == "hkex"
    assert pt.symbol == "01810"
    assert pt.last == Decimal("34.72")
    assert pt.cum_volume == Decimal("117377688")
    assert pt.ts_utc == datetime(2026, 2, 3, 7, 46, 50, tzinfo=timezone.utc)

