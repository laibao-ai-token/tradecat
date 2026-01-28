"""REST Metrics 采集（复用旧 data-service 实现）。"""

from __future__ import annotations

import logging
from pathlib import Path
import sys


# ==================== 旧服务复用入口 ====================

def _legacy_src() -> Path:
    """定位旧 data-service 的 src 目录。"""
    here = Path(__file__).resolve()
    for p in [here] + list(here.parents):
        if (p / ".git").exists():
            return p / "services" / "data-service" / "src"
    raise RuntimeError("未找到仓库根目录（.git）")


sys.path.insert(0, str(_legacy_src()))

from collectors.metrics import MetricsCollector  # noqa: E402


def run_once() -> int:
    """执行一次 Metrics 采集。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    c = MetricsCollector()
    try:
        return c.run_once()
    finally:
        c.close()


if __name__ == "__main__":
    run_once()
