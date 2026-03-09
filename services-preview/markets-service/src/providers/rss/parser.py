"""Minimal RSS/Atom parser (stdlib only).

We avoid extra third-party deps (e.g. feedparser) for portability.
The output is a list of dict entries with best-effort fields:
- title, url, published_at (datetime), summary, content, categories
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import re
from typing import Any
import xml.etree.ElementTree as ET


_TAG_RE = re.compile(r"<[^>]+>")


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    raw = (el.text or "").strip()
    return raw


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    raw = html.unescape(raw)
    raw = _TAG_RE.sub(" ", raw)
    return " ".join(raw.split())


def _parse_dt(raw: str) -> datetime | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Atom often uses RFC3339/ISO8601 (e.g. 2026-03-05T12:34:56Z)
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _find_first(el: ET.Element, names: tuple[str, ...]) -> ET.Element | None:
    for child in el:
        if _local(child.tag) in names:
            return child
    return None


def _all_children(el: ET.Element, name: str) -> list[ET.Element]:
    out: list[ET.Element] = []
    for child in el:
        if _local(child.tag) == name:
            out.append(child)
    return out


def _atom_link(entry: ET.Element) -> str:
    for link in _all_children(entry, "link"):
        rel = (link.attrib.get("rel") or "").strip().lower()
        href = (link.attrib.get("href") or "").strip()
        if not href:
            continue
        if rel in ("", "alternate"):
            return href
    # fall back to any href
    for link in _all_children(entry, "link"):
        href = (link.attrib.get("href") or "").strip()
        if href:
            return href
    return ""


@dataclass(frozen=True)
class ParsedFeed:
    title: str
    entries: list[dict[str, Any]]
    parse_error: str | None = None


def parse_feed(xml_text: str) -> ParsedFeed:
    """Parse RSS/Atom XML text into normalized entries."""
    xml_text = (xml_text or "").strip()
    if not xml_text:
        return ParsedFeed(title="", entries=[], parse_error="empty_payload")

    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:
        return ParsedFeed(title="", entries=[], parse_error=f"xml_parse_error: {exc}")

    root_name = _local(root.tag).lower()

    if root_name == "feed":  # Atom
        feed_title = _strip_html(_text(_find_first(root, ("title",))))
        entries: list[dict[str, Any]] = []
        for entry in _all_children(root, "entry"):
            title = _strip_html(_text(_find_first(entry, ("title",))))
            url = _atom_link(entry) or _strip_html(_text(_find_first(entry, ("id",))))
            summary = _strip_html(_text(_find_first(entry, ("summary",))))
            content = _strip_html(_text(_find_first(entry, ("content",))))
            published = _text(_find_first(entry, ("published", "updated")))
            published_at = _parse_dt(published)
            categories: list[str] = []
            for cat in _all_children(entry, "category"):
                term = (cat.attrib.get("term") or "").strip()
                if term:
                    categories.append(term)
                else:
                    categories.append(_strip_html(_text(cat)))
            entries.append(
                {
                    "title": title,
                    "url": url,
                    "published_at": published_at,
                    "summary": summary,
                    "content": content,
                    "categories": [c for c in categories if c],
                }
            )
        return ParsedFeed(title=feed_title, entries=entries)

    # RSS 2.0: <rss><channel>...<item>...</item></channel></rss>
    channel = _find_first(root, ("channel",)) or root
    feed_title = _strip_html(_text(_find_first(channel, ("title",))))
    entries = []
    for item in channel.iter():
        if _local(item.tag) != "item":
            continue
        title = _strip_html(_text(_find_first(item, ("title",))))
        url = _strip_html(_text(_find_first(item, ("link",))))
        if not url:
            guid = _strip_html(_text(_find_first(item, ("guid",))))
            url = guid
        pub = _text(_find_first(item, ("pubDate", "date", "published", "updated")))
        published_at = _parse_dt(pub)
        summary = _strip_html(_text(_find_first(item, ("description", "summary"))))
        content_el = None
        for child in item:
            if _local(child.tag) in ("encoded", "content"):
                content_el = child
                break
        content = _strip_html(_text(content_el))
        categories = [_strip_html(_text(c)) for c in _all_children(item, "category")]
        entries.append(
            {
                "title": title,
                "url": url,
                "published_at": published_at,
                "summary": summary,
                "content": content,
                "categories": [c for c in categories if c],
            }
        )

    return ParsedFeed(title=feed_title, entries=entries)
