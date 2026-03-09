"""Rule-based news event clustering for the TUI news page."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

from .news_defaults import PRIMARY_TIER, SUPPLEMENTAL_TIER

if TYPE_CHECKING:
    from .tui import NewsItem


_EVENT_TIME_WINDOW_S = 90 * 60
_EN_WORD_RE = re.compile(r"[a-z]{2,}(?:['-][a-z0-9]+)*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_SPACE_RE = re.compile(r"\s+")
_PREFIX_PUNCT_RE = re.compile(r"^[\s\-|:：,，;；|｜/\\]+")
_SUFFIX_PUNCT_RE = re.compile(r"[\s\-|:：,，;；|｜/\\。．.!！?？、]+$")
_TITLE_BRACKET_RE = re.compile(r"^[\[【](.{4,80}?)[\]】](?:\s|[\-|:：,，;；|｜。．.!！?？、])*")

_SOURCE_DATE_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:财联社|格隆汇|金十(?:数据)?|同花顺|东方财富(?:网)?|华尔街见闻|经济观察网|新浪财经)\s*\d{1,2}月\d{1,2}日(?:电|讯)?(?:[丨｜|:：,，;；\s-]+)?"),
    re.compile(r"^(?:globenewswire|reuters|cnbc|benzinga|seeking\s+alpha|federal\s+reserve|sec)\s*(?:[-:|]\s*)?", re.IGNORECASE),
)

_EN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "to",
    "was",
    "were",
    "with",
}

_CJK_STRUCTURAL_CHARS = set("的了在将与和及对称说表示指出宣布消息日电日讯今日昨天目前已经继续正在进行相关方面有关由于对于通过要求呼吁称据")
_SEVERITY_RANK = {"HIGH": 3, "MID": 2, "LOW": 1}
_CATEGORY_PRIORITY = ("宏观", "政策", "公司", "加密", "商品", "外汇")


@dataclass(frozen=True)
class NewsEvent:
    id: str
    primary_title: str
    primary_summary: str
    primary_source: str
    primary_url: str
    category: str
    severity: str
    direction: str
    confidence: float
    symbols: tuple[str, ...]
    impact_assets: tuple[str, ...]
    suggestion: str
    source_group: str
    source_tier: str
    first_seen_at: float
    last_updated_at: float
    article_count: int
    source_count: int
    top_sources: tuple[str, ...]
    items: tuple["NewsItem", ...]


@dataclass(frozen=True)
class _PreparedNewsItem:
    item: "NewsItem"
    display_title: str
    normalized_title: str
    tokens: frozenset[str]


@dataclass
class _NewsCluster:
    prepared: list[_PreparedNewsItem]
    first_seen_at: float
    last_updated_at: float

    def add(self, prepared: _PreparedNewsItem) -> None:
        self.prepared.append(prepared)
        self.first_seen_at = min(self.first_seen_at, float(prepared.item.published_at))
        self.last_updated_at = max(self.last_updated_at, float(prepared.item.published_at))

    def match_score(self, prepared: _PreparedNewsItem, *, max_time_gap_s: float) -> float:
        # Keep each event focused on a short time burst; avoid merging repeated hourly headlines.
        if self.last_updated_at - float(prepared.item.published_at) > max_time_gap_s:
            return 0.0
        best = 0.0
        for existing in self.prepared:
            score = _title_similarity(prepared, existing)
            if score > best:
                best = score
            if best >= 0.96:
                break
        return best


def cluster_news_items(items: Sequence["NewsItem"], *, max_time_gap_s: float = _EVENT_TIME_WINDOW_S) -> list[NewsEvent]:
    """Merge similar news articles into short-lived events for the TUI list."""

    prepared_items = [_prepare_item(item) for item in items if (item.title or item.summary or item.url)]
    if not prepared_items:
        return []

    prepared_items.sort(key=lambda entry: (float(entry.item.published_at), entry.item.id), reverse=True)

    clusters: list[_NewsCluster] = []
    for prepared in prepared_items:
        best_cluster: _NewsCluster | None = None
        best_score = 0.0
        for cluster in clusters:
            score = cluster.match_score(prepared, max_time_gap_s=max_time_gap_s)
            if score > best_score:
                best_score = score
                best_cluster = cluster
        if best_cluster is not None and best_score >= 0.50:
            best_cluster.add(prepared)
        else:
            ts = float(prepared.item.published_at)
            clusters.append(_NewsCluster(prepared=[prepared], first_seen_at=ts, last_updated_at=ts))

    events = [_build_event(cluster) for cluster in clusters]
    events.sort(key=lambda event: (float(event.last_updated_at), float(event.first_seen_at), event.id), reverse=True)
    return events


def _prepare_item(item: "NewsItem") -> _PreparedNewsItem:
    base_title = (item.title or item.summary or item.url or item.id).strip()
    display_title = _display_title(base_title)
    normalized_title = _normalize_title(display_title)
    tokens = _tokenize_title(display_title, normalized_title)
    return _PreparedNewsItem(item=item, display_title=display_title or base_title, normalized_title=normalized_title, tokens=tokens)


def _display_title(raw_title: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(raw_title or "")).strip()
    if not text:
        return ""

    bracket_match = _TITLE_BRACKET_RE.match(text)
    if bracket_match:
        inner = bracket_match.group(1).strip()
        if inner:
            text = inner

    changed = True
    while changed and text:
        changed = False
        for pattern in _SOURCE_DATE_PREFIX_PATTERNS:
            updated = pattern.sub("", text, count=1).strip()
            if updated != text:
                text = updated
                changed = True

    text = _PREFIX_PUNCT_RE.sub("", text)
    text = _SUFFIX_PUNCT_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def _normalize_title(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.lower()
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
    return normalized.strip()


def _tokenize_title(display_title: str, normalized_title: str) -> frozenset[str]:
    lowered = display_title.lower()
    tokens: set[str] = set()

    for word in _EN_WORD_RE.findall(lowered):
        if word not in _EN_STOPWORDS:
            tokens.add(word)

    for number in _NUMBER_RE.findall(lowered):
        tokens.add(number)

    for run in _CJK_RUN_RE.findall(display_title):
        run = run.strip()
        if len(run) < 2:
            continue
        if len(run) <= 4:
            if _is_informative_cjk_token(run):
                tokens.add(run)
            continue
        for size in (2, 3):
            if len(run) < size:
                continue
            for idx in range(len(run) - size + 1):
                piece = run[idx : idx + size]
                if _is_informative_cjk_token(piece):
                    tokens.add(piece)

    if normalized_title and not tokens:
        tokens.add(normalized_title[:16])

    return frozenset(tokens)


def _is_informative_cjk_token(token: str) -> bool:
    if not token:
        return False
    if len(set(token)) == 1:
        return False
    structural = sum(1 for ch in token if ch in _CJK_STRUCTURAL_CHARS)
    return structural < len(token)


def _title_similarity(left: _PreparedNewsItem, right: _PreparedNewsItem) -> float:
    if not left.normalized_title or not right.normalized_title:
        return 0.0
    if left.normalized_title == right.normalized_title:
        return 1.0

    shorter, longer = sorted((left.normalized_title, right.normalized_title), key=len)
    if len(shorter) >= 8 and shorter in longer:
        return 0.96

    if not left.tokens or not right.tokens:
        return 0.0

    intersection = left.tokens & right.tokens
    if not intersection:
        return 0.0

    union_size = len(left.tokens | right.tokens)
    min_size = min(len(left.tokens), len(right.tokens))
    if union_size <= 0 or min_size <= 0:
        return 0.0

    strong_intersection = {token for token in intersection if _is_strong_token(token)}
    jaccard = len(intersection) / float(union_size)
    coverage = len(intersection) / float(min_size)

    if len(strong_intersection) >= 4 and coverage >= 0.45:
        return max(jaccard, coverage)
    if len(strong_intersection) >= 3 and (jaccard >= 0.32 or coverage >= 0.55):
        return max(jaccard, coverage)
    if len(strong_intersection) >= 2 and coverage >= 0.72:
        return max(jaccard, coverage)
    return 0.0


def _is_strong_token(token: str) -> bool:
    if not token:
        return False
    if any(ch.isdigit() for ch in token):
        return True
    if _CJK_RUN_RE.fullmatch(token):
        return len(token) >= 3
    return len(token) >= 4


def _build_event(cluster: _NewsCluster) -> NewsEvent:
    prepared = sorted(cluster.prepared, key=lambda entry: float(entry.item.published_at), reverse=True)
    items = [entry.item for entry in prepared]

    primary_item = min(prepared, key=_primary_item_sort_key).item
    primary_title = _choose_primary_title(prepared)
    primary_summary = _pick_first_text([primary_item.summary, primary_item.title])

    top_sources = _top_sources(items)
    category = _pick_category(items, fallback=primary_item.category)
    severity = _pick_severity(items, fallback=primary_item.severity)
    direction = _pick_mode([item.direction for item in items], fallback=primary_item.direction or "Neutral")
    suggestion = _pick_first_text(item.suggestion for item in items)
    symbols = _merge_values(item.symbols for item in items)
    impact_assets = _merge_values(item.impact_assets for item in items)
    confidence_values = [float(item.confidence) for item in items if item.confidence is not None]
    confidence = max(confidence_values) if confidence_values else 0.50

    digest = hashlib.sha1(
        f"{int(cluster.first_seen_at)}|{_normalize_title(primary_title)}|{'|'.join(top_sources[:3])}".encode("utf-8")
    ).hexdigest()[:12]

    return NewsEvent(
        id=f"evt-{int(cluster.first_seen_at)}-{digest}",
        primary_title=primary_title,
        primary_summary=primary_summary,
        primary_source=(primary_item.source or "RSS").strip() or "RSS",
        primary_url=(primary_item.url or "").strip(),
        category=category,
        severity=severity,
        direction=direction,
        confidence=confidence,
        symbols=symbols,
        impact_assets=impact_assets,
        suggestion=suggestion,
        source_group=primary_item.source_group,
        source_tier=primary_item.source_tier,
        first_seen_at=float(cluster.first_seen_at),
        last_updated_at=float(cluster.last_updated_at),
        article_count=len(items),
        source_count=len({(item.source or "").strip().upper() for item in items if (item.source or "").strip()}) or 1,
        top_sources=top_sources,
        items=tuple(items),
    )


def _primary_item_sort_key(prepared: _PreparedNewsItem) -> tuple[int, int, int, float]:
    item = prepared.item
    display_len = len(prepared.display_title or item.title or "")
    verbosity = max(0, len(item.title or "") - len(prepared.display_title or ""))
    length_penalty = 0 if 6 <= display_len <= 48 else 1
    return (_tier_rank(item.source_tier), length_penalty, verbosity, -float(item.published_at))


def _choose_primary_title(prepared_items: Sequence[_PreparedNewsItem]) -> str:
    best = min(prepared_items, key=_title_quality_sort_key)
    return best.display_title or (best.item.title or best.item.summary or "")


def _title_quality_sort_key(prepared: _PreparedNewsItem) -> tuple[int, int, int, float]:
    original = prepared.item.title or ""
    display = prepared.display_title or original
    length = len(display)
    length_penalty = 0 if 6 <= length <= 48 else 1
    verbosity = max(0, len(original) - len(display))
    return (length_penalty, verbosity, length, -float(prepared.item.published_at))


def _tier_rank(source_tier: str) -> int:
    normalized = (source_tier or "").strip().lower()
    if normalized == PRIMARY_TIER:
        return 0
    if normalized == SUPPLEMENTAL_TIER:
        return 1
    return 2


def _top_sources(items: Sequence["NewsItem"], limit: int = 5) -> tuple[str, ...]:
    latest_by_source: dict[str, NewsItem] = {}
    for item in items:
        source = (item.source or "").strip().upper()
        if not source:
            continue
        current = latest_by_source.get(source)
        if current is None or float(item.published_at) > float(current.published_at):
            latest_by_source[source] = item
    ordered = sorted(
        latest_by_source.values(),
        key=lambda item: (_tier_rank(item.source_tier), -float(item.published_at), (item.source or "")),
    )
    return tuple((item.source or "RSS").strip() or "RSS" for item in ordered[:limit])


def _pick_category(items: Sequence["NewsItem"], *, fallback: str) -> str:
    counter = Counter((item.category or "").strip() for item in items if (item.category or "").strip())
    if not counter:
        return fallback or "宏观"
    return min(counter, key=lambda key: (-counter[key], _category_rank(key), key))


def _category_rank(category: str) -> int:
    try:
        return _CATEGORY_PRIORITY.index(category)
    except ValueError:
        return len(_CATEGORY_PRIORITY)


def _pick_severity(items: Sequence["NewsItem"], *, fallback: str) -> str:
    best = fallback or "MID"
    best_rank = _SEVERITY_RANK.get(best, 0)
    for item in items:
        rank = _SEVERITY_RANK.get((item.severity or "").upper(), 0)
        if rank > best_rank:
            best = (item.severity or best).upper()
            best_rank = rank
    return best or "MID"


def _pick_mode(values: Iterable[str], *, fallback: str) -> str:
    counter = Counter((value or "").strip() for value in values if (value or "").strip())
    if not counter:
        return fallback
    return min(counter, key=lambda key: (-counter[key], key))


def _merge_values(groups: Iterable[Sequence[str]]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for values in groups:
        for value in values:
            normalized = (value or "").strip()
            if not normalized:
                continue
            key = normalized.upper()
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
    return tuple(merged)


def _pick_first_text(values: Iterable[str], fallback: str = "") -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return fallback
