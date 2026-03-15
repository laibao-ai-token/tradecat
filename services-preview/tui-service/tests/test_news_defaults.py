import unittest

from src.news_defaults import (
    CORE_GROUP,
    CORE_TUI_NEWS_RSS_FEEDS,
    DEFAULT_TUI_NEWS_RSS_FEEDS,
    PRIMARY_TIER,
    SUPPLEMENTAL_TIER,
    WORLDMONITOR_TRADING_GROUP,
    default_tui_news_rss_feeds_value,
    news_source_group,
    news_source_tier,
    tui_news_rss_feeds_for_preset,
)
from src.tui import NewsItem, _filter_news_items, _news_source_code, _news_source_filter_options


class TestNewsDefaults(unittest.TestCase):
    def test_default_tui_news_feeds_use_live_sources(self) -> None:
        self.assertTrue(DEFAULT_TUI_NEWS_RSS_FEEDS)
        self.assertTrue(
            all(feed.startswith(("https://", "direct://")) for feed in DEFAULT_TUI_NEWS_RSS_FEEDS)
        )
        self.assertFalse(any(feed.startswith("file://") for feed in DEFAULT_TUI_NEWS_RSS_FEEDS))
        self.assertIn("direct://jin10", DEFAULT_TUI_NEWS_RSS_FEEDS)
        self.assertIn("direct://10jqka/realtimenews", DEFAULT_TUI_NEWS_RSS_FEEDS)
        self.assertIn("direct://sina/7x24", DEFAULT_TUI_NEWS_RSS_FEEDS)
        self.assertIn("direct://eastmoney/kuaixun", DEFAULT_TUI_NEWS_RSS_FEEDS)
        self.assertIn("direct://cls/telegraph", DEFAULT_TUI_NEWS_RSS_FEEDS)
        self.assertIn("https://www.federalreserve.gov/feeds/press_all.xml", DEFAULT_TUI_NEWS_RSS_FEEDS)

    def test_news_source_codes_cover_new_direct_sources(self) -> None:
        self.assertEqual(_news_source_code("direct://sina/7x24", ""), "SINA")
        self.assertEqual(_news_source_code("direct://eastmoney/kuaixun", ""), "EM24")
        self.assertEqual(_news_source_code("direct://cls/telegraph", ""), "CLS")
        self.assertEqual(_news_source_code("https://content-static.cctvnews.cctv.com/snow-book/index.html", ""), "CCTV")
        self.assertEqual(_news_source_code("https://baijiahao.baidu.com/s?id=1", ""), "BJH")
        self.assertEqual(_news_source_code("https://weibo.com/123/abc", ""), "WB")
        self.assertEqual(_news_source_code("https://news.cn/world/20260308/abc.htm", ""), "XH")

    def test_default_tui_news_feeds_value_is_csv(self) -> None:
        value = default_tui_news_rss_feeds_value()
        self.assertIn(",", value)
        self.assertIn("direct://jin10", value)
        self.assertIn("direct://sina/7x24", value)
        self.assertIn("direct://eastmoney/kuaixun", value)
        self.assertIn("direct://cls/telegraph", value)
        self.assertIn("globenewswire.com", value)
        self.assertIn("sec.gov/news/pressreleases.rss", value)
        self.assertIn("cointelegraph.com", value)
        self.assertIn("federalreserve.gov/feeds/press_all.xml", value)

    def test_default_tui_news_feeds_include_worldmonitor_subset(self) -> None:
        feeds = tui_news_rss_feeds_for_preset("default")
        self.assertIn("direct://jin10", feeds)
        self.assertIn("direct://sina/7x24", feeds)
        self.assertIn("https://www.federalreserve.gov/feeds/press_all.xml", feeds)
        self.assertIn("https://feeds.content.dowjones.io/public/rss/RSSUSnews", feeds)
        self.assertEqual(len(feeds), len(set(feeds)))
        self.assertGreater(len(feeds), len(CORE_TUI_NEWS_RSS_FEEDS))

    def test_core_preset_keeps_original_fast_mix(self) -> None:
        self.assertEqual(tui_news_rss_feeds_for_preset("core"), list(CORE_TUI_NEWS_RSS_FEEDS))

    def test_unknown_preset_falls_back_to_default(self) -> None:
        self.assertEqual(tui_news_rss_feeds_for_preset("unknown"), list(DEFAULT_TUI_NEWS_RSS_FEEDS))


    def test_tui_source_tier_group_metadata_matches_presets(self) -> None:
        self.assertEqual(news_source_group("direct://jin10"), CORE_GROUP)
        self.assertEqual(news_source_tier("direct://jin10"), PRIMARY_TIER)
        self.assertEqual(news_source_group("https://www.federalreserve.gov/feeds/press_all.xml"), WORLDMONITOR_TRADING_GROUP)
        self.assertEqual(news_source_group("https://cointelegraph.com/rss"), CORE_GROUP)
        self.assertEqual(news_source_tier("https://cointelegraph.com/rss"), PRIMARY_TIER)
        self.assertEqual(news_source_tier("https://www.federalreserve.gov/feeds/press_all.xml"), SUPPLEMENTAL_TIER)

    def test_source_filter_options_and_filtering(self) -> None:
        items = [
            NewsItem(id="1", published_at=100.0, source="J10", category="宏观", severity="MID", symbols=(), source_group=CORE_GROUP, source_tier=PRIMARY_TIER, title="a", summary="a", url="https://www.jin10.com/", direction="Neutral", confidence=0.5, impact_assets=(), suggestion=""),
            NewsItem(id="2", published_at=100.0, source="FED", category="政策", severity="MID", symbols=(), source_group=WORLDMONITOR_TRADING_GROUP, source_tier=SUPPLEMENTAL_TIER, title="b", summary="b", url="https://www.federalreserve.gov/", direction="Neutral", confidence=0.5, impact_assets=(), suggestion=""),
        ]
        self.assertEqual(_news_source_filter_options(items), ("全部", "主链", "补充", "FED", "J10"))
        filtered = _filter_news_items(
            items,
            now_ts=120.0,
            category="全部",
            window_h=24,
            search_query="",
            source_filter="补充",
        )
        self.assertEqual([item.source for item in filtered], ["FED"])


    def test_default_tui_news_feeds_exclude_known_unstable_sources(self) -> None:
        feeds = tui_news_rss_feeds_for_preset("default")
        self.assertNotIn("https://www.benzinga.com/news/feed", feeds)
        self.assertNotIn("https://www.sec.gov/rss/news/press.xml", feeds)
        self.assertNotIn("https://finance.yahoo.com/rss/topstories", feeds)
        self.assertNotIn("https://www.ft.com/rss/home", feeds)
        self.assertNotIn("https://www.coindesk.com/arc/outboundfeeds/rss/", feeds)


if __name__ == "__main__":
    unittest.main()
