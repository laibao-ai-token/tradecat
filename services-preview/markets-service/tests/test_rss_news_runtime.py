from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from src.providers.rss import news as rss_news
from src.providers.rss.news import RssNewsFetcher, clear_feed_health_state


VALID_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>Fast headline</title>
      <link>https://example.com/a</link>
      <pubDate>{pub_date}</pubDate>
      <description>hello</description>
    </item>
  </channel>
</rss>
"""


def _query(fetcher: RssNewsFetcher, feed_url: str):
    return fetcher.transform_query({"feeds": feed_url, "limit": 10, "window_hours": 24, "timeout_s": 5})


def test_feed_failure_enters_cooldown_and_skips_retry(monkeypatch):
    clear_feed_health_state()
    fetcher = RssNewsFetcher()
    feed_url = "https://example.com/fail.xml"
    query = _query(fetcher, feed_url)
    calls = {"count": 0}

    async def fail_request(session, url, headers):
        calls["count"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(fetcher, "_request_feed_text", fail_request)
    monkeypatch.setattr(rss_news.settings, "news_rss_failure_threshold", 2, raising=False)
    monkeypatch.setattr(rss_news.settings, "news_rss_failure_cooldown_seconds", 300, raising=False)

    asyncio.run(fetcher.extract(query))
    asyncio.run(fetcher.extract(query))
    asyncio.run(fetcher.extract(query))

    summary = fetcher.get_feed_health_summary()
    snapshot = fetcher.get_feed_health_snapshot()

    assert calls["count"] == 2
    assert summary["total"] == 1
    assert summary["cooldown"] == 1
    assert snapshot[0]["status"] == "cooldown"
    assert snapshot[0]["failure_count"] == 2
    assert snapshot[0]["cooldown_remaining_s"] > 0

    clear_feed_health_state()


def test_feed_success_resets_failure_state(monkeypatch):
    clear_feed_health_state()
    fetcher = RssNewsFetcher()
    feed_url = "https://example.com/recover.xml"
    query = _query(fetcher, feed_url)
    calls = {"count": 0}
    pub_date = (datetime.now(tz=timezone.utc) - timedelta(minutes=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")

    async def fail_then_succeed(session, url, headers):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return VALID_RSS.format(pub_date=pub_date)

    monkeypatch.setattr(fetcher, "_request_feed_text", fail_then_succeed)
    monkeypatch.setattr(rss_news.settings, "news_rss_failure_threshold", 2, raising=False)

    first = asyncio.run(fetcher.extract(query))
    second = asyncio.run(fetcher.extract(query))

    summary = fetcher.get_feed_health_summary()
    snapshot = fetcher.get_feed_health_snapshot()

    assert first == []
    assert len(second) == 1
    assert summary["healthy"] == 1
    assert summary["failing"] == 0
    assert summary["cooldown"] == 0
    assert snapshot[0]["status"] == "healthy"
    assert snapshot[0]["failure_count"] == 0
    assert snapshot[0]["last_error"] is None
    assert snapshot[0]["last_item_count"] == 1

    clear_feed_health_state()
