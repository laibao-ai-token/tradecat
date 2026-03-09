from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.news_health import build_live_news_health, load_news_collector_health, resolve_news_health_log_path


class TestNewsHealth(unittest.TestCase):
    def test_resolve_news_health_log_path_default(self) -> None:
        root = Path('/tmp/tradecat-test-root')
        path = resolve_news_health_log_path(root, env={})
        self.assertTrue(str(path).endswith('services-preview/markets-service/logs/news_collect.log'))

    def test_resolve_news_health_log_path_override(self) -> None:
        root = Path('/tmp/tradecat-test-root')
        path = resolve_news_health_log_path(root, env={'TUI_NEWS_HEALTH_LOG_PATH': 'logs/custom_news.log'})
        self.assertEqual(path, (root / 'logs/custom_news.log').resolve())

    def test_load_news_collector_health_reads_latest_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / 'news_collect.log'
            log_path.write_text(
                '\n'.join(
                    [
                        '2026-03-07 19:05:45,166 - INFO - __main__ - collect-news health: total=13 healthy=11 failing=2 cooldown=0 new=0',
                        '2026-03-07 19:05:45,166 - WARNING - __main__ - collect-news unhealthy sample: rss:failing:https://www.benzinga.com/news/feed',
                        '2026-03-07 19:06:01,001 - INFO - __main__ - collect-news health: total=13 healthy=12 failing=1 cooldown=0 new=0',
                    ]
                ),
                encoding='utf-8',
            )
            snap = load_news_collector_health(log_path)

        self.assertEqual(snap.source, 'collector')
        self.assertEqual(snap.total, 13)
        self.assertEqual(snap.healthy, 12)
        self.assertEqual(snap.failing, 1)
        self.assertEqual(snap.cooldown, 0)
        self.assertEqual(snap.new, 0)
        self.assertGreater(snap.checked_at, 0.0)

    def test_build_live_news_health_summarizes_errors(self) -> None:
        snap = build_live_news_health(8, ['J10: TimeoutError', 'SEC: HTTPError'], checked_at=123.0)
        self.assertEqual(snap.source, 'live')
        self.assertEqual(snap.total, 8)
        self.assertEqual(snap.healthy, 6)
        self.assertEqual(snap.failing, 2)
        self.assertEqual(snap.cooldown, 0)
        self.assertEqual(snap.checked_at, 123.0)
        self.assertIn('J10', snap.sample)


if __name__ == '__main__':
    unittest.main()
