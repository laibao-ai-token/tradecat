"""Pytest configuration for markets-service tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Ensure `src/` is importable (providers/core/models/...).
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC_DIR))


@pytest.fixture
def sample_symbol():
    """Sample trading symbol for tests."""
    return "BTCUSDT"
