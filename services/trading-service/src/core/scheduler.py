"""调度辅助工具：为同步循环提供可中断等待。"""

from __future__ import annotations

from threading import Event


def wait_for_next_cycle(stop_event: Event, interval_s: float) -> bool:
    """等待到下一轮；若收到停止信号返回 False。"""
    timeout = float(interval_s)
    if timeout < 0:
        raise ValueError(f"interval_s must be >= 0, got {interval_s!r}")
    return not stop_event.wait(timeout)
