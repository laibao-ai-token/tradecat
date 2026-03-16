"""
信号规则汇总
导出所有规则列表
"""

import os
from collections import defaultdict

from .base import ConditionType, SignalRule
from .core import CORE_RULES
from .futures import FUTURES_RULES
from .misc import MISC_RULES
from .momentum import MOMENTUM_RULES
from .pattern import PATTERN_RULES
from .trend import TREND_RULES
from .volatility import VOLATILITY_RULES
from .volume import VOLUME_RULES

# Optional process-level override for rule timeframes.
# This is useful when you want to run the SQLite rule set on faster candles (e.g. 1m/5m) while keeping
# the codebase stable. Only rules that keep the default timeframes will be overridden.
#
# Example:
#   SIGNAL_RULE_TIMEFRAMES=1m,5m
_DEFAULT_TFS = ["1h", "4h", "1d"]
_override = (os.environ.get("SIGNAL_RULE_TIMEFRAMES") or "").strip()
_override_tfs = [x.strip() for x in _override.split(",") if x.strip()] if _override else []

# 所有规则汇总
ALL_RULES: list[SignalRule] = (
    CORE_RULES
    + MOMENTUM_RULES
    + TREND_RULES
    + VOLATILITY_RULES
    + VOLUME_RULES
    + FUTURES_RULES
    + PATTERN_RULES
    + MISC_RULES
)

if _override_tfs:
    for r in ALL_RULES:
        if r.timeframes == _DEFAULT_TFS:
            r.timeframes = list(_override_tfs)

# 按分类索引
RULES_BY_CATEGORY = {
    "core": CORE_RULES,
    "momentum": MOMENTUM_RULES,
    "trend": TREND_RULES,
    "volatility": VOLATILITY_RULES,
    "volume": VOLUME_RULES,
    "futures": FUTURES_RULES,
    "pattern": PATTERN_RULES,
    "misc": MISC_RULES,
}

# 按稳定 rule_id 索引
RULES_BY_ID: dict[str, SignalRule] = {rule.rule_id: rule for rule in ALL_RULES}

# 按表索引
RULES_BY_TABLE: dict[str, list[SignalRule]] = {}
for rule in ALL_RULES:
    if rule.table not in RULES_BY_TABLE:
        RULES_BY_TABLE[rule.table] = []
    RULES_BY_TABLE[rule.table].append(rule)

RULES_BY_NAME: dict[str, list[SignalRule]] = defaultdict(list)
for rule in ALL_RULES:
    RULES_BY_NAME[str(rule.name)].append(rule)


def resolve_rule_id(signal_type: str, *, category: str = "", subcategory: str = "") -> str:
    """Resolve legacy rule names to stable ids when possible."""
    key = str(signal_type or "").strip()
    if not key:
        return ""
    if key in RULES_BY_ID:
        return key

    matches = RULES_BY_NAME.get(key, [])
    if len(matches) == 1:
        return matches[0].rule_id
    if len(matches) > 1 and (category or subcategory):
        scoped = [
            rule
            for rule in matches
            if (not category or rule.category == category) and (not subcategory or rule.subcategory == subcategory)
        ]
        if len(scoped) == 1:
            return scoped[0].rule_id
    return key


def format_rule_display_key(rule: SignalRule) -> str:
    """Readable per-rule key for reports and compare views."""
    siblings = RULES_BY_NAME.get(str(rule.name), [])
    if len(siblings) <= 1:
        return str(rule.name)
    return f"{rule.name} ({rule.category}.{rule.subcategory})"


def resolve_rule_name(signal_type: str, *, category: str = "", subcategory: str = "") -> str:
    """Resolve signal_type/rule_id to a user-facing rule label."""
    key = resolve_rule_id(signal_type, category=category, subcategory=subcategory)
    rule = RULES_BY_ID.get(key)
    if rule is not None:
        return rule.name
    return str(signal_type or "").strip()


def format_signal_display_key(signal_type: str, *, category: str = "", subcategory: str = "") -> str:
    """Map stored signal types to a readable, collision-free report key."""
    key = resolve_rule_id(signal_type, category=category, subcategory=subcategory)
    rule = RULES_BY_ID.get(key)
    if rule is not None:
        return format_rule_display_key(rule)
    return str(signal_type or "").strip()

# 统计
RULE_COUNT = len(ALL_RULES)
TABLE_COUNT = len(RULES_BY_TABLE)

__all__ = [
    "SignalRule",
    "ConditionType",
    "ALL_RULES",
    "RULES_BY_CATEGORY",
    "RULES_BY_ID",
    "RULES_BY_TABLE",
    "RULES_BY_NAME",
    "RULE_COUNT",
    "TABLE_COUNT",
    "resolve_rule_id",
    "resolve_rule_name",
    "format_rule_display_key",
    "format_signal_display_key",
]
