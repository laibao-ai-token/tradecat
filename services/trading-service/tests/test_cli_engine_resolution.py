import argparse

import pytest

from src.__main__ import _is_engine_arg_explicit, _resolve_engine


def _ns(*, engine: str = "core", full_async: bool = False, event: bool = False) -> argparse.Namespace:
    return argparse.Namespace(engine=engine, full_async=full_async, event=event)


def test_engine_arg_explicit_detection():
    assert _is_engine_arg_explicit(["--engine", "core"]) is True
    assert _is_engine_arg_explicit(["--engine=event"]) is True
    assert _is_engine_arg_explicit(["--event"]) is False


def test_resolve_engine_conflict_when_engine_and_legacy_mixed():
    with pytest.raises(ValueError):
        _resolve_engine(_ns(engine="core", event=True), engine_explicit=True)


def test_resolve_engine_legacy_only_keeps_backward_compatibility():
    selected, legacy = _resolve_engine(_ns(engine="core", event=True), engine_explicit=False)
    assert selected == "event"
    assert legacy == "event"

