"""yfinance K线数据获取器"""
from __future__ import annotations

import asyncio
import os
from datetime import timezone
from decimal import Decimal
from typing import Any

from config import settings
from core.fetcher import BaseFetcher
from core.registry import register_fetcher
from models.candle import Candle, CandleQuery


@register_fetcher("yfinance", "candle")
class YFinanceCandleFetcher(BaseFetcher[CandleQuery, Candle]):
    """yfinance K线获取器 - 美股/港股/外汇/加密"""

    INTERVAL_MAP = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1wk", "1M": "1mo"
    }

    def __init__(self):
        # 设置代理环境变量 (yfinance 通过 requests 使用)
        if settings.http_proxy:
            os.environ.setdefault("HTTP_PROXY", settings.http_proxy)
            os.environ.setdefault("HTTPS_PROXY", settings.http_proxy)

    def transform_query(self, params: dict[str, Any]) -> CandleQuery:
        return CandleQuery(**params)

    async def extract(self, query: CandleQuery) -> list[dict[str, Any]]:
        import yfinance as yf

        ticker = yf.Ticker(query.symbol)
        interval = self.INTERVAL_MAP.get(query.interval, "1d")

        df = await asyncio.to_thread(
            ticker.history,
            start=query.start,
            end=query.end,
            interval=interval
        )

        if df is None or df.empty:
            return []

        df = df.reset_index()
        rows = df.to_dict("records")
        for r in rows:
            r["_market"] = query.market
            r["_symbol"] = query.symbol
            r["_interval"] = query.interval
        return rows

    def transform_data(self, raw: list[dict[str, Any]]) -> list[Candle]:
        results = []
        for r in raw:
            ts = r.get("Date") or r.get("Datetime")
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)

            market = r.get("_market") or "us_stock"
            symbol = r.get("_symbol") or ""
            interval = r.get("_interval") or "1d"

            # 轻量映射：仅用于分库/标识；真实交易所可后续通过 instruments/symbol_mapping 精化
            exchange = "hkex" if market == "hk_stock" else "us"

            results.append(Candle(
                market=market,
                asset_type="spot",
                exchange=exchange,
                symbol=symbol,
                interval=interval,
                timestamp=ts,
                open=Decimal(str(r.get("Open", 0))),
                high=Decimal(str(r.get("High", 0))),
                low=Decimal(str(r.get("Low", 0))),
                close=Decimal(str(r.get("Close", 0))),
                volume=Decimal(str(r.get("Volume", 0))),
                source="yfinance",
            ))
        return results
