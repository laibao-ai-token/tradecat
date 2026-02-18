"""WebSocket K线采集器 - 自动重连 + 缺口巡检 + 批量写入

优化策略：
- cryptofeed 每分钟闭合时，~300 个币种在 1-2 秒内推送
- 使用时间窗口批量写入：收集 3 秒内的数据后一次性写入
- 避免 300 次单独 DB 操作 → 1 次批量操作

Note:
This module historically used Binance Futures WebSocket via cryptofeed.
In some environments Binance endpoints are unreachable; we support a minimal
fallback provider that polls Gate spot 1m candles without API keys.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.ccxt import load_symbols, normalize_symbol
from adapters.cryptofeed import BinanceWSAdapter, CandleEvent, preload_symbols
from adapters.gate_spot import fetch_spot_candles, to_candle_row
from adapters.metrics import metrics
from adapters.timescale import TimescaleAdapter
from config import settings

logger = logging.getLogger("ws.collector")

# Optional: configured symbol groups live in libs/common.
_libs_path = str(Path(__file__).resolve().parents[5] / "libs")
if _libs_path not in sys.path:
    sys.path.insert(0, _libs_path)
try:
    from common.symbols import get_configured_symbols
except Exception:  # pragma: no cover
    get_configured_symbols = None


class WSCollector:
    """WebSocket 1m K线采集器 - 时间窗口批量写入

    推送模式：每分钟整点，~300 个币种在 1-2 秒内推送
    写入策略：收集 FLUSH_WINDOW 秒内的数据后批量写入
    """

    FLUSH_WINDOW = 3.0   # 时间窗口：3 秒（覆盖网络延迟）
    MAX_BUFFER = 1000    # 最大缓冲：> 606 币种，确保一次性写入

    def __init__(self):
        self._ts = TimescaleAdapter()
        # For polling providers (gate_spot_poll), we intentionally keep the symbol list small
        # (configured symbols only) to avoid per-symbol HTTP fanout.
        self._symbols = self._load_symbols()
        self._gap_stop = threading.Event()
        self._gap_thread: Optional[threading.Thread] = None

        # 批量写入缓冲
        self._buffer: List[dict] = []
        self._buffer_lock = asyncio.Lock()
        self._last_candle_time: float = 0  # 最后一条 K 线到达时间
        self._flush_task: Optional[asyncio.Task] = None

    def _load_symbols(self) -> Dict[str, str]:
        # Gate spot polling: only configured symbols (or fallback main4) are supported.
        if settings.candle_provider == "gate_spot_poll":
            raw = (get_configured_symbols() if get_configured_symbols else None) or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
            mapping: Dict[str, str] = {}
            for s in raw:
                n = normalize_symbol(s)
                if n:
                    mapping[n] = n
            logger.info("gate spot polling symbols=%d", len(mapping))
            return mapping

        raw = load_symbols(settings.ccxt_exchange)
        if not raw:
            raise RuntimeError("未加载到交易对")
        mapping = {}
        for s in raw:
            n = normalize_symbol(s)
            if n:
                mapping[f"{n[:-4]}-USDT-PERP"] = n
        preload_symbols(list(mapping.values()))
        logger.info("加载 %d 个交易对", len(mapping))
        return mapping

    async def _on_candle(self, e: CandleEvent) -> None:
        """K 线回调 - 缓冲后批量写入"""
        sym = self._symbols.get(e.symbol)
        if not sym:
            return

        row = {
            "exchange": settings.db_exchange, "symbol": sym,
            "bucket_ts": datetime.fromtimestamp(e.timestamp, tz=timezone.utc),
            "open": e.open, "high": e.high, "low": e.low, "close": e.close, "volume": e.volume,
            "quote_volume": float(e.quote_volume) if e.quote_volume else None,
            "trade_count": e.trade_count or 0, "is_closed": True, "source": settings.ws_source,
            "taker_buy_volume": float(e.taker_buy_volume) if e.taker_buy_volume else None,
            "taker_buy_quote_volume": float(e.taker_buy_quote_volume) if e.taker_buy_quote_volume else None,
        }

        async with self._buffer_lock:
            self._buffer.append(row)
            self._last_candle_time = time.monotonic()

            # 缓冲区满，立即刷新
            if len(self._buffer) >= self.MAX_BUFFER:
                await self._flush()
            # 启动延迟刷新任务
            elif self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        """延迟刷新：等待时间窗口后批量写入"""
        await asyncio.sleep(self.FLUSH_WINDOW)
        async with self._buffer_lock:
            # 检查是否有新数据到达（窗口内）
            if time.monotonic() - self._last_candle_time >= self.FLUSH_WINDOW:
                await self._flush()

    async def _flush(self) -> None:
        """刷新缓冲区到数据库"""
        if not self._buffer:
            return

        rows = self._buffer.copy()
        self._buffer.clear()

        try:
            # 异步执行同步写入
            n = await asyncio.to_thread(self._ts.upsert_candles, "1m", rows)
            metrics.inc("rows_written", n)
            logger.debug("批量写入 %d 条 K 线", n)
        except Exception as e:
            logger.error("批量写入失败: %s", e)

    def run(self) -> None:
        """运行采集器"""
        if settings.candle_provider == "gate_spot_poll":
            self._run_gate_spot_poll()
            return

        # 启动时补齐 - 后台线程，不阻塞 WebSocket
        if self._symbols:
            threading.Thread(target=self._run_backfill, args=(1,), daemon=True).start()

        # 启动周期巡检线程
        if settings.ws_gap_interval > 0:
            self._gap_stop.clear()
            self._gap_thread = threading.Thread(target=self._gap_loop, daemon=True)
            self._gap_thread.start()

        # 启动 WebSocket
        ws = BinanceWSAdapter(http_proxy=settings.http_proxy)
        ws.subscribe(list(self._symbols.keys()), self._on_candle_sync)

        try:
            ws.run()
        finally:
            # 退出前刷新
            asyncio.run(self._final_flush())
            self._gap_stop.set()
            self._ts.close()

    def _run_gate_spot_poll(self) -> None:
        """Gate spot 1m candle poller (no API key)."""
        poll_interval = float(os.getenv("GATE_SPOT_POLL_INTERVAL", "10"))
        timeout_s = float(os.getenv("GATE_SPOT_TIMEOUT", "10"))
        workers = int(os.getenv("GATE_SPOT_WORKERS", "4"))

        # Store under a separate exchange label to avoid mixing with futures data.
        db_exchange = os.getenv("GATE_SPOT_DB_EXCHANGE", "gate_spot")

        logger.info("启动 Gate spot polling: symbols=%d interval=%.1fs", len(self._symbols), poll_interval)

        while True:
            rows: List[dict] = []

            def _fetch_one(sym: str) -> List[dict]:
                base = sym[:-4]  # BTCUSDT -> BTC
                pair = f"{base}_USDT"
                candles = fetch_spot_candles(pair, interval="1m", limit=2, timeout_s=timeout_s)
                out: List[dict] = []
                for c in candles:
                    if c.is_closed:
                        out.append(to_candle_row(exchange=db_exchange, symbol=sym, candle=c, source="gate_spot"))
                return out

            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futs = {pool.submit(_fetch_one, sym): sym for sym in list(self._symbols.values())}
                for fut in as_completed(futs):
                    try:
                        rows.extend(fut.result())
                    except Exception as e:
                        logger.debug("gate spot fetch failed %s: %s", futs[fut], e)
            if rows:
                try:
                    n = self._ts.upsert_candles("1m", rows)
                    metrics.inc("rows_written", n)
                    logger.debug("gate spot upsert %d rows", n)
                except Exception as e:
                    logger.error("gate spot upsert failed: %s", e)
            time.sleep(max(1.0, poll_interval))

    def _on_candle_sync(self, e: CandleEvent) -> None:
        """同步回调包装器（cryptofeed 可能用同步回调）"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._on_candle(e))
            else:
                asyncio.run(self._on_candle(e))
        except RuntimeError:
            # 没有事件循环，创建新的
            asyncio.run(self._on_candle(e))

    async def _final_flush(self) -> None:
        """最终刷新"""
        async with self._buffer_lock:
            await self._flush()

    def _gap_loop(self) -> None:
        """智能缺口巡检 - 增量检查 + 自适应回溯"""
        lookback_days = 2  # 固定回溯 2 天 (今天+昨天+前天)
        unfillable: Set[tuple] = set()  # 缓存无法补齐的缺口 (symbol, date)

        while not self._gap_stop.wait(settings.ws_gap_interval):
            try:
                has_gaps, lookback_days = self._smart_backfill(lookback_days, unfillable)
                # 无缺口时缩小回溯，有缺口时扩大（最大 7 天）
                if not has_gaps:
                    lookback_days = max(1, lookback_days - 1)
                else:
                    lookback_days = min(7, lookback_days + 1)
            except Exception as e:
                logger.error("周期缺口检查失败: %s", e)

    def _smart_backfill(self, lookback_days: int, unfillable: Set[tuple]) -> tuple:
        """智能补齐 - 返回 (是否有缺口, 建议回溯天数)"""
        from collectors.backfill import GapScanner, RestBackfiller, ZipBackfiller

        t0 = time.perf_counter()
        symbols = list(self._symbols.values())
        end = date.today()
        start = end - timedelta(days=lookback_days)

        scanner = GapScanner(self._ts)
        gaps = scanner.scan_klines(symbols, start, end, "1m", 0.95)

        if not gaps:
            return False, lookback_days

        # 过滤已知无法补齐的缺口
        filtered = {}
        for sym, sym_gaps in gaps.items():
            new_gaps = [g for g in sym_gaps if (sym, g.date) not in unfillable]
            if new_gaps:
                filtered[sym] = new_gaps

        if not filtered:
            logger.debug("所有缺口已知无法补齐，跳过")
            return False, lookback_days

        total_gaps = sum(len(g) for g in filtered.values())
        metrics.inc("gaps_found", total_gaps)
        logger.info("发现 %d 个符号 %d 个缺口，开始补齐 (回溯%d天)", len(filtered), total_gaps, lookback_days)

        zip_bf = ZipBackfiller(self._ts, workers=2)
        zip_bf.cleanup_old_files()
        filled = zip_bf.fill_kline_gaps(filtered, "1m")

        # 复检 + REST 补齐
        remaining = scanner.scan_klines(list(filtered.keys()), start, end, "1m", 0.95)
        if remaining:
            rest_bf = RestBackfiller(self._ts, workers=2)
            filled += rest_bf.fill_gaps(remaining, "1m")

            # 再次复检，记录无法补齐的
            still_missing = scanner.scan_klines(list(remaining.keys()), start, end, "1m", 0.95)
            if still_missing:
                for sym, sym_gaps in still_missing.items():
                    for g in sym_gaps:
                        unfillable.add((sym, g.date))
                logger.debug("记录 %d 个无法补齐的缺口", sum(len(g) for g in still_missing.values()))

        metrics.inc("gaps_filled", filled)
        logger.info("缺口补齐完成: 填充 %d 条, 耗时 %.1fs", filled, time.perf_counter() - t0)
        return True, lookback_days

    def _run_backfill(self, lookback_days: int = 1, lookback_hours: int = 0) -> None:
        """运行缺口补齐 (启动时调用)"""
        self._smart_backfill(lookback_days or 1, set())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    WSCollector().run()


if __name__ == "__main__":
    main()
