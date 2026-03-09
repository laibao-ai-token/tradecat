from __future__ import annotations

from datetime import datetime, timezone

from src.models.news import NewsArticle
from src.storage import news_writer as news_writer_module
from src.storage.news_writer import TimescaleNewsWriter

try:
    UTC = datetime.UTC
except AttributeError:
    UTC = timezone.utc  # noqa: UP017


def _article() -> NewsArticle:
    return NewsArticle(
        dedup_hash="abc",
        source="J10",
        url="https://example.com/a",
        published_at=datetime(2026, 3, 8, 0, 0, tzinfo=UTC),
        title="headline",
        summary="summary",
        content="content",
        symbols=["BTC"],
        categories=["macro"],
        language="zh",
    )


def _build_writer(monkeypatch, *, retention_hours: int = 24, cleanup_interval_s: int = 600) -> TimescaleNewsWriter:
    monkeypatch.setattr(news_writer_module, "get_shared_pool", lambda: object())
    monkeypatch.setattr(news_writer_module.settings, "alternative_schema", "alternative", raising=False)
    monkeypatch.setattr(news_writer_module.settings, "news_retention_hours", retention_hours, raising=False)
    monkeypatch.setattr(
        news_writer_module.settings,
        "news_retention_cleanup_interval_seconds",
        cleanup_interval_s,
        raising=False,
    )
    return TimescaleNewsWriter()


def test_insert_articles_runs_cleanup_after_insert(monkeypatch) -> None:
    writer = _build_writer(monkeypatch)
    calls: list[tuple[str, object]] = []

    def fake_insert(articles, ingest_batch_id):
        calls.append(("insert", len(articles), ingest_batch_id))
        return 3

    def fake_cleanup(now_monotonic=None):
        calls.append(("cleanup", now_monotonic))
        return 2

    monkeypatch.setattr(writer, "_insert_article_batch", fake_insert)
    monkeypatch.setattr(writer, "_run_retention_cleanup", fake_cleanup)

    inserted = writer.insert_articles([_article()], ingest_batch_id=7)

    assert inserted == 3
    assert calls == [("insert", 1, 7), ("cleanup", None)]


def test_insert_articles_runs_cleanup_even_when_batch_is_empty(monkeypatch) -> None:
    writer = _build_writer(monkeypatch)
    called = {"cleanup": 0}

    def fake_cleanup(now_monotonic=None):
        called["cleanup"] += 1
        return 0

    monkeypatch.setattr(writer, "_run_retention_cleanup", fake_cleanup)

    inserted = writer.insert_articles([], ingest_batch_id=11)

    assert inserted == 0
    assert called["cleanup"] == 1


def test_should_run_retention_cleanup_respects_interval(monkeypatch) -> None:
    writer = _build_writer(monkeypatch, cleanup_interval_s=600)
    writer._last_cleanup_monotonic = 100.0

    assert writer._should_run_retention_cleanup(650.0) is False
    assert writer._should_run_retention_cleanup(700.0) is True


def test_should_run_retention_cleanup_disabled_when_retention_is_zero(monkeypatch) -> None:
    writer = _build_writer(monkeypatch, retention_hours=0)

    assert writer._should_run_retention_cleanup(999.0) is False
