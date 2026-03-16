"""
信号历史记录测试
"""
import json
from datetime import datetime


def test_history_save_signal_event(tmp_path, sample_signal_event):
    """SignalEvent 能正常落库"""
    from src.storage.history import SignalHistory

    db_path = tmp_path / "history.db"
    history = SignalHistory(db_path=str(db_path))

    row_id = history.save(sample_signal_event, source="pg")
    assert row_id > 0

    records = history.get_recent(limit=1)
    assert records
    record = records[0]

    assert record["message"] == sample_signal_event.message_key
    extra = json.loads(record["extra"])
    assert extra["message_key"] == sample_signal_event.message_key
    assert extra["message_params"] == sample_signal_event.message_params


def test_history_save_pg_signal(tmp_path):
    """PGSignal 能正常落库"""
    from src.engines.pg_engine import PGSignal
    from src.storage.history import SignalHistory

    db_path = tmp_path / "history.db"
    history = SignalHistory(db_path=str(db_path))

    signal = PGSignal(
        symbol="BTCUSDT",
        signal_type="price_surge",
        direction="BUY",
        strength=80,
        message_key="signal.pg.msg.price_surge",
        message_params={"pct": "3.5"},
        timestamp=datetime.now(),
        timeframe="5m",
        price=50000.0,
        extra={"change_pct": 3.5},
    )

    row_id = history.save(signal, source="pg")
    assert row_id > 0

    records = history.get_recent(limit=1)
    assert records
    record = records[0]
    assert record["signal_type"] == "price_surge"
    extra = json.loads(record["extra"])
    assert extra["message_key"] == "signal.pg.msg.price_surge"
    assert extra["message_params"]["pct"] == "3.5"


def test_history_save_signal_event_persists_rule_identity(tmp_path):
    """SQLite SignalEvent 落库时保留 rule_id / rule_name 供展示兼容。"""
    from src.events.types import SignalEvent
    from src.storage.history import SignalHistory

    db_path = tmp_path / "history.db"
    history = SignalHistory(db_path=str(db_path))

    signal = SignalEvent(
        symbol="BTCUSDT",
        signal_type="volume.taker.demo123456",
        direction="BUY",
        strength=70,
        message_key="signal.volume.taker",
        timeframe="1m",
        price=100.0,
        rule_id="volume.taker.demo123456",
        rule_name="主动买盘极端",
    )

    row_id = history.save(signal, source="sqlite")
    assert row_id > 0

    record = history.get_recent(limit=1)[0]
    assert record["signal_type"] == "volume.taker.demo123456"
    extra = json.loads(record["extra"])
    assert extra["rule_id"] == "volume.taker.demo123456"
    assert extra["rule_name"] == "主动买盘极端"
