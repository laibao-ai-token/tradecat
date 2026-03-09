from __future__ import annotations

import unittest

from src.news_defaults import CORE_GROUP, PRIMARY_TIER, SUPPLEMENTAL_TIER, WORLDMONITOR_TRADING_GROUP
from src.news_events import cluster_news_items
from src.tui import NewsItem


def _item(
    item_id: str,
    published_at: float,
    title: str,
    *,
    source: str,
    source_group: str = CORE_GROUP,
    source_tier: str = PRIMARY_TIER,
    category: str = "宏观",
    severity: str = "MID",
    symbols: tuple[str, ...] = (),
    impact_assets: tuple[str, ...] = (),
) -> NewsItem:
    return NewsItem(
        id=item_id,
        published_at=published_at,
        source=source,
        category=category,
        severity=severity,
        symbols=symbols,
        source_group=source_group,
        source_tier=source_tier,
        title=title,
        summary=title,
        url=f"https://example.com/{item_id}",
        direction="Neutral",
        confidence=0.5,
        impact_assets=impact_assets,
        suggestion="",
    )


class TestNewsEvents(unittest.TestCase):
    def test_cluster_news_items_merges_chinese_duplicates_within_time_window(self) -> None:
        items = [
            _item("j10-1", 1000.0, "卡塔尔首相与埃及外长呼吁通过谈判化解地区危机", source="J10"),
            _item(
                "sina-1",
                1010.0,
                "【卡塔尔首相与埃及外长呼吁通过谈判化解地区危机】当地时间3月7日，卡塔尔首相兼外交大臣表示各方应保持克制。",
                source="SINA",
            ),
        ]

        events = cluster_news_items(items)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.article_count, 2)
        self.assertEqual(event.source_count, 2)
        self.assertEqual(event.primary_title, "卡塔尔首相与埃及外长呼吁通过谈判化解地区危机")
        self.assertEqual(event.primary_source, "J10")
        self.assertEqual(event.top_sources[:2], ("SINA", "J10"))

    def test_cluster_news_items_respects_time_window(self) -> None:
        items = [
            _item("j10-1", 1000.0, "美国至3月6日当周石油钻井总数411口，前值407口。", source="J10"),
            _item("sina-1", 1000.0 - 7200.0, "【美国至3月6日当周石油钻井总数411口，前值407口。】", source="SINA"),
        ]

        events = cluster_news_items(items)

        self.assertEqual(len(events), 2)
        self.assertEqual([event.article_count for event in events], [1, 1])

    def test_cluster_news_items_keeps_generic_market_alerts_separate(self) -> None:
        items = [
            _item("j10-1", 1000.0, "美股三大股指跌幅收窄", source="J10"),
            _item("wscn-1", 1002.0, "纳斯达克100指数跌幅收窄至1%。", source="WSCN"),
        ]

        events = cluster_news_items(items)

        self.assertEqual(len(events), 2)

    def test_cluster_news_items_aggregates_sources_and_assets(self) -> None:
        items = [
            _item(
                "j10-1",
                1000.0,
                "美国天然气期货日内暴涨9.00%，现报3.274美元/百万英热。",
                source="J10",
                severity="HIGH",
                symbols=("HF_NG",),
                impact_assets=("HF_NG",),
            ),
            _item(
                "sina-1",
                1002.0,
                "【美国天然气期货日内暴涨9.00%，现报3.274美元/百万英热。】",
                source="SINA",
                symbols=("HF_NG", "XNGUSD"),
            ),
            _item(
                "fed-1",
                1005.0,
                "美国天然气期货日内暴涨9.00%，现报3.274美元/百万英热。",
                source="FED",
                source_group=WORLDMONITOR_TRADING_GROUP,
                source_tier=SUPPLEMENTAL_TIER,
                category="政策",
            ),
        ]

        events = cluster_news_items(items)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.article_count, 3)
        self.assertEqual(event.source_count, 3)
        self.assertEqual(event.severity, "HIGH")
        self.assertEqual(event.source_tier, PRIMARY_TIER)
        self.assertEqual(event.top_sources[-1], "FED")
        self.assertIn("HF_NG", event.symbols)
        self.assertIn("XNGUSD", event.symbols)
        self.assertIn("HF_NG", event.impact_assets)


if __name__ == "__main__":
    unittest.main()
