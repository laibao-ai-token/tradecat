"""配置与数据模型（datacat-service 版）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


def _project_root() -> Path:
    """定位仓库根目录（含 .git）。"""
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / ".git").exists():
            return p
    raise RuntimeError("未找到仓库根目录（.git）")


PROJECT_ROOT = _project_root()
SERVICE_ROOT = PROJECT_ROOT / "services-preview" / "datacat-service"

# 加载 config/.env（与原服务保持一致）
_env_file = PROJECT_ROOT / "config" / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _env(name: str, default: Optional[str] = None, fallback: Optional[str] = None) -> Optional[str]:
    """读取环境变量：优先 DATACAT_*，其次原变量。"""
    if name and name in os.environ:
        return os.environ.get(name)
    if fallback and fallback in os.environ:
        return os.environ.get(fallback)
    return default


def _int_env(name: str, default: int, fallback: Optional[str] = None) -> int:
    raw = _env(name, str(default), fallback)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass
class Settings:
    """服务配置（DATACAT_* 优先）。"""

    database_url: str = field(default_factory=lambda: _env(
        "DATACAT_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5433/market_data",
        "DATABASE_URL",
    ))
    http_proxy: Optional[str] = field(default_factory=lambda: _env(
        "DATACAT_HTTP_PROXY", None, "HTTP_PROXY"
    ) or _env("DATACAT_HTTPS_PROXY", None, "HTTPS_PROXY"))

    log_dir: Path = field(default_factory=lambda: Path(_env(
        "DATACAT_LOG_DIR",
        str(SERVICE_ROOT / "logs"),
        "DATA_SERVICE_LOG_DIR",
    )))
    log_level: str = field(default_factory=lambda: (_env(
        "DATACAT_LOG_LEVEL",
        "INFO",
        "DATA_SERVICE_LOG_LEVEL",
    ) or "INFO").upper())
    log_format: str = field(default_factory=lambda: (_env(
        "DATACAT_LOG_FORMAT",
        "plain",
        "DATA_SERVICE_LOG_FORMAT",
    ) or "plain").lower())
    log_file: Optional[str] = field(default_factory=lambda: _env(
        "DATACAT_LOG_FILE",
        None,
        "DATA_SERVICE_LOG_FILE",
    ))
    data_dir: Path = field(default_factory=lambda: Path(_env(
        "DATACAT_DATA_DIR",
        str(PROJECT_ROOT / "libs" / "database" / "csv"),
        "DATA_SERVICE_DATA_DIR",
    )))

    output_mode: str = field(default_factory=lambda: (_env(
        "DATACAT_OUTPUT_MODE", "db", "DATA_SERVICE_OUTPUT_MODE"
    ) or "db").lower())

    json_dir: Path = field(default_factory=lambda: Path(_env(
        "DATACAT_JSON_DIR",
        str(SERVICE_ROOT / "data-json"),
        "DATA_SERVICE_JSON_DIR",
    )))

    ws_gap_interval: int = field(default_factory=lambda: _int_env(
        "DATACAT_WS_GAP_INTERVAL", 600, "BINANCE_WS_GAP_INTERVAL"
    ))
    ws_gap_lookback: int = field(default_factory=lambda: _int_env(
        "DATACAT_WS_GAP_LOOKBACK", 10080, "BINANCE_WS_GAP_LOOKBACK"
    ))
    ws_source: str = field(default_factory=lambda: _env(
        "DATACAT_WS_SOURCE", "binance_ws", "BINANCE_WS_SOURCE"
    ))

    db_schema: str = field(default_factory=lambda: _env(
        "DATACAT_DB_SCHEMA", "market_data", "KLINE_DB_SCHEMA"
    ))
    db_exchange: str = field(default_factory=lambda: _env(
        "DATACAT_DB_EXCHANGE", "binance_futures_um", "BINANCE_WS_DB_EXCHANGE"
    ))
    ccxt_exchange: str = field(default_factory=lambda: _env(
        "DATACAT_CCXT_EXCHANGE", "binance", "BINANCE_WS_CCXT_EXCHANGE"
    ))

    rate_limit_per_minute: int = field(default_factory=lambda: _int_env(
        "DATACAT_RATE_LIMIT_PER_MINUTE", 1800, "RATE_LIMIT_PER_MINUTE"
    ))
    max_concurrent: int = field(default_factory=lambda: _int_env(
        "DATACAT_MAX_CONCURRENT", 5, "MAX_CONCURRENT"
    ))

    backfill_mode: str = field(default_factory=lambda: _env(
        "DATACAT_BACKFILL_MODE", "days", "BACKFILL_MODE"
    ) or "days")
    backfill_days: int = field(default_factory=lambda: _int_env(
        "DATACAT_BACKFILL_DAYS", 30, "BACKFILL_DAYS"
    ))
    backfill_start_date: Optional[date] = field(default_factory=lambda: _env(
        "DATACAT_BACKFILL_START_DATE", None, "BACKFILL_START_DATE"
    ))
    backfill_on_start: bool = field(default_factory=lambda: (
        (_env("DATACAT_BACKFILL_ON_START", "false", "BACKFILL_ON_START") or "false").lower() in ("true", "1", "yes")
    ))

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.json_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()


INTERVAL_TO_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000,
    "1d": 86_400_000, "1w": 604_800_000, "1M": 2_592_000_000,
}


def normalize_interval(interval: str) -> str:
    interval = interval.strip()
    if interval == "1M":
        return "1M"
    normalized = interval.lower()
    if normalized not in INTERVAL_TO_MS:
        raise ValueError(f"不支持的周期: {interval}")
    return normalized
