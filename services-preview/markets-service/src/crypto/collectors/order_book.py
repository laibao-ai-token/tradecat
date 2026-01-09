"""WebSocket 订单簿采集器 - 混合快照存储

存储结构: 1行=1快照
- 关键档位列存 (bid1/ask1, 深度统计)
- 完整盘口 JSONB (bids/asks 数组)

配置项 (config/.env):
    ORDER_BOOK_INTERVAL: 采样间隔秒数，默认 10
    ORDER_BOOK_DEPTH: 每侧档位数，默认 1000
    ORDER_BOOK_RETENTION_DAYS: 保留天数，默认 30
    ORDER_BOOK_SYMBOLS: 可选，逗号分隔，不设则用 SYMBOLS_GROUPS
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..adapters.ccxt import load_symbols, normalize_symbol
from ..adapters.cryptofeed import preload_symbols
from ..adapters.metrics import metrics
from ..adapters.timescale import TimescaleAdapter
from ..config import settings

_libs_path = Path(__file__).parent.parent.parent.parent.parent.parent / "libs" / "common"
if str(_libs_path) not in sys.path:
    sys.path.insert(0, str(_libs_path))

logger = logging.getLogger("ws.order_book")


def _get_config() -> dict:
    """读取配置"""
    return {
        "interval": int(os.getenv("ORDER_BOOK_INTERVAL", "10")),
        "depth": int(os.getenv("ORDER_BOOK_DEPTH", "1000")),
        "retention_days": int(os.getenv("ORDER_BOOK_RETENTION_DAYS", "30")),
        "symbols": os.getenv("ORDER_BOOK_SYMBOLS", ""),
    }


class OrderBookCollector:
    """订单簿采集器 - 混合快照存储

    数据流: L2_BOOK → 计算指标 → 1行快照 → raw.crypto_order_book

    存储优化:
    - 1 快照 = 1 行 (vs 旧方案 2000 行)
    - 预计算关键指标 (mid_price, spread, 深度)
    - JSONB 存储完整盘口
    """

    MAX_BUFFER = 1000  # 快照级别，1000 个足够

    def __init__(self):
        self._cfg = _get_config()
        self._ts = TimescaleAdapter()
        self._symbols = self._load_symbols()
        self._buffer: List[dict] = []
        self._buffer_lock = asyncio.Lock()
        self._last_snapshot: Dict[str, float] = {}
        self._flush_task: Optional[asyncio.Task] = None

    def _load_symbols(self) -> Dict[str, str]:
        """加载交易对映射"""
        from symbols import get_configured_symbols

        if self._cfg["symbols"]:
            raw = [s.strip().upper() for s in self._cfg["symbols"].split(",") if s.strip()]
            raw = [s if s.endswith("USDT") else f"{s}USDT" for s in raw]
            logger.info("使用 ORDER_BOOK_SYMBOLS: %d 个币种", len(raw))
        else:
            configured = get_configured_symbols()
            if configured:
                raw = [s if s.endswith("USDT") else f"{s}USDT" for s in configured]
                logger.info("使用 SYMBOLS_GROUPS: %d 个币种", len(raw))
            else:
                raw = load_symbols(settings.ccxt_exchange)
                if not raw:
                    raise RuntimeError("未加载到交易对")
                logger.info("使用交易所全部: %d 个币种", len(raw))

        mapping = {}
        for s in raw:
            n = normalize_symbol(s)
            if n:
                mapping[f"{n[:-4]}-USDT-PERP"] = n
        preload_symbols(list(mapping.values()))
        return mapping

    def _compute_depth_stats(
        self, mid_price: float, bids: List[tuple], asks: List[tuple]
    ) -> Dict[str, Any]:
        """计算深度统计指标

        Args:
            mid_price: 中间价
            bids: [(price, size), ...] 价格降序
            asks: [(price, size), ...] 价格升序

        Returns:
            深度统计字典
        """
        stats = {
            "bid_depth_1pct": Decimal(0),
            "ask_depth_1pct": Decimal(0),
            "bid_depth_5pct": Decimal(0),
            "ask_depth_5pct": Decimal(0),
            "bid_notional_1pct": Decimal(0),
            "ask_notional_1pct": Decimal(0),
            "bid_notional_5pct": Decimal(0),
            "ask_notional_5pct": Decimal(0),
        }

        if mid_price <= 0:
            return stats

        thresh_1pct = mid_price * 0.01
        thresh_5pct = mid_price * 0.05

        # 买侧: 价格 >= mid - threshold
        for price, size in bids:
            diff = mid_price - price
            p_dec = Decimal(str(price))
            s_dec = Decimal(str(size))
            notional = p_dec * s_dec

            if diff <= thresh_1pct:
                stats["bid_depth_1pct"] += s_dec
                stats["bid_notional_1pct"] += notional
            if diff <= thresh_5pct:
                stats["bid_depth_5pct"] += s_dec
                stats["bid_notional_5pct"] += notional
            else:
                break  # 已排序，后面都超出

        # 卖侧: 价格 <= mid + threshold
        for price, size in asks:
            diff = price - mid_price
            p_dec = Decimal(str(price))
            s_dec = Decimal(str(size))
            notional = p_dec * s_dec

            if diff <= thresh_1pct:
                stats["ask_depth_1pct"] += s_dec
                stats["ask_notional_1pct"] += notional
            if diff <= thresh_5pct:
                stats["ask_depth_5pct"] += s_dec
                stats["ask_notional_5pct"] += notional
            else:
                break

        return stats

    def _build_snapshot(
        self, sym: str, ts: datetime, bids_dict: dict, asks_dict: dict
    ) -> Optional[dict]:
        """构建快照行

        Args:
            sym: 标准化交易对
            ts: 时间戳
            bids_dict: {price: size, ...}
            asks_dict: {price: size, ...}

        Returns:
            快照字典或 None
        """
        if not bids_dict or not asks_dict:
            return None

        depth = self._cfg["depth"]

        # 排序: bids 降序, asks 升序
        bid_prices = sorted(bids_dict.keys(), reverse=True)[:depth]
        ask_prices = sorted(asks_dict.keys())[:depth]

        if not bid_prices or not ask_prices:
            return None

        # 最优档位
        bid1_price = float(bid_prices[0])
        bid1_size = float(bids_dict[bid_prices[0]])
        ask1_price = float(ask_prices[0])
        ask1_size = float(asks_dict[ask_prices[0]])

        # 中间价和价差
        mid_price = (bid1_price + ask1_price) / 2
        spread = ask1_price - bid1_price
        spread_bps = (spread / mid_price * 10000) if mid_price > 0 else 0

        # 构建盘口数组
        bids = [(float(p), float(bids_dict[p])) for p in bid_prices]
        asks = [(float(p), float(asks_dict[p])) for p in ask_prices]

        # 深度统计
        stats = self._compute_depth_stats(mid_price, bids, asks)

        # 买卖失衡 (基于 1% 深度)
        total_1pct = stats["bid_depth_1pct"] + stats["ask_depth_1pct"]
        imbalance = float(
            (stats["bid_depth_1pct"] - stats["ask_depth_1pct"]) / total_1pct
        ) if total_1pct > 0 else 0

        return {
            "timestamp": ts,
            "exchange": settings.db_exchange,
            "symbol": sym,
            "depth": len(bids),
            "mid_price": Decimal(str(mid_price)),
            "spread": Decimal(str(spread)),
            "spread_bps": Decimal(str(round(spread_bps, 4))),
            "bid1_price": Decimal(str(bid1_price)),
            "bid1_size": Decimal(str(bid1_size)),
            "ask1_price": Decimal(str(ask1_price)),
            "ask1_size": Decimal(str(ask1_size)),
            "bid_depth_1pct": stats["bid_depth_1pct"],
            "ask_depth_1pct": stats["ask_depth_1pct"],
            "bid_depth_5pct": stats["bid_depth_5pct"],
            "ask_depth_5pct": stats["ask_depth_5pct"],
            "bid_notional_1pct": stats["bid_notional_1pct"],
            "ask_notional_1pct": stats["ask_notional_1pct"],
            "bid_notional_5pct": stats["bid_notional_5pct"],
            "ask_notional_5pct": stats["ask_notional_5pct"],
            "imbalance": Decimal(str(round(imbalance, 6))),
            "bids": json.dumps(bids),
            "asks": json.dumps(asks),
        }

    async def _on_book(self, book, receipt_ts: float) -> None:
        """订单簿回调"""
        sym = self._symbols.get(book.symbol)
        if not sym:
            return

        if not book.book or not book.book.bids or not book.book.asks:
            return

        # 采样间隔控制
        now = time.time()
        last = self._last_snapshot.get(sym, 0)
        if now - last < self._cfg["interval"]:
            return
        self._last_snapshot[sym] = now

        ts = datetime.fromtimestamp(book.timestamp, tz=timezone.utc)
        bids_dict = book.book.bids.to_dict()
        asks_dict = book.book.asks.to_dict()

        row = self._build_snapshot(sym, ts, bids_dict, asks_dict)
        if not row:
            return

        async with self._buffer_lock:
            self._buffer.append(row)
            if len(self._buffer) >= self.MAX_BUFFER:
                await self._flush()
            elif self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(self._cfg["interval"])
        async with self._buffer_lock:
            await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return

        rows = self._buffer.copy()
        self._buffer.clear()

        try:
            n = await asyncio.to_thread(self._write_rows, rows)
            metrics.inc("order_book_written", n)
            logger.info("写入 %d 条订单簿快照", n)
        except Exception as e:
            logger.error("写入失败: %s", e)

    def _write_rows(self, rows: List[dict]) -> int:
        """写入数据库"""
        if not rows:
            return 0

        from psycopg import sql

        cols = [
            "timestamp", "exchange", "symbol", "depth",
            "mid_price", "spread", "spread_bps",
            "bid1_price", "bid1_size", "ask1_price", "ask1_size",
            "bid_depth_1pct", "ask_depth_1pct", "bid_depth_5pct", "ask_depth_5pct",
            "bid_notional_1pct", "ask_notional_1pct", "bid_notional_5pct", "ask_notional_5pct",
            "imbalance", "bids", "asks",
        ]
        update_cols = [c for c in cols if c not in ("timestamp", "symbol")]
        temp = f"temp_ob_{int(time.time() * 1000)}"

        with self._ts.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL(
                    "CREATE TEMP TABLE {t} (LIKE raw.crypto_order_book INCLUDING DEFAULTS) ON COMMIT DROP"
                ).format(t=sql.Identifier(temp)))

                with cur.copy(sql.SQL("COPY {t} ({c}) FROM STDIN").format(
                    t=sql.Identifier(temp),
                    c=sql.SQL(", ").join(map(sql.Identifier, cols))
                )) as copy:
                    for r in rows:
                        copy.write_row(tuple(r[c] for c in cols))

                cur.execute(sql.SQL("""
                    INSERT INTO raw.crypto_order_book ({c})
                    SELECT {c} FROM {t}
                    ON CONFLICT (symbol, timestamp) DO UPDATE SET
                        {updates}
                """).format(
                    c=sql.SQL(", ").join(map(sql.Identifier, cols)),
                    t=sql.Identifier(temp),
                    updates=sql.SQL(", ").join(
                        sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
                        for c in update_cols
                    ),
                ))
                n = cur.rowcount
            conn.commit()
        return n if n > 0 else len(rows)

    def run(self) -> None:
        """运行采集器"""
        from cryptofeed import FeedHandler
        from cryptofeed.defines import L2_BOOK
        from cryptofeed.exchanges import BinanceFutures

        cfg = self._cfg
        logger.info("配置: interval=%ds, depth=%d, symbols=%d",
                    cfg["interval"], cfg["depth"], len(self._symbols))

        log_file = settings.log_dir / "cryptofeed_orderbook.log"
        handler = FeedHandler(config={"uvloop": False, "log": {"filename": str(log_file), "level": "INFO"}})

        kw = {
            "symbols": list(self._symbols.keys()),
            "channels": [L2_BOOK],
            "callbacks": {L2_BOOK: self._on_book},
            "timeout": 60,
        }
        if settings.http_proxy:
            kw["http_proxy"] = settings.http_proxy

        handler.add_feed(BinanceFutures(**kw))
        logger.info("启动 OrderBook WSS (混合快照模式)")

        try:
            handler.run()
        finally:
            asyncio.run(self._final_flush())
            self._ts.close()

    async def _final_flush(self) -> None:
        async with self._buffer_lock:
            await self._flush()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    OrderBookCollector().run()


if __name__ == "__main__":
    main()
