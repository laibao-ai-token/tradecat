"""限流/ban 恢复测试（逻辑级）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from collectors.binance.um_futures.all.realtime.pull.rest.metrics import http as metrics_mod  # noqa: E402


def main() -> int:
    # 设置 ban 10 秒
    metrics_mod.set_ban(time.time() + 10)
    t0 = time.time()
    metrics_mod.acquire(1)
    elapsed = time.time() - t0
    return 0 if elapsed >= 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
