"""Read-only helpers for querying historical signals from SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from ..config import get_history_db_path
except ImportError:
    from config import get_history_db_path


_SYMBOL_SQL = "REPLACE(REPLACE(REPLACE(UPPER(symbol), '/', ''), '_', ''), '-', '')"


@dataclass(frozen=True)
class SignalHistoryRow:
    """Stable read model for a signal_history row."""

    id: int
    signal_ts: str
    symbol: str
    signal_type: str
    direction: str
    timeframe: str | None
    provider: str | None
    strength: int
    price: float | None
    message: str | None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready representation."""
        return asdict(self)


def _normalize_symbol(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if not normalized:
        return None
    return normalized.replace("/", "").replace("_", "").replace("-", "")


def _normalize_timeframe(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def resolve_history_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the signal history SQLite path without creating files."""
    if db_path is None:
        return Path(get_history_db_path()).resolve()
    return Path(db_path).expanduser().resolve()


def probe_signal_history(db_path: str | Path | None = None) -> tuple[bool, str]:
    """Check whether the signal history database is readable in read-only mode."""
    target = resolve_history_db_path(db_path)
    if not target.is_file():
        return False, f"signal history db not found: {target}"

    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("SELECT 1 FROM signal_history LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        return False, str(exc)
    return True, "ok"


def fetch_recent_signals(
    *,
    db_path: str | Path | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = 20,
) -> list[SignalHistoryRow]:
    """Return the newest signals from ``signal_history`` using read-only access."""
    target = resolve_history_db_path(db_path)
    limit_value = max(1, min(int(limit), 500))
    symbol_value = _normalize_symbol(symbol)
    timeframe_value = _normalize_timeframe(timeframe)

    where = ["1=1"]
    params: list[object] = []

    if symbol_value:
        where.append(f"{_SYMBOL_SQL} = ?")
        params.append(symbol_value)

    if timeframe_value:
        where.append("LOWER(COALESCE(timeframe, '')) = ?")
        params.append(timeframe_value)

    query = f"""
        SELECT id, timestamp, symbol, signal_type, direction, timeframe, source, strength, price, message
        FROM signal_history
        WHERE {' AND '.join(where)}
        ORDER BY timestamp DESC, id DESC
        LIMIT ?
    """
    params.append(limit_value)

    with sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=2) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    return [
        SignalHistoryRow(
            id=int(row["id"]),
            signal_ts=str(row["timestamp"]),
            symbol=str(row["symbol"]),
            signal_type=str(row["signal_type"]),
            direction=str(row["direction"]),
            timeframe=str(row["timeframe"]) if row["timeframe"] is not None else None,
            provider=str(row["source"]) if row["source"] is not None else None,
            strength=int(row["strength"]),
            price=float(row["price"]) if row["price"] is not None else None,
            message=str(row["message"]) if row["message"] is not None else None,
        )
        for row in rows
    ]
