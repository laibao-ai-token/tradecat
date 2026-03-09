from __future__ import annotations

import unittest
from unittest.mock import patch

from src.news_health import NewsHealthSnapshot
from src.tui import NewsItem, RssNewsPoller


def _news_item(item_id: str, published_at: float, title: str, source: str = "J10") -> NewsItem:
    return NewsItem(
        id=item_id,
        published_at=published_at,
        source=source,
        category="macro",
        severity="MID",
        symbols=(),
        title=title,
        summary=title,
        url=f"https://example.com/{item_id}",
        direction="Neutral",
        confidence=0.5,
        impact_assets=(),
        suggestion="",
    )


class TestNewsPoller(unittest.TestCase):
    def test_refresh_once_prefers_fresh_db_items(self) -> None:
        poller = RssNewsPoller(
            ["direct://jin10"],
            database_url="postgresql://postgres:postgres@localhost:5434/market_data",
            database_stale_after_s=60.0,
        )
        db_items = [_news_item("db-1", 980.0, "db headline")]
        health = NewsHealthSnapshot(source="collector", total=8, healthy=8, checked_at=995.0)

        with (
            patch.object(poller, "_fetch_database_items", return_value=db_items),
            patch.object(poller, "_fetch_live_items", return_value=([_news_item("live-1", 999.0, "live headline")], [])) as mock_live,
            patch("src.tui.load_news_collector_health", return_value=health),
            patch("src.tui.time.time", return_value=1000.0),
        ):
            poller._refresh_once(started=1000.0, headers={"User-Agent": "test"})

        snapshot = poller.snapshot()
        self.assertEqual(snapshot.mode, "DB")
        self.assertEqual(snapshot.items[0].title, "db headline")
        self.assertEqual(snapshot.latest_item_at, 980.0)
        self.assertEqual(snapshot.last_error, "")
        mock_live.assert_not_called()

    def test_refresh_once_falls_back_to_live_when_collector_is_stale(self) -> None:
        poller = RssNewsPoller(
            ["direct://jin10"],
            database_url="postgresql://postgres:postgres@localhost:5434/market_data",
            database_stale_after_s=60.0,
        )
        db_items = [_news_item("db-1", 970.0, "db headline")]
        live_items = [_news_item("live-1", 999.0, "live headline")]
        health = NewsHealthSnapshot(source="collector", total=8, healthy=8, checked_at=900.0)

        with (
            patch.object(poller, "_fetch_database_items", return_value=db_items),
            patch.object(poller, "_fetch_live_items", return_value=(live_items, [])) as mock_live,
            patch("src.tui.load_news_collector_health", return_value=health),
            patch("src.tui.time.time", return_value=1001.0),
        ):
            poller._refresh_once(started=1000.0, headers={"User-Agent": "test"})

        snapshot = poller.snapshot()
        self.assertEqual(snapshot.mode, "LIVE(1)")
        self.assertEqual(snapshot.items[0].title, "live headline")
        self.assertEqual(snapshot.latest_item_at, 999.0)
        self.assertEqual(snapshot.last_error, "")
        self.assertEqual(snapshot.health.source, "live")
        mock_live.assert_called_once()

    def test_refresh_once_keeps_stale_db_items_when_live_fallback_fails(self) -> None:
        poller = RssNewsPoller(
            ["direct://jin10"],
            database_url="postgresql://postgres:postgres@localhost:5434/market_data",
            database_stale_after_s=60.0,
        )
        db_items = [_news_item("db-1", 970.0, "db headline")]
        health = NewsHealthSnapshot(source="collector", total=8, healthy=8, checked_at=900.0)

        with (
            patch.object(poller, "_fetch_database_items", return_value=db_items),
            patch.object(poller, "_fetch_live_items", return_value=([], ["J10: TimeoutError"])) as mock_live,
            patch("src.tui.load_news_collector_health", return_value=health),
            patch("src.tui.time.time", return_value=1002.0),
        ):
            poller._refresh_once(started=1000.0, headers={"User-Agent": "test"})

        snapshot = poller.snapshot()
        self.assertEqual(snapshot.mode, "DB")
        self.assertEqual(snapshot.items[0].title, "db headline")
        self.assertIn("collector stale", snapshot.last_error)
        self.assertIn("J10: TimeoutError", snapshot.last_error)
        self.assertEqual(snapshot.health.source, "live")
        self.assertEqual(snapshot.health.failing, 1)
        mock_live.assert_called_once()


if __name__ == "__main__":
    unittest.main()
