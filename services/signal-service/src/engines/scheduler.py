"""调度辅助：同步循环使用可中断等待，避免散落的直接 sleep。"""

from __future__ import annotations

import threading


def wait_for_next_cycle(stop_event: threading.Event, interval_s: float) -> bool:
    """等待到下一轮；若收到停止信号返回 False。"""
    timeout = float(interval_s)
    if timeout < 0:
        raise ValueError(f"interval_s must be >= 0, got {interval_s!r}")
    return not stop_event.wait(timeout)
