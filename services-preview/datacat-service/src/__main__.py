"""入口: python -m src 或 python src/__main__.py"""
from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

from .config import settings

logger = logging.getLogger(__name__)

SRC_DIR = Path(__file__).parent


class Scheduler:
    """进程调度器"""

    def __init__(self) -> None:
        self._procs: Dict[str, dict] = {}
        self._running = False

    def add(self, name: str, cmd: List[str]) -> None:
        self._procs[name] = {"cmd": cmd, "proc": None, "restarts": 0}

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_running", False))

        for name, info in self._procs.items():
            self._start(name, info)

        while self._running:
            for name, info in self._procs.items():
                if info["proc"] and info["proc"].poll() is not None:
                    if info["restarts"] < 10:
                        logger.warning("%s 退出，重启", name)
                        info["restarts"] += 1
                        time.sleep(min(5 * info["restarts"], 60))
                        self._start(name, info)
            time.sleep(5)

        for info in self._procs.values():
            if info["proc"]:
                info["proc"].terminate()

    def _start(self, name: str, info: dict) -> None:
        log = settings.log_dir / f"{name}.log"
        with open(log, "a") as f:
            info["proc"] = subprocess.Popen(info["cmd"], stdout=f, stderr=subprocess.STDOUT, cwd=str(SRC_DIR))
        logger.info("启动 %s (PID=%d)", name, info["proc"].pid)


def _collector_path(*parts: str) -> str:
    return str(Path("collectors").joinpath(*parts))


def _run_step(name: str, cmd: List[str]) -> int:
    log = settings.log_dir / f"{name}.log"
    with open(log, "a") as f:
        logger.info("运行 %s: %s", name, " ".join(cmd))
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(SRC_DIR))
    if proc.returncode != 0:
        logger.warning("%s 执行失败: code=%s", name, proc.returncode)
    return proc.returncode


def _run_backfill_pipeline(py: str) -> int:
    steps = [
        ("backfill_zip_klines", [py, _collector_path("binance", "um_futures", "all", "backfill", "pull", "file", "klines", "http_zip.py")]),
        ("backfill_zip_metrics", [py, _collector_path("binance", "um_futures", "all", "backfill", "pull", "file", "metrics", "http_zip.py")]),
        ("backfill_rest_klines", [py, _collector_path("binance", "um_futures", "all", "backfill", "pull", "rest", "klines", "ccxt.py")]),
        ("backfill_rest_metrics", [py, _collector_path("binance", "um_futures", "all", "backfill", "pull", "rest", "metrics", "http.py")]),
    ]
    for name, cmd in steps:
        code = _run_step(name, cmd)
        if code != 0:
            return code
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Datacat Service")
    parser.add_argument("--ws", action="store_true", help="WebSocket 采集")
    parser.add_argument("--metrics", action="store_true", help="指标采集")
    parser.add_argument("--backfill", action="store_true", help="历史补齐")
    parser.add_argument("--alpha", action="store_true", help="Alpha 列表采集")
    parser.add_argument("--all", action="store_true", help="全部启动")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    py = sys.executable
    if args.backfill and not (args.ws or args.metrics or args.alpha or args.all):
        code = _run_backfill_pipeline(py)
        if code != 0:
            sys.exit(code)
        return

    sched = Scheduler()

    ws_cmd = [py, _collector_path("binance", "um_futures", "all", "realtime", "push", "ws", "klines", "cryptofeed.py")]
    metrics_cmd = [py, _collector_path("binance", "um_futures", "all", "realtime", "pull", "rest", "metrics", "http.py")]
    alpha_cmd = [py, _collector_path("binance", "um_futures", "all", "sync", "pull", "rest", "alpha", "http.py")]

    if args.all or args.ws:
        sched.add("ws", ws_cmd)
    if args.all or args.metrics:
        sched.add("metrics", metrics_cmd)
    if args.all or args.alpha:
        sched.add("alpha", alpha_cmd)
    if args.backfill:
        sched.add("backfill", [py, "__main__.py", "--backfill"])

    if not sched._procs:
        print("用法: python src/__main__.py --ws|--metrics|--backfill|--alpha|--all")
        sys.exit(1)

    sched.run()


if __name__ == "__main__":
    main()
