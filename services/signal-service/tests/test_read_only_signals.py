"""Tests for the read-only signal query helper and CLI wrapper."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


def _create_signal_history_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
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
                source TEXT DEFAULT 'sqlite',
                extra TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO signal_history
            (timestamp, symbol, signal_type, direction, strength, message, timeframe, price, source, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "2026-03-10T10:00:00",
                    "BTCUSDT",
                    "macd",
                    "BUY",
                    88,
                    "btc buy",
                    "1m",
                    82000.0,
                    "sqlite",
                    "{}",
                ),
                (
                    "2026-03-10T09:00:00",
                    "BTCUSDT",
                    "rsi",
                    "SELL",
                    77,
                    "btc sell",
                    "1h",
                    81800.0,
                    "pg",
                    "{}",
                ),
                (
                    "2026-03-10T08:30:00",
                    "NVDA",
                    "price_surge",
                    "BUY",
                    91,
                    "nvda buy",
                    "1m",
                    180.5,
                    "pg",
                    "{}",
                ),
            ],
        )


def _load_tradecat_get_signals_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "scripts" / "tradecat_get_signals.py"
    spec = importlib.util.spec_from_file_location("tradecat_get_signals_script", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_fetch_recent_signals_filters_symbol_timeframe_and_limit(tmp_path) -> None:
    from src.storage.read_only import fetch_recent_signals

    db_path = tmp_path / "signal_history.db"
    _create_signal_history_db(db_path)

    rows = fetch_recent_signals(
        db_path=db_path,
        symbol="btc_usdt",
        timeframe="1m",
        limit=1,
    )

    assert len(rows) == 1
    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].timeframe == "1m"
    assert rows[0].signal_type == "macd"
    assert rows[0].provider == "sqlite"


def test_tradecat_get_signals_returns_structured_empty_result(tmp_path, capsys) -> None:
    module = _load_tradecat_get_signals_module()
    db_path = tmp_path / "signal_history.db"
    _create_signal_history_db(db_path)

    rc = module.main(
        [
            "--db-path",
            str(db_path),
            "--symbol",
            "ETHUSDT",
            "--timeframe",
            "1m",
            "--limit",
            "5",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["tool"] == "tradecat_get_signals"
    assert payload["request"] == {"symbol": "ETHUSDT", "timeframe": "1m", "limit": 5}
    assert payload["source"]["available"] is True
    assert payload["data"] == []
    assert payload["error"] is None


def test_tradecat_get_signals_returns_structured_error_for_missing_source(tmp_path, capsys) -> None:
    module = _load_tradecat_get_signals_module()
    missing_db = tmp_path / "missing.db"

    rc = module.main(["--db-path", str(missing_db), "--limit", "3"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["tool"] == "tradecat_get_signals"
    assert payload["request"] == {"symbol": None, "timeframe": None, "limit": 3}
    assert payload["source"]["available"] is False
    assert payload["data"] == []
    assert payload["error"]["code"] == "source_unavailable"


def test_tradecat_get_signals_returns_structured_invalid_request_with_original_context(tmp_path, capsys) -> None:
    module = _load_tradecat_get_signals_module()
    db_path = tmp_path / "signal_history.db"
    _create_signal_history_db(db_path)

    rc = module.main(["--db-path", str(db_path), "--symbol", "BTCUSDT", "--limit", "0"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["request"] == {"symbol": "BTCUSDT", "timeframe": None, "limit": 0}
    assert payload["source"]["db_path"] == str(db_path.resolve())
    assert payload["error"]["code"] == "invalid_request"
