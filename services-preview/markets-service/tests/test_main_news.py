from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from src import __main__ as main_module
from src.config import settings
from src.core.registry import ProviderRegistry
from src.storage import batch as batch_mgr
from src.storage import news_writer as news_writer_module


class _FakeFetcher:
    def __init__(self, calls: list[dict[str, object]], *, should_raise: bool = False) -> None:
        self._calls = calls
        self._should_raise = should_raise

    async def fetch(self, **kwargs):
        self._calls.append(dict(kwargs))
        if self._should_raise:
            raise RuntimeError("boom")
        return []

    def transform_query(self, params: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(feeds=["https://example.com/feed.xml"])


class _FakeWriter:
    def __init__(self, *, should_raise: bool = False) -> None:
        self._should_raise = should_raise

    def insert_articles(self, articles, ingest_batch_id: int) -> int:
        if self._should_raise:
            raise RuntimeError("db down")
        return len(articles)


def test_collect_news_uses_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    fetcher = _FakeFetcher(calls)

    monkeypatch.setattr(main_module, "_ensure_provider_loaded", lambda provider: True)
    monkeypatch.setattr(ProviderRegistry, "get", staticmethod(lambda provider, kind: (lambda: fetcher)))
    monkeypatch.setattr(batch_mgr, "start_batch", lambda **kwargs: 7)
    monkeypatch.setattr(news_writer_module, "TimescaleNewsWriter", lambda: _FakeWriter())
    monkeypatch.setattr(settings, "news_rss_poll_interval_seconds", 3, raising=False)
    monkeypatch.setattr(settings, "news_rss_limit", 123, raising=False)
    monkeypatch.setattr(settings, "news_rss_window_hours", 48, raising=False)
    monkeypatch.setattr(settings, "news_rss_timeout_seconds", 9, raising=False)
    monkeypatch.setattr(sys, "argv", ["prog", "collect-news"])

    main_module.main()

    assert calls == [
        {
            "feeds": "",
            "limit": 123,
            "window_hours": 48,
            "timeout_s": 9,
        }
    ]


def test_collect_news_poll_continues_after_transient_insert_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class StopLoop(RuntimeError):
        pass

    calls: list[dict[str, object]] = []
    sleep_calls: list[int] = []
    fetcher = _FakeFetcher(calls)

    async def _fake_sleep(seconds: int) -> None:
        sleep_calls.append(int(seconds))
        raise StopLoop("stop after retry scheduling")

    monkeypatch.setattr(main_module, "_ensure_provider_loaded", lambda provider: True)
    monkeypatch.setattr(ProviderRegistry, "get", staticmethod(lambda provider, kind: (lambda: fetcher)))
    monkeypatch.setattr(batch_mgr, "start_batch", lambda **kwargs: 9)
    monkeypatch.setattr(news_writer_module, "TimescaleNewsWriter", lambda: _FakeWriter(should_raise=True))
    monkeypatch.setattr(settings, "news_rss_poll_interval_seconds", 7, raising=False)
    monkeypatch.setattr(sys, "argv", ["prog", "collect-news-poll"])
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    with pytest.raises(StopLoop):
        main_module.main()

    assert len(calls) == 1
    assert sleep_calls == [7]
