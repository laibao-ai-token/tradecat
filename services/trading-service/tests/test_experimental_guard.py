import pytest

from src.core.experimental import ensure_experimental_enabled, is_experimental_enabled


def test_is_experimental_enabled_false_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_EXPERIMENTAL_ENGINES", raising=False)
    assert is_experimental_enabled() is False


def test_is_experimental_enabled_true(monkeypatch):
    monkeypatch.setenv("ENABLE_EXPERIMENTAL_ENGINES", "1")
    assert is_experimental_enabled() is True


def test_ensure_experimental_enabled_raise(monkeypatch):
    monkeypatch.delenv("ENABLE_EXPERIMENTAL_ENGINES", raising=False)
    with pytest.raises(RuntimeError):
        ensure_experimental_enabled("event")

