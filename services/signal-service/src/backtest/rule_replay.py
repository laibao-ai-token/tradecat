"""Offline replay based on the full SQLite rule set.

This module replays `src.rules.ALL_RULES` on historical indicator rows in
`market_data.db`, then emits a deterministic signal stream for backtests.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from ..rules import ALL_RULES, RULES_BY_TABLE, SignalRule
from .data_loader import floor_minute, normalize_symbol, parse_timestamp
from .models import SignalEvent

logger = logging.getLogger(__name__)

_TS_KEYS = ("数据时间", "更新时间", "时间", "时间戳", "timestamp", "update_time", "updated_at", "created_at", "ts")
_PRICE_KEYS = ("当前价格", "价格", "收盘价", "close", "price")
_VOL_KEYS = ("成交额", "成交额（USDT）", "成交量", "amount", "volume")
_DEFAULT_RULE_TIMEFRAMES = frozenset({"1h", "4h", "1d"})
_TF_ALIASES = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "60m": "1h",
    "1hour": "1h",
    "120m": "2h",
    "240m": "4h",
    "1day": "1d",
    "24h": "1d",
    "1440m": "1d",
}


@dataclass(frozen=True)
class RuleReplayCounter:
    evaluated: int = 0
    timeframe_filtered: int = 0
    volume_filtered: int = 0
    condition_failed: int = 0
    cooldown_blocked: int = 0
    triggered: int = 0


@dataclass(frozen=True)
class RuleTimeframeProfile:
    configured_timeframes: tuple[str, ...] = ()
    observed_timeframes: tuple[str, ...] = ()
    overlap_timeframes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleReplayStats:
    table_count: int = 0
    row_count: int = 0
    signal_count: int = 0
    rule_counters: dict[str, RuleReplayCounter] = field(default_factory=dict)
    rule_timeframe_profiles: dict[str, RuleTimeframeProfile] = field(default_factory=dict)



def _safe_float(raw: object, default: float = 0.0) -> float:
    try:
        if raw is None:
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default



def _fmt_sqlite_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")



def _extract_row_ts(row: dict) -> datetime | None:
    for key in _TS_KEYS:
        if key not in row:
            continue
        ts = parse_timestamp(row.get(key))
        if ts is not None:
            return floor_minute(ts)
    return None



def _extract_price(row: dict) -> float | None:
    for key in _PRICE_KEYS:
        if key not in row:
            continue
        value = _safe_float(row.get(key), default=float("nan"))
        if value == value:
            return value
    return None



def _extract_volume(row: dict) -> float | None:
    for key in _VOL_KEYS:
        if key not in row:
            continue
        return _safe_float(row.get(key), default=0.0)
    return None



def _normalize_tf(raw: object, fallback: str = "") -> str:
    txt = str(raw or "").strip().lower().replace(" ", "")
    if not txt:
        txt = str(fallback or "").strip().lower().replace(" ", "")
    if not txt:
        return ""
    return _TF_ALIASES.get(txt, txt)



def _resolve_rule_timeframes(rule: SignalRule, preferred_timeframe: str) -> tuple[set[str], bool]:
    base = {_normalize_tf(tf) for tf in (rule.timeframes or []) if str(tf).strip()}
    base = {x for x in base if x}

    pref_tf = _normalize_tf(preferred_timeframe)
    if pref_tf and base == _DEFAULT_RULE_TIMEFRAMES:
        # Keep backtest replay aligned with history_signal(1m) when rules still
        # use legacy default [1h,4h,1d] but runtime is minute-first.
        return {pref_tf}, True

    return base, False



def _new_counter() -> dict[str, int]:
    return {
        "evaluated": 0,
        "timeframe_filtered": 0,
        "volume_filtered": 0,
        "condition_failed": 0,
        "cooldown_blocked": 0,
        "triggered": 0,
    }



def _load_rows_for_table(
    conn: sqlite3.Connection,
    *,
    table: str,
    symbols: list[str],
    start: datetime,
    end: datetime,
    max_rows: int,
) -> list[dict]:
    rows: list[dict] = []
    params: list[object] = []

    placeholders = ",".join(["?"] * max(1, len(symbols)))
    symbol_params = list(symbols) if symbols else ["__NONE__"]

    query = (
        f'SELECT rowid, * FROM "{table}" '
        'WHERE datetime("数据时间") >= datetime(?) AND datetime("数据时间") <= datetime(?) '
        f'AND upper("交易对") IN ({placeholders}) '
        'ORDER BY upper("交易对") ASC, COALESCE("周期", "") ASC, datetime("数据时间") ASC, rowid ASC'
    )
    params.extend([_fmt_sqlite_ts(start), _fmt_sqlite_ts(end), *symbol_params])

    try:
        cur = conn.execute(query, params)
        for raw in cur.fetchall():
            rows.append(dict(raw))
        return rows
    except sqlite3.OperationalError:
        # Fallback for non-standard historical tables.
        fallback = f'SELECT rowid, * FROM "{table}" ORDER BY rowid ASC'
        if max_rows > 0:
            fallback += " LIMIT ?"
            cur = conn.execute(fallback, (int(max_rows),))
        else:
            cur = conn.execute(fallback)

        start_ts = start
        end_ts = end
        symbol_set = {normalize_symbol(s) for s in symbols}
        for raw in cur.fetchall():
            row = dict(raw)
            sym = str(row.get("交易对") or "").strip().upper()
            if symbol_set and normalize_symbol(sym) not in symbol_set:
                continue
            ts = _extract_row_ts(row)
            if ts is None:
                continue
            if ts < start_ts or ts > end_ts:
                continue
            rows.append(row)

        rows.sort(
            key=lambda item: (
                str(item.get("交易对") or "").upper(),
                str(item.get("周期") or "").lower(),
                str(item.get("数据时间") or ""),
                int(item.get("rowid") or 0),
            )
        )
        return rows



def replay_signals_from_rules(
    sqlite_path: str,
    *,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    preferred_timeframe: str = "",
    start_event_id: int = 1,
    max_rows_per_table: int = 200_000,
) -> tuple[list[SignalEvent], RuleReplayStats]:
    """Replay full SQLite rules against historical table rows.

    Signals are generated with deterministic order and cooldown handling.
    Cooldown is applied per `(rule, symbol, timeframe)` on event timestamp.
    """

    symbol_list = sorted({str(s).upper().strip() for s in symbols if str(s).strip()})
    if not symbol_list:
        return [], RuleReplayStats()

    preferred_tf = _normalize_tf(preferred_timeframe)
    active_rules = [rule for rule in ALL_RULES if rule.enabled and rule.direction in {"BUY", "SELL"}]
    rules_by_table: dict[str, list[SignalRule]] = defaultdict(list)
    rule_timeframes: dict[int, set[str]] = {}
    rule_timeframe_locked: dict[int, bool] = {}
    rule_profile_raw: dict[str, dict[str, set[str]]] = {}

    for rule in active_rules:
        rules_by_table[rule.table].append(rule)
        resolved_tfs, locked = _resolve_rule_timeframes(rule, preferred_timeframe)
        rule_timeframes[id(rule)] = resolved_tfs
        rule_timeframe_locked[id(rule)] = locked
        rule_profile_raw[str(rule.name)] = {
            "configured_timeframes": set(resolved_tfs),
            "observed_timeframes": set(),
        }

    event_id = max(1, int(start_event_id))
    events: list[SignalEvent] = []

    row_total = 0
    cooldown_last_ts: dict[str, datetime] = {}
    symbol_norm_set = {normalize_symbol(s) for s in symbol_list}
    rule_counter_raw: dict[str, dict[str, int]] = {}

    conn = sqlite3.connect(sqlite_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        for table in sorted(RULES_BY_TABLE.keys()):
            table_rules = rules_by_table.get(table, [])
            if not table_rules:
                continue

            rows = _load_rows_for_table(
                conn,
                table=table,
                symbols=symbol_list,
                start=start,
                end=end,
                max_rows=max_rows_per_table,
            )
            if not rows:
                continue

            row_total += len(rows)
            prev_by_key: dict[tuple[str, str], dict] = {}
            observed_timeframes = {_normalize_tf(item.get("周期"), preferred_timeframe) for item in rows}
            observed_timeframes = {tf for tf in observed_timeframes if tf}
            for rule in table_rules:
                profile = rule_profile_raw.setdefault(
                    str(rule.name),
                    {"configured_timeframes": set(), "observed_timeframes": set()},
                )
                profile["observed_timeframes"].update(observed_timeframes)

            for row in rows:
                symbol = str(row.get("交易对") or "").strip().upper()
                if not symbol:
                    continue
                if normalize_symbol(symbol) not in symbol_norm_set:
                    continue

                row_ts = _extract_row_ts(row)
                if row_ts is None:
                    continue

                timeframe = _normalize_tf(row.get("周期"), preferred_timeframe)
                rule_key = (symbol, timeframe)
                prev_row = prev_by_key.get(rule_key)
                if prev_row is None:
                    prev_by_key[rule_key] = row
                    continue

                volume = _extract_volume(row)

                for rule in table_rules:
                    counter = rule_counter_raw.setdefault(rule.name, _new_counter())
                    counter["evaluated"] += 1

                    rule_tfs = rule_timeframes.get(id(rule), set())
                    is_locked = rule_timeframe_locked.get(id(rule), False)
                    if rule_tfs:
                        if timeframe:
                            if timeframe not in rule_tfs:
                                counter["timeframe_filtered"] += 1
                                continue
                        elif is_locked and preferred_tf and preferred_tf not in rule_tfs:
                            counter["timeframe_filtered"] += 1
                            continue

                    # Keep compatibility with online engine behavior but don't block
                    # tables that don't contain volume columns.
                    if volume is not None and volume < _safe_float(rule.min_volume, 0.0):
                        counter["volume_filtered"] += 1
                        continue

                    if not rule.check_condition(prev_row, row):
                        counter["condition_failed"] += 1
                        continue

                    cooldown_key = f"{rule.name}_{symbol}_{timeframe}"
                    last_ts = cooldown_last_ts.get(cooldown_key)
                    if last_ts is not None:
                        if (row_ts - last_ts).total_seconds() <= max(0, int(rule.cooldown)):
                            counter["cooldown_blocked"] += 1
                            continue

                    cooldown_last_ts[cooldown_key] = row_ts
                    counter["triggered"] += 1
                    events.append(
                        SignalEvent(
                            event_id=event_id,
                            timestamp=row_ts,
                            symbol=symbol,
                            direction=str(rule.direction).upper(),
                            strength=int(rule.strength),
                            signal_type=str(rule.name),
                            timeframe=timeframe or preferred_timeframe,
                            source="offline_rule_replay",
                            price=_extract_price(row),
                        )
                    )
                    event_id += 1

                prev_by_key[rule_key] = row
    finally:
        conn.close()

    events.sort(key=lambda ev: (ev.timestamp, ev.symbol, ev.event_id))

    rule_counters = {
        name: RuleReplayCounter(**counts)
        for name, counts in sorted(
            rule_counter_raw.items(),
            key=lambda item: (-int(item[1].get("triggered", 0)), -int(item[1].get("evaluated", 0)), item[0]),
        )
    }

    rule_timeframe_profiles: dict[str, RuleTimeframeProfile] = {}
    for rule_name, profile in sorted(rule_profile_raw.items(), key=lambda item: item[0]):
        configured = tuple(
            sorted(
                {
                    str(tf).strip()
                    for tf in (profile.get("configured_timeframes") or set())
                    if str(tf).strip()
                }
            )
        )
        observed = tuple(
            sorted(
                {
                    str(tf).strip()
                    for tf in (profile.get("observed_timeframes") or set())
                    if str(tf).strip()
                }
            )
        )
        if configured:
            overlap_set = set(configured) & set(observed)
        else:
            overlap_set = set(observed)
        overlap = tuple(sorted(overlap_set))
        rule_timeframe_profiles[str(rule_name)] = RuleTimeframeProfile(
            configured_timeframes=configured,
            observed_timeframes=observed,
            overlap_timeframes=overlap,
        )

    stats = RuleReplayStats(
        table_count=sum(1 for table in rules_by_table if rules_by_table.get(table)),
        row_count=row_total,
        signal_count=len(events),
        rule_counters=rule_counters,
        rule_timeframe_profiles=rule_timeframe_profiles,
    )
    logger.info(
        "Rule replay generated %d signals from %d rows across %d tables",
        stats.signal_count,
        stats.row_count,
        stats.table_count,
    )
    return events, stats
