"""Tencent Quote -> 分钟线（免费 / 无 API Key）

数据源：
- https://qt.gtimg.cn/q=<codes>

覆盖：
- A股: sz000001 / sh688256 ...
- 港股: hk01810 / hk00700 ...
- 美股: usAAPL / usTSLA ...

说明：
- 该接口提供的是“最新报价快照”，不提供完整的分钟 OHLCV 历史。
- 本 fetcher 通过“轮询 + 归并到分钟桶”的方式，生成分钟级 Candle（适合接入与调度脚手架）。
- 港股免费源通常存在约 15min 延迟（以返回的时间字段为准）。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import requests

from core.fetcher import BaseFetcher
from core.registry import register_fetcher
from models.candle import Candle, CandleQuery

_VAR_RE = re.compile(r'^v_(?P<code>[A-Za-z0-9_]+)="(?P<value>.*)";\s*$')
_HK_TS_RE = re.compile(r"^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}$")
_US_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$")
_CN_TS_RE = re.compile(r"^\d{14}$")  # YYYYMMDDHHMMSS

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://quote.eastmoney.com/",
}


@dataclass(frozen=True)
class _QuotePoint:
    market: str
    symbol: str
    exchange: str
    ts_utc: datetime
    last: Decimal
    cum_volume: Decimal | None


def _floor_minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def _normalize_us(symbol: str) -> tuple[str, str, str]:
    sym = str(symbol).strip()
    if sym.upper().endswith(".US"):
        sym = sym.rsplit(".", 1)[0]
    # qt.gtimg.cn: usAAPL
    code = f"us{sym.upper()}"
    return code, sym.upper(), "us"


def _normalize_hk(symbol: str) -> tuple[str, str, str]:
    sym = str(symbol).strip()
    if sym.upper().endswith(".HK"):
        sym = sym.rsplit(".", 1)[0]
    if sym.isdigit() and len(sym) < 5:
        sym = sym.zfill(5)
    code = f"hk{sym}"
    return code, sym, "hkex"


def _normalize_cn(symbol: str) -> tuple[str, str, str]:
    sym = str(symbol).strip()
    exch_hint = ""
    if "." in sym:
        base, suffix = sym.split(".", 1)
        exch_hint = suffix.upper()
        sym = base
    if len(sym) >= 8 and sym[:2].upper() in {"SZ", "SH"} and sym[2:].isdigit():
        exch_hint = sym[:2].upper()
        sym = sym[2:]

    if exch_hint == "SH":
        prefix, exchange = "sh", "sse"
    elif exch_hint == "SZ":
        prefix, exchange = "sz", "szse"
    else:
        prefix, exchange = ("sh", "sse") if sym and sym[0] in {"5", "6", "9"} else ("sz", "szse")

    code = f"{prefix}{sym}"
    return code, sym, exchange


def _fetch_var(code: str) -> str:
    url = f"https://qt.gtimg.cn/q={code}"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = "gbk"
    line = resp.text.strip()
    m = _VAR_RE.match(line)
    if not m:
        return ""
    return m.group("value")


def _parse_point(code: str, value: str, market: str, symbol: str, exchange: str) -> _QuotePoint | None:
    if not value:
        return None

    fields = value.split("~")
    if len(fields) < 7:
        return None

    try:
        last = Decimal(fields[3])
        cum_vol = Decimal(fields[6])
    except Exception:
        return None

    ts_field = None
    for f in fields:
        if market == "hk_stock" and _HK_TS_RE.match(f):
            ts_field = f
            break
        if market == "cn_stock" and _CN_TS_RE.match(f):
            ts_field = f
            break
        if market == "us_stock" and _US_TS_RE.match(f):
            ts_field = f
            break
    if not ts_field:
        return None

    try:
        from zoneinfo import ZoneInfo

        if market == "hk_stock":
            dt = datetime.strptime(ts_field, "%Y/%m/%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
        elif market == "cn_stock":
            dt = datetime.strptime(ts_field, "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        else:
            # us_stock: 以纽约时间解释（足够用于计算延迟/分钟桶）
            dt = datetime.strptime(ts_field, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/New_York"))

        dt_utc = dt.astimezone(timezone.utc)
    except Exception:
        return None

    return _QuotePoint(market=market, symbol=symbol, exchange=exchange, ts_utc=dt_utc, last=last, cum_volume=cum_vol)


@register_fetcher("tencent", "candle")
class TencentQuoteCandleFetcher(BaseFetcher[CandleQuery, Candle]):
    """基于 qt.gtimg.cn 最新报价的分钟线接入（轮询型）"""

    def __init__(self):
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def transform_query(self, params: dict[str, Any]) -> CandleQuery:
        return CandleQuery(**params)

    async def extract(self, query: CandleQuery) -> list[dict[str, Any]]:
        if query.interval != "1m":
            return []

        if query.market == "us_stock":
            code, sym, exchange = _normalize_us(query.symbol)
        elif query.market == "hk_stock":
            code, sym, exchange = _normalize_hk(query.symbol)
        elif query.market == "cn_stock":
            code, sym, exchange = _normalize_cn(query.symbol)
        else:
            return []

        try:
            value = await asyncio.to_thread(_fetch_var, code)
        except Exception:
            return []

        point = _parse_point(code, value, market=query.market, symbol=sym, exchange=exchange)
        if point is None:
            return []

        minute_open = _floor_minute(point.ts_utc)
        state_key = f"{point.market}:{point.symbol}"

        async with self._lock:
            st = self._state.get(state_key)
            if st and st.get("minute_open") == minute_open:
                last = point.last
                st["high"] = max(st["high"], last)
                st["low"] = min(st["low"], last)
                st["close"] = last

                if point.cum_volume is not None and st.get("cum_volume") is not None:
                    delta = point.cum_volume - st["cum_volume"]
                    if delta < 0:
                        delta = Decimal(0)
                    st["minute_volume"] += delta
                    st["cum_volume"] = point.cum_volume
            else:
                minute_volume = Decimal(0)
                if st and point.cum_volume is not None and st.get("cum_volume") is not None:
                    delta = point.cum_volume - st["cum_volume"]
                    if delta < 0:
                        delta = Decimal(0)
                    minute_volume = delta

                st = {
                    "minute_open": minute_open,
                    "open": point.last,
                    "high": point.last,
                    "low": point.last,
                    "close": point.last,
                    "cum_volume": point.cum_volume,
                    "minute_volume": minute_volume,
                }
                self._state[state_key] = st

            row = {
                "exchange": point.exchange,
                "symbol": point.symbol,
                "timestamp": st["minute_open"],
                "open": st["open"],
                "high": st["high"],
                "low": st["low"],
                "close": st["close"],
                "volume": st["minute_volume"],
                "_market": point.market,
                "_interval": query.interval,
                "_source_event_time": point.ts_utc,  # 用于计算延迟/对账
            }

        return [row]

    def transform_data(self, raw: list[dict[str, Any]]) -> list[Candle]:
        results: list[Candle] = []
        for r in raw:
            ts = r.get("timestamp")
            if not isinstance(ts, datetime):
                continue
            results.append(
                Candle(
                    market=str(r.get("_market") or ""),
                    asset_type="spot",
                    exchange=str(r.get("exchange") or ""),
                    symbol=str(r.get("symbol") or ""),
                    interval=str(r.get("_interval") or "1m"),
                    timestamp=ts,
                    open=Decimal(str(r.get("open") or 0)),
                    high=Decimal(str(r.get("high") or 0)),
                    low=Decimal(str(r.get("low") or 0)),
                    close=Decimal(str(r.get("close") or 0)),
                    volume=Decimal(str(r.get("volume") or 0)),
                    quote_volume=None,
                    source="tencent",
                )
            )
        return results


# Export helpers for tests
__all__ = [
    "_parse_point",
]

