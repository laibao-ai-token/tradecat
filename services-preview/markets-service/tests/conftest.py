"""Pytest configuration for markets-service tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def _bootstrap_src_package() -> None:
    if "src" in sys.modules:
        return
    init_file = _SRC_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "src",
        init_file,
        submodule_search_locations=[str(_SRC_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 markets-service src 包: {_SRC_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["src"] = module
    spec.loader.exec_module(module)


_bootstrap_src_package()


@pytest.fixture
def sample_symbol():
    """Sample trading symbol for tests."""
    return "BTCUSDT"
