#!/usr/bin/env python3
"""Minimal read-only command for querying recent TradeCat signals."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "signal-service"))

from src.storage.read_only import fetch_recent_signals, probe_signal_history, resolve_history_db_path


TOOL_NAME = "tradecat_get_signals"


class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that surfaces validation errors as exceptions."""

    def error(self, message: str) -> None:
        raise ValueError(message)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error_payload(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _build_source_payload(db_path: Path, *, available: bool) -> dict[str, object]:
    return {
        "type": "sqlite",
        "table": "signal_history",
        "db_path": str(db_path),
        "available": available,
    }


def _build_request_payload(args: argparse.Namespace, limit: int) -> dict[str, object]:
    return {
        "symbol": args.symbol or None,
        "timeframe": args.timeframe or None,
        "limit": limit,
    }


def _build_payload(
    *,
    ok: bool,
    source: dict[str, object],
    request: dict[str, object],
    data: list[dict[str, object]],
    error: dict[str, str] | None,
) -> dict[str, object]:
    return {
        "ok": ok,
        "tool": TOOL_NAME,
        "ts": _utc_now_iso(),
        "source": source,
        "request": request,
        "data": data,
        "error": error,
    }


def build_parser() -> JsonArgumentParser:
    """Create the CLI parser for the read-only signal query tool."""
    parser = JsonArgumentParser(description="Read recent signals from TradeCat signal_history.db as JSON.")
    parser.add_argument("--symbol", help="Filter by symbol, for example BTCUSDT or NVDA.")
    parser.add_argument("--timeframe", help="Filter by timeframe, for example 1m or 1h.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of rows to return (1-500).")
    parser.add_argument("--db-path", help="Optional override for signal_history.db.")
    return parser


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and print a stable JSON payload."""
    parser = build_parser()
    args: argparse.Namespace | None = None
    db_path = resolve_history_db_path(None)
    request = {
        "symbol": None,
        "timeframe": None,
        "limit": None,
    }

    try:
        args = parser.parse_args(argv)
        db_path = resolve_history_db_path(args.db_path)
        request = _build_request_payload(args, args.limit)
        if args.limit <= 0:
            raise ValueError("--limit must be greater than 0")

        limit = min(args.limit, 500)
        request = _build_request_payload(args, limit)
        available, probe_message = probe_signal_history(db_path)
        source = _build_source_payload(db_path, available=available)

        if not available:
            payload = _build_payload(
                ok=False,
                source=source,
                request=request,
                data=[],
                error=_error_payload("source_unavailable", probe_message),
            )
            _emit(payload)
            return 1

        rows = fetch_recent_signals(
            db_path=db_path,
            symbol=args.symbol,
            timeframe=args.timeframe,
            limit=limit,
        )
        payload = _build_payload(
            ok=True,
            source=source,
            request=request,
            data=[row.to_dict() for row in rows],
            error=None,
        )
        _emit(payload)
        return 0
    except ValueError as exc:
        payload = _build_payload(
            ok=False,
            source=_build_source_payload(db_path, available=False),
            request=request,
            data=[],
            error=_error_payload("invalid_request", str(exc)),
        )
        _emit(payload)
        return 1
    except Exception as exc:
        payload = _build_payload(
            ok=False,
            source=_build_source_payload(db_path, available=False),
            request=request,
            data=[],
            error=_error_payload("unexpected_error", str(exc)),
        )
        _emit(payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
