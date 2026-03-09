from __future__ import annotations

from datetime import datetime, timezone

from src.providers.rss.news import RssNewsFetcher
from src.providers.rss.parser import parse_feed

try:
    UTC = datetime.UTC
except AttributeError:
    UTC = timezone.utc  # noqa: UP017


def test_parse_rss2_feed():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example RSS</title>
    <item>
      <title>Breaking: BTC moves</title>
      <link>https://example.com/a</link>
      <pubDate>Thu, 05 Mar 2026 12:34:56 GMT</pubDate>
      <description><![CDATA[<p>Hello <b>world</b></p>]]></description>
      <category>crypto</category>
      <category>macro</category>
    </item>
  </channel>
</rss>
"""
    parsed = parse_feed(xml)
    assert parsed.title == "Example RSS"
    assert parsed.parse_error is None
    assert len(parsed.entries) == 1
    entry = parsed.entries[0]
    assert entry["title"] == "Breaking: BTC moves"
    assert entry["url"] == "https://example.com/a"
    assert isinstance(entry["published_at"], datetime)
    assert entry["published_at"].tzinfo is not None
    assert entry["summary"] == "Hello world"
    assert entry["categories"] == ["crypto", "macro"]


def test_parse_atom_feed():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <title>SEC filing update</title>
    <id>tag:example.com,2026:1</id>
    <updated>2026-03-05T12:34:56Z</updated>
    <link rel="alternate" href="https://example.com/b"/>
    <summary><![CDATA[<div>Guidance update</div>]]></summary>
    <category term="sec"/>
  </entry>
</feed>
"""
    parsed = parse_feed(xml)
    assert parsed.title == "Example Atom"
    assert parsed.parse_error is None
    assert len(parsed.entries) == 1
    entry = parsed.entries[0]
    assert entry["title"] == "SEC filing update"
    assert entry["url"] == "https://example.com/b"
    assert isinstance(entry["published_at"], datetime)
    assert entry["published_at"].tzinfo is not None
    assert entry["summary"] == "Guidance update"
    assert entry["categories"] == ["sec"]


def test_parse_invalid_xml_returns_parse_error():
    parsed = parse_feed("<rss><channel><title>broken</title>")
    assert parsed.entries == []
    assert parsed.parse_error is not None
    assert parsed.parse_error.startswith("xml_parse_error:")


def test_rss_fetcher_transform_data_dedup_hash():
    fetcher = RssNewsFetcher()
    raw = [
        {
            "title": "A",
            "url": "https://example.com/a",
            "published_at": datetime(2026, 3, 5, 12, 0, 0, tzinfo=UTC),
            "summary": "S",
            "_source": "example.com",
            "categories": ["x"],
        }
    ]
    out = fetcher.transform_data(raw)
    assert len(out) == 1
    article = out[0]
    assert article.dedup_hash and len(article.dedup_hash) == 64
    assert article.source == "example.com"
    assert article.categories == ["x"]


def test_rss_fetcher_transform_data_keeps_direct_items_distinct():
    fetcher = RssNewsFetcher()
    raw = [
        {
            "title": "Headline A",
            "url": "https://www.jin10.com/",
            "published_at": datetime(2026, 3, 5, 12, 0, 0, tzinfo=UTC),
            "summary": "S1",
            "_source": "J10",
            "language": "zh",
            "symbols": ["BTC"],
        },
        {
            "title": "Headline B",
            "url": "https://www.jin10.com/",
            "published_at": datetime(2026, 3, 5, 12, 0, 5, tzinfo=UTC),
            "summary": "S2",
            "_source": "J10",
            "language": "zh",
            "symbols": ["ETH"],
        },
    ]
    out = fetcher.transform_data(raw)
    assert len(out) == 2
    assert out[0].dedup_hash != out[1].dedup_hash
    assert out[0].language == "zh"
    assert "tc_source_group:core" in out[0].categories
    assert "tc_source_tier:primary" in out[0].categories
    assert out[1].symbols == ["ETH"]
