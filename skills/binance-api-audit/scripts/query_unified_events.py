#!/usr/bin/env python3
"""
Datacat unified.db 币种命中扫描（只读）
用途：
- 按币种 + 类别（新闻/公告/信号/清算/链上/大额转账）筛选事件
- 支持多币种、时间窗、关键词与 tag 组合
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_COINS = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE"]

CATEGORY_TAGS = {
    "signal": {"信号"},
    "news": {"新闻"},
    "announcement": {"公告"},
    "liquidation": {"清算"},
    "onchain": {"链上"},
    "sentiment": {"舆情"},
    "twitter": {"推特"},
}

CATEGORY_KEYWORDS = {
    "signal": ["信号", "signal", "entry", "止损", "止盈", "做多", "做空", "long", "short"],
    "news": ["新闻", "快讯", "突发", "breaking", "headline"],
    "announcement": ["公告", "announcement", "更新", "上线", "下线"],
    "liquidation": ["清算", "爆仓", "强平", "liquidation", "liquidated", "liquidations"],
    "onchain": ["链上", "on-chain", "onchain", "tx", "transaction", "hash", "地址", "区块"],
    "transfer": [
        "转账",
        "大额",
        "whale",
        "鲸鱼",
        "inflow",
        "outflow",
        "充值",
        "提现",
        "moved",
        "transfer",
        "sent",
        "received",
        "deposit",
        "withdrawal",
    ],
}

JSON_TEXT_KEYS = {
    "text",
    "content",
    "title",
    "raw_text",
    "message",
    "msg",
    "body",
    "desc",
    "summary",
    "signal",
    "signal_text",
}

JSON_COIN_KEYS = {
    "symbol",
    "coin",
    "ticker",
    "pair",
    "base",
    "asset",
    "currency",
}


@dataclass
class EventItem:
    id: int
    ts: str
    type: str | None
    tag: str | None
    source_key: str | None
    label: str | None
    content: str | None
    data: Any


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_text(text: str) -> str:
    return text.upper()


def coin_match(text: str, coin: str) -> bool:
    if not text:
        return False
    u = normalize_text(text)
    coin_u = coin.upper()
    if coin_u in ("USD", "USDT", "USDC"):
        return False
    pair_patterns = [
        f"{coin_u}USDT",
        f"{coin_u}-USDT",
        f"{coin_u}/USDT",
        f"{coin_u}USD",
        f"{coin_u}-USD",
        f"{coin_u}/USD",
        f"{coin_u}PERP",
    ]
    for p in pair_patterns:
        if p in u:
            return True
    if re.search(rf"(?<![A-Z0-9]){re.escape(coin_u)}(?![A-Z0-9])", u):
        return True
    if re.search(rf"\\${re.escape(coin_u)}\\b", text, re.IGNORECASE):
        return True
    if re.search(rf"#{re.escape(coin_u)}\\b", text, re.IGNORECASE):
        return True
    return False


def extract_coin_values(data: Any, max_depth: int = 4, _depth: int = 0) -> list[str]:
    if data is None or _depth > max_depth:
        return []
    if isinstance(data, dict):
        out: list[str] = []
        for k, v in data.items():
            if isinstance(v, str) and k in JSON_COIN_KEYS:
                out.append(v)
            else:
                out.extend(extract_coin_values(v, max_depth, _depth + 1))
        return out
    if isinstance(data, list):
        out: list[str] = []
        for item in data:
            out.extend(extract_coin_values(item, max_depth, _depth + 1))
        return out
    return []


def extract_texts(data: Any, max_depth: int = 4, _depth: int = 0) -> list[str]:
    if data is None or _depth > max_depth:
        return []
    if isinstance(data, str):
        return [data]
    if isinstance(data, (int, float, bool)):
        return []
    if isinstance(data, list):
        out: list[str] = []
        for item in data:
            out.extend(extract_texts(item, max_depth, _depth + 1))
        return out
    if isinstance(data, dict):
        out: list[str] = []
        for k, v in data.items():
            if isinstance(v, str) and (k in JSON_TEXT_KEYS):
                out.append(v)
            else:
                out.extend(extract_texts(v, max_depth, _depth + 1))
        return out
    return []


def match_coins(content: str, data: Any, coins: Iterable[str]) -> list[str]:
    matched: list[str] = []
    text_pool = [content] if content else []
    text_pool.extend(extract_texts(data))
    coin_fields = extract_coin_values(data)
    for v in coin_fields:
        text_pool.append(v)
    # 追加 JSON 预览，提升字段覆盖率（可容忍少量误判）
    try:
        text_pool.append(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass
    for coin in coins:
        if any(coin_match(text, coin) for text in text_pool if text):
            matched.append(coin.upper())
    return matched


def match_categories(
    tag: str | None,
    content: str,
    data: Any,
    categories: Iterable[str],
    mode: str,
    extra_keywords: list[str],
    exclude_keywords: list[str],
) -> list[str]:
    selected = set(c.lower() for c in categories)
    if not selected:
        return []
    text_pool = [content] if content else []
    text_pool.extend(extract_texts(data))
    try:
        text_pool.append(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass
    text_blob = "\n".join(t for t in text_pool if t)

    matched: list[str] = []
    for cat in selected:
        tags = CATEGORY_TAGS.get(cat)
        has_tag = bool(tags and tag in tags)
        keywords = CATEGORY_KEYWORDS.get(cat, []) + extra_keywords
        has_kw = False
        for kw in keywords:
            if kw and kw.lower() in text_blob.lower():
                has_kw = True
                break
        if exclude_keywords:
            for kw in exclude_keywords:
                if kw and kw.lower() in text_blob.lower():
                    has_kw = False
                    break
        if mode == "tag" and has_tag:
            matched.append(cat)
        elif mode == "keyword" and has_kw:
            matched.append(cat)
        elif mode == "and" and has_tag and has_kw:
            matched.append(cat)
        elif mode == "or" and (has_tag or has_kw):
            matched.append(cat)
    return matched


def load_events(
    db_path: Path,
    since_hours: int | None,
    since_id: int | None,
    types: list[str],
    tags: list[str],
    source_like: str | None,
    scan_limit: int,
    timeout: float,
) -> list[EventItem]:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    items: list[EventItem] = []
    with conn:
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")

        where = []
        params: list[Any] = []
        if since_id is not None:
            where.append("e.id > ?")
            params.append(since_id)
        elif since_hours is not None:
            where.append("e.ts > datetime('now', '-' || ? || ' hours')")
            params.append(int(since_hours))

        if types:
            placeholders = ",".join(["?"] * len(types))
            where.append(f"e.type IN ({placeholders})")
            params.extend(types)

        # tags 仅在用户显式指定时才用于 SQL 过滤
        if tags:
            placeholders = ",".join(["?"] * len(tags))
            where.append(f"s.tag IN ({placeholders})")
            params.extend(tags)
        if source_like:
            where.append("(s.label LIKE ? OR s.source_key LIKE ?)")
            like = f"%{source_like}%"
            params.extend([like, like])

        sql = (
            "SELECT e.id,e.ts,e.type,e.source_key,e.content,e.data,"
            "s.tag,s.label "
            "FROM events e LEFT JOIN sources s ON e.source_key=s.source_key"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY e.id DESC LIMIT ?"
        params.append(int(scan_limit))

        for row in cur.execute(sql, params):
            raw_data = row["data"]
            data_obj: Any = None
            if raw_data:
                try:
                    data_obj = json.loads(raw_data)
                except Exception:
                    data_obj = raw_data
            items.append(
                EventItem(
                    id=row["id"],
                    ts=row["ts"],
                    type=row["type"],
                    tag=row["tag"],
                    source_key=row["source_key"],
                    label=row["label"],
                    content=row["content"],
                    data=data_obj,
                )
            )
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="Datacat unified.db 币种命中扫描（只读）")
    parser.add_argument(
        "--db",
        default="/home/lenovo/.projects/datacat/libs/database/unified.db",
        help="SQLite 路径（默认 unified.db）",
    )
    parser.add_argument("--coins", help="币种列表（逗号分隔）", default=",".join(DEFAULT_COINS))
    parser.add_argument(
        "--strict-coins",
        action="store_true",
        help="严格只使用 --coins（不自动合并默认主流币）",
    )
    parser.add_argument("--categories", help="类别列表（逗号分隔）")
    parser.add_argument("--tags", help="强制 tag 过滤（逗号分隔，仅 SQL 层）")
    parser.add_argument("--types", help="events.type 过滤（逗号分隔）")
    parser.add_argument("--source-like", help="来源过滤（label/source_key 模糊匹配）")
    parser.add_argument("--category-mode", choices=["or", "and", "tag", "keyword"], default="or")
    parser.add_argument("--keywords", help="额外关键词（逗号分隔）")
    parser.add_argument("--exclude-keywords", help="排除关键词（逗号分隔）")
    parser.add_argument("--since-hours", type=int, default=24, help="时间窗（小时）")
    parser.add_argument("--since-id", type=int, help="增量拉取（id > since_id）")
    parser.add_argument("--scan-limit", type=int, default=5000, help="扫描最大行数")
    parser.add_argument("--min-content-len", type=int, default=0, help="最小内容长度过滤")
    parser.add_argument("--max-content", type=int, default=300, help="输出内容截断长度")
    parser.add_argument("--timeout", type=float, default=5.0, help="SQLite busy timeout 秒")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        raise SystemExit(f"DB 不存在: {db_path}")

    coins = [c.strip().upper() for c in parse_csv(args.coins)]
    if not args.strict_coins:
        for c in DEFAULT_COINS:
            if c not in coins:
                coins.append(c)
    categories = [c.strip().lower() for c in parse_csv(args.categories)]
    tags = parse_csv(args.tags)
    types = parse_csv(args.types)
    extra_keywords = parse_csv(args.keywords)
    exclude_keywords = parse_csv(args.exclude_keywords)

    events = load_events(
        db_path=db_path,
        since_hours=args.since_hours if args.since_id is None else None,
        since_id=args.since_id,
        types=types,
        tags=tags,
        source_like=args.source_like,
        scan_limit=args.scan_limit,
        timeout=args.timeout,
    )

    items: list[dict[str, Any]] = []
    counts_by_coin: dict[str, int] = {c: 0 for c in coins}
    counts_by_category: dict[str, int] = {c: 0 for c in categories}

    for ev in events:
        content = ev.content or ""
        matched_coins = match_coins(content, ev.data, coins)
        if not matched_coins:
            continue
        if args.min_content_len and len(content) < args.min_content_len:
            continue
        matched_categories = match_categories(
            ev.tag,
            content,
            ev.data,
            categories,
            mode=args.category_mode,
            extra_keywords=extra_keywords,
            exclude_keywords=exclude_keywords,
        )
        if categories and not matched_categories:
            continue

        for coin in matched_coins:
            counts_by_coin[coin] = counts_by_coin.get(coin, 0) + 1
        for cat in matched_categories:
            counts_by_category[cat] = counts_by_category.get(cat, 0) + 1

        snippet = content[: args.max_content] + ("..." if content and len(content) > args.max_content else "")
        items.append(
            {
                "id": ev.id,
                "ts": ev.ts,
                "type": ev.type,
                "tag": ev.tag,
                "source_key": ev.source_key,
                "label": ev.label,
                "coins": matched_coins,
                "categories": matched_categories,
                "content": snippet,
            }
        )

    summary = {
        "db": str(db_path),
        "since_hours": args.since_hours if args.since_id is None else None,
        "since_id": args.since_id,
        "scan_limit": args.scan_limit,
        "coins": coins,
        "categories": categories,
        "counts_by_coin": counts_by_coin,
        "counts_by_category": counts_by_category,
        "matched": len(items),
        "scanned": len(events),
    }

    if args.format == "json":
        print(json.dumps({"summary": summary, "items": items}, ensure_ascii=False, indent=2))
        return

    print("SUMMARY")
    for k, v in summary.items():
        print(f"- {k}: {v}")
    print("\nITEMS")
    for item in items:
        print(
            f"- {item['id']} {item['ts']} {item['tag']} {item['label']} coins={item['coins']} "
            f"cat={item['categories']} :: {item['content']}"
        )


if __name__ == "__main__":
    main()
