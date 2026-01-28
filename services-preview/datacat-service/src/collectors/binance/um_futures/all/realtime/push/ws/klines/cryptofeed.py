"""WebSocket K线采集器 - 自动重连 + 缺口巡检 + 批量写入

优化策略：
- cryptofeed 每分钟闭合时，~300 个币种在 1-2 秒内推送
- 使用时间窗口批量写入：收集 3 秒内的数据后一次性写入
- 避免 300 次单独 DB 操作 → 1 次批量操作
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set

import requests
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import INTERVAL_TO_MS, normalize_interval, settings

logger = logging.getLogger("ws.collector")


# ==================== 基础工具：Metrics ====================

@dataclass
class Metrics:
    """监控指标"""
    requests_total: int = 0
    requests_failed: int = 0
    rows_written: int = 0
    gaps_found: int = 0
    gaps_filled: int = 0
    zip_downloads: int = 0

    last_collect_duration: float = 0
    last_backfill_duration: float = 0

    last_collect_time: float = 0
    last_backfill_time: float = 0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name, 0) + value)

    def set(self, name: str, value: float) -> None:
        with self._lock:
            setattr(self, name, value)

    def to_dict(self) -> Dict[str, float]:
        with self._lock:
            return {
                "requests_total": self.requests_total,
                "requests_failed": self.requests_failed,
                "rows_written": self.rows_written,
                "gaps_found": self.gaps_found,
                "gaps_filled": self.gaps_filled,
                "zip_downloads": self.zip_downloads,
                "last_collect_duration": self.last_collect_duration,
                "last_backfill_duration": self.last_backfill_duration,
                "last_collect_time": self.last_collect_time,
                "last_backfill_time": self.last_backfill_time,
            }

    def __str__(self) -> str:
        d = self.to_dict()
        return " | ".join(f"{k}={v}" for k, v in d.items() if v)


metrics = Metrics()


class Timer:
    """计时上下文管理器"""

    def __init__(self, metric_name: str):
        self.metric_name = metric_name
        self.start = 0.0

    def __enter__(self) -> "Timer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args) -> None:
        duration = time.perf_counter() - self.start
        metrics.set(self.metric_name, duration)
        metrics.set(f"{self.metric_name.replace('duration', 'time')}", time.time())


# ==================== 全局限流器（隔离到 datacat 日志目录） ====================

_BASE_DIR = settings.log_dir
_STATE_FILE = _BASE_DIR / ".rate_limit_state"
_LOCK_FILE = _BASE_DIR / ".rate_limit.lock"
_BAN_FILE = _BASE_DIR / ".ban_until"

RATE_PER_MINUTE = min(int(os.getenv("DATACAT_RATE_LIMIT_PER_MINUTE", str(settings.rate_limit_per_minute))), 2400)
MAX_CONCURRENT = min(int(os.getenv("DATACAT_MAX_CONCURRENT", str(settings.max_concurrent))), 20)


class GlobalLimiter:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self.capacity = float(RATE_PER_MINUTE)
        self.rate = RATE_PER_MINUTE / 60.0
        self._sem = threading.Semaphore(MAX_CONCURRENT)
        self._ban_until = 0.0
        self._ban_lock = threading.Lock()
        _BASE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_ban()

    def _load_ban(self):
        try:
            if _BAN_FILE.exists():
                self._ban_until = float(_BAN_FILE.read_text().strip())
        except Exception:
            pass

    def _save_ban(self):
        try:
            tmp = _BAN_FILE.with_suffix(".tmp")
            tmp.write_text(str(self._ban_until))
            tmp.rename(_BAN_FILE)
        except Exception:
            pass

    def set_ban(self, until: float):
        with self._ban_lock:
            if until > self._ban_until:
                self._ban_until = until
                self._save_ban()
                logger.warning("IP ban 至 %s", time.strftime("%H:%M:%S", time.localtime(until)))

    def _wait_ban(self):
        self._load_ban()
        with self._ban_lock:
            if self._ban_until > time.time():
                wait = self._ban_until - time.time() + 5
                logger.warning("等待 ban 解除 %.0fs", wait)
                time.sleep(wait)

    def _acquire_tokens(self, weight: int):
        while True:
            with open(_LOCK_FILE, "w") as f:
                import fcntl

                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    tokens, last = self._read_state()
                    now = time.time()
                    tokens = min(self.capacity, tokens + (now - last) * self.rate)
                    if tokens >= weight:
                        tokens -= weight
                        self._write_state(tokens, now)
                        return
                    wait = (weight - tokens) / self.rate
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            time.sleep(max(0.05, wait))

    def _read_state(self):
        try:
            if _STATE_FILE.exists():
                d = json.loads(_STATE_FILE.read_text())
                return d.get("tokens", self.capacity), d.get("last", time.time())
        except Exception:
            pass
        return self.capacity, time.time()

    def _write_state(self, tokens, last):
        try:
            tmp = _STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tokens": tokens, "last": last}))
            tmp.rename(_STATE_FILE)
        except Exception:
            pass

    def acquire(self, weight: int = 1):
        """获取许可：等ban -> 获取信号量 -> 获取令牌"""
        self._wait_ban()
        self._sem.acquire()
        try:
            self._acquire_tokens(weight)
        except Exception:
            self._sem.release()
            raise

    def release(self):
        """释放信号量"""
        self._sem.release()

    def parse_ban(self, msg: str) -> float:
        import re

        m = re.search(r"banned until (\d+)", str(msg))
        return int(m.group(1)) / 1000 if m else 0


def acquire(weight: int = 1):
    GlobalLimiter().acquire(weight)


def release():
    GlobalLimiter().release()


def set_ban(until: float):
    GlobalLimiter().set_ban(until)


def parse_ban(msg: str) -> float:
    return GlobalLimiter().parse_ban(msg)


# ==================== CCXT 适配（符号加载 + OHLCV） ====================

_clients: Dict[str, "ccxt.Exchange"] = {}
_symbols: Dict[str, List[str]] = {}
DEFAULT_PROXY = os.getenv("DATACAT_HTTP_PROXY") or os.getenv("DATACAT_HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / ".git").exists():
            return p
    raise RuntimeError("未找到仓库根目录（.git）")


_libs_path = str(_project_root() / "libs")
if _libs_path not in sys.path:
    sys.path.insert(0, _libs_path)

from common.symbols import get_configured_symbols  # noqa: E402
from common import symbols as common_symbols  # noqa: E402
import ccxt  # noqa: E402


def _parse_list(raw: str) -> List[str]:
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _ensure_proxy_env() -> None:
    if settings.http_proxy:
        os.environ.setdefault("HTTP_PROXY", settings.http_proxy)
        os.environ.setdefault("HTTPS_PROXY", settings.http_proxy)


def get_client(exchange: str = "binance") -> "ccxt.Exchange":
    if exchange not in _clients:
        cls = getattr(ccxt, exchange, None)
        if not cls:
            raise ValueError(f"不支持: {exchange}")
        _clients[exchange] = cls({
            "enableRateLimit": True,
            "timeout": 30000,
            "proxies": {"http": DEFAULT_PROXY, "https": DEFAULT_PROXY} if DEFAULT_PROXY else None,
            "options": {"defaultType": "swap"},
        })
    return _clients[exchange]


def load_symbols(exchange: str = "binance") -> List[str]:
    key = f"{exchange}_usdt"
    if key not in _symbols:
        configured = get_configured_symbols()
        if configured:
            _symbols[key] = configured
            logger.info("使用配置币种 %d 个", len(_symbols[key]))
        else:
            acquire(5)
            try:
                client = get_client(exchange)
                client.load_markets()
                all_symbols = sorted({
                    f"{m['base']}USDT" for m in client.markets.values()
                    if m.get("swap") and m.get("settle") == "USDT" and m.get("linear")
                })
            except Exception as exc:
                logger.warning("ccxt 加载币种失败，尝试 REST 兜底: %s", exc)
                all_symbols = []
            finally:
                release()
            if not all_symbols:
                _ensure_proxy_env()
                try:
                    all_symbols = common_symbols._fetch_all_symbols_rest()
                except Exception as exc:
                    logger.warning("REST 兜底失败: %s", exc)
                    all_symbols = []
            exclude = set(_parse_list(os.getenv("SYMBOLS_EXCLUDE", "")))
            extra = _parse_list(os.getenv("SYMBOLS_EXTRA", ""))
            _symbols[key] = [s for s in all_symbols if s not in exclude]
            _symbols[key] = sorted(set(_symbols[key]) | set(extra))
            if _symbols[key]:
                logger.info("加载 %s USDT永续 %d 个", exchange, len(_symbols[key]))
    return _symbols[key]


def fetch_ohlcv(exchange: str, symbol: str, interval: str = "1m",
               since_ms: Optional[int] = None, limit: int = 1000) -> List[List]:
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        return []

    ccxt_sym = f"{symbol[:-4]}/USDT:USDT"

    for attempt in range(3):
        acquire(2)
        try:
            return get_client(exchange).fetch_ohlcv(ccxt_sym, interval, since=since_ms, limit=limit)
        except ccxt.RateLimitExceeded as e:
            err_str = str(e)
            if "418" in err_str:
                ban_time = parse_ban(err_str)
                set_ban(ban_time if ban_time > time.time() else time.time() + 120)
            else:
                set_ban(time.time() + 60)
            if attempt == 2:
                logger.warning("fetch_ohlcv 限流: %s", e)
                return []
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            if attempt == 2:
                logger.warning("fetch_ohlcv 网络错误: %s", e)
                return []
            time.sleep(1 * (2 ** attempt))
        finally:
            release()


def to_rows(exchange: str, symbol: str, candles: List[List], source: str = "ccxt") -> List[dict]:
    return [{
        "exchange": exchange, "symbol": symbol.upper(),
        "bucket_ts": datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
        "open": float(c[1]), "high": float(c[2]), "low": float(c[3]),
        "close": float(c[4]), "volume": float(c[5]),
        "quote_volume": None, "trade_count": None, "is_closed": True, "source": source,
        "taker_buy_volume": None, "taker_buy_quote_volume": None,
    } for c in candles if len(c) >= 6]


def normalize_symbol(symbol: str) -> Optional[str]:
    s = symbol.upper().replace("/", "").replace(":", "").replace("-", "")
    return s if s.endswith("USDT") else None


# ==================== Cryptofeed 适配 ====================

@dataclass
class CandleEvent:
    """K线事件"""
    symbol: str
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: Optional[Decimal] = None
    taker_buy_volume: Optional[Decimal] = None
    taker_buy_quote_volume: Optional[Decimal] = None
    trade_count: Optional[int] = None


class BinanceWSAdapter:
    """Binance WebSocket 适配器"""

    def __init__(self, http_proxy: Optional[str] = None):
        self._proxy = http_proxy
        self._handler = None
        self._callback: Optional[callable] = None
        self._symbols: List[str] = []

    def subscribe(self, symbols: List[str], callback: callable) -> None:
        self._symbols = symbols
        self._callback = callback

    async def _on_candle(self, candle, receipt_ts: float) -> None:
        if not candle.closed or not self._callback:
            return
        raw = getattr(candle, "raw", {}) or {}
        k = raw.get("k", {})
        self._callback(CandleEvent(
            symbol=candle.symbol, timestamp=candle.start,
            open=candle.open, high=candle.high, low=candle.low, close=candle.close, volume=candle.volume,
            quote_volume=Decimal(k.get("q", "0")), taker_buy_volume=Decimal(k.get("V", "0")),
            taker_buy_quote_volume=Decimal(k.get("Q", "0")), trade_count=candle.trades,
        ))

    def run(self) -> None:
        from cryptofeed import FeedHandler
        from cryptofeed.defines import CANDLES
        from cryptofeed.exchanges import BinanceFutures

        log_file = settings.log_dir / "cryptofeed.log"
        self._handler = FeedHandler(config={"uvloop": False, "log": {"filename": str(log_file), "level": "INFO"}})
        kw = {
            "symbols": self._symbols,
            "channels": [CANDLES],
            "callbacks": {CANDLES: self._on_candle},
            "candle_interval": "1m",
            "candle_closed_only": True,
            "timeout": 60,
        }
        if self._proxy:
            kw["http_proxy"] = self._proxy
        self._handler.add_feed(BinanceFutures(**kw))
        logger.info("启动 Binance WSS: 符号=%d", len(self._symbols))
        self._handler.run()

    def stop(self) -> None:
        if self._handler:
            self._handler.stop()


def preload_symbols(symbols: List[str]) -> None:
    try:
        from cryptofeed.defines import BINANCE_FUTURES, PERPETUAL
        from cryptofeed.exchanges import BinanceFutures
        from cryptofeed.symbols import Symbol, Symbols

        mapping = {Symbol(s[:-4], "USDT", type=PERPETUAL).normalized: s for s in symbols if s.upper().endswith("USDT")}
        if mapping:
            Symbols.set(BINANCE_FUTURES, mapping, {"symbols": list(mapping.keys()), "channels": {"rest": [], "websocket": list(BinanceFutures.websocket_channels.keys())}})
            logger.info("预置 cryptofeed 映射 %d 个", len(mapping))
    except Exception as e:
        logger.warning("预置映射失败: %s", e)


# ==================== Timescale 适配 ====================

class TimescaleAdapter:
    """TimescaleDB 操作"""

    def __init__(self, db_url: Optional[str] = None, schema: Optional[str] = None,
                 pool_min: int = 2, pool_max: int = 10, timeout: float = 30.0):
        self.db_url = db_url or settings.database_url
        self.schema = schema or settings.db_schema
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._timeout = timeout
        self._pool: Optional[ConnectionPool] = None

    @property
    def pool(self) -> ConnectionPool:
        if self._pool is None:
            self._pool = ConnectionPool(
                self.db_url,
                min_size=self._pool_min,
                max_size=self._pool_max,
                timeout=self._timeout,
                max_idle=300,
                max_lifetime=3600,
            )
        return self._pool

    def close(self) -> None:
        if self._pool:
            self._pool.close()
            self._pool = None

    @contextmanager
    def connection(self) -> Iterator:
        with self.pool.connection() as conn:
            yield conn

    def upsert_candles(self, interval: str, rows: Sequence[dict], batch_size: int = 2000) -> int:
        if not rows:
            return 0

        interval = normalize_interval(interval)
        table_name = f"candles_{interval}"
        cols = list(rows[0].keys())

        if "bucket_ts" not in cols or "symbol" not in cols or "exchange" not in cols:
            raise ValueError("Rows must contain bucket_ts, symbol, and exchange")

        temp_table_name = f"temp_{table_name}_{int(datetime.now().timestamp() * 1000)}"

        sql_create_temp = sql.SQL("""
            CREATE TEMP TABLE {temp_table} (LIKE {target_table} INCLUDING DEFAULTS)
            ON COMMIT DROP;
        """).format(
            temp_table=sql.Identifier(temp_table_name),
            target_table=sql.Identifier(self.schema, table_name)
        )

        update_cols = [col for col in cols if col not in ("exchange", "symbol", "bucket_ts")]
        sql_upsert_from_temp = sql.SQL("""
            INSERT INTO {target_table} ({cols})
            SELECT {cols} FROM {temp_table}
            ON CONFLICT (exchange, symbol, bucket_ts) DO UPDATE SET
                {update_assignments},
                updated_at = NOW();
        """).format(
            target_table=sql.Identifier(self.schema, table_name),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            temp_table=sql.Identifier(temp_table_name),
            update_assignments=sql.SQL(", ").join(
                sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(col))
                for col in update_cols
            )
        )

        total_inserted = 0
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_create_temp)

                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    with cur.copy(sql.SQL("COPY {temp_table} ({cols}) FROM STDIN").format(
                        temp_table=sql.Identifier(temp_table_name),
                        cols=sql.SQL(", ").join(map(sql.Identifier, cols))
                    )) as copy:
                        for row in batch:
                            copy.write_row(tuple(row.get(col) for col in cols))

                cur.execute(sql_upsert_from_temp)
                total_inserted = cur.rowcount if cur.rowcount > 0 else len(rows)

            conn.commit()

        return total_inserted

    def upsert_metrics(self, rows: Sequence[dict], batch_size: int = 2000) -> int:
        if not rows:
            return 0

        table_name = "binance_futures_metrics_5m"
        cols = list(rows[0].keys())

        if "create_time" not in cols or "symbol" not in cols:
            raise ValueError("Rows must contain create_time and symbol")

        temp_table_name = f"temp_{table_name}_{int(datetime.now().timestamp() * 1000)}"

        sql_create_temp = sql.SQL("""
            CREATE TEMP TABLE {temp_table} (LIKE {target_table} INCLUDING DEFAULTS)
            ON COMMIT DROP;
        """).format(
            temp_table=sql.Identifier(temp_table_name),
            target_table=sql.Identifier(self.schema, table_name)
        )

        update_cols = [col for col in cols if col not in ("symbol", "create_time")]
        sql_upsert_from_temp = sql.SQL("""
            INSERT INTO {target_table} ({cols})
            SELECT {cols} FROM {temp_table}
            ON CONFLICT (symbol, create_time) DO UPDATE SET
                {update_assignments},
                updated_at = NOW();
        """).format(
            target_table=sql.Identifier(self.schema, table_name),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            temp_table=sql.Identifier(temp_table_name),
            update_assignments=sql.SQL(", ").join(
                sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(col))
                for col in update_cols
            )
        )

        total_inserted = 0
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_create_temp)

                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    with cur.copy(sql.SQL("COPY {temp_table} ({cols}) FROM STDIN").format(
                        temp_table=sql.Identifier(temp_table_name),
                        cols=sql.SQL(", ").join(map(sql.Identifier, cols))
                    )) as copy:
                        for row in batch:
                            copy.write_row(tuple(row.get(col) for col in cols))

                cur.execute(sql_upsert_from_temp)
                total_inserted = cur.rowcount if cur.rowcount > 0 else len(rows)

            conn.commit()

        return total_inserted

    def get_symbols(self, exchange: str, interval: str = "1m") -> List[str]:
        table = f"{self.schema}.candles_{normalize_interval(interval)}"
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT DISTINCT symbol FROM {table} WHERE exchange = %s ORDER BY symbol", (exchange,))
                return [r[0] for r in cur.fetchall()]

    def get_counts(self, exchange: str, interval: str, symbols: Sequence[str]) -> Dict[str, int]:
        if not symbols:
            return {}
        table = f"{self.schema}.candles_{normalize_interval(interval)}"
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT symbol, COUNT(*) FROM {table} WHERE exchange = %s AND symbol = ANY(%s) GROUP BY symbol", (exchange, list(symbols)))
                return {r[0]: r[1] for r in cur.fetchall()}

    def detect_gaps(self, exchange: str, interval: str, symbols: Sequence[str], lookback_min: int = 10080, threshold_sec: int = 120, limit: int = 50) -> List[tuple]:
        table = f"{self.schema}.candles_{normalize_interval(interval)}"
        sql_str = f"""
            WITH o AS (SELECT symbol, bucket_ts, LEAD(bucket_ts) OVER (PARTITION BY symbol ORDER BY bucket_ts) AS next_ts
                       FROM {table} WHERE exchange = %(ex)s AND symbol = ANY(%(sym)s) AND bucket_ts >= NOW() - INTERVAL '{lookback_min} minutes')
            SELECT symbol, bucket_ts, next_ts FROM o WHERE next_ts IS NOT NULL AND next_ts - bucket_ts >= INTERVAL '{threshold_sec} seconds' ORDER BY bucket_ts LIMIT %(lim)s
        """
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_str, {"ex": exchange, "sym": list(symbols), "lim": limit})
                return cur.fetchall()

    def query(self, exchange: str, symbol: str, interval: str, start: Optional[datetime] = None, end: Optional[datetime] = None, limit: int = 1000) -> List[dict]:
        table = f"{self.schema}.candles_{normalize_interval(interval)}"
        conds, params = ["exchange = %s", "symbol = %s"], [exchange, symbol]
        if start:
            conds.append("bucket_ts >= %s")
            params.append(start)
        if end:
            conds.append("bucket_ts <= %s")
            params.append(end)
        params.append(limit)
        with self.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"SELECT * FROM {table} WHERE {' AND '.join(conds)} ORDER BY bucket_ts DESC LIMIT %s", params)
                return cur.fetchall()


# ==================== 回填相关（K线） ====================

BINANCE_DATA_URL = "https://data.binance.vision"
EXPECTED_1M_PER_DAY = 1440
EXPECTED_5M_PER_DAY = 288


@dataclass
class GapInfo:
    """缺口信息"""
    symbol: str
    date: date
    expected: int
    actual: int
    missing: int = field(init=False)

    def __post_init__(self):
        self.missing = self.expected - self.actual


class GapScanner:
    """精确缺口扫描器"""

    def __init__(self, ts: TimescaleAdapter):
        self._ts = ts

    def scan_klines(self, symbols: Sequence[str], start: date, end: date,
                    interval: str = "1m", threshold: float = 0.95) -> Dict[str, List[GapInfo]]:
        expected = EXPECTED_1M_PER_DAY if interval == "1m" else int(EXPECTED_1M_PER_DAY / INTERVAL_TO_MS.get(interval, 60000) * 60000)
        min_count = int(expected * threshold)

        table = f"{self._ts.schema}.candles_{interval}"
        sql_str = f"""
            SELECT symbol, DATE(bucket_ts AT TIME ZONE 'UTC') AS d, COUNT(*) AS c
            FROM {table}
            WHERE exchange = %s AND symbol = ANY(%s)
              AND bucket_ts >= %s AND bucket_ts < %s
            GROUP BY symbol, DATE(bucket_ts AT TIME ZONE 'UTC')
        """
        start_ts = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        end_ts = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

        counts: Dict[tuple, int] = {}
        with self._ts.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_str, (settings.db_exchange, list(symbols), start_ts, end_ts))
                for sym, d, c in cur.fetchall():
                    counts[(sym, d)] = c

        gaps: Dict[str, List[GapInfo]] = {}
        for sym in symbols:
            sym_gaps = []
            for i in range((end - start).days + 1):
                d = start + timedelta(days=i)
                actual = counts.get((sym, d), 0)
                if actual < min_count:
                    sym_gaps.append(GapInfo(sym, d, expected, actual))
            if sym_gaps:
                gaps[sym] = sym_gaps
        return gaps


class RestBackfiller:
    """REST API 分页补齐 (用于小缺口) - 并行版"""

    def __init__(self, ts: TimescaleAdapter, workers: int = 8):
        self._ts = ts
        self._workers = workers

    def fill_kline_gap(self, symbol: str, gap: GapInfo, interval: str = "1m") -> int:
        start_ts = datetime.combine(gap.date, datetime.min.time(), tzinfo=timezone.utc)
        end_ts = datetime.combine(gap.date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        since_ms = int(start_ts.timestamp() * 1000)
        target_ms = int(end_ts.timestamp() * 1000)

        all_rows = []
        max_iterations = 100

        for _ in range(max_iterations):
            candles = fetch_ohlcv(settings.ccxt_exchange, symbol, interval, since_ms, 1000)
            if not candles:
                break

            rows = [r for r in to_rows(settings.db_exchange, symbol, candles, "ccxt_gap")
                    if start_ts <= r["bucket_ts"] < end_ts]
            all_rows.extend(rows)

            last_ms = int(candles[-1][0])
            if last_ms == since_ms or last_ms >= target_ms:
                break
            since_ms = last_ms + INTERVAL_TO_MS.get(interval, 60000)

        if all_rows:
            self._ts.upsert_candles(interval, all_rows)
        return len(all_rows)

    def fill_gaps(self, gaps: Dict[str, List[GapInfo]], interval: str = "1m") -> int:
        tasks = [(sym, gap, interval) for sym, sym_gaps in gaps.items() for gap in sym_gaps]
        if not tasks:
            return 0

        total = 0
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(self.fill_kline_gap, sym, gap, iv): (sym, gap.date) for sym, gap, iv in tasks}
            for future in as_completed(futures):
                sym, d = futures[future]
                try:
                    n = future.result()
                    if n > 0:
                        logger.info("[%s] %s REST补齐 %d 条", sym, d, n)
                        total += n
                except Exception as e:
                    logger.warning("[%s] %s REST失败: %s", sym, d, e)
        return total


class ZipBackfiller:
    """Binance Vision ZIP 补齐 - 智能颗粒度 + 代理重试"""

    MAX_CACHE_DAYS = 7

    def __init__(self, ts: TimescaleAdapter, workers: int = 8):
        self._ts = ts
        self.workers = workers
        self._kline_dir = settings.data_dir / "downloads" / "klines"
        self._metrics_dir = settings.data_dir / "downloads" / "metrics"
        self._kline_dir.mkdir(parents=True, exist_ok=True)
        self._metrics_dir.mkdir(parents=True, exist_ok=True)
        self._proxies = {"http": settings.http_proxy, "https": settings.http_proxy} if settings.http_proxy else {}
        self._fallback_proxies = self._proxies

    def cleanup_old_files(self, max_age_days: int = None) -> int:
        max_age = max_age_days or self.MAX_CACHE_DAYS
        cutoff = time.time() - max_age * 86400
        removed = 0
        for d in [self._kline_dir, self._metrics_dir]:
            for f in d.glob("*.zip"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            logger.info("清理 %d 个过期 ZIP 文件", removed)
        return removed

    def _download_with_retry(self, url: str, path: Path) -> bool:
        acquire(1)
        try:
            for attempt, proxies in enumerate([self._proxies, self._fallback_proxies]):
                try:
                    r = requests.get(url, proxies=proxies, timeout=60)
                    if r.status_code == 404:
                        return False
                    if r.status_code == 429:
                        retry_after = int(r.headers.get("Retry-After", 60))
                        set_ban(time.time() + retry_after)
                        return False
                    if r.status_code == 418:
                        retry_after = int(r.headers.get("Retry-After", 0))
                        ban_time = parse_ban(r.text) if not retry_after else time.time() + retry_after
                        set_ban(ban_time if ban_time > time.time() else time.time() + 120)
                        return False
                    r.raise_for_status()
                    path.write_bytes(r.content)
                    metrics.inc("zip_downloads")
                    return True
                except Exception as e:
                    if attempt == 0:
                        logger.debug("直连失败，尝试代理: %s", url)
                    else:
                        logger.debug("代理也失败 %s: %s", url, e)
            return False
        finally:
            release()

    def fill_kline_gaps(self, gaps: Dict[str, List[GapInfo]], interval: str = "1m") -> int:
        if not gaps:
            return 0

        month_groups: Dict[tuple, List[date]] = {}
        for sym, sym_gaps in gaps.items():
            for gap in sym_gaps:
                key = (sym, gap.date.strftime("%Y-%m"))
                month_groups.setdefault(key, []).append(gap.date)

        tasks = [(sym, month, dates, interval) for (sym, month), dates in month_groups.items()]
        logger.info("K线 ZIP 补齐: %d 个月度任务 (原 %d 个日任务)", len(tasks), sum(len(g) for g in gaps.values()))

        total = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._download_kline_month, sym, month, dates, iv): (sym, month) for sym, month, dates, iv in tasks}
            for future in as_completed(futures):
                sym, month = futures[future]
                try:
                    n = future.result()
                    if n > 0:
                        logger.info("[%s] %s ZIP导入 %d 条", sym, month, n)
                        total += n
                except Exception as e:
                    logger.warning("[%s] %s 失败: %s", sym, month, e)

        return total

    def _download_kline_month(self, symbol: str, month: str, dates: List[date], interval: str) -> int:
        sym = symbol.upper()
        url = f"{BINANCE_DATA_URL}/data/futures/um/daily/klines/{sym}/{interval}/{sym}-{interval}-{month}.zip"
        path = self._kline_dir / f"{sym}-{interval}-{month}.zip"

        if not path.exists():
            if not self._download_with_retry(url, path):
                return 0

        return self._import_kline_zip(path, sym, dates, interval)

    def _import_kline_zip(self, path: Path, symbol: str, dates: List[date], interval: str) -> int:
        rows = []
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if not name.endswith(".csv"):
                        continue
                    with zf.open(name) as f:
                        for row in csv.reader(line.decode() for line in f):
                            if len(row) < 6:
                                continue
                            ts = int(row[0])
                            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                            if dates and dt.date() not in dates:
                                continue
                            rows.append({
                                "exchange": settings.db_exchange,
                                "symbol": symbol,
                                "bucket_ts": dt,
                                "open": float(row[1]),
                                "high": float(row[2]),
                                "low": float(row[3]),
                                "close": float(row[4]),
                                "volume": float(row[5]),
                                "quote_volume": float(row[7]) if len(row) > 7 and row[7] else None,
                                "trade_count": int(row[8]) if len(row) > 8 and row[8] else None,
                                "is_closed": True,
                                "source": "binance_zip",
                                "taker_buy_volume": float(row[9]) if len(row) > 9 and row[9] else None,
                                "taker_buy_quote_volume": float(row[10]) if len(row) > 10 and row[10] else None,
                            })
        except Exception as e:
            logger.error("解析失败 %s: %s", path, e)
            return 0

        if rows:
            n = self._ts.upsert_candles(interval, rows)
            metrics.inc("rows_written", n)
            return n
        return 0


# ==================== WS 采集器 ====================

class WSCollector:
    """WebSocket 1m K线采集器 - 时间窗口批量写入"""

    FLUSH_WINDOW = 3.0
    MAX_BUFFER = 1000

    def __init__(self):
        self._ts = TimescaleAdapter()
        self._symbols = self._load_symbols()
        self._gap_stop = threading.Event()
        self._gap_thread: Optional[threading.Thread] = None

        self._buffer: List[dict] = []
        self._buffer_lock = asyncio.Lock()
        self._last_candle_time: float = 0
        self._flush_task: Optional[asyncio.Task] = None

    def _load_symbols(self) -> Dict[str, str]:
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

            if len(self._buffer) >= self.MAX_BUFFER:
                await self._flush()
            elif self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(self.FLUSH_WINDOW)
        async with self._buffer_lock:
            if time.monotonic() - self._last_candle_time >= self.FLUSH_WINDOW:
                await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return

        rows = self._buffer.copy()
        self._buffer.clear()

        try:
            n = await asyncio.to_thread(self._ts.upsert_candles, "1m", rows)
            metrics.inc("rows_written", n)
            logger.debug("批量写入 %d 条 K 线", n)
        except Exception as e:
            logger.error("批量写入失败: %s", e)

    def run(self) -> None:
        if self._symbols:
            threading.Thread(target=self._run_backfill, args=(1,), daemon=True).start()

        if settings.ws_gap_interval > 0:
            self._gap_stop.clear()
            self._gap_thread = threading.Thread(target=self._gap_loop, daemon=True)
            self._gap_thread.start()

        ws = BinanceWSAdapter(http_proxy=settings.http_proxy)
        ws.subscribe(list(self._symbols.keys()), self._on_candle_sync)

        try:
            ws.run()
        finally:
            asyncio.run(self._final_flush())
            self._gap_stop.set()
            self._ts.close()

    def _on_candle_sync(self, e: CandleEvent) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._on_candle(e))
            else:
                asyncio.run(self._on_candle(e))
        except RuntimeError:
            asyncio.run(self._on_candle(e))

    async def _final_flush(self) -> None:
        async with self._buffer_lock:
            await self._flush()

    def _gap_loop(self) -> None:
        lookback_days = 2
        unfillable: Set[tuple] = set()

        while not self._gap_stop.wait(settings.ws_gap_interval):
            try:
                has_gaps, lookback_days = self._smart_backfill(lookback_days, unfillable)
                if not has_gaps:
                    lookback_days = max(1, lookback_days - 1)
                else:
                    lookback_days = min(7, lookback_days + 1)
            except Exception as e:
                logger.error("周期缺口检查失败: %s", e)

    def _smart_backfill(self, lookback_days: int, unfillable: Set[tuple]) -> tuple:
        t0 = time.perf_counter()
        symbols = list(self._symbols.values())
        end = date.today()
        start = end - timedelta(days=lookback_days)

        scanner = GapScanner(self._ts)
        gaps = scanner.scan_klines(symbols, start, end, "1m", 0.95)

        if not gaps:
            return False, lookback_days

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

        remaining = scanner.scan_klines(list(filtered.keys()), start, end, "1m", 0.95)
        if remaining:
            rest_bf = RestBackfiller(self._ts, workers=2)
            filled += rest_bf.fill_gaps(remaining, "1m")

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
        self._smart_backfill(lookback_days or 1, set())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    WSCollector().run()


if __name__ == "__main__":
    main()
