from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.db import fetch_recent


def _init_signal_history(db_path: Path, with_extra: bool) -> None:
    extra_col = ", extra TEXT" if with_extra else ""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            f"""
            CREATE TABLE signal_history (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                symbol TEXT,
                signal_type TEXT,
                direction TEXT,
                strength INTEGER,
                message TEXT,
                timeframe TEXT,
                price REAL,
                source TEXT
                {extra_col}
            )
            """
        )


class TestSignalDbCompatibility(unittest.TestCase):
    def test_fetch_recent_prefers_rule_name_from_extra(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "signal_history.db"
            _init_signal_history(db_path, with_extra=True)
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO signal_history
                    (id, timestamp, symbol, signal_type, direction, strength, message, timeframe, price, source, extra)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "2026-03-18T12:00:00Z",
                        "BTCUSDT",
                        "momentum.rsi.1234567890",
                        "BUY",
                        80,
                        "msg",
                        "1m",
                        60000.0,
                        "sqlite",
                        '{"rule_name":"RSI Oversold"}',
                    ),
                )

            rows = fetch_recent(str(db_path), limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].signal_type, "RSI Oversold")

    def test_fetch_recent_supports_legacy_schema_without_extra(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "signal_history_legacy.db"
            _init_signal_history(db_path, with_extra=False)
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO signal_history
                    (id, timestamp, symbol, signal_type, direction, strength, message, timeframe, price, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "2026-03-18 12:00:00",
                        "ETHUSDT",
                        "macd.cross",
                        "SELL",
                        70,
                        "msg",
                        "5m",
                        3000.0,
                        "sqlite",
                    ),
                )

            rows = fetch_recent(str(db_path), limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].signal_type, "macd.cross")


if __name__ == "__main__":
    unittest.main()
