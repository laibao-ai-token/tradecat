"""Direct fast-news connectors reused from the TUI news page.

These sources are higher frequency than normal RSS feeds and are expressed as
`direct://...` specs so they can live in the same configured feed list.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urljoin

import aiohttp

DIRECT_NEWS_PREFIX = "direct://"
_DIRECT_NEWS_ALIASES: dict[str, str] = {
    "jin10": "J10",
    "gelonghui/live": "GLH",
    "10jqka/realtimenews": "THS",
    "sina/7x24": "SINA",
    "eastmoney/kuaixun": "EM24",
    "cls/telegraph": "CLS",
    "wallstreetcn/live": "WSCN",
    "eeo/kuaixun": "EEO",
}
_TAG_RE = re.compile(r"<[^>]+>")
_NUMERIC_RE = re.compile(r"-?\d+(?:\.\d+)?")
_NAIVE_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")


def is_direct_news_source(source: str) -> bool:
    return (source or "").strip().lower().startswith(DIRECT_NEWS_PREFIX)


def split_direct_news_source(source: str) -> str:
    value = (source or "").strip().lower()
    if value.startswith(DIRECT_NEWS_PREFIX):
        return value[len(DIRECT_NEWS_PREFIX) :].strip().strip("/")
    return value


def direct_news_source_label(source: str) -> str:
    return _DIRECT_NEWS_ALIASES.get(split_direct_news_source(source), "LIVE")


def _normalize_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = html.unescape(raw)
    raw = _TAG_RE.sub(" ", raw)
    return " ".join(raw.split())


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    raw = str(value).strip()
    if not raw:
        return None
    if _NUMERIC_RE.fullmatch(raw):
        try:
            return _parse_timestamp(float(raw))
        except Exception:
            return None

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _with_default_tz(value: object, tz_suffix: str = "+08:00") -> object:
    raw = str(value or "").strip()
    if not raw:
        return value
    if re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", raw):
        return raw
    if _NAIVE_DATETIME_RE.match(raw):
        return f"{raw}{tz_suffix}"
    return value


def _symbol_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        candidate = ""
        if isinstance(item, dict):
            for key in ("symbol", "code", "stockCode", "stock_code", "secuCode", "secu_code", "ticker", "name"):
                candidate = str(item.get(key) or "").strip()
                if candidate:
                    break
        else:
            candidate = str(item or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def _coalesce_url(url: object, fallback: str) -> str:
    candidate = str(url or "").strip()
    if not candidate:
        return fallback
    if candidate.startswith("//"):
        return f"https:{candidate}"
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    return urljoin(fallback, candidate)


async def _http_get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, object] | None = None,
    timeout_s: float = 10.0,
    allow_jsonp: bool = False,
    proxy: str | None = None,
) -> Any:
    query = None
    if params:
        query = [(str(key), str(value)) for key, value in params.items()]
    req_headers = {"User-Agent": "TradeCat/markets-service live-news"}
    if headers:
        req_headers.update(headers)
    timeout = aiohttp.ClientTimeout(total=float(timeout_s))
    async with session.get(url, params=query, headers=req_headers, proxy=proxy, timeout=timeout) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}")
        raw = (await resp.text(errors="ignore")).lstrip("﻿").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if not allow_jsonp:
            raise
        start = raw.find("(")
        end = raw.rfind(")")
        if start >= 0 and end > start:
            return json.loads(raw[start + 1 : end].strip())
        raise


async def _http_get_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, object] | None = None,
    timeout_s: float = 10.0,
    proxy: str | None = None,
) -> str:
    query = None
    if params:
        query = [(str(key), str(value)) for key, value in params.items()]
    req_headers = {"User-Agent": "TradeCat/markets-service live-news"}
    if headers:
        req_headers.update(headers)
    timeout = aiohttp.ClientTimeout(total=float(timeout_s))
    async with session.get(url, params=query, headers=req_headers, proxy=proxy, timeout=timeout) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}")
        return await resp.text(errors="ignore")


def _entry(
    *,
    title: object,
    summary: object,
    url: object,
    published_at: object,
    categories: list[str] | None = None,
    symbols: list[str] | None = None,
) -> dict[str, Any] | None:
    title_text = _normalize_text(title)
    summary_text = _normalize_text(summary)
    if not title_text:
        title_text = summary_text
    if not title_text:
        return None
    published_dt = _parse_timestamp(published_at)
    if published_dt is None:
        return None
    return {
        "title": title_text,
        "summary": summary_text or title_text,
        "url": str(url or "").strip(),
        "published_at": published_dt,
        "categories": [str(cat).strip() for cat in (categories or []) if str(cat).strip()],
        "symbols": list(symbols or []),
        "language": "zh",
    }


async def fetch_direct_news_entries(
    session: aiohttp.ClientSession,
    source: str,
    *,
    timeout_s: float,
    proxy: str | None = None,
) -> list[dict[str, Any]]:
    target = split_direct_news_source(source)

    if target == "jin10":
        payload = await _http_get_json(
            session,
            "https://flash-api.jin10.com/get_flash_list",
            headers={"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0"},
            params={"channel": "-8200", "vip": "1"},
            timeout_s=timeout_s,
            proxy=proxy,
        )
        rows = payload.get("data") if isinstance(payload, dict) else []
        entries: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or int(row.get("type") or 0) == 1:
                continue
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            content = _normalize_text(data.get("content") if isinstance(data, dict) else "")
            title = _normalize_text(data.get("title") if isinstance(data, dict) else "")
            if not title and content:
                matched = re.match(r"^【([^】]+)】\s*(.*)$", content)
                if matched:
                    title = matched.group(1).strip()
                    content = matched.group(2).strip() or content
                else:
                    title = content
            entry = _entry(
                title=title,
                summary=content,
                url=_coalesce_url(
                    data.get("source_link") if isinstance(data, dict) else "",
                    "https://www.jin10.com/",
                ),
                published_at=_with_default_tz(row.get("time") if isinstance(row, dict) else None),
                categories=[
                    _normalize_text(tag.get("name"))
                    for tag in (row.get("tags") if isinstance(row.get("tags"), list) else [])
                    if isinstance(tag, dict)
                ],
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "gelonghui/live":
        payload = await _http_get_json(
            session,
            "https://www.gelonghui.com/api/live-channels/all/lives/v4",
            timeout_s=timeout_s,
            proxy=proxy,
        )
        rows = payload.get("result") if isinstance(payload, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            source_info = row.get("source") if isinstance(row.get("source"), dict) else {}
            categories = [
                _normalize_text(source_info.get("name") if isinstance(source_info, dict) else ""),
                _normalize_text(row.get("contentPrefix")),
            ]
            entry = _entry(
                title=row.get("title") or row.get("content"),
                summary=row.get("content") or row.get("title"),
                url=_coalesce_url(row.get("route"), "https://www.gelonghui.com/live"),
                published_at=row.get("createTimestamp"),
                categories=categories,
                symbols=_symbol_list(row.get("relatedStocks")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "10jqka/realtimenews":
        payload = await _http_get_json(
            session,
            "https://news.10jqka.com.cn/tapp/news/push/stock",
            params={"page": "1", "tag": ""},
            timeout_s=timeout_s,
            proxy=proxy,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        rows = data.get("list") if isinstance(data, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            categories: list[str] = []
            for tag in (row.get("tags") if isinstance(row.get("tags"), list) else []):
                if isinstance(tag, dict):
                    categories.append(_normalize_text(tag.get("name")))
            for tag in (row.get("tagInfo") if isinstance(row.get("tagInfo"), list) else []):
                if isinstance(tag, dict):
                    categories.append(_normalize_text(tag.get("name")))
            entry = _entry(
                title=row.get("title") or row.get("digest"),
                summary=row.get("short") or row.get("digest") or row.get("title"),
                url=_coalesce_url(row.get("url") or row.get("shareUrl"), "https://news.10jqka.com.cn/realtimenews.html"),
                published_at=row.get("ctime") or row.get("rtime"),
                categories=categories,
                symbols=_symbol_list(row.get("stock")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "sina/7x24":
        payload = await _http_get_json(
            session,
            "https://zhibo.sina.com.cn/api/zhibo/feed",
            params={"zhibo_id": "152", "page": "1", "pagesize": "50", "tag_id": "0", "dire": "f"},
            timeout_s=timeout_s,
            proxy=proxy,
        )
        data = payload.get("result") if isinstance(payload, dict) else {}
        feed = data.get("data") if isinstance(data, dict) else {}
        feed_info = feed.get("feed") if isinstance(feed, dict) else {}
        rows = feed_info.get("list") if isinstance(feed_info, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            ext_raw = row.get("ext")
            ext: dict[str, object] = {}
            if isinstance(ext_raw, str) and ext_raw.strip():
                try:
                    parsed_ext = json.loads(ext_raw)
                    if isinstance(parsed_ext, dict):
                        ext = parsed_ext
                except Exception:
                    ext = {}
            categories = [
                _normalize_text(tag.get("name"))
                for tag in (row.get("tag") if isinstance(row.get("tag"), list) else [])
                if isinstance(tag, dict)
            ]
            entry = _entry(
                title=row.get("rich_text"),
                summary=row.get("rich_text"),
                url=_coalesce_url(
                    row.get("docurl") or ext.get("docurl"),
                    "https://finance.sina.com.cn/7x24/notification.shtml",
                ),
                published_at=_with_default_tz(row.get("create_time")),
                categories=categories,
                symbols=_symbol_list(ext.get("stocks")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "eastmoney/kuaixun":
        payload = await _http_get_json(
            session,
            "http://newsapi.eastmoney.com/kuaixun/v2/api/list",
            params={"column": "102", "p": "1", "limit": "50", "callback": "cb"},
            timeout_s=timeout_s,
            allow_jsonp=True,
            proxy=proxy,
        )
        rows = payload.get("news") if isinstance(payload, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            entry = _entry(
                title=row.get("title") or row.get("digest"),
                summary=row.get("digest") or row.get("title"),
                url=_coalesce_url(row.get("url_m") or row.get("url_w"), "https://kuaixun.eastmoney.com/7_24.html"),
                published_at=_with_default_tz(row.get("showtime") or row.get("ordertime")),
                categories=[_normalize_text(row.get("Art_Media_Name"))],
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "cls/telegraph":
        raw = await _http_get_text(
            session,
            "https://www.cls.cn/telegraph",
            timeout_s=timeout_s,
            proxy=proxy,
        )
        matched = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', raw, re.S)
        if not matched:
            raise RuntimeError("missing_cls_next_data")
        payload = json.loads(matched.group(1))
        props = payload.get("props") if isinstance(payload, dict) else {}
        state: dict[str, Any] = {}
        if isinstance(props, dict):
            state = props.get("initialState") if isinstance(props.get("initialState"), dict) else {}
            if not state:
                page_props = props.get("pageProps") if isinstance(props.get("pageProps"), dict) else {}
                state = page_props.get("initialState") if isinstance(page_props.get("initialState"), dict) else {}
        telegraph = state.get("telegraph") if isinstance(state, dict) else {}
        rows = telegraph.get("telegraphList") if isinstance(telegraph, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            categories = []
            for subject in (row.get("subjects") if isinstance(row.get("subjects"), list) else []):
                if isinstance(subject, dict):
                    categories.append(_normalize_text(subject.get("subject_name")))
            entry = _entry(
                title=row.get("title") or row.get("content"),
                summary=row.get("brief") or row.get("content") or row.get("title"),
                url=_coalesce_url(row.get("shareurl"), "https://www.cls.cn/telegraph"),
                published_at=row.get("ctime") or row.get("modified_time"),
                categories=categories,
                symbols=_symbol_list(row.get("stock_list")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "wallstreetcn/live":
        payload = await _http_get_json(
            session,
            "https://api-one.wallstcn.com/apiv1/content/lives",
            params={"channel": "global-channel", "limit": "100"},
            timeout_s=timeout_s,
            proxy=proxy,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        rows = data.get("items") if isinstance(data, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            categories = [_normalize_text(row.get("global_channel_name"))]
            categories.extend(
                _normalize_text(tag)
                for tag in (row.get("channels") if isinstance(row.get("channels"), list) else [])
                if isinstance(tag, str)
            )
            entry = _entry(
                title=row.get("title") or row.get("content_text"),
                summary=row.get("content_text") or row.get("title"),
                url=_coalesce_url(row.get("uri"), "https://wallstreetcn.com/live"),
                published_at=row.get("display_time"),
                categories=categories,
                symbols=_symbol_list(row.get("symbols")),
            )
            if entry is not None:
                entries.append(entry)
        return entries

    if target == "eeo/kuaixun":
        payload = await _http_get_json(
            session,
            "https://app.eeo.com.cn/",
            params={
                "app": "article",
                "controller": "index",
                "action": "getMoreArticle",
                "catid": "3690",
                "uuid": "b048c7211db949eeb7443cd5b9b3bfe3",
                "page": "1",
                "pageSize": "50",
            },
            timeout_s=timeout_s,
            proxy=proxy,
        )
        rows = payload.get("data") if isinstance(payload, dict) else []
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            entry = _entry(
                title=row.get("title") or row.get("description"),
                summary=row.get("description") or row.get("content") or row.get("title"),
                url=_coalesce_url(row.get("url"), "https://www.eeo.com.cn/kuaixun/"),
                published_at=_with_default_tz(row.get("published")),
                categories=[_normalize_text(row.get("catname"))],
            )
            if entry is not None:
                entries.append(entry)
        return entries

    raise ValueError(f"unsupported direct news source: {source}")
