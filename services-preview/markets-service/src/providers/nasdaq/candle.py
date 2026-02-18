"""Nasdaq K线数据获取器（免费 / 无 API Key）

数据源：
- https://charting.nasdaq.com/data/charting/intraday
  返回最近 N 个交易日的分钟级价格序列（Value）与分钟成交量（Volume）。

限制：
- 该接口提供的是“分钟价格点”(Value) 而非严格意义的分钟 OHLC。
  这里会生成“伪 OHLC”：
    open = 上一分钟 close（首条使用 close）
    close = Value
    high/low = max/min(open, close)
  适用于需要 close 序列的指标；若策略强依赖真实 high/low，请使用授权源（如 AllTick）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any

import requests

from core.fetcher import BaseFetcher
from core.registry import register_fetcher
from models.candle import Candle, CandleQuery

def _get_ny_tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")


_REQUEST_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://www.nasdaq.com",
    "referer": "https://www.nasdaq.com/",
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

_CHARTING_HEADERS = {
    # charting.nasdaq.com 对 Referer/User-Agent 更敏感
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://charting.nasdaq.com/dynamic/chart.html",
    "user-agent": _REQUEST_HEADERS["user-agent"],
}


@lru_cache(maxsize=512)
def _fetch_exchange(symbol: str) -> str:
    """查询 Nasdaq quote/info 获取交易所标识（用于 raw.us_equity_* 的 exchange 字段）"""
    url = f"https://api.nasdaq.com/api/quote/{symbol}/info?assetclass=stocks"
    resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    exchange = data.get("exchange")
    return str(exchange or "US")


def _parse_et_to_utc(dt_str: str) -> datetime:
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    tz = _get_ny_tz()
    return dt.replace(tzinfo=tz).astimezone(timezone.utc)


@register_fetcher("nasdaq", "candle")
class NasdaqCandleFetcher(BaseFetcher[CandleQuery, Candle]):
    """Nasdaq 分钟线 fetcher（无 API Key）。"""

    def transform_query(self, params: dict[str, Any]) -> CandleQuery:
        return CandleQuery(**params)

    async def extract(self, query: CandleQuery) -> list[dict[str, Any]]:
        if query.market != "us_stock":
            return []
        if query.interval != "1m":
            # 目前只实现分钟线接入；更高周期可在 storage/compute 层聚合
            return []

        # 兼容 AllTick 常见格式: AAPL.US -> AAPL
        symbol = str(query.symbol).strip()
        if symbol.upper().endswith(".US"):
            symbol = symbol.rsplit(".", 1)[0]

        most_recent_days = 1  # 近实时调度只需要当天分钟线
        url = (
            "https://charting.nasdaq.com/data/charting/intraday"
            f"?symbol={symbol}&mostRecent={most_recent_days}&includeLatestIntradayData=1"
        )

        # 该接口对 headers 比较敏感；放到线程池避免阻塞事件循环
        resp = await asyncio.to_thread(requests.get, url, headers=_CHARTING_HEADERS, timeout=20)
        resp.raise_for_status()
        payload = resp.json()

        market_data = payload.get("marketData") or []
        if not market_data:
            return []

        exchange = _fetch_exchange(symbol)

        rows: list[dict[str, Any]] = []
        for r in market_data:
            # r: {"Date": "2026-02-02 09:30:00", "Value": 123.45, "Volume": 123456}
            rows.append(
                {
                    "datetime": r.get("Date"),
                    "price": r.get("Value"),
                    "volume": r.get("Volume"),
                    "_market": query.market,
                    "_symbol": symbol,
                    "_interval": query.interval,
                    "_exchange": exchange,
                    "_limit": query.limit,
                }
            )
        return rows

    def transform_data(self, raw: list[dict[str, Any]]) -> list[Candle]:
        if not raw:
            return []

        # 取公共字段（extract 已写入到每行）
        market = raw[0].get("_market") or "us_stock"
        symbol = str(raw[0].get("_symbol") or "")
        interval = raw[0].get("_interval") or "1m"
        exchange = str(raw[0].get("_exchange") or "US")
        limit = raw[0].get("_limit")
        try:
            limit_i = int(limit) if limit is not None else None
        except Exception:
            limit_i = None

        # sort by time
        rows = [r for r in raw if r.get("datetime")]
        rows.sort(key=lambda r: r["datetime"])

        candles: list[Candle] = []
        prev_close: Decimal | None = None
        for r in rows:
            ts_utc = _parse_et_to_utc(str(r["datetime"]))
            close = Decimal(str(r.get("price") or 0))
            open_ = prev_close if prev_close is not None else close
            high = close if close >= open_ else open_
            low = close if close <= open_ else open_
            volume = Decimal(str(r.get("volume") or 0))

            candles.append(
                Candle(
                    market=market,
                    asset_type="spot",
                    exchange=exchange,
                    symbol=symbol,
                    interval=interval,
                    timestamp=ts_utc,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    quote_volume=None,
                    source="nasdaq",
                )
            )
            prev_close = close

        if limit_i is not None and limit_i > 0:
            return candles[-limit_i:]
        return candles
