from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.backtest.data_loader import load_signals_from_sqlite
from src.rules import ALL_RULES


def test_load_signals_from_sqlite_resolves_legacy_name_with_message_scope(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                strength INTEGER NOT NULL,
                message TEXT,
                timeframe TEXT,
                price REAL,
                source TEXT,
                extra TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO signal_history
            (timestamp, symbol, signal_type, direction, strength, message, timeframe, price, source, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-01-01T00:01:00+00:00",
                "BTCUSDT",
                "主动买盘极端",
                "BUY",
                80,
                "legacy row",
                "1m",
                100.0,
                "sqlite",
                json.dumps({"message_key": "signal.futures.sentiment"}, ensure_ascii=True),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    events = load_signals_from_sqlite(str(db), ["BTCUSDT"], start, end, timeframe="1m")

    assert len(events) == 1
    target = next(rule for rule in ALL_RULES if rule.name == "主动买盘极端" and rule.category == "futures")
    assert events[0].signal_type == target.rule_id
    assert events[0].rule_id == target.rule_id
    assert events[0].rule_name == target.name
