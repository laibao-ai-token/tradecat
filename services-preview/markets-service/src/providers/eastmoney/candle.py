"""Eastmoney Quote -> 分钟线（免费 / 无 API Key）

数据源：
- https://push2.eastmoney.com/api/qt/stock/get

覆盖（目前实现）：
- 港股: secid=116.<symbol>，例如 01810 -> 116.01810

说明：
- push2 接口返回“最新报价快照”，并带有更新时间戳（f86，Unix 秒）。
- 本 fetcher 通过“轮询 + 归并到分钟桶”的方式，生成分钟级 Candle（适合接入与调度脚手架）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import requests

from core.fetcher import BaseFetcher
from core.registry import register_fetcher
from models.candle import Candle, CandleQuery

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com/",
}

_QUOTE_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86"


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


def _normalize_hk_symbol(symbol: str) -> str:
    sym = str(symbol).strip()
    if sym.upper().endswith(".HK"):
        sym = sym.rsplit(".", 1)[0]
    if sym.isdigit() and len(sym) < 5:
        sym = sym.zfill(5)
    return sym


def _parse_quote_hk(data: dict[str, Any], *, symbol: str) -> _QuotePoint | None:
    """Parse Eastmoney quote payload (data) into a QuotePoint."""
    # f86: update time (Unix seconds)
    ts = data.get("f86")
    if not ts:
        return None

    try:
        ts_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        # HK price fields are scaled by 1000 (e.g., 34720 -> 34.720)
        scale = Decimal("1000")
        last = Decimal(str(data.get("f43", 0))) / scale

        cum_vol = data.get("f47")
        cum_vol_dec = Decimal(str(cum_vol)) if cum_vol is not None else None
    except Exception:
        return None

    return _QuotePoint(market="hk_stock", symbol=symbol, exchange="hkex", ts_utc=ts_utc, last=last, cum_volume=cum_vol_dec)


def _fetch_quote_hk(symbol: str) -> _QuotePoint | None:
    sym = _normalize_hk_symbol(symbol)
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {"secid": f"116.{sym}", "fields": _QUOTE_FIELDS}

    resp = requests.get(url, params=params, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}

    return _parse_quote_hk(data, symbol=sym)


@register_fetcher("eastmoney", "candle")
class EastmoneyQuoteCandleFetcher(BaseFetcher[CandleQuery, Candle]):
    """基于 Eastmoney push2 最新报价的分钟线接入（轮询型）"""

    def __init__(self):
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def transform_query(self, params: dict[str, Any]) -> CandleQuery:
        return CandleQuery(**params)

    async def extract(self, query: CandleQuery) -> list[dict[str, Any]]:
        if query.interval != "1m":
            return []
        if query.market != "hk_stock":
            return []

        try:
            point = await asyncio.to_thread(_fetch_quote_hk, query.symbol)
        except Exception:
            return []
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
                "_source_event_time": point.ts_utc,
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
                    source="eastmoney",
                )
            )
        return results


__all__ = [
    "_fetch_quote_hk",
    "_parse_quote_hk",
]
