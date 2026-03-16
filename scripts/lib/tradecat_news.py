#!/usr/bin/env python3
"""Read-only news query helpers for local TradeCat tooling."""

from __future__ import annotations

import csv
import io
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse, urlunparse


DEFAULT_NEWS_DATABASE_URL = "postgresql://postgres:postgres@localhost:5434/market_data"
DEFAULT_NEWS_SOURCE = "alternative.news_articles"
MAX_NEWS_LIMIT = 200
MAX_SINCE_MINUTES = 60 * 24 * 30
_COMMON_QUOTE_SUFFIXES = ("USDT", "USD", "USDC", "BUSD", "FDUSD", "BTC", "ETH")


@dataclass(frozen=True)
class StoredNewsArticle:
    """Minimal stored news row returned by the read-only query."""

    dedup_hash: str
    published_at: float
    source: str
    url: str
    title: str
    summary: str
    symbols: tuple[str, ...]
    categories: tuple[str, ...]
    language: str


def _read_env_value(env_file: Path, key: str) -> str:
    try:
        lines = env_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""

    value = ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        env_key, env_value = line.split("=", 1)
        env_key = env_key.strip()
        if env_key != key:
            continue
        env_value = env_value.strip()
        if env_value.startswith('"') and env_value.endswith('"') and len(env_value) >= 2:
            env_value = env_value[1:-1]
        elif env_value.startswith("'") and env_value.endswith("'") and len(env_value) >= 2:
            env_value = env_value[1:-1]
        else:
            comment_pos = env_value.find(" #")
            if comment_pos >= 0:
                env_value = env_value[:comment_pos].rstrip()
        value = env_value
    return value


def resolve_news_database_url(repo_root: Path, env: Mapping[str, str] | None = None) -> str:
    """Resolve the news database URL from env vars, config/.env, or the local default."""

    data = os.environ if env is None else env
    for key in ("TUI_NEWS_DATABASE_URL", "MARKETS_SERVICE_DATABASE_URL", "DATABASE_URL"):
        value = str(data.get(key, "") or "").strip()
        if value:
            return value

    env_file = repo_root / "config" / ".env"
    for key in ("TUI_NEWS_DATABASE_URL", "MARKETS_SERVICE_DATABASE_URL", "DATABASE_URL"):
        value = _read_env_value(env_file, key).strip()
        if value:
            return value
    return DEFAULT_NEWS_DATABASE_URL


def clamp_limit(limit: int) -> int:
    """Clamp the requested limit to a safe range for local read-only tooling."""

    return max(1, min(int(limit), MAX_NEWS_LIMIT))


def clamp_since_minutes(since_minutes: int) -> int:
    """Clamp the lookback window to a bounded number of minutes."""

    return max(1, min(int(since_minutes), MAX_SINCE_MINUTES))


def _split_pipe(value: str) -> tuple[str, ...]:
    parts = [piece.strip() for piece in str(value or "").split("|")]
    return tuple(piece for piece in parts if piece)


def _build_netloc(parsed, port: int) -> str:
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        auth = f"{auth}@"
    host = parsed.hostname or "localhost"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{auth}{host}:{int(port)}"


def _candidate_database_urls(db_url: str) -> list[str]:
    target = str(db_url or "").strip()
    if not target:
        return []

    candidates = [target]
    try:
        parsed = urlparse(target)
    except Exception:
        return candidates

    host = (parsed.hostname or "").strip().lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return candidates

    current_port = parsed.port or 5432
    for port in (5434, 5433, 5432):
        if port == current_port:
            continue
        candidate = urlunparse(parsed._replace(netloc=_build_netloc(parsed, port)))
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _is_connection_error(detail: str) -> bool:
    blob = (detail or "").lower()
    keys = (
        "connection to server at",
        "connection refused",
        "could not connect to server",
        "no route to host",
        "timeout expired",
    )
    return any(key in blob for key in keys)


def _normalize_symbol_term(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _symbol_search_terms(symbol_value: str) -> list[str]:
    raw = str(symbol_value or "").strip()
    if not raw:
        return []

    seen: set[str] = set()
    out: list[str] = []
    chunks = re.split(r"[\s,]+", raw)
    for chunk in chunks:
        if not chunk:
            continue

        candidates = {chunk}
        candidates.update(part for part in re.split(r"[-_./:]+", chunk) if part)
        if "." in chunk:
            base = chunk.split(".", 1)[0].strip()
            if base:
                candidates.add(base)

        for candidate in list(candidates):
            normalized = _normalize_symbol_term(candidate)
            if normalized:
                candidates.add(normalized)
                if len(normalized) > 4:
                    for suffix in _COMMON_QUOTE_SUFFIXES:
                        if normalized.endswith(suffix) and len(normalized) > len(suffix):
                            candidates.add(normalized[: -len(suffix)])
                if re.match(r"^(SH|SZ|HK|US)[A-Z0-9]+$", normalized) and len(normalized) > 2:
                    candidates.add(normalized[2:])

        for candidate in candidates:
            normalized = _normalize_symbol_term(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)

    return sorted(out)


def _sql_literal(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _sql_text_array(items: list[str]) -> str:
    escaped = ", ".join(_sql_literal(item) for item in items if item)
    return f"ARRAY[{escaped}]::text[]"


def _build_symbol_filter_sql(symbol_value: str) -> str:
    terms = _symbol_search_terms(symbol_value)
    if not terms:
        return ""

    return f"""
    AND EXISTS (
        SELECT 1
        FROM unnest(COALESCE(symbols, ARRAY[]::text[])) AS news_symbol(symbol_value)
        WHERE REGEXP_REPLACE(UPPER(news_symbol.symbol_value), '[^A-Z0-9]+', '', 'g') = ANY ({_sql_text_array(terms)})
    )
""".rstrip()


def _build_query_filter_sql(query: str) -> str:
    needle = str(query or "").strip()
    if not needle:
        return ""

    return f"""
    AND POSITION(
        LOWER({_sql_literal(needle)}) IN LOWER(
            CONCAT_WS(
                ' ',
                COALESCE(title, ''),
                COALESCE(summary, ''),
                COALESCE(source, ''),
                COALESCE(url, ''),
                COALESCE(array_to_string(symbols, ' '), ''),
                COALESCE(array_to_string(categories, ' '), '')
            )
        )
    ) > 0
""".rstrip()


def _build_copy_sql(*, limit: int, since_minutes: int, symbol: str, query: str) -> str:
    safe_limit = clamp_limit(limit)
    safe_since_minutes = clamp_since_minutes(since_minutes)
    symbol_filter = _build_symbol_filter_sql(symbol)
    query_filter = _build_query_filter_sql(query)

    return f"""
COPY (
    SELECT
        dedup_hash,
        EXTRACT(EPOCH FROM published_at) AS published_at,
        COALESCE(source, '') AS source,
        COALESCE(url, '') AS url,
        COALESCE(title, '') AS title,
        COALESCE(summary, '') AS summary,
        COALESCE(array_to_string(symbols, '|'), '') AS symbols,
        COALESCE(array_to_string(categories, '|'), '') AS categories,
        COALESCE(language, 'en') AS language
    FROM {DEFAULT_NEWS_SOURCE}
    WHERE published_at >= NOW() - INTERVAL '{safe_since_minutes} minutes'
{symbol_filter}
{query_filter}
    ORDER BY published_at DESC
    LIMIT {safe_limit}
) TO STDOUT WITH (FORMAT CSV, HEADER TRUE)
""".strip()


def query_news_articles(
    db_url: str,
    *,
    symbol: str = "",
    query: str = "",
    limit: int = 20,
    since_minutes: int = 24 * 60,
    timeout_s: float = 5.0,
) -> list[StoredNewsArticle]:
    """Query recent news rows from TimescaleDB via `psql` and parse them into dataclasses."""

    target = str(db_url or "").strip()
    if not target:
        return []

    env = dict(os.environ)
    env.setdefault("PGAPPNAME", "tradecat_get_news")
    last_error = ""
    sql = _build_copy_sql(
        limit=limit,
        since_minutes=since_minutes,
        symbol=symbol,
        query=query,
    )

    for candidate in _candidate_database_urls(target):
        cmd = [
            "psql",
            candidate,
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]

        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(1.0, float(timeout_s)),
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"psql_timeout_{int(float(timeout_s))}s"
            raise RuntimeError(last_error) from exc
        except FileNotFoundError as exc:
            raise RuntimeError("psql_not_found") from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"psql_exit_{proc.returncode}").strip()
            last_error = detail[:240] or f"psql_exit_{proc.returncode}"
            if _is_connection_error(last_error):
                continue
            raise RuntimeError(last_error)

        reader = csv.DictReader(io.StringIO(proc.stdout))
        rows: list[StoredNewsArticle] = []
        for row in reader:
            if not isinstance(row, dict):
                continue
            try:
                published_at = float(row.get("published_at") or 0.0)
            except Exception:
                published_at = 0.0
            if published_at <= 0:
                continue

            title = str(row.get("title") or "").strip()
            if not title:
                continue

            rows.append(
                StoredNewsArticle(
                    dedup_hash=str(row.get("dedup_hash") or "").strip(),
                    published_at=published_at,
                    source=str(row.get("source") or "").strip(),
                    url=str(row.get("url") or "").strip(),
                    title=title,
                    summary=str(row.get("summary") or "").strip(),
                    symbols=_split_pipe(str(row.get("symbols") or "")),
                    categories=_split_pipe(str(row.get("categories") or "")),
                    language=str(row.get("language") or "en").strip() or "en",
                )
            )
        return rows

    raise RuntimeError(last_error or "psql_connection_failed")
