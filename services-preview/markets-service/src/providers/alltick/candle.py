"""AllTick K线数据获取器（REST）"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import requests

from config import settings
from core.fetcher import BaseFetcher
from core.registry import register_fetcher
from models.candle import Candle, CandleQuery


def _market_default_suffix(market: str) -> str:
    if market == "us_stock":
        return ".US"
    if market == "hk_stock":
        return ".HK"
    if market == "cn_stock":
        # 默认按 A 股常见规则推断（仅兜底）
        return ".SH"  # 6xxxx
    return ""


def _to_alltick_code(market: str, symbol: str) -> str:
    """将输入 symbol 规范化为 AllTick code（尽量兼容简写）"""
    sym = symbol.strip()
    if "." in sym:
        return sym

    if market == "cn_stock":
        suffix = ".SH" if sym.startswith("6") else ".SZ"
        return f"{sym}{suffix}"

    return f"{sym}{_market_default_suffix(market)}"


def _split_alltick_code(code: str) -> tuple[str, str]:
    """AAPL.US -> (symbol, suffix)"""
    if "." not in code:
        return code, ""
    sym, suffix = code.rsplit(".", 1)
    return sym, suffix.upper()


def _suffix_to_exchange(suffix: str) -> str:
    # AllTick 的 code 后缀更像“市场/交易所”标识，这里做轻量映射
    if suffix in {"US", "NYSE", "NASDAQ"}:
        return "us"
    if suffix == "HK":
        return "hkex"
    if suffix == "SZ":
        return "szse"
    if suffix == "SH":
        return "sse"
    return suffix.lower() or "unknown"


@register_fetcher("alltick", "candle")
class AllTickCandleFetcher(BaseFetcher[CandleQuery, Candle]):
    """AllTick K线获取器 - 美股/港股/A股（分钟级）"""

    # https://docs.alltick.co/quote-stock-b-api/kline.html (kline_type)
    INTERVAL_TO_KLINE_TYPE = {
        "1m": 1,
        "5m": 2,
        "15m": 3,
        "30m": 4,
        "60m": 5,
        "1d": 6,
        "1w": 7,
        "1M": 10,
    }

    def transform_query(self, params: dict[str, Any]) -> CandleQuery:
        return CandleQuery(**params)

    async def extract(self, query: CandleQuery) -> list[dict[str, Any]]:
        token = getattr(settings, "alltick_token", None) or ""
        if not token:
            raise RuntimeError("ALLTICK_TOKEN 未配置，无法调用 AllTick K线接口")

        base_url = getattr(settings, "alltick_stock_base_url", None) or "https://quote.alltick.io/quote-stock-b-api"
        kline_type = self.INTERVAL_TO_KLINE_TYPE.get(query.interval, 1)
        code = _to_alltick_code(query.market, query.symbol)

        # 说明: AllTick 股票 K 线接口目前不支持通过 timestamp 回溯，kline_timestamp_end 必须为 0。
        # 仅用于获取“最新 N 根”K线，适合分钟级调度。
        payload = {
            "trace": "tradecat_markets_service",
            "data": {
                "code": code,
                "kline_type": kline_type,
                "kline_timestamp_end": 0,
                "query_kline_num": min(int(query.limit or 100), 500),
                "adjust_type": 0,
            },
        }

        url = f"{base_url}/kline?token={quote(token)}&query={quote(json.dumps(payload, separators=(',', ':')))}"

        def _do_request() -> dict[str, Any]:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()

        data = await asyncio.to_thread(_do_request)

        # ret != 0 视为失败
        if int(data.get("ret", -1)) != 0:
            raise RuntimeError(f"AllTick kline error: ret={data.get('ret')} msg={data.get('msg')}")

        kline_list = (data.get("data") or {}).get("kline_list") or []

        # 注入 query 元数据，便于 transform_data 标准化
        out: list[dict[str, Any]] = []
        for r in kline_list:
            out.append(
                {
                    **r,
                    "_market": query.market,
                    "_symbol": query.symbol,
                    "_interval": query.interval,
                    "_code": code,
                }
            )
        return out

    def transform_data(self, raw: list[dict[str, Any]]) -> list[Candle]:
        results: list[Candle] = []
        for r in raw:
            market = r.get("_market") or "us_stock"
            symbol = r.get("_symbol") or ""
            interval = r.get("_interval") or "1m"

            code = r.get("_code") or ""
            _, suffix = _split_alltick_code(code)
            exchange = _suffix_to_exchange(suffix)

            # docs: timestamp 为字符串（通常是 Unix 秒）
            ts_raw = r.get("timestamp")
            try:
                ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
            except Exception:
                # 兜底: 尝试 ISO / 其他格式
                ts = datetime.now(tz=timezone.utc)

            open_p = Decimal(str(r.get("open_price", "0")))
            high_p = Decimal(str(r.get("high_price", "0")))
            low_p = Decimal(str(r.get("low_price", "0")))
            close_p = Decimal(str(r.get("close_price", "0")))
            volume = Decimal(str(r.get("volume", "0")))

            # AllTick 返回 turnover，可理解为成交额/换手金额
            amount = r.get("turnover")
            quote_volume = Decimal(str(amount)) if amount is not None else None

            results.append(
                Candle(
                    market=market,
                    asset_type="spot",
                    exchange=exchange,
                    symbol=symbol,
                    interval=interval,
                    timestamp=ts,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=volume,
                    quote_volume=quote_volume,
                    source="alltick",
                )
            )
        return results

