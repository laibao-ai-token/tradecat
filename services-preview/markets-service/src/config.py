"""配置管理"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from common.config_loader import load_repo_env
from common.db_url import resolve_database_url

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
    http_proxy: Optional[str] = field(default_factory=lambda: os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"))

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
