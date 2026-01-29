"""WS 断线重连测试（进程级）。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SRC}:{Path(ROOT).parent / 'libs'}"
    cmd = [sys.executable, str(SRC / "collectors/binance/um_futures/all/realtime/push/ws/klines/cryptofeed.py")]

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(10)
    if proc.poll() is not None:
        return 1

    proc.send_signal(signal.SIGTERM)
    time.sleep(5)

    proc2 = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(10)
    ok = proc2.poll() is None

    proc2.send_signal(signal.SIGTERM)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
