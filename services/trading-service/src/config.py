"""
配置管理

环境变量:
    DATABASE_URL: TimescaleDB 连接串
    INDICATOR_SQLITE_PATH: SQLite 输出路径
    MAX_WORKERS: 并行计算线程数
    KLINE_DB_EXCHANGE: candles_* 表的 exchange 字段过滤（默认 binance_futures_um）
    KLINE_INTERVALS: K线指标计算周期
    FUTURES_INTERVALS: 期货情绪计算周期
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from common.config_loader import load_repo_env
from common.db_url import resolve_database_url

SERVICE_ROOT = Path(__file__).parents[1]  # src/config.py -> src -> trading-service
# Repo root: .../tradecat-origin
REPO_ROOT = SERVICE_ROOT.parents[1]


load_repo_env(repo_root=REPO_ROOT, set_os_env=True, override=False)


def _parse_intervals(env_key: str, default: str) -> List[str]:
    return [x.strip() for x in os.getenv(env_key, default).split(",") if x.strip()]

def _resolve_repo_path(env_key: str, default: Path) -> Path:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return default
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


@dataclass
class Config:
    # TimescaleDB（读取K线）
    db_url: str = field(default_factory=lambda: resolve_database_url("DATABASE_URL"))

    # SQLite（写入指标结果）
    # 支持相对路径（相对 REPO_ROOT），避免在不同 cwd 下写到意外位置。
    sqlite_path: Path = field(default_factory=lambda: _resolve_repo_path(
        "INDICATOR_SQLITE_PATH",
        REPO_ROOT / "libs/database/services/telegram-service/market_data.db",
    ))

    # 计算参数
    default_lookback: int = 300
    max_workers: int = field(default_factory=lambda: int(os.getenv("MAX_WORKERS", "6")))
    exchange: str = field(default_factory=lambda: os.getenv("KLINE_DB_EXCHANGE", "binance_futures_um"))
    # 计算后端: thread | process | hybrid（IO用线程，CPU用进程）
    compute_backend: str = field(default_factory=lambda: os.getenv("COMPUTE_BACKEND", "thread").lower())

    # IO/CPU 拆分执行器配置
    max_io_workers: int = field(default_factory=lambda: int(os.getenv("MAX_IO_WORKERS", "8")))
    max_cpu_workers: int = field(default_factory=lambda: int(os.getenv("MAX_CPU_WORKERS", "4")))

    # K线指标周期
    kline_intervals: List[str] = field(default_factory=lambda: _parse_intervals(
        "KLINE_INTERVALS", "1m,5m,15m,1h,4h,1d,1w"
    ))

    # 期货情绪周期
    futures_intervals: List[str] = field(default_factory=lambda: _parse_intervals(
        "FUTURES_INTERVALS", "5m,15m,1h,4h,1d,1w"
    ))

    # 兼容旧代码
    @property
    def intervals(self) -> List[str]:
        return self.kline_intervals


config = Config()
