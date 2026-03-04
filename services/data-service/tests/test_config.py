from __future__ import annotations

import pytest

from src.config import _int_env, normalize_interval


def test_int_env_returns_default_for_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_SERVICE_TEST_INT", "oops")
    assert _int_env("DATA_SERVICE_TEST_INT", 7) == 7


def test_int_env_parses_valid_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_SERVICE_TEST_INT", "11")
    assert _int_env("DATA_SERVICE_TEST_INT", 7) == 11


def test_normalize_interval_accepts_month_alias() -> None:
    assert normalize_interval("1M") == "1M"


def test_normalize_interval_normalizes_common_value() -> None:
    assert normalize_interval(" 1H ") == "1h"


def test_normalize_interval_rejects_unknown_interval() -> None:
    with pytest.raises(ValueError):
        normalize_interval("2x")
