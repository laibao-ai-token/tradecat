from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping


_HEALTH_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*collect-news health: "
    r"total=(?P<total>\d+) healthy=(?P<healthy>\d+) failing=(?P<failing>\d+) cooldown=(?P<cooldown>\d+) new=(?P<new>\d+)"
)
_SAMPLE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*collect-news unhealthy sample: (?P<sample>.+)$"
)
_DEFAULT_RELATIVE_LOG_PATH = Path("services-preview/markets-service/logs/news_collect.log")


@dataclass(frozen=True)
class NewsHealthSnapshot:
    source: str = ""
    total: int = 0
    healthy: int = 0
    failing: int = 0
    cooldown: int = 0
    new: int = 0
    checked_at: float = 0.0
    sample: str = ""

    @property
    def available(self) -> bool:
        return self.total > 0 or any((self.healthy, self.failing, self.cooldown, self.new))


def resolve_news_health_log_path(repo_root: Path, env: Mapping[str, str] | None = None) -> Path:
    data = os.environ if env is None else env
    override = str(data.get("TUI_NEWS_HEALTH_LOG_PATH", "") or "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = (repo_root / candidate).resolve()
        return candidate
    return (repo_root / _DEFAULT_RELATIVE_LOG_PATH).resolve()


def _parse_log_timestamp(raw: str) -> float:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except Exception:
        return 0.0


def _read_tail_lines(path: Path, *, max_bytes: int = 65536) -> list[str]:
    try:
        size = path.stat().st_size
    except Exception:
        return []

    start = max(0, int(size) - max(1024, int(max_bytes)))
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            data = fh.read()
    except Exception:
        return []

    if start > 0:
        nl = data.find(b"\n")
        if nl >= 0:
            data = data[nl + 1 :]
    text = data.decode("utf-8", errors="ignore")
    return text.splitlines()


def load_news_collector_health(log_path: Path) -> NewsHealthSnapshot:
    lines = _read_tail_lines(log_path)
    if not lines:
        return NewsHealthSnapshot()

    health_row: NewsHealthSnapshot | None = None
    health_ts = 0.0
    sample_text = ""
    sample_ts = 0.0

    for line in reversed(lines):
        if not sample_text:
            sample_match = _SAMPLE_RE.search(line)
            if sample_match:
                sample_text = (sample_match.group("sample") or "").strip()
                sample_ts = _parse_log_timestamp(sample_match.group("ts") or "")
                continue

        if health_row is None:
            health_match = _HEALTH_RE.search(line)
            if health_match:
                health_ts = _parse_log_timestamp(health_match.group("ts") or "")
                health_row = NewsHealthSnapshot(
                    source="collector",
                    total=int(health_match.group("total") or 0),
                    healthy=int(health_match.group("healthy") or 0),
                    failing=int(health_match.group("failing") or 0),
                    cooldown=int(health_match.group("cooldown") or 0),
                    new=int(health_match.group("new") or 0),
                    checked_at=health_ts,
                    sample="",
                )
                if sample_text and sample_ts and health_ts and sample_ts + 300 < health_ts:
                    sample_text = ""
                if sample_text:
                    health_row = NewsHealthSnapshot(
                        source=health_row.source,
                        total=health_row.total,
                        healthy=health_row.healthy,
                        failing=health_row.failing,
                        cooldown=health_row.cooldown,
                        new=health_row.new,
                        checked_at=health_row.checked_at,
                        sample=sample_text,
                    )
                break

    return health_row or NewsHealthSnapshot()


def build_live_news_health(total: int, errors: list[str], *, checked_at: float) -> NewsHealthSnapshot:
    safe_total = max(0, int(total))
    failing = max(0, len(errors))
    healthy = max(0, safe_total - failing)
    sample = ", ".join(piece.strip() for piece in errors[:3] if piece.strip())
    return NewsHealthSnapshot(
        source="live",
        total=safe_total,
        healthy=healthy,
        failing=failing,
        cooldown=0,
        new=0,
        checked_at=max(0.0, float(checked_at)),
        sample=sample,
    )
