from src.news_defaults import (
    CORE_GROUP,
    CORE_NEWS_RSS_FEEDS,
    DEFAULT_NEWS_RSS_FEEDS,
    PRIMARY_TIER,
    SUPPLEMENTAL_TIER,
    WORLDMONITOR_TRADING_GROUP,
    WORLDMONITOR_TRADING_RSS_FEEDS,
    append_source_meta_tags,
    default_news_rss_feeds_value,
    extract_source_meta_tags,
    news_rss_feeds_for_preset,
    news_source_alias,
    news_source_group,
    news_source_tier,
)


def test_default_news_rss_feeds_include_direct_and_rss_sources() -> None:
    assert DEFAULT_NEWS_RSS_FEEDS
    assert any(feed.startswith("direct://") for feed in DEFAULT_NEWS_RSS_FEEDS)
    assert any(feed.startswith("https://") for feed in DEFAULT_NEWS_RSS_FEEDS)
    assert not any(feed.startswith("file://") for feed in DEFAULT_NEWS_RSS_FEEDS)
    assert "https://www.federalreserve.gov/feeds/press_all.xml" in DEFAULT_NEWS_RSS_FEEDS


def test_default_news_rss_feeds_value_is_csv() -> None:
    value = default_news_rss_feeds_value()
    assert "," in value
    assert "direct://jin10" in value
    assert "fxstreet.com" in value
    assert "federalreserve.gov/feeds/press_all.xml" in value


def test_default_news_rss_feeds_bundle_worldmonitor_subset_and_dedup() -> None:
    feeds = news_rss_feeds_for_preset("default")
    assert feeds
    assert len(feeds) > len(CORE_NEWS_RSS_FEEDS)
    assert len(feeds) == len(set(feeds))
    assert all(feed.startswith("https://") for feed in WORLDMONITOR_TRADING_RSS_FEEDS)
    assert "https://www.federalreserve.gov/feeds/press_all.xml" in feeds
    assert "https://feeds.content.dowjones.io/public/rss/RSSUSnews" in feeds
    assert feeds.count("https://cointelegraph.com/rss") == 1


def test_core_preset_keeps_original_fast_mix() -> None:
    assert news_rss_feeds_for_preset("core") == list(CORE_NEWS_RSS_FEEDS)


def test_unknown_preset_falls_back_to_default() -> None:
    assert news_rss_feeds_for_preset("unknown") == list(DEFAULT_NEWS_RSS_FEEDS)


def test_source_tier_group_metadata_matches_curated_presets() -> None:
    assert news_source_alias("direct://jin10") == "J10"
    assert news_source_group("direct://jin10") == CORE_GROUP
    assert news_source_tier("direct://jin10") == PRIMARY_TIER
    assert news_source_alias("https://www.federalreserve.gov/feeds/press_all.xml") == "FED"
    assert news_source_group("https://www.federalreserve.gov/feeds/press_all.xml") == WORLDMONITOR_TRADING_GROUP
    assert news_source_tier("https://www.federalreserve.gov/feeds/press_all.xml") == SUPPLEMENTAL_TIER
    assert news_source_alias("https://content-static.cctvnews.cctv.com/snow-book/index.html") == "CCTV"
    assert news_source_alias("https://baijiahao.baidu.com/s?id=1") == "BJH"
    assert news_source_alias("https://weibo.com/123/abc") == "WB"
    assert news_source_alias("https://news.cn/world/20260308/abc.htm") == "XH"


def test_append_source_meta_tags_round_trips() -> None:
    categories = append_source_meta_tags(["macro", "policy"], feed_or_url="direct://jin10", source_hint="J10")
    assert "macro" in categories
    assert "policy" in categories
    group, tier = extract_source_meta_tags(categories)
    assert group == CORE_GROUP
    assert tier == PRIMARY_TIER


def test_default_news_rss_feeds_exclude_known_unstable_sources() -> None:
    feeds = news_rss_feeds_for_preset("default")
    assert "https://www.benzinga.com/news/feed" not in feeds
    assert "https://www.sec.gov/rss/news/press.xml" not in feeds
    assert "https://finance.yahoo.com/rss/topstories" not in feeds
    assert "https://www.ft.com/rss/home" not in feeds
    assert "https://www.coindesk.com/arc/outboundfeeds/rss/" not in feeds
