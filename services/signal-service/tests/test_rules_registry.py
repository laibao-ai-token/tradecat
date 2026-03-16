from __future__ import annotations

import string

from src.rules import ALL_RULES, RULE_COUNT, format_rule_display_key, format_signal_display_key, resolve_rule_id


def _rule(name: str, *, category: str = ""):
    for rule in ALL_RULES:
        if rule.name != name:
            continue
        if category and rule.category != category:
            continue
        return rule
    raise AssertionError(f"rule not found: {name} ({category})")


def test_rule_ids_are_unique() -> None:
    assert len({rule.rule_id for rule in ALL_RULES}) == RULE_COUNT


def test_rule_display_keys_are_unique() -> None:
    keys = [format_rule_display_key(rule) for rule in ALL_RULES]
    assert len(set(keys)) == len(keys)


def test_duplicate_rule_names_resolve_with_scope() -> None:
    volume_rule = _rule("主动买盘极端", category="volume")
    futures_rule = _rule("主动买盘极端", category="futures")

    assert resolve_rule_id("主动买盘极端", category="volume", subcategory="taker") == volume_rule.rule_id
    assert resolve_rule_id("主动买盘极端", category="futures", subcategory="sentiment") == futures_rule.rule_id
    assert format_signal_display_key(volume_rule.rule_id) == "主动买盘极端 (volume.taker)"
    assert format_signal_display_key(futures_rule.rule_id) == "主动买盘极端 (futures.sentiment)"


def test_momentum_exit_rules_keep_reversal_direction() -> None:
    assert _rule("RSI离开超买区").direction == "SELL"
    assert _rule("RSI离开超卖区").direction == "BUY"
    assert _rule("CCI离开超买").direction == "SELL"
    assert _rule("CCI离开超卖").direction == "BUY"
    assert _rule("WR离开超买").direction == "SELL"
    assert _rule("WR离开超卖").direction == "BUY"
    assert _rule("MFI离开超买").direction == "SELL"
    assert _rule("MFI离开超卖").direction == "BUY"


def test_message_templates_match_declared_fields() -> None:
    formatter = string.Formatter()
    for rule in ALL_RULES:
        placeholders = {
            field.split(".", 1)[0].split("[", 1)[0]
            for _, field, _, _ in formatter.parse(rule.message_template)
            if field
        }
        assert placeholders <= set(rule.fields.keys()), rule.name
