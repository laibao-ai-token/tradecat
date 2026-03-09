"""News feed provider.

Supports both normal RSS/Atom URLs and higher-frequency `direct://...` sources.
The provider keeps per-feed runtime health state inspired by `repository/worldmonitor`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import aiohttp

from config import settings
from core.fetcher import BaseFetcher
from core.registry import register_fetcher
from models.news import NewsArticle, NewsQuery

from ...news_defaults import append_source_meta_tags
from .direct import (
    DIRECT_NEWS_PREFIX,
    direct_news_source_label,
    fetch_direct_news_entries,
    is_direct_news_source,
)
from .parser import parse_feed

logger = logging.getLogger(__name__)
try:
    UTC = datetime.UTC
except AttributeError:
    UTC = timezone.utc  # noqa: UP017

_STATUS_PRIORITY = {"cooldown": 0, "failing": 1, "healthy": 2, "new": 3}
_FEED_HEALTH: dict[str, FeedHealthState] = {}


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _parse_feeds(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
        return [v for v in items if v]
    raw = str(value).strip()
    if not raw:
        return []
    parts: list[str] = []
    for piece in raw.replace("\n", ",").split(","):
        piece = piece.strip()
        if piece:
            parts.append(piece)
    return parts


def _source_label(feed_url: str, feed_title: str) -> str:
    if is_direct_news_source(feed_url):
        return direct_news_source_label(feed_url)

    try:
        host = urlparse(feed_url).hostname or ""
    except Exception:
        host = ""
    host = host.lower().strip()
    if host:
        return host
    return (feed_title or "rss").strip() or "rss"


def _dedup_hash(source: str, url: str | None, title: str, published_at: datetime) -> str:
    key = "|".join(
        [
            source.strip(),
            (url or "").strip(),
            published_at.astimezone(UTC).isoformat(),
            title.strip(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()


def _sanitize_error(error: object) -> str:
    text = str(error).strip()
    if not text:
        return type(error).__name__
    return text[:240]


def _normalize_published_at(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass
class FeedHealthState:
    feed_url: str
    source: str | None = None
    failure_count: int = 0
    cooldown_until: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_item_count: int = 0

    def status(self, now: datetime | None = None) -> str:
        current = now or _utcnow()
        if self.cooldown_until and self.cooldown_until > current:
            return "cooldown"
        if self.failure_count > 0:
            return "failing"
        if self.last_success_at is not None:
            return "healthy"
        return "new"

    def to_snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or _utcnow()
        cooldown_remaining_s = 0
        if self.cooldown_until and self.cooldown_until > current:
            cooldown_remaining_s = int((self.cooldown_until - current).total_seconds())
        return {
            "feed_url": self.feed_url,
            "source": self.source,
            "status": self.status(current),
            "failure_count": self.failure_count,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "cooldown_remaining_s": cooldown_remaining_s,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error": self.last_error,
            "last_item_count": self.last_item_count,
        }


def clear_feed_health_state() -> None:
    _FEED_HEALTH.clear()


def _ensure_feed_state(feed_url: str) -> FeedHealthState:
    state = _FEED_HEALTH.get(feed_url)
    if state is None:
        state = FeedHealthState(feed_url=feed_url)
        _FEED_HEALTH[feed_url] = state
    return state


def _is_feed_on_cooldown(feed_url: str, now: datetime | None = None) -> bool:
    current = now or _utcnow()
    state = _ensure_feed_state(feed_url)
    if state.cooldown_until is None:
        return False
    if state.cooldown_until > current:
        return True
    state.cooldown_until = None
    return False


def _record_feed_failure(feed_url: str, error: object, *, source: str | None = None, now: datetime | None = None) -> None:
    current = now or _utcnow()
    state = _ensure_feed_state(feed_url)
    state.last_attempt_at = current
    state.last_error = _sanitize_error(error)
    if source:
        state.source = source
    state.failure_count += 1

    threshold = max(1, int(getattr(settings, "news_rss_failure_threshold", 2)))
    cooldown_seconds = max(1, int(getattr(settings, "news_rss_failure_cooldown_seconds", 300)))
    if state.failure_count >= threshold:
        state.cooldown_until = current + timedelta(seconds=cooldown_seconds)
        logger.warning(
            "news feed on cooldown: source=%s feed=%s failures=%d cooldown_s=%d error=%s",
            state.source or "news",
            feed_url,
            state.failure_count,
            cooldown_seconds,
            state.last_error,
        )


def _record_feed_success(
    feed_url: str,
    *,
    source: str | None = None,
    item_count: int = 0,
    now: datetime | None = None,
) -> None:
    current = now or _utcnow()
    state = _ensure_feed_state(feed_url)
    state.last_attempt_at = current
    state.last_success_at = current
    state.last_error = None
    state.failure_count = 0
    state.cooldown_until = None
    state.last_item_count = int(item_count)
    if source:
        state.source = source


def _iter_feed_states(feed_urls: Iterable[str] | None = None) -> list[FeedHealthState]:
    if feed_urls is None:
        return list(_FEED_HEALTH.values())
    seen: set[str] = set()
    states: list[FeedHealthState] = []
    for feed_url in feed_urls:
        feed = str(feed_url).strip()
        if not feed or feed in seen:
            continue
        seen.add(feed)
        states.append(_ensure_feed_state(feed))
    return states


def get_feed_health_snapshot(feed_urls: Iterable[str] | None = None) -> list[dict[str, Any]]:
    now = _utcnow()
    rows = [state.to_snapshot(now) for state in _iter_feed_states(feed_urls)]
    rows.sort(key=lambda row: (_STATUS_PRIORITY.get(str(row.get("status")), 99), str(row.get("feed_url") or "")))
    return rows


def get_feed_health_summary(feed_urls: Iterable[str] | None = None) -> dict[str, int]:
    summary = {"total": 0, "healthy": 0, "failing": 0, "cooldown": 0, "new": 0}
    for row in get_feed_health_snapshot(feed_urls):
        status = str(row.get("status") or "new")
        summary["total"] += 1
        if status in summary:
            summary[status] += 1
        else:
            summary["new"] += 1
    return summary


@register_fetcher("rss", "news")
class RssNewsFetcher(BaseFetcher[NewsQuery, NewsArticle]):
    """News feed fetcher supporting RSS and direct live sources."""

    def __init__(self) -> None:
        self._last_query_feeds: list[str] = []

    def transform_query(self, params: dict[str, Any]) -> NewsQuery:
        feeds = _parse_feeds(params.get("feeds") or params.get("feed") or params.get("urls"))
        if not feeds:
            feeds = _parse_feeds(
                settings.__dict__.get("news_rss_feeds")  # type: ignore[attr-defined]
                if hasattr(settings, "news_rss_feeds")
                else None
            )
        if not feeds:
            import os

            feeds = _parse_feeds(os.getenv("NEWS_RSS_FEEDS", ""))

        feeds = [
            feed
            for feed in feeds
            if feed.startswith("http://") or feed.startswith("https://") or feed.lower().startswith(DIRECT_NEWS_PREFIX)
        ]

        limit = params.get("limit", 50)
        window_hours = params.get("window_hours", params.get("window", 24))
        timeout_s = params.get("timeout_s", params.get("timeout", 20))
        return NewsQuery(
            feeds=feeds,
            limit=int(limit),
            window_hours=int(window_hours),
            timeout_s=int(timeout_s),
        )

    async def _request_feed_text(
        self,
        session: aiohttp.ClientSession,
        feed_url: str,
        headers: dict[str, str],
    ) -> str:
        async with session.get(feed_url, proxy=settings.http_proxy, headers=headers) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
            return await resp.text(errors="ignore")

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        query: NewsQuery,
        feed_url: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        now = _utcnow()
        state = _ensure_feed_state(feed_url)
        state.last_attempt_at = now
        if _is_feed_on_cooldown(feed_url, now=now):
            return []

        try:
            if is_direct_news_source(feed_url):
                source = _source_label(feed_url, "")
                entries = await fetch_direct_news_entries(
                    session,
                    feed_url,
                    timeout_s=float(query.timeout_s),
                    proxy=settings.http_proxy,
                )
            else:
                text = await self._request_feed_text(session, feed_url, headers)
                parsed = parse_feed(text)
                source = _source_label(feed_url, parsed.title)
                if parsed.parse_error:
                    raise RuntimeError(parsed.parse_error)
                entries = parsed.entries
        except Exception as exc:
            _record_feed_failure(feed_url, exc, source=state.source, now=now)
            return []

        cutoff = now - timedelta(hours=int(query.window_hours))
        out: list[dict[str, Any]] = []
        for entry in entries:
            published_at = _normalize_published_at(entry.get("published_at"))
            if published_at is None or published_at < cutoff:
                continue
            normalized = dict(entry)
            normalized["_feed_url"] = feed_url
            normalized["_source"] = source
            normalized["published_at"] = published_at
            out.append(normalized)

        out.sort(key=lambda row: row.get("published_at") or datetime.min.replace(tzinfo=UTC), reverse=True)
        out = out[: int(query.limit)]
        _record_feed_success(feed_url, source=source, item_count=len(out), now=now)
        return out

    async def extract(self, query: NewsQuery) -> list[dict[str, Any]]:
        if not query.feeds:
            self._last_query_feeds = []
            return []

        self._last_query_feeds = list(query.feeds)
        for feed_url in self._last_query_feeds:
            _ensure_feed_state(feed_url)

        headers = {"User-Agent": "TradeCat/markets-service news"}
        timeout = aiohttp.ClientTimeout(total=float(query.timeout_s))

        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = await asyncio.gather(
                *[self._fetch_one(session, query, feed_url, headers) for feed_url in self._last_query_feeds],
                return_exceptions=True,
            )

        items: list[dict[str, Any]] = []
        for feed_url, result in zip(self._last_query_feeds, results, strict=False):
            if isinstance(result, Exception):
                _record_feed_failure(feed_url, result)
                continue
            items.extend(result)

        items.sort(key=lambda row: row.get("published_at") or datetime.min.replace(tzinfo=UTC), reverse=True)
        return items[: int(query.limit)]

    def get_feed_health_snapshot(self, feed_urls: Iterable[str] | None = None) -> list[dict[str, Any]]:
        scope = list(feed_urls) if feed_urls is not None else self._last_query_feeds
        return get_feed_health_snapshot(scope)

    def get_feed_health_summary(self, feed_urls: Iterable[str] | None = None) -> dict[str, int]:
        scope = list(feed_urls) if feed_urls is not None else self._last_query_feeds
        return get_feed_health_summary(scope)

    def transform_data(self, raw: list[dict[str, Any]]) -> list[NewsArticle]:
        out: list[NewsArticle] = []
        for row in raw:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            published_at = _normalize_published_at(row.get("published_at"))
            if published_at is None:
                continue
            source = (row.get("_source") or "news").strip() or "news"
            url = (row.get("url") or "").strip() or None
            summary = (row.get("summary") or "").strip() or None
            content = (row.get("content") or "").strip() or None
            categories = row.get("categories") or []
            if not isinstance(categories, list):
                categories = []
            categories = append_source_meta_tags(
                categories,
                feed_or_url=str(row.get("_feed_url") or url or source),
                source_hint=source,
            )
            symbols = row.get("symbols") or []
            if not isinstance(symbols, (list, tuple)):
                symbols = []
            language = (row.get("language") or "en").strip() or "en"

            out.append(
                NewsArticle(
                    dedup_hash=_dedup_hash(source, url, title, published_at),
                    source=source,
                    url=url,
                    published_at=published_at,
                    title=title,
                    summary=summary,
                    content=content,
                    symbols=[str(item).strip() for item in symbols if str(item).strip()],
                    categories=[str(item).strip() for item in categories if str(item).strip()],
                    language=language,
                )
            )
        return out
