"""
数据库读写（高性能版）

优化点：
1. PG 连接池复用 + 扩大池大小
2. 多周期并行查询
3. 批量 SQL 查询（IN 子句）
4. SQLite 连接复用 + WAL 模式
5. 批量写入
"""
import os
import sqlite3
import threading
import logging
from pathlib import Path
from typing import Dict, List, Sequence
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import config
from ..observability import metrics

_sqlite_lock = threading.Lock()
LOG = logging.getLogger("indicator_service.db")
_pg_query_total = metrics.counter("pg_query_total", "PG 查询次数")
_sqlite_commit_total = metrics.counter("sqlite_commit_total", "SQLite 提交次数")

# 共享 PG 连接池（默认行工厂）
_shared_pg_pool: ConnectionPool | None = None
_shared_pg_pool_lock = threading.Lock()

_DEFAULT_RETENTION = {
    "1m": 120,   # 2小时
    "5m": 120,   # 10小时
    "15m": 96,   # 24小时
    "1h": 144,   # 6天
    "4h": 120,   # 20天
    "1d": 180,   # 6个月
    "1w": 104,   # 2年
}
_RETENTION_ENV_KEYS = {
    "1m": "INDICATOR_RETENTION_1M",
    "5m": "INDICATOR_RETENTION_5M",
    "15m": "INDICATOR_RETENTION_15M",
    "1h": "INDICATOR_RETENTION_1H",
    "4h": "INDICATOR_RETENTION_4H",
    "1d": "INDICATOR_RETENTION_1D",
    "1w": "INDICATOR_RETENTION_1W",
}


def _parse_positive_int(raw: str) -> int | None:
    try:
        value = int(str(raw).strip())
    except Exception:
        return None
    return value if value > 0 else None


def get_indicator_retention_map() -> dict[str, int]:
    """Return per-interval retention map with env overrides."""

    out = dict(_DEFAULT_RETENTION)

    raw_overrides = (os.environ.get("INDICATOR_RETENTION_OVERRIDES") or "").strip()
    if raw_overrides:
        for item in raw_overrides.replace(";", ",").split(","):
            part = item.strip()
            if not part or "=" not in part:
                continue
            interval, raw_value = [x.strip() for x in part.split("=", 1)]
            if interval not in out:
                continue
            parsed = _parse_positive_int(raw_value)
            if parsed is not None:
                out[interval] = parsed

    for interval, env_key in _RETENTION_ENV_KEYS.items():
        raw_value = (os.environ.get(env_key) or "").strip()
        if not raw_value:
            continue
        parsed = _parse_positive_int(raw_value)
        if parsed is not None:
            out[interval] = parsed

    return out


def apply_indicator_retention_overrides(overrides: dict[str, int] | None) -> None:
    """Apply retention overrides into environment for current process."""

    if not overrides:
        return
    for interval, value in overrides.items():
        if interval not in _RETENTION_ENV_KEYS:
            continue
        parsed = _parse_positive_int(str(value))
        if parsed is None:
            continue
        os.environ[_RETENTION_ENV_KEYS[interval]] = str(parsed)


def get_db_counters() -> Dict[str, float]:
    """获取 DB 计数器快照"""
    return {
        "pg_query_total": _pg_query_total.get(),
        "sqlite_commit_total": _sqlite_commit_total.get(),
    }


def inc_pg_query():
    """记录 PG 查询次数"""
    _pg_query_total.inc()


def inc_sqlite_commit():
    """记录 SQLite commit 次数"""
    _sqlite_commit_total.inc()


def get_shared_pg_pool() -> ConnectionPool:
    """获取共享 PG 连接池"""
    global _shared_pg_pool
    if _shared_pg_pool is None:
        with _shared_pg_pool_lock:
            if _shared_pg_pool is None:
                _shared_pg_pool = ConnectionPool(
                    config.db_url,
                    min_size=1,
                    max_size=10,
                    timeout=30,
                    kwargs={"connect_timeout": 3},
                )
    return _shared_pg_pool


@contextmanager
def shared_pg_conn():
    """共享 PG 连接上下文"""
    with get_shared_pg_pool().connection() as conn:
        yield conn


class DataReader:
    """从 TimescaleDB 读取 K 线数据（高性能版）"""

    def __init__(self, db_url: str = None, pool_size: int = 10):
        self.db_url = db_url or config.db_url
        self._pool = None
        self._pool_size = pool_size
        self._pool_lock = threading.Lock()
        self._table_exists_cache: dict[str, bool] = {}

    @property
    def pool(self):
        """懒加载连接池（线程安全）"""
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        self.db_url,
                        min_size=2,
                        max_size=self._pool_size,
                        kwargs={"row_factory": dict_row},
                        timeout=120,
                    )
        return self._pool

    @contextmanager
    def _conn(self):
        """从连接池获取连接"""
        with self.pool.connection() as conn:
            yield conn

    def _execute_pg(self, conn, sql: str, params=None):
        """执行 PG 查询并计数"""
        inc_pg_query()
        return conn.execute(sql, params) if params is not None else conn.execute(sql)

    def _table_exists(self, table: str) -> bool:
        """Check whether a table exists in market_data schema (cached)."""
        if table in self._table_exists_cache:
            return self._table_exists_cache[table]
        try:
            with self._conn() as conn:
                # to_regclass returns NULL if not found.
                sql = "SELECT to_regclass(%s) AS reg"
                row = self._execute_pg(conn, sql, (f"market_data.{table}",)).fetchone()
                ok = bool(row and row.get("reg"))
        except Exception:
            ok = False
        self._table_exists_cache[table] = ok
        return ok

    @staticmethod
    def _resample_1m_to_interval(df_1m: pd.DataFrame, interval: str) -> pd.DataFrame:
        """Resample a 1m OHLCV DataFrame to the requested interval."""
        rule_map = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}
        rule = rule_map.get(interval)
        if not rule or df_1m is None or df_1m.empty:
            return pd.DataFrame()

        df = df_1m.copy()
        # Ensure index is datetime-like for resample.
        if not isinstance(df.index, pd.DatetimeIndex):
            return pd.DataFrame()

        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "quote_volume": "sum",
            "trade_count": "sum",
            "taker_buy_volume": "sum",
            "taker_buy_quote_volume": "sum",
        }
        # Keep only columns we know how to aggregate.
        cols = [c for c in agg.keys() if c in df.columns]
        if not cols:
            return pd.DataFrame()
        agg2 = {c: agg[c] for c in cols}

        out = df[cols].resample(rule).agg(agg2)
        out = out.dropna(subset=[c for c in ("open", "high", "low", "close") if c in out.columns], how="any")
        return out

    def _get_klines_1m_direct(
        self, symbols: Sequence[str], limit: int, exchange: str
    ) -> Dict[str, pd.DataFrame]:
        """Fetch 1m candles directly (no table existence logic)."""
        if not symbols:
            return {}
        table = "candles_1m"
        symbols_list = list(symbols)

        minutes = 1 * limit * 2
        sql = f"""
            WITH ranked AS (
                SELECT symbol, bucket_ts, open, high, low, close, volume,
                       quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bucket_ts DESC) as rn
                FROM market_data.{table}
                WHERE symbol = ANY(%s) AND exchange = %s AND bucket_ts > NOW() - INTERVAL '{minutes} minutes'
            )
            SELECT symbol, bucket_ts, open, high, low, close, volume,
                   quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume
            FROM ranked WHERE rn <= %s
            ORDER BY symbol, bucket_ts ASC
        """
        result: Dict[str, pd.DataFrame] = {}
        with self._conn() as conn:
            rows = self._execute_pg(conn, sql, (symbols_list, exchange, limit)).fetchall()
            if rows:
                from itertools import groupby

                for symbol, group in groupby(rows, key=lambda x: x["symbol"]):
                    row_list = list(group)
                    if row_list:
                        result[symbol] = self._rows_to_df(row_list)
        return result

    def get_klines(self, symbols: Sequence[str], interval: str, limit: int = 300, exchange: str = None) -> Dict[str, pd.DataFrame]:
        """批量获取 K 线数据 - 并行查询"""
        exchange = exchange or config.exchange
        if not symbols:
            return {}

        table = f"candles_{interval}"
        symbols_list = list(symbols)

        # If the requested candles table doesn't exist, resample from candles_1m to keep the service usable
        # without requiring DB schema changes (continuous aggregates).
        if interval != "1m" and not self._table_exists(table):
            if not self._table_exists("candles_1m"):
                return {}
            interval_minutes = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
            mult = interval_minutes.get(interval)
            if not mult:
                return {}
            base_limit = limit * mult + mult * 5  # small padding to avoid partial buckets
            raw_1m = self._get_klines_1m_direct(symbols_list, base_limit, exchange)
            out: Dict[str, pd.DataFrame] = {}
            for sym, df1m in raw_1m.items():
                rs = self._resample_1m_to_interval(df1m, interval)
                if rs is not None and not rs.empty:
                    out[sym] = rs.tail(limit)
            return out

        # 根据周期计算时间范围，避免扫描全部分区
        interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
        minutes = interval_minutes.get(interval, 5) * limit * 2

        # 对于大量币种，使用并行单币种查询更快
        if len(symbols_list) > 50:
            return self._get_klines_parallel(symbols_list, interval, limit, exchange)

        # 小批量使用窗口函数
        sql = f"""
            WITH ranked AS (
                SELECT symbol, bucket_ts, open, high, low, close, volume,
                       quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY bucket_ts DESC) as rn
                FROM market_data.{table}
                WHERE symbol = ANY(%s) AND exchange = %s AND bucket_ts > NOW() - INTERVAL '{minutes} minutes'
            )
            SELECT symbol, bucket_ts, open, high, low, close, volume,
                   quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume
            FROM ranked WHERE rn <= %s
            ORDER BY symbol, bucket_ts ASC
        """

        result = {}
        try:
            with self._conn() as conn:
                rows = self._execute_pg(conn, sql, (symbols_list, exchange, limit)).fetchall()
                if rows:
                    from itertools import groupby
                    for symbol, group in groupby(rows, key=lambda x: x['symbol']):
                        row_list = list(group)
                        if row_list:
                            result[symbol] = self._rows_to_df(row_list)
        except Exception as e:
            LOG.warning(f"批量查询失败，回退并行查询: {e}")
            result = self._get_klines_parallel(symbols_list, interval, limit, exchange)

        return result

    def _get_klines_parallel(self, symbols: Sequence[str], interval: str, limit: int, exchange: str) -> Dict[str, pd.DataFrame]:
        """并行查询多币种"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        result = {}
        table = f"candles_{interval}"

        # 根据周期计算时间范围，避免扫描全部分区
        interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
        minutes = interval_minutes.get(interval, 5) * limit * 2  # 2倍余量

        def fetch_one(symbol: str):
            try:
                with self.pool.connection() as conn:
                    sql = f"""
                        SELECT bucket_ts, open, high, low, close, volume, 
                               quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume
                        FROM market_data.{table}
                        WHERE symbol = %s AND exchange = %s AND bucket_ts > NOW() - INTERVAL '{minutes} minutes'
                        ORDER BY bucket_ts DESC
                        LIMIT %s
                    """
                    rows = self._execute_pg(conn, sql, (symbol, exchange, limit)).fetchall()
                    if rows:
                        return symbol, self._rows_to_df(list(reversed(rows)))
            except Exception:
                pass
            return symbol, None

        workers = min(self._pool_size - 1, 8)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_one, s) for s in symbols]
            for future in as_completed(futures):
                sym, df = future.result()
                if df is not None:
                    result[sym] = df

        return result

    def get_klines_multi_interval(self, symbols: Sequence[str], intervals: Sequence[str], limit: int = 300, exchange: str = None) -> Dict[str, Dict[str, pd.DataFrame]]:
        """多周期并行获取数据"""
        exchange = exchange or config.exchange
        if not symbols or not intervals:
            return {}

        result = {}

        # 并行查询所有周期
        workers = min(len(intervals), self._pool_size - 1, 7)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.get_klines, symbols, iv, limit, exchange): iv
                for iv in intervals
            }
            for future in as_completed(futures):
                iv = futures[future]
                try:
                    result[iv] = future.result()
                except Exception as e:
                    LOG.error(f"[{iv}] 查询失败: {e}")
                    result[iv] = {}

        return result

    def _get_klines_fallback(self, symbols: Sequence[str], interval: str, limit: int, exchange: str) -> Dict[str, pd.DataFrame]:
        """回退方案：逐个查询"""
        result = {}
        table = f"candles_{interval}"

        with self._conn() as conn:
            for symbol in symbols:
                sql = f"""
                    SELECT bucket_ts, open, high, low, close, volume, 
                           quote_volume, trade_count, taker_buy_volume, taker_buy_quote_volume
                    FROM market_data.{table}
                    WHERE symbol = %s AND exchange = %s
                    ORDER BY bucket_ts DESC
                    LIMIT %s
                """
                try:
                    rows = self._execute_pg(conn, sql, (symbol, exchange, limit)).fetchall()
                except Exception:
                    continue

                if rows:
                    result[symbol] = self._rows_to_df(list(reversed(rows)))

        return result

    def _rows_to_df(self, rows: list) -> pd.DataFrame:
        """将行数据转换为 DataFrame"""
        df = pd.DataFrame([dict(r) for r in rows])
        if "symbol" in df.columns:
            df.drop(columns=["symbol"], inplace=True)
        ts_index = pd.to_datetime(df["bucket_ts"], errors="coerce", utc=True)
        df.set_index(pd.DatetimeIndex(ts_index), inplace=True)
        df.drop(columns=["bucket_ts"], inplace=True)
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def get_symbols(self, exchange: str = None, interval: str = "1m") -> List[str]:
        """获取交易所所有交易对"""
        exchange = exchange or config.exchange
        with self._conn() as conn:
            table = f"candles_{interval}"
            if not self._table_exists(table):
                table = "candles_1m"
            sql = f"SELECT DISTINCT symbol FROM market_data.{table} WHERE exchange = %s"
            return [r["symbol"] for r in self._execute_pg(conn, sql, (exchange,)).fetchall()]

    def get_latest_ts(self, interval: str, exchange: str = None):
        """获取某周期最新 K 线时间戳"""
        exchange = exchange or config.exchange
        table = f"candles_{interval}"
        if not self._table_exists(table):
            table = "candles_1m"
        try:
            with self._conn() as conn:
                sql = f"SELECT MAX(bucket_ts) FROM market_data.{table} WHERE exchange = %s"
                row = self._execute_pg(conn, sql, (exchange,)).fetchone()
                if row and row["max"]:
                    return row["max"]
        except Exception:
            pass
        return None

    def close(self):
        """关闭连接池"""
        if self._pool:
            self._pool.close()
            self._pool = None


class DataWriter:
    """将指标结果写入 SQLite（优化版）"""

    def __init__(self, sqlite_path: Path = None):
        self.sqlite_path = sqlite_path or config.sqlite_path
        self._conn = None
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        """获取或创建连接"""
        if self._conn is None:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
            self._conn.execute("PRAGMA auto_vacuum=FULL")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=10000")
        return self._conn

    def write(self, table: str, df: pd.DataFrame, interval: str = None):
        """写入单个表 - 批量 INSERT"""
        with self._lock:
            conn = self._get_conn()
            self._write_table(conn, table, df)
            inc_sqlite_commit()
            conn.commit()

    def _write_table(self, conn, table: str, df: pd.DataFrame):
        """写入单表 - 复用逻辑，便于批量事务"""
        if df.empty:
            return

        # 检查表是否存在及列是否匹配
        try:
            existing_cols = [c[1] for c in conn.execute(f'PRAGMA table_info([{table}])').fetchall()]
        except Exception:
            existing_cols = []

        df_cols = list(df.columns)
        is_new_table = not existing_cols

        if existing_cols:
            # 对齐列：缺失的补 None，多余的丢弃，避免因列不匹配重建表
            missing = [c for c in existing_cols if c not in df_cols]
            for c in missing:
                df[c] = None
            df = df[existing_cols]
            df_cols = existing_cols
        else:
            # 表不存在，按当前列创建
            df.head(0).to_sql(table, conn, if_exists="replace", index=False)
            existing_cols = df_cols

        # 先删除同一 (交易对, 周期, 数据时间) 的旧数据
        # 新建表一定是空表，跳过逐行 DELETE（对历史回填/首写入可显著提速）。
        if "交易对" in df_cols and "周期" in df_cols and "数据时间" in df_cols and not is_new_table:
            # Skip expensive per-row deletes when this (symbol, interval) doesn't exist yet.
            # This is common for backfill jobs that append a new symbol into existing tables.
            keys = df[["交易对", "周期"]].drop_duplicates()
            existing_pairs: set[tuple[str, str]] = set()
            if not keys.empty:
                try:
                    check_sql = (
                        f"SELECT 1 FROM [{table}] "
                        'WHERE upper("交易对")=? AND COALESCE("周期","")=? '
                        "LIMIT 1"
                    )
                    for sym, interval in keys.itertuples(index=False, name=None):
                        sym_u = str(sym or "").strip().upper()
                        iv = str(interval or "").strip()
                        if not sym_u:
                            continue
                        if conn.execute(check_sql, (sym_u, iv)).fetchone():
                            existing_pairs.add((sym_u, iv))
                except Exception:
                    # Be conservative: if we cannot check existence, keep original behavior.
                    existing_pairs = {(str(sym or "").strip().upper(), str(iv or "").strip()) for sym, iv in keys.itertuples(index=False, name=None)}

            if existing_pairs:
                dup_rows = df[["交易对", "周期", "数据时间"]].drop_duplicates()
                if not dup_rows.empty:
                    delete_sql = f"DELETE FROM [{table}] WHERE [交易对]=? AND [周期]=? AND [数据时间]=?"
                    if len(existing_pairs) < len(keys):
                        delete_params = [
                            (sym, iv, ts)
                            for sym, iv, ts in dup_rows.itertuples(index=False, name=None)
                            if (str(sym or "").strip().upper(), str(iv or "").strip()) in existing_pairs
                        ]
                    else:
                        delete_params = list(dup_rows.itertuples(index=False, name=None))
                    if delete_params:
                        conn.executemany(delete_sql, delete_params)

        # 批量 INSERT - 列名用方括号包裹以支持特殊字符
        placeholders = ",".join(["?"] * len(df_cols))
        cols_escaped = ",".join(f"[{c}]" for c in df_cols)
        sql = f"INSERT INTO [{table}] ({cols_escaped}) VALUES ({placeholders})"
        data = list(df.itertuples(index=False, name=None))
        conn.executemany(sql, data)

        # 清理旧数据
        self._cleanup_old_data(conn, table, df)

    def _cleanup_old_data(self, conn, table: str, df: pd.DataFrame):
        """清理旧数据，保留每个币种每个周期最新N条"""
        retention_map = get_indicator_retention_map()

        if "周期" not in df.columns or "交易对" not in df.columns or "数据时间" not in df.columns:
            return

        keys = df[["交易对", "周期"]].drop_duplicates()
        if keys.empty:
            return

        params = []
        for symbol, interval in keys.itertuples(index=False, name=None):
            limit = retention_map.get(interval, 60)
            params.append((symbol, interval, symbol, interval, limit))

        try:
            # 删除超出保留数量的旧数据
            conn.executemany(f"""
                DELETE FROM [{table}]
                WHERE 交易对 = ? AND 周期 = ?
                AND 数据时间 NOT IN (
                    SELECT 数据时间 FROM [{table}]
                    WHERE 交易对 = ? AND 周期 = ?
                    ORDER BY 数据时间 DESC
                    LIMIT ?
                )
            """, params)
        except Exception:
            pass

    def write_batch(self, data: Dict[str, pd.DataFrame], interval: str = None):
        """批量写入多个表 - 单次事务，executemany 批量插入"""
        if not data:
            return

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")

                for table, df in data.items():
                    self._write_table(conn, table, df)

                inc_sqlite_commit()
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e

    def close(self):
        """关闭连接"""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


# 全局单例
reader = DataReader()
writer = DataWriter()
