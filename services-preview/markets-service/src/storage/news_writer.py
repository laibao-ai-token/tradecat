"""News articles writer (alternative schema)."""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from psycopg import sql

from config import settings
from models.news import NewsArticle

from .timescale import get_shared_pool

logger = logging.getLogger(__name__)
try:
    UTC = datetime.UTC
except AttributeError:
    UTC = timezone.utc  # noqa: UP017


class TimescaleNewsWriter:
    """Write to alternative.news_articles with dedup on `dedup_hash`."""

    def __init__(self, db_url: str | None = None):
        # MVP: reuse shared pool; if a different db_url is needed later, we can extend here.
        self.pool = get_shared_pool()
        self.schema = settings.alternative_schema
        self._retention_hours = max(0, int(getattr(settings, "news_retention_hours", 24)))
        self._cleanup_interval_s = max(60, int(getattr(settings, "news_retention_cleanup_interval_seconds", 600)))
        self._last_cleanup_monotonic = 0.0

    def _build_insert_values(self, articles: Sequence[NewsArticle], ingest_batch_id: int) -> list[tuple[object, ...]]:
        values: list[tuple[object, ...]] = []
        for article in articles:
            published_at = article.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            else:
                published_at = published_at.astimezone(UTC)

            values.append(
                (
                    article.dedup_hash,
                    article.source,
                    article.url,
                    published_at,
                    article.title,
                    article.summary,
                    article.content,
                    article.symbols or None,
                    article.categories or None,
                    article.language or "en",
                    int(ingest_batch_id),
                )
            )
        return values

    def _insert_article_batch(self, articles: Sequence[NewsArticle], ingest_batch_id: int) -> int:
        if not articles:
            return 0

        cols = [
            "dedup_hash",
            "source",
            "url",
            "published_at",
            "title",
            "summary",
            "content",
            "symbols",
            "categories",
            "language",
            "ingest_batch_id",
        ]
        values = self._build_insert_values(articles, ingest_batch_id)

        insert_sql = sql.SQL(
            """
            INSERT INTO {table} ({cols})
            VALUES ({placeholders})
            ON CONFLICT (dedup_hash) DO NOTHING;
            """
        ).format(
            table=sql.Identifier(self.schema, "news_articles"),
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            placeholders=sql.SQL(", ").join([sql.Placeholder()] * len(cols)),
        )

        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(insert_sql.as_string(cur), values)
            inserted = int(cur.rowcount or 0)
            conn.commit()

        return inserted

    def _should_run_retention_cleanup(self, now_monotonic: float | None = None) -> bool:
        if self._retention_hours <= 0:
            return False
        current = time.monotonic() if now_monotonic is None else float(now_monotonic)
        return (current - self._last_cleanup_monotonic) >= self._cleanup_interval_s

    def _run_retention_cleanup(self, now_monotonic: float | None = None) -> int:
        current = time.monotonic() if now_monotonic is None else float(now_monotonic)
        if not self._should_run_retention_cleanup(current):
            return 0

        self._last_cleanup_monotonic = current
        cutoff = datetime.now(tz=UTC) - timedelta(hours=self._retention_hours)
        delete_sql = sql.SQL(
            "DELETE FROM {table} WHERE published_at < %s"
        ).format(table=sql.Identifier(self.schema, "news_articles"))

        try:
            with self.pool.connection() as conn, conn.cursor() as cur:
                cur.execute(delete_sql.as_string(cur), (cutoff,))
                deleted = int(cur.rowcount or 0)
                conn.commit()
        except Exception as exc:
            logger.warning("news retention cleanup failed: hours=%s error=%s", self._retention_hours, exc)
            return 0

        if deleted > 0:
            logger.info("news retention cleanup: deleted=%d older_than=%sh", deleted, self._retention_hours)
        return deleted

    def insert_articles(self, articles: Sequence[NewsArticle], ingest_batch_id: int) -> int:
        inserted = self._insert_article_batch(articles, ingest_batch_id)
        self._run_retention_cleanup()
        return inserted
