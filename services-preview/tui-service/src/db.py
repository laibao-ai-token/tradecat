from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional


@dataclass(frozen=True)
class SignalRow:
    id: int
    timestamp: str
    symbol: str
    signal_type: str
    direction: str
    strength: int
    message: str | None
    timeframe: str | None
    price: float | None
    source: str | None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def parse_ts(ts: str) -> datetime:
    """Parse ISO-ish timestamp to naive datetime for display."""
    if not ts:
        return datetime.min
    s = ts.strip()
    # Normalize common variants.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(s)
        # Keep downstream code simple: always return naive datetime.
        if dt.tzinfo is not None:
            return dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return datetime.min


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def fetch_recent(
    db_path: str,
    limit: int = 200,
    *,
    min_id: int | None = None,
    sources: Optional[Iterable[str]] = None,
    directions: Optional[Iterable[str]] = None,
) -> list[SignalRow]:
    where = ["1=1"]
    params: list[object] = []

    if min_id is not None:
        where.append("id > ?")
        params.append(int(min_id))

    if sources:
        src_list = [s for s in sources if s]
        if src_list:
            where.append(f"source IN ({','.join(['?'] * len(src_list))})")
            params.extend(src_list)

    if directions:
        dir_list = [d for d in directions if d]
        if dir_list:
            where.append(f"direction IN ({','.join(['?'] * len(dir_list))})")
            params.extend(dir_list)

    sql = f"""
        SELECT id, timestamp, symbol, signal_type, direction, strength, message, timeframe, price, source
        FROM signal_history
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT ?
    """
    params.append(int(limit))

    try:
        with _connect(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            out.append(
                SignalRow(
                    id=_safe_int(r["id"]),
                    timestamp=str(r["timestamp"]),
                    symbol=str(r["symbol"]),
                    signal_type=str(r["signal_type"]),
                    direction=str(r["direction"]),
                    strength=_safe_int(r["strength"]),
                    message=r["message"],
                    timeframe=r["timeframe"],
                    price=r["price"],
                    source=r["source"],
                )
            )
        return out
    except Exception:
        return []


def probe(db_path: str) -> tuple[bool, str]:
    """Lightweight check to see if DB and table are readable."""
    try:
        with _connect(db_path) as conn:
            conn.execute("SELECT 1 FROM signal_history LIMIT 1").fetchone()
        return True, "ok"
    except Exception as e:
        return False, str(e)
