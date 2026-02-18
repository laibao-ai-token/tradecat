"""Tests for SQLite full-rule replay backtest mode."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.rules.base import ConditionType, SignalRule


def _mk_db(path: Path, *, with_volume: bool) -> None:
    conn = sqlite3.connect(path)
    try:
        if with_volume:
            conn.execute(
                'CREATE TABLE "demo_table" ('
                '"交易对" TEXT, "周期" TEXT, "数据时间" TEXT, "指标值" REAL, "成交额" REAL, "当前价格" REAL'
                ')' 
            )
            rows = [
                ("BTCUSDT", "1m", "2026-01-01 00:00:00", 8.0, 200000.0, 100.0),
                ("BTCUSDT", "1m", "2026-01-01 00:01:00", 12.0, 200000.0, 101.0),
                ("BTCUSDT", "1m", "2026-01-01 00:02:00", 13.0, 200000.0, 102.0),
            ]
            conn.executemany('INSERT INTO "demo_table" VALUES (?, ?, ?, ?, ?, ?)', rows)
        else:
            conn.execute(
                'CREATE TABLE "demo_table" ('
                '"交易对" TEXT, "周期" TEXT, "数据时间" TEXT, "指标值" REAL, "当前价格" REAL'
                ')' 
            )
            rows = [
                ("BTCUSDT", "1m", "2026-01-01 00:00:00", 8.0, 100.0),
                ("BTCUSDT", "1m", "2026-01-01 00:01:00", 12.0, 101.0),
            ]
            conn.executemany('INSERT INTO "demo_table" VALUES (?, ?, ?, ?, ?)', rows)
        conn.commit()
    finally:
        conn.close()


def test_replay_signals_from_rules_threshold_cross(monkeypatch, tmp_path: Path) -> None:
    from src.backtest.rule_replay import replay_signals_from_rules

    db = tmp_path / "demo.db"
    _mk_db(db, with_volume=True)

    rule = SignalRule(
        name="demo_cross_up",
        table="demo_table",
        category="unit",
        subcategory="unit",
        direction="BUY",
        strength=66,
        timeframes=["1m"],
        cooldown=0,
        condition_type=ConditionType.THRESHOLD_CROSS_UP,
        condition_config={"field": "指标值", "threshold": 10.0},
    )

    monkeypatch.setattr("src.backtest.rule_replay.ALL_RULES", [rule], raising=False)
    monkeypatch.setattr("src.backtest.rule_replay.RULES_BY_TABLE", {"demo_table": [rule]}, raising=False)

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc)
    events, stats = replay_signals_from_rules(
        str(db),
        symbols=["BTCUSDT"],
        start=start,
        end=end,
        preferred_timeframe="1m",
    )

    assert stats.table_count == 1
    assert stats.row_count >= 2
    assert len(events) == 1
    assert events[0].direction == "BUY"
    assert events[0].source == "offline_rule_replay"
    assert events[0].timeframe == "1m"
    assert events[0].symbol == "BTCUSDT"


def test_replay_signals_from_rules_works_without_volume_column(monkeypatch, tmp_path: Path) -> None:
    from src.backtest.rule_replay import replay_signals_from_rules

    db = tmp_path / "demo_no_vol.db"
    _mk_db(db, with_volume=False)

    rule = SignalRule(
        name="demo_cross_up_no_vol",
        table="demo_table",
        category="unit",
        subcategory="unit",
        direction="BUY",
        strength=60,
        timeframes=["1m"],
        cooldown=0,
        min_volume=100000,
        condition_type=ConditionType.THRESHOLD_CROSS_UP,
        condition_config={"field": "指标值", "threshold": 10.0},
    )

    monkeypatch.setattr("src.backtest.rule_replay.ALL_RULES", [rule], raising=False)
    monkeypatch.setattr("src.backtest.rule_replay.RULES_BY_TABLE", {"demo_table": [rule]}, raising=False)

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
    events, _stats = replay_signals_from_rules(
        str(db),
        symbols=["BTCUSDT"],
        start=start,
        end=end,
        preferred_timeframe="1m",
    )

    # Missing volume field should not hard-block the replay.
    assert len(events) == 1
    assert events[0].signal_type == "demo_cross_up_no_vol"


def test_replay_signals_from_rules_uses_preferred_for_default_rule_timeframes(monkeypatch, tmp_path: Path) -> None:
    from src.backtest.rule_replay import replay_signals_from_rules

    db = tmp_path / "demo_pref_tf.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            'CREATE TABLE "demo_table" ('
            '"交易对" TEXT, "周期" TEXT, "数据时间" TEXT, "指标值" REAL, "成交额" REAL, "当前价格" REAL'
            ')'
        )
        rows = [
            ("BTCUSDT", "1m", "2026-01-01 00:00:00", 8.0, 200000.0, 100.0),
            ("BTCUSDT", "1m", "2026-01-01 00:01:00", 12.0, 200000.0, 101.0),
            ("BTCUSDT", "1h", "2026-01-01 00:00:00", 8.0, 200000.0, 100.0),
            ("BTCUSDT", "1h", "2026-01-01 01:00:00", 12.0, 200000.0, 102.0),
        ]
        conn.executemany('INSERT INTO "demo_table" VALUES (?, ?, ?, ?, ?, ?)', rows)
        conn.commit()
    finally:
        conn.close()

    # Keep default timeframes from dataclass: [1h, 4h, 1d].
    rule = SignalRule(
        name="demo_default_tfs",
        table="demo_table",
        category="unit",
        subcategory="unit",
        direction="BUY",
        strength=66,
        cooldown=0,
        condition_type=ConditionType.THRESHOLD_CROSS_UP,
        condition_config={"field": "指标值", "threshold": 10.0},
    )

    monkeypatch.setattr("src.backtest.rule_replay.ALL_RULES", [rule], raising=False)
    monkeypatch.setattr("src.backtest.rule_replay.RULES_BY_TABLE", {"demo_table": [rule]}, raising=False)

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    events, _stats = replay_signals_from_rules(
        str(db),
        symbols=["BTCUSDT"],
        start=start,
        end=end,
        preferred_timeframe="1m",
    )

    assert len(events) == 1
    assert events[0].timeframe == "1m"


def test_replay_signals_from_rules_normalizes_timeframe_alias(monkeypatch, tmp_path: Path) -> None:
    from src.backtest.rule_replay import replay_signals_from_rules

    db = tmp_path / "demo_tf_alias.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            'CREATE TABLE "demo_table" ('
            '"交易对" TEXT, "周期" TEXT, "数据时间" TEXT, "指标值" REAL, "成交额" REAL, "当前价格" REAL'
            ')'
        )
        rows = [
            ("BTCUSDT", "60m", "2026-01-01 00:00:00", 8.0, 200000.0, 100.0),
            ("BTCUSDT", "60m", "2026-01-01 01:00:00", 12.0, 200000.0, 101.0),
        ]
        conn.executemany('INSERT INTO "demo_table" VALUES (?, ?, ?, ?, ?, ?)', rows)
        conn.commit()
    finally:
        conn.close()

    rule = SignalRule(
        name="demo_alias_1h",
        table="demo_table",
        category="unit",
        subcategory="unit",
        direction="BUY",
        strength=66,
        timeframes=["1h"],
        cooldown=0,
        condition_type=ConditionType.THRESHOLD_CROSS_UP,
        condition_config={"field": "指标值", "threshold": 10.0},
    )

    monkeypatch.setattr("src.backtest.rule_replay.ALL_RULES", [rule], raising=False)
    monkeypatch.setattr("src.backtest.rule_replay.RULES_BY_TABLE", {"demo_table": [rule]}, raising=False)

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    events, _stats = replay_signals_from_rules(
        str(db),
        symbols=["BTCUSDT"],
        start=start,
        end=end,
        preferred_timeframe="",
    )

    assert len(events) == 1
    assert events[0].timeframe == "1h"


def test_replay_signals_from_rules_strict_timeframe_filter_without_overlap(monkeypatch, tmp_path: Path) -> None:
    from src.backtest.rule_replay import replay_signals_from_rules

    db = tmp_path / "demo_no_overlap.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            'CREATE TABLE "demo_table" ('
            '"交易对" TEXT, "周期" TEXT, "数据时间" TEXT, "指标值" REAL, "成交额" REAL, "当前价格" REAL'
            ')'
        )
        rows = [
            ("BTCUSDT", "1h", "2026-01-01 00:00:00", 8.0, 200000.0, 100.0),
            ("BTCUSDT", "1h", "2026-01-01 01:00:00", 12.0, 200000.0, 101.0),
        ]
        conn.executemany('INSERT INTO "demo_table" VALUES (?, ?, ?, ?, ?, ?)', rows)
        conn.commit()
    finally:
        conn.close()

    rule = SignalRule(
        name="demo_only_1m",
        table="demo_table",
        category="unit",
        subcategory="unit",
        direction="BUY",
        strength=66,
        timeframes=["1m"],
        cooldown=0,
        condition_type=ConditionType.THRESHOLD_CROSS_UP,
        condition_config={"field": "指标值", "threshold": 10.0},
    )

    monkeypatch.setattr("src.backtest.rule_replay.ALL_RULES", [rule], raising=False)
    monkeypatch.setattr("src.backtest.rule_replay.RULES_BY_TABLE", {"demo_table": [rule]}, raising=False)

    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    events, stats = replay_signals_from_rules(
        str(db),
        symbols=["BTCUSDT"],
        start=start,
        end=end,
        preferred_timeframe="1m",
    )

    assert events == []
    counter = stats.rule_counters["demo_only_1m"]
    assert counter.evaluated == 1
    assert counter.timeframe_filtered == 1
    assert counter.condition_failed == 0

    profile = stats.rule_timeframe_profiles["demo_only_1m"]
    assert profile.configured_timeframes == ("1m",)
    assert profile.observed_timeframes == ("1h",)
    assert profile.overlap_timeframes == ()
