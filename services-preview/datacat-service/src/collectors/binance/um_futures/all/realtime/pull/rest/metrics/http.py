"""期货指标采集器 - 高性能版"""
from __future__ import annotations

import sys
from pathlib import Path

# -------------------- 路径修正：避免 http.py 影子 --------------------
_THIS_DIR = Path(__file__).resolve().parent
if sys.path and sys.path[0] == str(_THIS_DIR):
    sys.path.pop(0)
for p in _THIS_DIR.parents:
    if (p / 'config.py').exists() and p.name == 'src':
        sys.path.insert(0, str(p))
        break

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Iterator, List, Optional, Sequence

import requests
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import settings

logger = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"


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
        self._wait_ban()
        self._sem.acquire()
        try:
            self._acquire_tokens(weight)
        except Exception:
            self._sem.release()
            raise

    def release(self):
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


# ==================== CCXT 符号加载（最小集） ====================

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


# ==================== Timescale 适配（metrics 用） ====================

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


# ==================== Metrics 采集器 ====================

# 配置连接池
_adapter = requests.adapters.HTTPAdapter(pool_connections=30, pool_maxsize=30)
_session = requests.Session()
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


class MetricsCollector:
    """Binance 期货指标采集（5m 粒度）- 并发版"""

    def __init__(self, workers: int = 8):
        self._ts = TimescaleAdapter()
        self._workers = workers
        self._proxies = {"http": settings.http_proxy, "https": settings.http_proxy} if settings.http_proxy else {}

    def _get(self, url: str, params: dict) -> Optional[list]:
        """REST 请求 - 使用全局限流"""
        acquire(1)
        metrics.inc("requests_total")
        try:
            r = _session.get(url, params=params, proxies=self._proxies, timeout=10)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 60))
                set_ban(time.time() + retry_after)
                logger.warning("429 限流警告，等待 %ds", retry_after)
                metrics.inc("requests_failed")
                return None
            if r.status_code == 418:
                retry_after = int(r.headers.get("Retry-After", 0))
                ban_time = parse_ban(r.text) if not retry_after else time.time() + retry_after
                set_ban(ban_time if ban_time > time.time() else time.time() + 120)
                logger.warning("418 IP 被 ban")
                metrics.inc("requests_failed")
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            metrics.inc("requests_failed")
            logger.debug("请求失败 %s: %s", params.get("symbol", ""), e)
            return None
        finally:
            release()

    def _collect_one(self, sym: str) -> Optional[dict]:
        """采集单个符号 - 串行请求避免并发放大"""
        sym = sym.upper()
        apis = [
            ("oi", f"{FAPI}/futures/data/openInterestHist", {"symbol": sym, "period": "5m", "limit": 1}),
            ("pos", f"{FAPI}/futures/data/topLongShortPositionRatio", {"symbol": sym, "period": "5m", "limit": 1}),
            ("acc", f"{FAPI}/futures/data/topLongShortAccountRatio", {"symbol": sym, "period": "5m", "limit": 1}),
            ("glb", f"{FAPI}/futures/data/globalLongShortAccountRatio", {"symbol": sym, "period": "5m", "limit": 1}),
            ("taker", f"{FAPI}/futures/data/takerlongshortRatio", {"symbol": sym, "period": "5m", "limit": 1}),
        ]

        results = {}
        for key, url, params in apis:
            results[key] = self._get(url, params)

        oi = results.get("oi")
        pos = results.get("pos")
        acc = results.get("acc")
        glb = results.get("glb")
        taker = results.get("taker")

        if not oi or not isinstance(oi, list) or not oi:
            return None

        ts = int(oi[0].get("timestamp", 0))
        ts = (ts // 300000) * 300000

        return {
            "create_time": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).replace(tzinfo=None),
            "symbol": sym,
            "exchange": settings.db_exchange,
            "sum_open_interest": _to_decimal(oi[0].get("sumOpenInterest")) if oi else None,
            "sum_open_interest_value": _to_decimal(oi[0].get("sumOpenInterestValue")) if oi else None,
            "count_toptrader_long_short_ratio": _to_decimal(acc[0].get("longShortRatio")) if acc else None,
            "sum_toptrader_long_short_ratio": _to_decimal(pos[0].get("longShortRatio")) if pos else None,
            "count_long_short_ratio": _to_decimal(glb[0].get("longShortRatio")) if glb else None,
            "sum_taker_long_short_vol_ratio": _to_decimal(taker[0].get("buySellRatio")) if taker else None,
            "source": "binance_api",
            "is_closed": True,
        }

    def collect(self, symbols: Sequence[str]) -> List[dict]:
        rows = []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(self._collect_one, sym): sym for sym in symbols}
            for future in as_completed(futures):
                try:
                    row = future.result()
                    if row:
                        rows.append(row)
                except Exception as e:
                    logger.debug("采集异常 %s: %s", futures[future], e)
        return rows

    def save(self, rows: List[dict]) -> int:
        if not rows:
            return 0
        n = self._ts.upsert_metrics(rows)
        metrics.inc("rows_written", n)
        return n

    def run_once(self, symbols: Optional[Sequence[str]] = None) -> int:
        symbols = symbols or load_symbols(settings.ccxt_exchange)
        logger.info("采集 %d 个符号 (并发=%d)", len(symbols), self._workers)
        with Timer("last_collect_duration"):
            rows = self.collect(symbols)
            n = self.save(rows)
        logger.info("保存 %d 条 | %s", n, metrics)
        return n

    def close(self) -> None:
        self._ts.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    c = MetricsCollector()
    try:
        c.run_once()
    finally:
        c.close()


if __name__ == "__main__":
    main()
