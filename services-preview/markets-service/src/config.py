"""配置管理"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SERVICE_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = SERVICE_ROOT.parent.parent

def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    # quoted values keep inner content as-is
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]

    # unquoted values: drop trailing inline comments like `foo # comment`
    hash_pos = value.find("#")
    if hash_pos > 0 and value[hash_pos - 1].isspace():
        value = value[:hash_pos].rstrip()
    return value


# 加载 config/.env
_env_file = PROJECT_ROOT / "config" / ".env"
if _env_file.exists():
    for raw_line in _env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), _parse_env_value(value))

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
    database_url: str = field(default_factory=lambda: os.getenv(
        "MARKETS_SERVICE_DATABASE_URL",
        os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/market_data"),
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
