"""AKShare K线数据获取器"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from config import settings
from core.fetcher import BaseFetcher
from core.registry import register_fetcher
from models.candle import Candle, CandleQuery


@register_fetcher("akshare", "candle")
class AKShareCandleFetcher(BaseFetcher[CandleQuery, Candle]):
    """AKShare K线获取器 - A股/港股/期货/债券"""

    _MINUTE_PERIOD_MAP = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "60m": "60",
    }

    def __init__(self, market: str | None = None):
        # 兼容旧用法：允许构造时指定固定 market；若不指定则以 query.market 为准
        self.market = market
        # akshare 通过 requests 使用代理
        if settings.http_proxy:
            os.environ.setdefault("HTTP_PROXY", settings.http_proxy)
            os.environ.setdefault("HTTPS_PROXY", settings.http_proxy)

    @staticmethod
    def _normalize_cn_symbol(symbol: str) -> str:
        sym = str(symbol).strip()
        # 兼容 AllTick: 000001.SZ / 600519.SH
        if "." in sym:
            sym = sym.split(".", 1)[0]
        # 兼容部分写法: SZ000001 / SH600519
        if len(sym) >= 8 and sym[:2].upper() in {"SZ", "SH"} and sym[2:].isdigit():
            sym = sym[2:]
        return sym

    @staticmethod
    def _normalize_hk_symbol(symbol: str) -> str:
        sym = str(symbol).strip()
        # 兼容 AllTick: 700.HK / 00700.HK
        if "." in sym:
            sym = sym.split(".", 1)[0]
        if sym.isdigit() and len(sym) < 5:
            sym = sym.zfill(5)
        return sym

    def transform_query(self, params: dict[str, Any]) -> CandleQuery:
        return CandleQuery(**params)

    async def extract(self, query: CandleQuery) -> list[dict[str, Any]]:
        import akshare as ak

        market = self.market or query.market
        sym = query.symbol

        # 对分钟线接口，akshare 通常接受 "YYYY-MM-DD HH:MM:SS"
        start_min = query.start.strftime("%Y-%m-%d %H:%M:%S") if query.start else "1970-01-01 00:00:00"
        end_min = query.end.strftime("%Y-%m-%d %H:%M:%S") if query.end else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        start_day = query.start.strftime("%Y%m%d") if query.start else "20200101"
        end_day = query.end.strftime("%Y%m%d") if query.end else datetime.now().strftime("%Y%m%d")

        is_minute = query.interval.endswith("m") and query.interval != "1M"
        minute_period = self._MINUTE_PERIOD_MAP.get(query.interval, "1")

        # 根据市场类型调用不同接口
        if market == "cn_stock":
            sym = self._normalize_cn_symbol(query.symbol)
            if is_minute:
                # 分钟线：优先 Eastmoney（历史更完整）；若网络不稳定则退回 Sina（更稳，但字段略不同）
                def _call_em():
                    try:
                        return ak.stock_zh_a_hist_min_em(
                            symbol=sym,
                            start_date=start_min,
                            end_date=end_min,
                            period=minute_period,
                            adjust="",
                        )
                    except TypeError:
                        # 不同版本的参数可能略有差异
                        return ak.stock_zh_a_hist_min_em(symbol=sym, period=minute_period, adjust="")

                def _call_sina():
                    # Sina 需要带交易所前缀: sh600519 / sz000001
                    prefix = "sh" if sym and sym[0] in {"5", "6", "9"} else "sz"
                    return ak.stock_zh_a_minute(symbol=f"{prefix}{sym}", period=minute_period, adjust="")

                try:
                    df = await asyncio.to_thread(_call_em)
                except Exception:
                    df = await asyncio.to_thread(_call_sina)
            else:
                df = await asyncio.to_thread(
                    ak.stock_zh_a_hist,
                    symbol=sym,
                    period="daily",
                    start_date=start_day,
                    end_date=end_day,
                    adjust="",
                )
        elif market == "hk_stock":
            sym = self._normalize_hk_symbol(query.symbol)
            if is_minute:
                def _call():
                    try:
                        return ak.stock_hk_hist_min_em(
                            symbol=sym,
                            start_date=start_min,
                            end_date=end_min,
                            period=minute_period,
                            adjust="",
                        )
                    except TypeError:
                        return ak.stock_hk_hist_min_em(symbol=sym, period=minute_period, adjust="")

                df = await asyncio.to_thread(_call)
            else:
                df = await asyncio.to_thread(
                    ak.stock_hk_hist,
                    symbol=sym,
                    period="daily",
                    start_date=start_day,
                    end_date=end_day,
                    adjust="",
                )
        elif market == "futures":
            df = await asyncio.to_thread(
                ak.futures_zh_daily_sina,
                symbol=query.symbol
            )
        else:
            return []

        if df is None or df.empty:
            return []

        # 分钟线/轮询场景下，避免每次拉全量导致写入压力过大
        try:
            if query.limit and len(df) > query.limit:
                df = df.tail(int(query.limit))
        except Exception:
            pass

        rows = df.to_dict("records")
        for r in rows:
            r["_market"] = market
            r["_symbol"] = sym
            r["_interval"] = "1d" if not is_minute else query.interval
        return rows

    def transform_data(self, raw: list[dict[str, Any]]) -> list[Candle]:
        from zoneinfo import ZoneInfo

        results = []
        for r in raw:
            # A股字段映射
            ts = r.get("时间") or r.get("日期") or r.get("date") or r.get("datetime") or r.get("day")
            if isinstance(ts, date) and not isinstance(ts, datetime):
                # 日线接口可能直接返回 date 对象
                ts = datetime.combine(ts, datetime.min.time())
            if isinstance(ts, str):
                # 兼容分钟/日线格式
                try:
                    ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    ts = datetime.strptime(ts, "%Y-%m-%d")

            market = r.get("_market") or "cn_stock"
            interval = r.get("_interval") or "1d"
            symbol = str(r.get("股票代码", r.get("symbol", ""))) or str(r.get("_symbol", ""))

            exchange = "sse"
            if market == "cn_stock":
                # 优先使用用户输入的后缀判断交易所；否则按 A 股常见规则粗略推断
                user_symbol = str(r.get("_symbol") or "")
                if user_symbol.upper().endswith(".SZ") or user_symbol.upper().startswith("SZ"):
                    exchange = "szse"
                elif user_symbol.upper().endswith(".SH") or user_symbol.upper().startswith("SH"):
                    exchange = "sse"
                elif symbol and symbol[0] not in {"5", "6", "9"}:
                    exchange = "szse"
            elif market == "hk_stock":
                exchange = "hkex"

            # 将本地交易所时间转 UTC，便于统一存储
            if ts.tzinfo is None:
                if market == "hk_stock":
                    ts = ts.replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
                else:
                    ts = ts.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            ts_utc = ts.astimezone(timezone.utc)

            results.append(Candle(
                market=market,
                asset_type="spot",
                exchange=exchange,
                symbol=symbol,
                interval=interval,
                timestamp=ts_utc,
                open=Decimal(str(r.get("开盘", r.get("open", 0)))),
                high=Decimal(str(r.get("最高", r.get("high", 0)))),
                low=Decimal(str(r.get("最低", r.get("low", 0)))),
                close=Decimal(str(r.get("收盘", r.get("close", 0)))),
                volume=Decimal(str(r.get("成交量", r.get("volume", 0)))),
                quote_volume=Decimal(str(r.get("成交额", 0))) if r.get("成交额") else None,
                source="akshare",
            ))
        return results
