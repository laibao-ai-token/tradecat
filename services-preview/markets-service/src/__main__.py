"""markets-service 入口"""
import argparse
import logging
import os
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)


def _ensure_provider_loaded(provider: str) -> bool:
    """按需导入 provider，避免因未安装的依赖导致整个 CLI 启动失败。"""
    import importlib

    try:
        importlib.import_module(f"providers.{provider}")
        return True
    except Exception as e:
        logger.error("加载 provider 失败: %s (%s)", provider, e, exc_info=True)
        return False


def main():
    parser = argparse.ArgumentParser(description="Markets Data Service")
    parser.add_argument("command", choices=[
        "test", "collect", "pricing",
        # 股票/港股/A股 分钟线调度
        "equity-test", "equity-poll",
        # 加密货币采集命令 (移植自 data-service)
        "crypto-backfill", "crypto-metrics", "crypto-ws", "crypto-scan", "crypto-test",
        "crypto-book-depth", "crypto-order-book"
    ], help="命令")
    parser.add_argument("--provider", default="yfinance", help="数据源")
    parser.add_argument("--symbol", default="AAPL", help="标的代码")
    parser.add_argument("--symbols", default="", help="多个标的 (逗号分隔)")
    parser.add_argument("--market", default="us_stock", help="市场类型")
    parser.add_argument("--interval", default="1d", help="K线周期: 1m/5m/15m/30m/60m/1d...")
    parser.add_argument("--days", type=int, default=30, help="回溯天数")
    parser.add_argument("--sleep", type=int, default=60, help="轮询间隔秒数（分钟线默认 60）")
    parser.add_argument("--limit", type=int, default=5, help="单次拉取条数（AllTick 单次最多 500）")
    parser.add_argument("--klines", action="store_true", help="补齐K线")
    parser.add_argument("--metrics", action="store_true", help="补齐期货指标")
    parser.add_argument("--all", action="store_true", help="补齐全部")
    args = parser.parse_args()

    if args.command == "test":
        from core.registry import ProviderRegistry

        _ensure_provider_loaded(args.provider)
        logger.info("已注册的 Providers: %s", ProviderRegistry.list_providers())
        fetcher_cls = ProviderRegistry.get(args.provider, "candle")
        if fetcher_cls:
            fetcher = fetcher_cls()
            data = fetcher.fetch_sync(market=args.market, symbol=args.symbol, interval=args.interval, limit=args.limit)
            logger.info("获取到 %d 条数据", len(data))
            for d in data[:3]:
                logger.info("  %s", d)
        else:
            logger.error("未找到 Provider: %s", args.provider)

    elif args.command == "equity-test":
        from core.registry import ProviderRegistry

        if not _ensure_provider_loaded(args.provider):
            return
        fetcher_cls = ProviderRegistry.get(args.provider, "candle")
        if not fetcher_cls:
            logger.error("未找到 Provider: %s", args.provider)
            return

        fetcher = fetcher_cls()
        data = fetcher.fetch_sync(market=args.market, symbol=args.symbol, interval=args.interval, limit=args.limit)
        logger.info("equity-test: provider=%s market=%s symbol=%s interval=%s -> %d 条", args.provider, args.market, args.symbol, args.interval, len(data))
        for d in data[:5]:
            logger.info("  %s", d)

    elif args.command == "equity-poll":
        import asyncio
        from datetime import datetime, timezone, timedelta

        from core.registry import ProviderRegistry
        from storage import batch as batch_mgr
        from storage.raw_writer import TimescaleRawWriter

        if not _ensure_provider_loaded(args.provider):
            return

        symbols = [s.strip() for s in (args.symbols.split(",") if args.symbols else [args.symbol]) if s.strip()]
        if not symbols:
            logger.error("未提供 symbols")
            return

        fetcher_cls = ProviderRegistry.get(args.provider, "candle")
        if not fetcher_cls:
            logger.error("未找到 Provider: %s", args.provider)
            return

        fetcher = fetcher_cls()
        writer = TimescaleRawWriter()

        batch_id = batch_mgr.start_batch(source=f"{args.provider}_equity_poll", data_type="equity_1m", market=args.market)
        logger.info(
            "启动 equity-poll: provider=%s market=%s interval=%s symbols=%d sleep=%ss batch_id=%s",
            args.provider, args.market, args.interval, len(symbols), args.sleep, batch_id,
        )

        async def _fetch_one(sym: str):
            try:
                return await fetcher.fetch(market=args.market, symbol=sym, interval=args.interval, limit=args.limit)
            except Exception as e:
                logger.error("拉取失败: %s (%s)", sym, e, exc_info=True)
                return []

        async def _loop():
            while True:
                t0 = datetime.now(tz=timezone.utc)
                results = await asyncio.gather(*[_fetch_one(s) for s in symbols])
                candles = [c for sub in results for c in sub]

                rows = []
                for c in candles:
                    # 仅支持 1m 调度；其他周期也可以写入同一表，但 close_time 需要与 interval 对齐。
                    close_time = c.timestamp + timedelta(minutes=1) if args.interval == "1m" else None
                    rows.append(
                        {
                            "exchange": c.exchange,
                            "symbol": c.symbol,
                            "open_time": c.timestamp,
                            "close_time": close_time,
                            "open": c.open,
                            "high": c.high,
                            "low": c.low,
                            "close": c.close,
                            "volume": c.volume,
                            "amount": c.quote_volume,
                            "source": c.source or args.provider,
                            "source_event_time": t0,
                        }
                    )

                if rows:
                    try:
                        n = writer.upsert_equity_1m(args.market, rows, ingest_batch_id=batch_id, source=args.provider)
                        logger.info("写入 %d 条 1m K线 (rows=%d, candles=%d)", n, len(rows), len(candles))
                    except Exception as e:
                        logger.error("写入失败: %s", e, exc_info=True)
                else:
                    logger.info("无数据 (symbols=%d)", len(symbols))

                # 固定间隔轮询（简单稳）
                await asyncio.sleep(max(1, int(args.sleep)))

        asyncio.run(_loop())

    elif args.command == "pricing":
        from datetime import date, timedelta

        from providers.quantlib import OptionPricer

        pricer = OptionPricer(risk_free_rate=0.05)
        greeks = pricer.price_european(
            spot=100, strike=100,
            expiry=date.today() + timedelta(days=30),
            volatility=0.2, option_type="call"
        )
        logger.info("期权定价 (ATM Call, 30天到期, IV=20%%):")
        logger.info("  价格: %.4f", greeks.price)
        logger.info("  Delta: %.4f, Gamma: %.4f", greeks.delta, greeks.gamma)
        logger.info("  Theta: %.4f, Vega: %.4f", greeks.theta, greeks.vega)

    elif args.command == "collect":
        # Cryptofeed WS 采集入口（按 env 分组解析，写 raw.crypto_kline_1m）
        from datetime import datetime, timezone

        from cryptofeed.defines import CANDLES

        from providers.cryptofeed.stream import (
            CandleEvent,
            CryptoFeedStream,
            _from_binance_perp,
            _to_binance_perp,
            load_symbols_from_env,
        )
        from storage import batch as batch_mgr
        from storage.raw_writer import TimescaleRawWriter

        symbols = load_symbols_from_env()
        if not symbols:
            # auto/all 模式或未配置时，退回默认 main6
            symbols = [_to_binance_perp(s) for s in [
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"
            ]]
            logger.warning("未提供分组或为 auto/all，使用默认 main6: %s", symbols)
        else:
            logger.info("使用 env 分组解析的交易对: %s", symbols)

        writer = TimescaleRawWriter()
        batch_id = batch_mgr.start_batch(source="binance_ws", data_type="kline", market="crypto")
        logger.info("批次 ID: %s", batch_id)

        def handle_candle(event: CandleEvent):
            row = {
                "exchange": event.exchange or "binance",
                "symbol": _from_binance_perp(event.symbol),
                "bucket_ts": datetime.fromtimestamp(event.timestamp, tz=timezone.utc),
                "open": event.open,
                "high": event.high,
                "low": event.low,
                "close": event.close,
                "volume": event.volume,
                "is_closed": event.closed,
                "source": "binance_ws",
            }
            writer.upsert_kline_1m([row], ingest_batch_id=batch_id)

        stream = CryptoFeedStream(exchange="binance")
        stream.on_candle(handle_candle)
        stream.subscribe(symbols, channels=[CANDLES])
        logger.info("启动 Cryptofeed WS 采集并写入 raw.crypto_kline_1m ...")
        stream.run()

    # ==================== 加密货币采集命令 (移植自 data-service) ====================

    elif args.command == "crypto-test":
        # 测试配置
        from crypto.config import settings
        logger.info("=== Crypto 模块配置 ===")
        logger.info("  write_mode: %s", settings.write_mode)
        logger.info("  database_url: %s", settings.database_url[:50] + "...")
        logger.info("  db_schema: %s", settings.db_schema)
        logger.info("  raw_schema: %s", settings.raw_schema)
        logger.info("  is_raw_mode: %s", settings.is_raw_mode)

    elif args.command == "crypto-scan":
        # 仅扫描缺口
        from datetime import date, timedelta

        from crypto.adapters.ccxt import load_symbols
        from crypto.adapters.timescale import TimescaleAdapter
        from crypto.collectors.backfill import GapScanner
        from crypto.config import settings

        symbols = args.symbols.split(",") if args.symbols else load_symbols(settings.ccxt_exchange)
        ts = TimescaleAdapter()
        scanner = GapScanner(ts)

        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=args.days)

        logger.info("扫描缺口: %d 个符号, %s ~ %s (模式: %s)", len(symbols), start, end, settings.write_mode)

        if args.klines or args.all or not args.metrics:
            gaps = scanner.scan_klines(symbols, start, end)
            total = sum(len(g) for g in gaps.values())
            logger.info("K线缺口: %d 个符号, %d 个缺口", len(gaps), total)
            for sym, sym_gaps in list(gaps.items())[:5]:
                logger.info("  %s: %s", sym, [str(g.date) for g in sym_gaps[:3]])

        if args.metrics or args.all:
            gaps = scanner.scan_metrics(symbols, start, end)
            total = sum(len(g) for g in gaps.values())
            logger.info("Metrics缺口: %d 个符号, %d 个缺口", len(gaps), total)

        ts.close()

    elif args.command == "crypto-backfill":
        # K线 + 期货指标补齐
        from crypto.collectors.backfill import DataBackfiller
        from crypto.config import settings

        symbols = args.symbols.split(",") if args.symbols else None
        lookback = args.days or int(os.getenv("BACKFILL_DAYS", "30"))

        logger.info("开始补齐 (模式: %s, 回溯: %d 天)", settings.write_mode, lookback)

        bf = DataBackfiller(lookback_days=lookback)
        try:
            if args.all:
                result = bf.run_all(symbols)
            elif args.klines:
                result = {"klines": bf.run_klines(symbols)}
            elif args.metrics:
                result = {"metrics": bf.run_metrics(symbols)}
            else:
                result = bf.run_all(symbols)
            logger.info("补齐结果: %s", result)
        finally:
            bf.close()

    elif args.command == "crypto-metrics":
        # 期货指标采集 (单次)
        from crypto.collectors.metrics import MetricsCollector
        from crypto.config import settings

        symbols = args.symbols.split(",") if args.symbols else None
        logger.info("采集期货指标 (模式: %s)", settings.write_mode)

        c = MetricsCollector()
        try:
            c.run_once(symbols)
        finally:
            c.close()

    elif args.command == "crypto-ws":
        # WebSocket 实时采集
        from crypto.collectors.ws import WSCollector
        from crypto.config import settings

        logger.info("启动 WebSocket 采集 (模式: %s)", settings.write_mode)
        WSCollector().run()

    elif args.command == "crypto-book-depth":
        # WebSocket 订单簿采集 (百分比聚合)
        from crypto.collectors.book_depth import BookDepthCollector
        from crypto.config import settings

        logger.info("启动 BookDepth WebSocket 采集 (模式: %s)", settings.write_mode)
        BookDepthCollector().run()

    elif args.command == "crypto-order-book":
        # WebSocket 原始逐档盘口采集
        from crypto.collectors.order_book import OrderBookCollector
        from crypto.config import settings

        logger.info("启动 OrderBook WebSocket 采集 (模式: %s)", settings.write_mode)
        OrderBookCollector().run()


if __name__ == "__main__":
    main()
