"""JSONL 写入工具（测试输出）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence, Tuple

from config import settings


def json_path(name: str) -> Path:
    """构造 JSONL 输出路径。"""
    filename = f"{name}.jsonl"
    return settings.json_dir / filename


def _row_key(row: dict, key_fields: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(str(row.get(k)) for k in key_fields)


def _load_keys(path: Path, key_fields: Tuple[str, ...]) -> set:
    keys = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            keys.add(_row_key(row, key_fields))
    return keys


def append_jsonl(path: Path, rows: Sequence[dict], dedup_keys: Tuple[str, ...] | None = None) -> int:
    """追加写入 JSONL，返回写入条数（可选去重）。"""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_keys(path, dedup_keys) if dedup_keys else set()
    with path.open("a", encoding="utf-8") as f:
        written = 0
        for row in rows:
            if dedup_keys:
                key = _row_key(row, dedup_keys)
                if key in existing:
                    continue
                existing.add(key)
            f.write(json.dumps(row, ensure_ascii=False, default=str))
            f.write("\n")
            written += 1
    return written
