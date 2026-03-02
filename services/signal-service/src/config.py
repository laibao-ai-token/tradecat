"""
Signal Service 配置
"""

import importlib.util
import os
from pathlib import Path

# 路径
SRC_DIR = Path(__file__).parent
PROJECT_ROOT = SRC_DIR.parent
REPO_ROOT = PROJECT_ROOT.parent.parent


def _load_repo_env() -> None:
    try:
        from libs.common.config_loader import load_repo_env
    except ModuleNotFoundError:
        loader_path = REPO_ROOT / "libs" / "common" / "config_loader.py"
        if not loader_path.exists():
            return
        spec = importlib.util.spec_from_file_location("tradecat_config_loader", loader_path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except OSError:
            return
        load_repo_env = getattr(module, "load_repo_env", None)
        if load_repo_env is None:
            return
    load_repo_env(repo_root=REPO_ROOT, set_os_env=True, override=False)


_load_repo_env()


# 数据库配置
def get_database_url() -> str:
    """获取 TimescaleDB 连接 URL"""
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/market_data")


def get_sqlite_path() -> Path:
    """获取 SQLite 指标数据库路径"""
    env_path = os.environ.get("INDICATOR_SQLITE_PATH")
    if env_path:
        p = Path(env_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        return p
    return REPO_ROOT / "libs/database/services/telegram-service/market_data.db"


def get_history_db_path() -> Path:
    """获取信号历史数据库路径"""
    env_path = os.environ.get("SIGNAL_HISTORY_DB_PATH")
    if env_path:
        return Path(env_path)
    return REPO_ROOT / "libs/database/services/signal-service/signal_history.db"


def get_subscription_db_path() -> Path:
    """获取订阅数据库路径"""
    return REPO_ROOT / "libs/database/services/signal-service/signal_subs.db"


# 信号检测配置
DEFAULT_TIMEFRAMES = ["1h", "4h", "1d"]
DEFAULT_MIN_VOLUME = 100000
DEFAULT_CHECK_INTERVAL = 60  # 秒
COOLDOWN_SECONDS = 300  # 同一信号冷却时间
# 数据新鲜度阈值（秒），超过则视为陈旧数据不参与信号计算
DATA_MAX_AGE_SECONDS = int(os.environ.get("SIGNAL_DATA_MAX_AGE", "600"))

# 历史记录配置
MAX_RETENTION_DAYS = int(os.environ.get("SIGNAL_HISTORY_RETENTION_DAYS", "30"))
