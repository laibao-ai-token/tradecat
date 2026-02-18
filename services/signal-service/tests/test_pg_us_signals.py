"""PG 美股分钟信号测试"""

from datetime import datetime, timedelta


def test_normalize_us_symbol():
    from src.engines.pg_engine import _normalize_us_symbol

    assert _normalize_us_symbol("nvda") == "NVDA"
    assert _normalize_us_symbol("AAPL.US") == "AAPL"
    assert _normalize_us_symbol(" brk.b ") == "BRK.B"


def test_get_us_symbols_from_env(monkeypatch):
    import src.engines.pg_engine as pg_engine

    monkeypatch.setenv("SIGNAL_US_SYMBOLS", "NVDA,AAPL.US,BRK.B,bad$")
    symbols = pg_engine._get_us_symbols()

    assert symbols == ["NVDA", "AAPL", "BRK.B"]


def test_pg_engine_generates_us_signal(monkeypatch):
    import src.engines.pg_engine as pg_engine
    from src.events.publisher import SignalPublisher

    class DummyStorage:
        def __init__(self):
            self.data = {}

        def load_all(self):
            return {}

        def set(self, key, ts):
            self.data[key] = ts

    class DummyHistory:
        pass

    monkeypatch.setenv("SIGNAL_US_SYMBOLS", "NVDA")
    monkeypatch.setenv("SIGNAL_US_PRICE_THRESHOLD_PCT", "0.8")
    monkeypatch.setenv("SIGNAL_US_VOL_SPIKE_MULTIPLIER", "10")
    monkeypatch.setattr(pg_engine, "get_cooldown_storage", lambda: DummyStorage())
    monkeypatch.setattr(pg_engine, "get_history", lambda: DummyHistory())

    SignalPublisher.clear()
    engine = pg_engine.PGSignalEngine(db_url="postgresql://invalid", symbols=["BTCUSDT"])

    now = datetime.now()
    row1 = {
        "symbol": "NVDA",
        "bucket_ts": now - timedelta(seconds=1),
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
        "volume": 10.0,
        "quote_volume": 1000.0,
    }
    row2 = {
        "symbol": "NVDA",
        "bucket_ts": now,
        "open": 100.0,
        "high": 101.5,
        "low": 99.8,
        "close": 101.0,
        "volume": 12.0,
        "quote_volume": 1000.0,
    }

    monkeypatch.setattr(engine, "_fetch_latest_candles", lambda: {})
    monkeypatch.setattr(engine, "_fetch_latest_metrics", lambda: {})
    monkeypatch.setattr(engine, "_fetch_latest_us_equity_candles", lambda: {"NVDA": row1})

    # 第一轮仅建立基线
    assert engine.check_signals() == []

    monkeypatch.setattr(engine, "_fetch_latest_us_equity_candles", lambda: {"NVDA": row2})
    signals = engine.check_signals()

    assert signals
    assert any(s.symbol == "NVDA" for s in signals)
    assert any((s.extra or {}).get("market") == "us_stock" for s in signals)
    assert any(k.startswith("pg:us:NVDA_") for k in engine.cooldowns)


def _mk_us_candle(close: float, volume: float = 1000.0, symbol: str = "NVDA") -> dict:
    return {
        "symbol": symbol,
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": volume,
    }


def test_us_ema_cross_rule_triggers_up_signal():
    from src.engines.pg_engine import PGSignalRules

    closes = [
        100.0,
        100.01,
        99.47,
        98.81,
        98.74,
        98.67,
        97.91,
        98.5,
        98.97,
        98.76,
        98.04,
        98.39,
        98.24,
        98.41,
        98.27,
        97.78,
        98.13,
        98.7,
        98.17,
        98.1,
        97.78,
        98.26,
        98.12,
        98.54,
        98.94,
        98.45,
        98.74,
        98.46,
        98.96,
        98.82,
        99.15,
        98.67,
        98.22,
        98.2,
        98.8,
        100.29,
    ]
    recent = [_mk_us_candle(close=v) for v in closes]
    signal = PGSignalRules().check_us_ema_cross(recent, fast=9, slow=21, min_spread_pct=0.01)

    assert signal is not None
    assert signal.signal_type == "us_ema_cross_up"
    assert signal.direction == "BUY"


def test_us_rsi_rebound_rule_triggers_buy_signal():
    from src.engines.pg_engine import PGSignalRules

    closes = [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85, 84, 87, 90]
    recent = [_mk_us_candle(close=float(v), volume=1200.0) for v in closes]
    signal = PGSignalRules().check_us_rsi_reversal(recent, period=14, oversold=30, overbought=70)

    assert signal is not None
    assert signal.signal_type == "us_rsi_rebound"
    assert signal.direction == "BUY"


def test_us_range_breakout_rule_triggers_with_volume_confirmation():
    from src.engines.pg_engine import PGSignalRules

    recent = []
    for idx in range(20):
        close = 100.0 + (idx % 3) * 0.05
        recent.append(
            {
                "symbol": "NVDA",
                "open": close - 0.05,
                "high": 100.2,
                "low": 99.8,
                "close": close,
                "volume": 1000.0,
            }
        )
    recent.append(
        {
            "symbol": "NVDA",
            "open": 100.2,
            "high": 101.0,
            "low": 100.1,
            "close": 100.7,
            "volume": 2500.0,
        }
    )

    signal = PGSignalRules().check_us_range_breakout(
        recent,
        lookback=20,
        vol_multiplier=1.8,
        breakout_buffer_pct=0.12,
    )

    assert signal is not None
    assert signal.signal_type == "us_range_breakout_up"
    assert signal.direction == "BUY"
