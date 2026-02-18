"""Config env parser tests."""

from src.config import _parse_env_value


def test_parse_env_value_strips_inline_comment_for_unquoted() -> None:
    assert _parse_env_value("raw              # schema comment") == "raw"


def test_parse_env_value_keeps_quoted_hash() -> None:
    assert _parse_env_value('"abc#123"') == "abc#123"
    assert _parse_env_value("'x#y'") == "x#y"
