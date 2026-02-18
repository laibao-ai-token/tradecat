"""Sina Quote -> 分钟线（免费 / 无 API Key）

数据源：
- https://hq.sinajs.cn/list=<codes>

说明：
- 该接口返回“最新报价/日内累计成交量”等信息，不提供完整的分钟 OHLCV 历史。
- 本 fetcher 通过“轮询 + 归并到分钟桶”的方式，生成分钟级 Candle（适合接入与调度脚手架）。
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

_VAR_RE = re.compile(r'^var\s+hq_str_(?P<code>[A-Za-z0-9_]+)="(?P<value>.*)";\s*$')
_CN_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CN_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
    "Accept": "*/*",
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


def _normalize_us_symbol(symbol: str) -> tuple[str, str, str]:
    sym = str(symbol).strip()
    if sym.upper().endswith(".US"):
        sym = sym.rsplit(".", 1)[0]
    code = f"gb_{sym.lower()}"
    return code, sym.upper(), "us"


def _normalize_hk_symbol(symbol: str) -> tuple[str, str, str]:
    sym = str(symbol).strip()
    if sym.upper().endswith(".HK"):
        sym = sym.rsplit(".", 1)[0]
    if sym.isdigit() and len(sym) < 5:
        sym = sym.zfill(5)
    code = f"hk{sym}"
    return code, sym, "hkex"


def _normalize_cn_symbol(symbol: str) -> tuple[str, str, str]:
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


def _fetch_sina_vars(codes: list[str]) -> dict[str, str]:
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = "gb18030"

    out: dict[str, str] = {}
    for line in resp.text.splitlines():
        m = _VAR_RE.match(line.strip())
        if not m:
            continue
        out[m.group("code")] = m.group("value")
    return out


def _parse_hk_point(value: str, symbol: str, exchange: str) -> _QuotePoint | None:
    if not value:
        return None
    fields = value.split(",")
    if len(fields) < 19:
        return None
    try:
        last = Decimal(fields[6])
        cum_vol = Decimal(fields[12])
        dt_str = f"{fields[17]} {fields[18]}:00"
        dt = datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S")
        from zoneinfo import ZoneInfo

        dt_utc = dt.replace(tzinfo=ZoneInfo("Asia/Hong_Kong")).astimezone(timezone.utc)
    except Exception:
        return None
    return _QuotePoint(market="hk_stock", symbol=symbol, exchange=exchange, ts_utc=dt_utc, last=last, cum_volume=cum_vol)


def _parse_us_point(value: str, symbol: str, exchange: str) -> _QuotePoint | None:
    if not value:
        return None
    fields = value.split(",")
    if len(fields) < 12:
        return None
    try:
        last = Decimal(fields[1])
        cum_vol = Decimal(fields[10])
        dt = datetime.strptime(fields[3], "%Y-%m-%d %H:%M:%S")
        from zoneinfo import ZoneInfo

        dt_utc = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
    except Exception:
        return None
    return _QuotePoint(market="us_stock", symbol=symbol, exchange=exchange, ts_utc=dt_utc, last=last, cum_volume=cum_vol)


def _parse_cn_point(value: str, symbol: str, exchange: str) -> _QuotePoint | None:
    if not value:
        return None
    fields = value.split(",")
    if len(fields) < 32:
        return None
    try:
        last = Decimal(fields[3])
        cum_vol = Decimal(fields[8])
        # A 股行情字符串尾部字段会因市场/接口版本略有差异：
        # - 常见: ..., YYYY-MM-DD, HH:MM:SS
        # - 也可能: ..., YYYY-MM-DD, HH:MM:SS, 00, ''
        date_str = None
        time_str = None
        for i in range(len(fields) - 1):
            if _CN_DATE_RE.match(fields[i]) and _CN_TIME_RE.match(fields[i + 1]):
                date_str = fields[i]
                time_str = fields[i + 1]
                break
        if not date_str or not time_str:
            return None

        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        from zoneinfo import ZoneInfo

        dt_utc = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
    except Exception:
        return None
    return _QuotePoint(market="cn_stock", symbol=symbol, exchange=exchange, ts_utc=dt_utc, last=last, cum_volume=cum_vol)


@register_fetcher("sina", "candle")
class SinaQuoteCandleFetcher(BaseFetcher[CandleQuery, Candle]):
    """基于 Sina 最新报价的分钟线接入（轮询型）"""

    def __init__(self):
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def transform_query(self, params: dict[str, Any]) -> CandleQuery:
        return CandleQuery(**params)

    async def extract(self, query: CandleQuery) -> list[dict[str, Any]]:
        if query.interval != "1m":
            return []

        if query.market == "us_stock":
            code, sym, exchange = _normalize_us_symbol(query.symbol)
        elif query.market == "hk_stock":
            code, sym, exchange = _normalize_hk_symbol(query.symbol)
        elif query.market == "cn_stock":
            code, sym, exchange = _normalize_cn_symbol(query.symbol)
        else:
            return []

        try:
            vars_map = await asyncio.to_thread(_fetch_sina_vars, [code])
        except Exception:
            return []

        value = vars_map.get(code, "")
        if query.market == "hk_stock":
            point = _parse_hk_point(value, symbol=sym, exchange=exchange)
        elif query.market == "us_stock":
            point = _parse_us_point(value, symbol=sym, exchange=exchange)
        else:
            point = _parse_cn_point(value, symbol=sym, exchange=exchange)

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
                    source="sina",
                )
            )
        return results
