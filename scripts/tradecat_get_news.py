#!/usr/bin/env python3
"""Read recent TradeCat news from the local database and emit stable JSON."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:
    from lib.tradecat_news import (
        DEFAULT_NEWS_SOURCE,
        StoredNewsArticle,
        clamp_limit,
        clamp_since_minutes,
        query_news_articles,
        resolve_news_database_url,
    )
except ModuleNotFoundError:
    from scripts.lib.tradecat_news import (
        DEFAULT_NEWS_SOURCE,
        StoredNewsArticle,
        clamp_limit,
        clamp_since_minutes,
        query_news_articles,
        resolve_news_database_url,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL_NAME = "tradecat_get_news"


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _published_at_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _article_payload(article: StoredNewsArticle) -> dict[str, object]:
    return {
        "title": article.title,
        "summary": article.summary,
        "published_at": _published_at_iso(article.published_at),
        "provider": article.source,
        "url": article.url,
        "symbols": list(article.symbols),
        "category": article.categories[0] if article.categories else "",
    }


def _source_payload() -> dict[str, object]:
    return {
        "type": "postgresql",
        "table": DEFAULT_NEWS_SOURCE,
        "reader": "scripts/lib/tradecat_news.py",
        "writes": False,
    }


def _error_payload(code: str, message: str) -> dict[str, str]:
    return {
        "code": str(code),
        "message": str(message),
    }


def _query_error_code(exc: Exception) -> str:
    message = str(exc).strip()
    if message in {"psql_not_found", "psql_connection_failed"}:
        return message
    if message.startswith("psql_timeout_"):
        return "query_timeout"
    return "query_failed"


def _emit(payload: dict[str, object]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _request_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "symbol": (args.symbol or "").strip() or None,
        "query": (args.query or "").strip() or None,
        "limit": clamp_limit(args.limit),
        "since_minutes": clamp_since_minutes(args.since_minutes),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(
        prog=TOOL_NAME,
        description="Read recent TradeCat news from local TimescaleDB and emit stable JSON.",
        allow_abbrev=False,
    )
    parser.add_argument("--symbol", default="", help="Optional symbol filter (supports common pair forms like BTCUSDT)")
    parser.add_argument("--query", default="", help="Optional case-insensitive text filter")
    parser.add_argument("--limit", type=int, default=20, help="Max rows to return (default: 20, max: 200)")
    parser.add_argument(
        "--since-minutes",
        type=int,
        default=24 * 60,
        help="Only include rows published within the last N minutes (default: 1440)",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="psql timeout in seconds (default: 5)")
    parser.add_argument("--database-url", default="", help="Optional DB override; defaults to config/.env resolution")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only news CLI and emit a stable JSON envelope."""

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except ValueError as exc:
        _emit(
            {
                "ok": False,
                "tool": TOOL_NAME,
                "ts": _utc_now_iso(),
                "source": _source_payload(),
                "request": None,
                "data": [],
                "error": _error_payload("argument_error", str(exc)),
            }
        )
        return 2

    request = _request_payload(args)
    db_url = (args.database_url or "").strip() or resolve_news_database_url(PROJECT_ROOT)

    try:
        rows = query_news_articles(
            db_url,
            symbol=str(request["symbol"] or ""),
            query=str(request["query"] or ""),
            limit=int(request["limit"]),
            since_minutes=int(request["since_minutes"]),
            timeout_s=float(args.timeout),
        )
    except Exception as exc:
        _emit(
            {
                "ok": False,
                "tool": TOOL_NAME,
                "ts": _utc_now_iso(),
                "source": _source_payload(),
                "request": request,
                "data": [],
                "error": _error_payload(_query_error_code(exc), str(exc) or exc.__class__.__name__),
            }
        )
        return 1

    _emit(
        {
            "ok": True,
            "tool": TOOL_NAME,
            "ts": _utc_now_iso(),
            "source": _source_payload(),
            "request": request,
            "data": [_article_payload(row) for row in rows],
            "error": None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
