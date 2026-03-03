"""入口: python -m src"""
from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
from pathlib import Path
from threading import Event
from typing import Dict, List

from .config import settings

SERVICE_SRC_DIR = Path(__file__).parent

logger = logging.getLogger(__name__)


class Scheduler:
    """进程调度器"""

    def __init__(self):
        self._procs: Dict[str, dict] = {}
        self._running = False
        self._stop_event = Event()

    def add(self, name: str, cmd: List[str]) -> None:
        self._procs[name] = {"cmd": cmd, "proc": None, "restarts": 0}

    def _request_stop(self, *_):
        self._running = False
        self._stop_event.set()

    def run(self) -> None:
        self._running = True
        self._stop_event.clear()
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        for name, info in self._procs.items():
            self._start(name, info)

        while self._running:
            for name, info in self._procs.items():
                if info["proc"] and info["proc"].poll() is not None:
                    if info["restarts"] < 10:
                        logger.warning("%s 退出，重启", name)
                        info["restarts"] += 1
                        if self._stop_event.wait(min(5 * info["restarts"], 60)):
                            self._running = False
                            break
                        self._start(name, info)
            if not self._running:
                break
            if self._stop_event.wait(5):
                break

        for info in self._procs.values():
            if info["proc"]:
                info["proc"].terminate()

    def _start(self, name: str, info: dict) -> None:
        log = settings.log_dir / f"{name}.log"
        with open(log, "a") as f:
            info["proc"] = subprocess.Popen(info["cmd"], stdout=f, stderr=subprocess.STDOUT, cwd=str(SERVICE_SRC_DIR))
        logger.info("启动 %s (PID=%d)", name, info["proc"].pid)


def main() -> None:
    parser = argparse.ArgumentParser(description="Data Service")
    parser.add_argument("--ws", action="store_true", help="WebSocket 采集")
    parser.add_argument("--metrics", action="store_true", help="指标采集")
    parser.add_argument("--backfill", action="store_true", help="历史补齐")
    parser.add_argument("--all", action="store_true", help="全部启动")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    py = sys.executable
    sched = Scheduler()

    if args.all or args.ws:
        sched.add("ws", [py, "-m", "collectors.ws"])
    if args.all or args.metrics:
        sched.add("metrics", [py, "-m", "collectors.metrics"])
    if args.backfill:
        sched.add("backfill", [py, "-m", "collectors.backfill"])

    if not sched._procs:
        print("用法: python src/__main__.py --ws|--metrics|--backfill|--all")
        sys.exit(1)

    sched.run()


if __name__ == "__main__":
    main()
