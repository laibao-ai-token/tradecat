"""Metrics ZIP 回填采集器（Binance Vision）

职责：
- 扫描指标缺口
- 仅用 ZIP 补齐
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import requests
from psycopg import sql
from psycopg_pool import ConnectionPool

from config import settings

logger = logging.getLogger(__name__)

BINANCE_DATA_URL = "https://data.binance.vision"
EXPECTED_5M_PER_DAY = 288


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

    def _init(self) -> None:
        self.capacity = float(RATE_PER_MINUTE)
        self.rate = RATE_PER_MINUTE / 60.0
        self._sem = threading.Semaphore(MAX_CONCURRENT)
        self._ban_until = 0.0
        self._ban_lock = threading.Lock()
        _BASE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_ban()

    def _load_ban(self) -> None:
        try:
            if _BAN_FILE.exists():
                self._ban_until = float(_BAN_FILE.read_text().strip())
        except Exception:
            pass

    def _save_ban(self) -> None:
        try:
            tmp = _BAN_FILE.with_suffix(".tmp")
            tmp.write_text(str(self._ban_until))
            tmp.rename(_BAN_FILE)
        except Exception:
            pass

    def set_ban(self, until: float) -> None:
        with self._ban_lock:
            if until > self._ban_until:
                self._ban_until = until
                self._save_ban()
                logger.warning("IP ban 至 %s", time.strftime("%H:%M:%S", time.localtime(until)))

    def _wait_ban(self) -> None:
        self._load_ban()
        with self._ban_lock:
            if self._ban_until > time.time():
                wait = self._ban_until - time.time() + 5
                logger.warning("等待 ban 解除 %.0fs", wait)
                time.sleep(wait)

    def _acquire_tokens(self, weight: int) -> None:
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

    def _write_state(self, tokens: float, last: float) -> None:
        try:
            tmp = _STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tokens": tokens, "last": last}))
            tmp.rename(_STATE_FILE)
        except Exception:
            pass

    def acquire(self, weight: int = 1) -> None:
        self._wait_ban()
        self._sem.acquire()
        try:
            self._acquire_tokens(weight)
        except Exception:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()

    def parse_ban(self, msg: str) -> float:
        try:
            for line in msg.split("\n"):
                if "banned until" in line:
                    ts = int(line.strip().split("banned until")[1].strip())
                    return ts / 1000
        except Exception:
            pass
        return time.time() + 60


def acquire(weight: int = 1) -> None:
    GlobalLimiter().acquire(weight)


def release() -> None:
    GlobalLimiter().release()


def set_ban(until: float) -> None:
    GlobalLimiter().set_ban(until)


def parse_ban(msg: str) -> float:
    return GlobalLimiter().parse_ban(msg)


# ==================== CCXT 适配（用于加载币种） ====================

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
if _libs_path not in os.sys.path:
    os.sys.path.insert(0, _libs_path)

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

    def connection(self):
        return self.pool.connection()

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


# ==================== 缺口检测 ====================

@dataclass
class GapInfo:
    """缺口信息"""
    symbol: str
    date: date
    expected: int
    actual: int
    missing: int = field(init=False)

    def __post_init__(self) -> None:
        self.missing = self.expected - self.actual


class GapScanner:
    """精确缺口扫描器"""

    def __init__(self, ts: TimescaleAdapter):
        self._ts = ts

    def scan_metrics(self, symbols: Sequence[str], start: date, end: date,
                     threshold: float = 0.95) -> Dict[str, List[GapInfo]]:
        min_count = int(EXPECTED_5M_PER_DAY * threshold)

        sql_str = """
            SELECT symbol, DATE(create_time) AS d, COUNT(*) AS c
            FROM market_data.binance_futures_metrics_5m
            WHERE symbol = ANY(%s) AND create_time >= %s AND create_time < %s
            GROUP BY symbol, DATE(create_time)
        """
        start_ts = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        end_ts = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

        counts: Dict[tuple, int] = {}
        with self._ts.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_str, (list(symbols), start_ts, end_ts))
                for sym, d, c in cur.fetchall():
                    counts[(sym, d)] = c

        gaps: Dict[str, List[GapInfo]] = {}
        for sym in symbols:
            sym_gaps = []
            for i in range((end - start).days + 1):
                d = start + timedelta(days=i)
                actual = counts.get((sym, d), 0)
                if actual < min_count:
                    sym_gaps.append(GapInfo(sym, d, EXPECTED_5M_PER_DAY, actual))
            if sym_gaps:
                gaps[sym] = sym_gaps
        return gaps


# ==================== ZIP 回填（Metrics） ====================

class ZipBackfiller:
    """Binance Vision ZIP 补齐 - Metrics"""

    MAX_CACHE_DAYS = 7

    def __init__(self, ts: TimescaleAdapter, workers: int = 8):
        self._ts = ts
        self.workers = workers
        self._metrics_dir = settings.data_dir / "downloads" / "metrics"
        self._metrics_dir.mkdir(parents=True, exist_ok=True)
        self._proxies = {"http": settings.http_proxy, "https": settings.http_proxy} if settings.http_proxy else {}
        self._fallback_proxies = self._proxies

    def cleanup_old_files(self, max_age_days: int = None) -> int:
        max_age = max_age_days or self.MAX_CACHE_DAYS
        cutoff = time.time() - max_age * 86400
        removed = 0
        for f in self._metrics_dir.glob("*.zip"):
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
            metrics.inc("requests_total")
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
        except Exception:
            metrics.inc("requests_failed")
            return False
        finally:
            release()

    def fill_metrics_gaps(self, gaps: Dict[str, List[GapInfo]]) -> int:
        if not gaps:
            return 0

        month_groups: Dict[tuple, List[date]] = {}
        for sym, sym_gaps in gaps.items():
            for gap in sym_gaps:
                key = (sym, gap.date.strftime("%Y-%m"))
                month_groups.setdefault(key, []).append(gap.date)

        tasks = [(sym, month, dates) for (sym, month), dates in month_groups.items()]
        logger.info("Metrics ZIP 补齐: %d 个月度任务 (原 %d 个日任务)", len(tasks), sum(len(g) for g in gaps.values()))

        from concurrent.futures import ThreadPoolExecutor, as_completed

        total = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._download_metrics_month, sym, month, dates): (sym, month) for sym, month, dates in tasks}
            for future in as_completed(futures):
                sym, month = futures[future]
                try:
                    n = future.result()
                    if n > 0:
                        logger.info("[%s] %s Metrics导入 %d 条", sym, month, n)
                        total += n
                except Exception as e:
                    logger.warning("[%s] %s 失败: %s", sym, month, e)

        return total

    def _download_metrics_month(self, symbol: str, month: str, dates: List[date]) -> int:
        sym = symbol.upper()
        total = 0
        current_month = date.today().strftime("%Y-%m")

        if month == current_month:
            for d in dates:
                day_str = d.strftime("%Y-%m-%d")
                day_fname = f"{sym}-metrics-{day_str}.zip"
                day_path = self._metrics_dir / day_fname
                if not day_path.exists():
                    day_url = f"{BINANCE_DATA_URL}/data/futures/um/daily/metrics/{sym}/{day_fname}"
                    if not self._download_with_retry(day_url, day_path):
                        continue
                total += self._import_metrics_zip(day_path, symbol)
            return total

        month_fname = f"{sym}-metrics-{month}.zip"
        month_path = self._metrics_dir / month_fname
        if not month_path.exists():
            month_url = f"{BINANCE_DATA_URL}/data/futures/um/monthly/metrics/{sym}/{month_fname}"
            self._download_with_retry(month_url, month_path)

        if month_path.exists():
            for d in dates:
                n = self._import_metrics_zip(month_path, symbol, d)
                total += n
            return total

        for d in dates:
            day_str = d.strftime("%Y-%m-%d")
            day_fname = f"{sym}-metrics-{day_str}.zip"
            day_path = self._metrics_dir / day_fname
            if not day_path.exists():
                day_url = f"{BINANCE_DATA_URL}/data/futures/um/daily/metrics/{sym}/{day_fname}"
                if not self._download_with_retry(day_url, day_path):
                    continue
            total += self._import_metrics_zip(day_path, symbol)

        return total

    def _import_metrics_zip(self, path: Path, symbol: str, filter_date: date = None) -> int:
        rows = []
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if not name.endswith(".csv"):
                        continue
                    with zf.open(name) as f:
                        for row in csv.reader(line.decode() for line in f):
                            if len(row) < 4:
                                continue
                            try:
                                ts_val = row[0]
                                if ts_val.isdigit():
                                    ts = int(ts_val)
                                else:
                                    ts = int(datetime.fromisoformat(ts_val.replace("Z", "+00:00")).timestamp() * 1000)

                                ts = (ts // 300000) * 300000
                                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                                if filter_date and dt.date() != filter_date:
                                    continue

                                rows.append({
                                    "create_time": dt.replace(tzinfo=None),
                                    "symbol": symbol.upper(),
                                    "exchange": settings.db_exchange,
                                    "sum_open_interest": Decimal(row[2]) if len(row) > 2 and row[2] else None,
                                    "sum_open_interest_value": Decimal(row[3]) if len(row) > 3 and row[3] else None,
                                    "count_toptrader_long_short_ratio": Decimal(row[5]) if len(row) > 5 and row[5] else None,
                                    "sum_toptrader_long_short_ratio": Decimal(row[4]) if len(row) > 4 and row[4] else None,
                                    "count_long_short_ratio": Decimal(row[6]) if len(row) > 6 and row[6] else None,
                                    "sum_taker_long_short_vol_ratio": Decimal(row[7]) if len(row) > 7 and row[7] else None,
                                    "source": "binance_zip",
                                    "is_closed": True,
                                })
                            except (ValueError, IndexError):
                                pass
        except Exception as e:
            logger.error("解析失败 %s: %s", path, e)
            return 0

        if rows:
            n = self._ts.upsert_metrics(rows)
            metrics.inc("rows_written", n)
            return n
        return 0


# ==================== 入口逻辑 ====================

def get_backfill_config():
    mode_raw = os.environ.get("DATACAT_BACKFILL_MODE", os.environ.get("BACKFILL_MODE", "days")).lower()
    mode = "all" if mode_raw == "full" else mode_raw

    try:
        days = int(os.environ.get("DATACAT_BACKFILL_DAYS", os.environ.get("BACKFILL_DAYS", "30")))
    except ValueError:
        days = 30

    start_date = None
    start_date_raw = os.environ.get("DATACAT_BACKFILL_START_DATE", os.environ.get("BACKFILL_START_DATE"))
    if start_date_raw:
        try:
            start_date = datetime.fromisoformat(start_date_raw).date()
        except ValueError:
            logger.warning("无效的 BACKFILL_START_DATE: %s", start_date_raw)

    on_start = os.environ.get("DATACAT_BACKFILL_ON_START", os.environ.get("BACKFILL_ON_START", "false")).lower() in ("true", "1", "yes")
    return mode, days, on_start, start_date


def compute_lookback(mode: str, days: int, start_date: Optional[date] = None) -> int:
    mode = (mode or "days").lower()

    if mode == "none":
        return 0

    if mode == "all":
        if start_date:
            delta = (date.today() - start_date).days
            return max(delta, 1)
        return 3650

    return max(days, 1)


def run_zip_metrics(lookback_days: int, workers: int, threshold: float,
                    symbols: Optional[Sequence[str]] = None,
                    scan_only: bool = False) -> Dict[str, int]:
    ts = TimescaleAdapter()
    scanner = GapScanner(ts)
    zip_filler = ZipBackfiller(ts, workers=workers)

    try:
        symbols = symbols or load_symbols(settings.ccxt_exchange)
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=lookback_days)

        logger.info("扫描 Metrics 缺口: %d 个符号, %s ~ %s", len(symbols), start, end)
        gaps = scanner.scan_metrics(symbols, start, end, threshold)

        if not gaps:
            logger.info("Metrics 无缺口")
            return {"scanned": len(symbols), "gaps": 0, "filled": 0}

        total_gaps = sum(len(g) for g in gaps.values())
        metrics.inc("gaps_found", total_gaps)
        logger.info("发现 %d 个符号共 %d 个缺口", len(gaps), total_gaps)

        if scan_only:
            return {"scanned": len(symbols), "gaps": total_gaps, "filled": 0}

        filled = zip_filler.fill_metrics_gaps(gaps)
        metrics.inc("gaps_filled", filled)
        logger.info("Metrics ZIP 补齐完成: 填充 %d 条 | %s", filled, metrics)
        return {"scanned": len(symbols), "gaps": total_gaps, "filled": filled}
    finally:
        ts.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Metrics ZIP 回填")
    parser.add_argument("--lookback", type=int, help="回溯天数（覆盖env配置）")
    parser.add_argument("--symbols", type=str, help="交易对列表(逗号分隔)")
    parser.add_argument("--workers", type=int, default=3, help="并发线程数")
    parser.add_argument("--threshold", type=float, default=0.95, help="完整度阈值")
    parser.add_argument("--scan-only", action="store_true", help="仅扫描不补齐")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    mode, env_days, _, start_date = get_backfill_config()
    lookback = args.lookback if args.lookback else compute_lookback(mode, env_days, start_date)
    if lookback <= 0:
        logger.info("BACKFILL_MODE=none，跳过")
        return

    symbols = args.symbols.split(",") if args.symbols else None
    result = run_zip_metrics(lookback, args.workers, args.threshold, symbols, args.scan_only)
    logger.info("Metrics ZIP 结果: %s", result)


if __name__ == "__main__":
    main()
