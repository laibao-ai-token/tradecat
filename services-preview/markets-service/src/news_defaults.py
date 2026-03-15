"""Built-in news feed presets and source metadata for 24x7 collection."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

PRIMARY_TIER = "primary"
SUPPLEMENTAL_TIER = "supplemental"
UNKNOWN_TIER = "unknown"
CORE_GROUP = "core"
WORLDMONITOR_TRADING_GROUP = "worldmonitor_trading"
UNKNOWN_GROUP = "unknown"

_INTERNAL_SOURCE_GROUP_PREFIX = "tc_source_group:"
_INTERNAL_SOURCE_TIER_PREFIX = "tc_source_tier:"
_DIRECT_PREFIX = "direct://"

_DIRECT_SOURCE_CODES: dict[str, str] = {
    "jin10": "J10",
    "gelonghui/live": "GLH",
    "10jqka/realtimenews": "THS",
    "sina/7x24": "SINA",
    "eastmoney/kuaixun": "EM24",
    "cls/telegraph": "CLS",
    "wallstreetcn/live": "WSCN",
    "eeo/kuaixun": "EEO",
}

_HOST_CODE_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("jin10.com", "J10"),
    ("gelonghui.com", "GLH"),
    ("10jqka.com.cn", "THS"),
    ("sina.com.cn", "SINA"),
    ("sina.cn", "SINA"),
    ("eastmoney.com", "EM24"),
    ("cls.cn", "CLS"),
    ("wallstreetcn.com", "WSCN"),
    ("wallstcn.com", "WSCN"),
    ("eeo.com.cn", "EEO"),
    ("cctvnews.cctv.com", "CCTV"),
    ("cctv.com", "CCTV"),
    ("baijiahao.baidu.com", "BJH"),
    ("weibo.com", "WB"),
    ("weibo.cn", "WB"),
    ("news.cn", "XH"),
    ("xinhua-news.com", "XH"),
    ("benzinga.com", "BENZ"),
    ("fxstreet.com", "FXST"),
    ("cnbc.com", "CNBC"),
    ("cointelegraph.com", "COIN"),
    ("globenewswire.com", "GNW"),
    ("theblock.co", "BLCK"),
    ("seekingalpha.com", "SALP"),
    ("finance.yahoo.com", "YHOO"),
    ("yahoo.com", "YHOO"),
    ("feeds.content.dowjones.io", "DJNW"),
    ("dowjones.io", "DJNW"),
    ("ft.com", "FT"),
    ("politico.com", "PLTC"),
    ("federalreserve.gov", "FED"),
    ("sec.gov", "SEC"),
    ("bis.org", "BIS"),
    ("ecb.europa.eu", "ECB"),
    ("bls.gov", "BLS"),
    ("news.un.org", "UN"),
    ("un.org", "UN"),
    ("oilprice.com", "OILP"),
    ("coindesk.com", "DSK"),
)


@dataclass(frozen=True)
class NewsFeedDescriptor:
    feed: str
    code: str
    group: str
    tier: str


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().removeprefix("www.")


def _split_direct_target(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw.startswith(_DIRECT_PREFIX):
        raw = raw[len(_DIRECT_PREFIX) :]
    return raw.strip().strip("/")


def _extract_host(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw.lower().startswith(_DIRECT_PREFIX):
        return ""
    if "://" in raw:
        try:
            return _normalize_host(urlparse(raw).hostname or "")
        except Exception:
            return ""
    if "/" in raw and "." in raw.split("/", 1)[0]:
        return _normalize_host(raw.split("/", 1)[0])
    if "." in raw and " " not in raw:
        return _normalize_host(raw)
    return ""


def _match_known_host_code(host: str) -> str:
    normalized = _normalize_host(host)
    if not normalized:
        return ""
    for suffix, code in _HOST_CODE_SUFFIXES:
        safe_suffix = _normalize_host(suffix)
        if normalized == safe_suffix or normalized.endswith(f".{safe_suffix}"):
            return code
    return ""


CORE_NEWS_RSS_FEEDS: tuple[str, ...] = (
    "direct://jin10",
    "direct://10jqka/realtimenews",
    "direct://sina/7x24",
    "direct://eastmoney/kuaixun",
    "direct://cls/telegraph",
    "direct://gelonghui/live",
    "direct://wallstreetcn/live",
    "direct://eeo/kuaixun",
    "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies",
    "https://www.fxstreet.com/rss/news",
    "https://cointelegraph.com/rss",
)

WORLDMONITOR_TRADING_RSS_FEEDS: tuple[str, ...] = (
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://seekingalpha.com/market_currents.xml",
    "https://feeds.content.dowjones.io/public/rss/RSSUSnews",
    "https://rss.politico.com/politics-news.xml",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.sec.gov/news/pressreleases.rss",
    "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
    "https://oilprice.com/rss/main",
)


def _merge_unique(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for feed in group:
            if feed in seen:
                continue
            seen.add(feed)
            merged.append(feed)
    return tuple(merged)


def news_source_alias(feed_or_url: str, source_hint: str = "") -> str:
    direct_target = _split_direct_target(feed_or_url) or _split_direct_target(source_hint)
    if direct_target in _DIRECT_SOURCE_CODES:
        return _DIRECT_SOURCE_CODES[direct_target]

    for candidate in (feed_or_url, source_hint):
        code = _match_known_host_code(_extract_host(candidate))
        if code:
            return code

    return ""


def news_source_code(feed_or_url: str, source_hint: str = "") -> str:
    alias = news_source_alias(feed_or_url, source_hint)
    if alias:
        return alias

    for candidate in (feed_or_url, source_hint):
        host = _extract_host(candidate)
        if not host:
            continue
        token = host.split(".")[0].upper()
        if token:
            return (token[:4] or "RSS").strip() or "RSS"

    hint = (source_hint or "").strip()
    if hint:
        return (hint[:4] or "RSS").upper()
    return "RSS"


DEFAULT_NEWS_RSS_FEEDS: tuple[str, ...] = _merge_unique(CORE_NEWS_RSS_FEEDS, WORLDMONITOR_TRADING_RSS_FEEDS)

NEWS_RSS_PRESETS: dict[str, tuple[str, ...]] = {
    "default": DEFAULT_NEWS_RSS_FEEDS,
    "core": CORE_NEWS_RSS_FEEDS,
    "worldmonitor_trading": WORLDMONITOR_TRADING_RSS_FEEDS,
}

_NEWS_FEED_DESCRIPTORS: tuple[NewsFeedDescriptor, ...] = (
    *(NewsFeedDescriptor(feed=feed, code=news_source_code(feed), group=CORE_GROUP, tier=PRIMARY_TIER) for feed in CORE_NEWS_RSS_FEEDS),
    *(NewsFeedDescriptor(feed=feed, code=news_source_code(feed), group=WORLDMONITOR_TRADING_GROUP, tier=SUPPLEMENTAL_TIER) for feed in WORLDMONITOR_TRADING_RSS_FEEDS),
)

_EXACT_GROUP: dict[str, str] = {descriptor.feed: descriptor.group for descriptor in _NEWS_FEED_DESCRIPTORS}
_EXACT_TIER: dict[str, str] = {descriptor.feed: descriptor.tier for descriptor in _NEWS_FEED_DESCRIPTORS}
_CORE_CODES: set[str] = {descriptor.code for descriptor in _NEWS_FEED_DESCRIPTORS if descriptor.group == CORE_GROUP}
_WM_CODES: set[str] = {descriptor.code for descriptor in _NEWS_FEED_DESCRIPTORS if descriptor.group == WORLDMONITOR_TRADING_GROUP}


def news_source_group(feed_or_url: str, source_hint: str = "") -> str:
    exact = (feed_or_url or "").strip()
    if exact in _EXACT_GROUP:
        return _EXACT_GROUP[exact]

    alias = news_source_alias(feed_or_url, source_hint) or (source_hint or "").strip().upper()
    if alias in _CORE_CODES:
        return CORE_GROUP
    if alias in _WM_CODES:
        return WORLDMONITOR_TRADING_GROUP
    return UNKNOWN_GROUP


def news_source_tier(feed_or_url: str, source_hint: str = "") -> str:
    exact = (feed_or_url or "").strip()
    if exact in _EXACT_TIER:
        return _EXACT_TIER[exact]

    group = news_source_group(feed_or_url, source_hint)
    if group == CORE_GROUP:
        return PRIMARY_TIER
    if group == WORLDMONITOR_TRADING_GROUP:
        return SUPPLEMENTAL_TIER
    return UNKNOWN_TIER


def append_source_meta_tags(categories: list[str] | tuple[str, ...] | None, *, feed_or_url: str, source_hint: str = "") -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in categories or []:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)

    group = news_source_group(feed_or_url, source_hint)
    tier = news_source_tier(feed_or_url, source_hint)
    if group != UNKNOWN_GROUP:
        tag = f"{_INTERNAL_SOURCE_GROUP_PREFIX}{group}"
        if tag not in seen:
            merged.append(tag)
            seen.add(tag)
    if tier != UNKNOWN_TIER:
        tag = f"{_INTERNAL_SOURCE_TIER_PREFIX}{tier}"
        if tag not in seen:
            merged.append(tag)
            seen.add(tag)
    return merged


def extract_source_meta_tags(categories: list[str] | tuple[str, ...] | None) -> tuple[str, str]:
    group = UNKNOWN_GROUP
    tier = UNKNOWN_TIER
    for item in categories or []:
        text = str(item).strip().lower()
        if text.startswith(_INTERNAL_SOURCE_GROUP_PREFIX):
            group = text[len(_INTERNAL_SOURCE_GROUP_PREFIX) :] or UNKNOWN_GROUP
        elif text.startswith(_INTERNAL_SOURCE_TIER_PREFIX):
            tier = text[len(_INTERNAL_SOURCE_TIER_PREFIX) :] or UNKNOWN_TIER
    return group, tier


def _preset_names(value: str | None) -> list[str]:
    raw = (value or "").strip()
    if not raw:
        return ["default"]

    names: list[str] = []
    for piece in raw.replace("\n", ",").split(","):
        name = piece.strip().lower().replace("-", "_")
        if name:
            names.append(name)
    return names or ["default"]

def news_rss_feeds_for_preset(preset_value: str | None = None) -> list[str]:
    feeds: list[str] = []
    seen: set[str] = set()

    preset_names = _preset_names(preset_value)
    matched = False
    for name in preset_names:
        preset = NEWS_RSS_PRESETS.get(name)
        if not preset:
            continue
        matched = True
        for feed in preset:
            if feed in seen:
                continue
            seen.add(feed)
            feeds.append(feed)

    if matched:
        return feeds
    return list(DEFAULT_NEWS_RSS_FEEDS)


def default_news_rss_feeds() -> list[str]:
    return news_rss_feeds_for_preset("default")


def default_news_rss_feeds_value(preset_value: str | None = None) -> str:
    return ",".join(news_rss_feeds_for_preset(preset_value))
