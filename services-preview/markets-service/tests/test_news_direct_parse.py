from __future__ import annotations

import asyncio

from src.providers.rss.direct import direct_news_source_label, fetch_direct_news_entries


async def _fake_json_jin10(session, url, **kwargs):
    return {
        "data": [
            {
                "type": 0,
                "time": "2026-03-07 10:00:00",
                "important": 1,
                "tags": [{"name": "macro"}],
                "data": {
                    "content": "【美联储】降息预期升温",
                    "source_link": "",
                },
            }
        ]
    }


async def _fake_json_sina(session, url, **kwargs):
    return {
        "result": {
            "data": {
                "feed": {
                    "list": [
                        {
                            "rich_text": "美国天然气期货日内暴涨 9%",
                            "create_time": "2026-03-07 02:01:13",
                            "is_focus": 1,
                            "tag": [{"name": "macro"}],
                            "ext": '{"stocks":[{"symbol":"HF_NG"}]}'
                        }
                    ]
                }
            }
        }
    }


def test_direct_source_label_mapping() -> None:
    assert direct_news_source_label("direct://jin10") == "J10"
    assert direct_news_source_label("direct://sina/7x24") == "SINA"


def test_fetch_direct_news_entries_jin10(monkeypatch) -> None:
    monkeypatch.setattr("src.providers.rss.direct._http_get_json", _fake_json_jin10)

    entries = asyncio.run(fetch_direct_news_entries(session=None, source="direct://jin10", timeout_s=5))

    assert len(entries) == 1
    assert entries[0]["title"] == "美联储"
    assert entries[0]["summary"] == "降息预期升温"
    assert entries[0]["language"] == "zh"
    assert entries[0]["url"] == "https://www.jin10.com/"


def test_fetch_direct_news_entries_sina(monkeypatch) -> None:
    monkeypatch.setattr("src.providers.rss.direct._http_get_json", _fake_json_sina)

    entries = asyncio.run(fetch_direct_news_entries(session=None, source="direct://sina/7x24", timeout_s=5))

    assert len(entries) == 1
    assert entries[0]["title"] == "美国天然气期货日内暴涨 9%"
    assert entries[0]["symbols"] == ["HF_NG"]
    assert entries[0]["categories"] == ["macro"]
