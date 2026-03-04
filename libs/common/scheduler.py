"""Synchronous scheduling helpers shared across services."""

from __future__ import annotations

import threading
import time


def wait_seconds(seconds: float, *, stop_event: threading.Event | None = None) -> bool:
    """Wait for a non-negative duration.

    Returns False only when interrupted by ``stop_event``.
    """
    delay = float(seconds)
    if delay < 0:
        raise ValueError(f"seconds must be >= 0, got {seconds!r}")
    if stop_event is None:
        time.sleep(delay)
        return True
    return not stop_event.wait(delay)


def exponential_backoff(
    attempt: int,
    *,
    base_seconds: float = 1.0,
    factor: float = 2.0,
    max_seconds: float | None = None,
) -> float:
    """Compute exponential backoff delay for the given retry attempt."""
    if base_seconds < 0:
        raise ValueError(f"base_seconds must be >= 0, got {base_seconds!r}")
    if factor <= 0:
        raise ValueError(f"factor must be > 0, got {factor!r}")
    step = max(0, int(attempt))
    delay = float(base_seconds) * (float(factor) ** step)
    if max_seconds is not None:
        delay = min(delay, float(max_seconds))
    return delay


def wait_with_backoff(
    attempt: int,
    *,
    base_seconds: float = 1.0,
    factor: float = 2.0,
    max_seconds: float | None = None,
    stop_event: threading.Event | None = None,
) -> bool:
    """Wait using exponential backoff."""
    delay = exponential_backoff(
        attempt,
        base_seconds=base_seconds,
        factor=factor,
        max_seconds=max_seconds,
    )
    return wait_seconds(delay, stop_event=stop_event)
