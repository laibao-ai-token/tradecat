"""配置管理"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from common.config_loader import load_repo_env
from common.db_url import resolve_database_url

from .news_defaults import default_news_rss_feeds_value

SERVICE_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = SERVICE_ROOT.parent.parent


def _parse_env_value(raw: str) -> str:
    """解析 dotenv 值，兼容引号与行尾注释。"""
    value = (raw or "").strip()
    if not value:
        return ""

    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]

    comment_pos = value.find(" #")
    if comment_pos >= 0:
        value = value[:comment_pos].rstrip()
    return value


def _env_int(key: str, default: int) -> int:
    raw = (os.getenv(key, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)

load_repo_env(repo_root=PROJECT_ROOT, set_os_env=True, override=False)

# 可选：本地代理（用于部分网络环境访问外部数据源）。
# 默认不强制，避免在无代理环境下导致所有请求失败。
if os.getenv("MARKETS_SERVICE_FORCE_PROXY_9910", "0") == "1":
    os.environ.setdefault("http_proxy", "http://127.0.0.1:9910")
    os.environ.setdefault("https_proxy", "http://127.0.0.1:9910")
    os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:9910")
    os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:9910")


@dataclass
class Settings:
    """服务配置"""
    # 数据库
    database_url: str = field(default_factory=lambda: resolve_database_url(
        "MARKETS_SERVICE_DATABASE_URL",
        "DATABASE_URL",
    ))
    db_schema: str = field(default_factory=lambda: os.getenv("MARKET_DB_SCHEMA", "market_data"))
    raw_schema: str = field(default_factory=lambda: os.getenv("RAW_DB_SCHEMA", "raw"))
    quality_schema: str = field(default_factory=lambda: os.getenv("QUALITY_DB_SCHEMA", "quality"))
    alternative_schema: str = field(default_factory=lambda: os.getenv("ALTERNATIVE_DB_SCHEMA", "alternative"))

    # 代理
    http_proxy: str | None = field(default_factory=lambda: os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"))

    # 新闻
    news_rss_feeds: str = field(
        default_factory=lambda: (os.getenv("NEWS_RSS_FEEDS", "") or "").strip()
        or default_news_rss_feeds_value(os.getenv("NEWS_RSS_PRESET", ""))
    )
    news_rss_poll_interval_seconds: int = field(default_factory=lambda: _env_int("NEWS_RSS_POLL_INTERVAL_SECONDS", 2))
    news_rss_limit: int = field(default_factory=lambda: _env_int("NEWS_RSS_LIMIT", 100))
    news_rss_window_hours: int = field(default_factory=lambda: _env_int("NEWS_RSS_WINDOW_HOURS", 72))
    news_rss_timeout_seconds: int = field(default_factory=lambda: _env_int("NEWS_RSS_TIMEOUT_SECONDS", 20))
    news_retention_hours: int = field(default_factory=lambda: max(0, _env_int("NEWS_RETENTION_HOURS", 24)))
    news_retention_cleanup_interval_seconds: int = field(
        default_factory=lambda: max(60, _env_int("NEWS_RETENTION_CLEANUP_INTERVAL_SECONDS", 600))
    )
    news_rss_failure_threshold: int = field(default_factory=lambda: max(1, _env_int("NEWS_RSS_FAILURE_THRESHOLD", 2)))
    news_rss_failure_cooldown_seconds: int = field(
        default_factory=lambda: max(1, _env_int("NEWS_RSS_FAILURE_COOLDOWN_SECONDS", 300))
    )

    # AllTick（分钟级/实时数据，需 Token）
    alltick_token: str = field(default_factory=lambda: os.getenv("ALLTICK_TOKEN", ""))
    alltick_stock_base_url: str = field(
        default_factory=lambda: os.getenv("ALLTICK_STOCK_BASE_URL", "https://quote.alltick.io/quote-stock-b-api")
    )

    # 目录
    log_dir: Path = field(default_factory=lambda: SERVICE_ROOT / "logs")

    def __post_init__(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
