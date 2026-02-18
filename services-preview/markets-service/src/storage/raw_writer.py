"""Raw 表写入器（TimescaleDB@raw schema）

将 1m K 线与 5m 指标写入:
- raw.crypto_kline_1m
- raw.crypto_metrics_5m
- raw.{us,cn,hk}_equity_1m
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

from psycopg import sql
from psycopg_pool import ConnectionPool

from config import settings
from .timescale import get_shared_pool


class TimescaleRawWriter:
    """写入 raw.* 表"""

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.database_url
        if self.db_url == settings.database_url:
            self.pool = get_shared_pool()
        else:
            self.pool = ConnectionPool(self.db_url, min_size=1, max_size=5, timeout=30.0)

    def upsert_kline_1m(self, rows: Sequence[dict], ingest_batch_id: int, source: str = "binance_ws") -> int:
        """批量写入/更新 1m K 线到 raw.crypto_kline_1m"""
        if not rows:
            return 0

        cols = [
            "exchange", "symbol", "open_time", "close_time",
            "open", "high", "low", "close", "volume",
            "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume",
            "is_closed", "source", "ingest_batch_id"
        ]

        values = []
        for r in rows:
            open_time = r["bucket_ts"] if isinstance(r["bucket_ts"], datetime) else datetime.fromtimestamp(r["bucket_ts"], tz=r.get("tz"))
            close_time = r.get("close_time") or open_time + timedelta(minutes=1)
            values.append((
                r["exchange"],
                r["symbol"],
                open_time,
                close_time,
                r["open"],
                r["high"],
                r["low"],
                r["close"],
                r.get("volume"),
                r.get("quote_volume"),
                r.get("trades"),
                r.get("taker_buy_volume"),
                r.get("taker_buy_quote_volume"),
                bool(r.get("is_closed", True)),
                r.get("source", source),
                ingest_batch_id,
            ))

        insert_sql = sql.SQL("""
            INSERT INTO {table} ({cols})
            VALUES ({placeholders})
            ON CONFLICT (exchange, symbol, open_time) DO UPDATE SET
                close_time = EXCLUDED.close_time,
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                quote_volume = EXCLUDED.quote_volume,
                trades = EXCLUDED.trades,
                taker_buy_volume = EXCLUDED.taker_buy_volume,
                taker_buy_quote_volume = EXCLUDED.taker_buy_quote_volume,
                is_closed = EXCLUDED.is_closed,
                source = EXCLUDED.source,
                updated_at = NOW();
        """).format(
            table=sql.Identifier(settings.raw_schema, "crypto_kline_1m"),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            placeholders=sql.SQL(", ").join(sql.Placeholder() * len(cols))
        )

        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(insert_sql.as_string(cur), values)
            conn.commit()
        return len(values)

    def upsert_metrics_5m(self, rows: Sequence[dict], ingest_batch_id: int, source: str = "binance_api") -> int:
        """批量写入/更新 5m 期货指标到 raw.crypto_metrics_5m"""
        if not rows:
            return 0

        cols = [
            "exchange", "symbol", "timestamp",
            "open_interest", "open_interest_value",
            "long_short_ratio", "top_long_short_ratio", "taker_buy_sell_ratio",
            "source", "ingest_batch_id"
        ]
        values = []
        for r in rows:
            ts = r["create_time"] if isinstance(r["create_time"], datetime) else datetime.fromtimestamp(r["create_time"], tz=r.get("tz"))
            values.append((
                r.get("exchange", "binance"),
                r["symbol"],
                ts,
                r.get("sumOpenInterest"),
                r.get("sumOpenInterestValue"),
                r.get("long_short_ratio"),
                r.get("topAccountLongShortRatio"),
                r.get("takerBuySellRatio"),
                r.get("source", source),
                ingest_batch_id,
            ))

        insert_sql = sql.SQL("""
            INSERT INTO {table} ({cols})
            VALUES ({placeholders})
            ON CONFLICT (exchange, symbol, timestamp) DO UPDATE SET
                open_interest = EXCLUDED.open_interest,
                open_interest_value = EXCLUDED.open_interest_value,
                long_short_ratio = EXCLUDED.long_short_ratio,
                top_long_short_ratio = EXCLUDED.top_long_short_ratio,
                taker_buy_sell_ratio = EXCLUDED.taker_buy_sell_ratio,
                source = EXCLUDED.source,
                updated_at = NOW();
        """).format(
            table=sql.Identifier(settings.raw_schema, "crypto_metrics_5m"),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            placeholders=sql.SQL(", ").join(sql.Placeholder() * len(cols))
        )

        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(insert_sql.as_string(cur), values)
            conn.commit()
        return len(values)

    def upsert_equity_1m(self, market: str, rows: Sequence[dict], ingest_batch_id: int, source: str) -> int:
        """批量写入/更新 1m 分钟线到 raw.{market}_equity_1m.

        market:
          - us_stock -> raw.us_equity_1m
          - cn_stock -> raw.cn_equity_1m
          - hk_stock -> raw.hk_equity_1m
        """
        if not rows:
            return 0

        market_to_table = {
            "us_stock": "us_equity_1m",
            "cn_stock": "cn_equity_1m",
            "hk_stock": "hk_equity_1m",
        }
        table_name = market_to_table.get(market)
        if not table_name:
            raise ValueError(f"不支持的 market: {market}")

        cols = [
            "exchange", "symbol", "open_time", "close_time",
            "open", "high", "low", "close", "volume", "amount",
            "source", "ingest_batch_id", "source_event_time",
        ]

        values = []
        for r in rows:
            open_time = r["open_time"]
            if not isinstance(open_time, datetime):
                open_time = datetime.fromtimestamp(open_time, tz=r.get("tz"))
            close_time = r.get("close_time")
            if close_time and not isinstance(close_time, datetime):
                close_time = datetime.fromtimestamp(close_time, tz=r.get("tz"))
            if close_time is None:
                close_time = open_time + timedelta(minutes=1)

            values.append((
                r["exchange"],
                r["symbol"],
                open_time,
                close_time,
                r["open"],
                r["high"],
                r["low"],
                r["close"],
                r.get("volume", 0),
                r.get("amount"),
                r.get("source", source),
                ingest_batch_id,
                r.get("source_event_time"),
            ))

        insert_sql = sql.SQL("""
            INSERT INTO {table} ({cols})
            VALUES ({placeholders})
            ON CONFLICT (exchange, symbol, open_time) DO UPDATE SET
                close_time = EXCLUDED.close_time,
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                amount = EXCLUDED.amount,
                source = EXCLUDED.source,
                source_event_time = EXCLUDED.source_event_time,
                updated_at = NOW();
        """).format(
            table=sql.Identifier(settings.raw_schema, table_name),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            placeholders=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        )

        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(insert_sql.as_string(cur), values)
            conn.commit()
        return len(values)
