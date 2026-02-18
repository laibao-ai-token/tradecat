"""
信号规则汇总
导出所有规则列表
"""

import os

from .base import ConditionType, SignalRule
from .core import CORE_RULES
from .futures import FUTURES_RULES
from .misc import MISC_RULES
from .momentum import MOMENTUM_RULES
from .pattern import PATTERN_RULES
from .trend import TREND_RULES
from .volatility import VOLATILITY_RULES
from .volume import VOLUME_RULES

# Optional global override for rule timeframes.
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

# 按表索引
RULES_BY_TABLE: dict[str, list[SignalRule]] = {}
for rule in ALL_RULES:
    if rule.table not in RULES_BY_TABLE:
        RULES_BY_TABLE[rule.table] = []
    RULES_BY_TABLE[rule.table].append(rule)

# 统计
RULE_COUNT = len(ALL_RULES)
TABLE_COUNT = len(RULES_BY_TABLE)

__all__ = [
    "SignalRule",
    "ConditionType",
    "ALL_RULES",
    "RULES_BY_CATEGORY",
    "RULES_BY_TABLE",
    "RULE_COUNT",
    "TABLE_COUNT",
]
