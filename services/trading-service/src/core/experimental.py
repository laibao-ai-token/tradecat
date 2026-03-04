"""Guards for experimental engine execution."""

from __future__ import annotations

import os

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_experimental_enabled() -> bool:
    value = os.getenv("ENABLE_EXPERIMENTAL_ENGINES", "")
    return value.strip().lower() in _TRUE_VALUES


def ensure_experimental_enabled(engine_name: str) -> None:
    if is_experimental_enabled():
        return
    raise RuntimeError(
        f"{engine_name} 引擎属于实验态，默认禁用。请先设置 ENABLE_EXPERIMENTAL_ENGINES=1 再运行。"
    )

