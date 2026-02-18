"""
信号规则基础定义
"""

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_ROW_NUMERIC_CACHE_KEY = "__tc_numeric_cache__"
_RULE_ERROR_LOG_FIRST_N = max(0, int(os.environ.get("SIGNAL_RULE_ERROR_LOG_FIRST_N", "3")))
_RULE_ERROR_LOG_EVERY_N = max(0, int(os.environ.get("SIGNAL_RULE_ERROR_LOG_EVERY_N", "0")))
_RULE_ERROR_COUNTS: dict[tuple[str, str], int] = {}


def _parse_numeric_text(raw: str) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    text = text.replace(",", "")
    if not text or not _NUMERIC_RE.match(text):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _to_numeric_if_possible(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        parsed = _parse_numeric_text(value)
        if parsed is not None:
            return parsed
    return value


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        parsed = _parse_numeric_text(value)
        if parsed is not None:
            return parsed
    return default


def _normalize_row_for_numeric(row: dict | None) -> dict | None:
    if row is None or not isinstance(row, dict):
        return row
    cached = row.get(_ROW_NUMERIC_CACHE_KEY)
    if isinstance(cached, dict):
        return cached

    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if key == _ROW_NUMERIC_CACHE_KEY:
            continue
        normalized[key] = _to_numeric_if_possible(value)

    # Cache per-row to avoid repeated conversion across many rules.
    row[_ROW_NUMERIC_CACHE_KEY] = normalized
    return normalized


def _log_rule_error_limited(rule_name: str, error: Exception) -> None:
    msg = str(error)
    key = (rule_name, msg)
    count = _RULE_ERROR_COUNTS.get(key, 0) + 1
    _RULE_ERROR_COUNTS[key] = count

    should_log = count <= _RULE_ERROR_LOG_FIRST_N
    if not should_log and _RULE_ERROR_LOG_EVERY_N > 0:
        should_log = count % _RULE_ERROR_LOG_EVERY_N == 0
    if not should_log:
        return

    if count == 1:
        logger.warning("规则检查异常 %s: %s", rule_name, msg)
    else:
        logger.warning("规则检查异常 %s: %s (same_error_count=%d)", rule_name, msg, count)


class ConditionType(Enum):
    """条件类型枚举"""

    STATE_CHANGE = "state_change"  # 状态变化 (prev_value → curr_value)
    THRESHOLD_CROSS_UP = "cross_up"  # 从下方穿越阈值
    THRESHOLD_CROSS_DOWN = "cross_down"  # 从上方穿越阈值
    CROSS_UP = "line_cross_up"  # 两值交叉上穿 (a < b → a > b)
    CROSS_DOWN = "line_cross_down"  # 两值交叉下穿 (a > b → a < b)
    CONTAINS = "contains"  # 字符串包含
    RANGE_ENTER = "range_enter"  # 进入区间
    RANGE_EXIT = "range_exit"  # 离开区间
    CUSTOM = "custom"  # 自定义lambda


@dataclass
class SignalRule:
    """信号规则数据类"""

    name: str  # 规则名称
    table: str  # 数据表名
    category: str  # 分类: momentum/trend/volatility/volume/futures/pattern/misc
    subcategory: str  # 子分类: rsi/kdj/macd 等
    direction: str  # 方向: BUY/SELL/ALERT
    strength: int  # 强度: 0-100
    priority: str = "medium"  # 优先级: high/medium/low
    timeframes: list[str] = field(default_factory=lambda: ["1h", "4h", "1d"])
    cooldown: int = 3600  # 冷却时间(秒)
    min_volume: float = 100000  # 最小成交额
    condition_type: ConditionType = ConditionType.CUSTOM
    condition_config: dict[str, Any] = field(default_factory=dict)
    message_template: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    def check_condition(self, prev: dict | None, curr: dict) -> bool:
        """检查条件是否满足"""
        if not self.enabled:
            return False

        try:
            ct = self.condition_type
            cfg = self.condition_config

            if ct == ConditionType.STATE_CHANGE:
                if not prev:
                    return False
                fld = cfg.get("field", "")
                from_vals = cfg.get("from_values", [])
                to_vals = cfg.get("to_values", [])
                prev_val = str(prev.get(fld, ""))
                curr_val = str(curr.get(fld, ""))
                return prev_val in from_vals and curr_val in to_vals

            elif ct == ConditionType.THRESHOLD_CROSS_UP:
                if not prev:
                    return False
                fld = cfg.get("field", "")
                threshold = _to_float(cfg.get("threshold", 0), 0.0)
                prev_val = _to_float(prev.get(fld, 0), 0.0)
                curr_val = _to_float(curr.get(fld, 0), 0.0)
                return prev_val <= threshold < curr_val

            elif ct == ConditionType.THRESHOLD_CROSS_DOWN:
                if not prev:
                    return False
                fld = cfg.get("field", "")
                threshold = _to_float(cfg.get("threshold", 0), 0.0)
                prev_val = _to_float(prev.get(fld, 0), 0.0)
                curr_val = _to_float(curr.get(fld, 0), 0.0)
                return prev_val >= threshold > curr_val

            elif ct == ConditionType.CROSS_UP:
                if not prev:
                    return False
                fa = cfg.get("field_a", "")
                fb = cfg.get("field_b", "")
                prev_a = _to_float(prev.get(fa, 0), 0.0)
                prev_b = _to_float(prev.get(fb, 0), 0.0)
                curr_a = _to_float(curr.get(fa, 0), 0.0)
                curr_b = _to_float(curr.get(fb, 0), 0.0)
                return prev_a <= prev_b and curr_a > curr_b

            elif ct == ConditionType.CROSS_DOWN:
                if not prev:
                    return False
                fa = cfg.get("field_a", "")
                fb = cfg.get("field_b", "")
                prev_a = _to_float(prev.get(fa, 0), 0.0)
                prev_b = _to_float(prev.get(fb, 0), 0.0)
                curr_a = _to_float(curr.get(fa, 0), 0.0)
                curr_b = _to_float(curr.get(fb, 0), 0.0)
                return prev_a >= prev_b and curr_a < curr_b

            elif ct == ConditionType.CONTAINS:
                fld = cfg.get("field", "")
                patterns = cfg.get("patterns", [])
                match_any = cfg.get("match_any", True)
                val = str(curr.get(fld, ""))
                if match_any:
                    return any(p in val for p in patterns)
                return all(p in val for p in patterns)

            elif ct == ConditionType.RANGE_ENTER:
                if not prev:
                    return False
                fld = cfg.get("field", "")
                min_v = _to_float(cfg.get("min_value", float("-inf")), float("-inf"))
                max_v = _to_float(cfg.get("max_value", float("inf")), float("inf"))
                prev_val = _to_float(prev.get(fld, 0), 0.0)
                curr_val = _to_float(curr.get(fld, 0), 0.0)
                prev_in = min_v <= prev_val <= max_v
                curr_in = min_v <= curr_val <= max_v
                return not prev_in and curr_in

            elif ct == ConditionType.RANGE_EXIT:
                if not prev:
                    return False
                fld = cfg.get("field", "")
                min_v = _to_float(cfg.get("min_value", float("-inf")), float("-inf"))
                max_v = _to_float(cfg.get("max_value", float("inf")), float("inf"))
                prev_val = _to_float(prev.get(fld, 0), 0.0)
                curr_val = _to_float(curr.get(fld, 0), 0.0)
                prev_in = min_v <= prev_val <= max_v
                curr_in = min_v <= curr_val <= max_v
                return prev_in and not curr_in

            elif ct == ConditionType.CUSTOM:
                func = cfg.get("func")
                if callable(func):
                    prev_norm = _normalize_row_for_numeric(prev) if prev else None
                    curr_norm = _normalize_row_for_numeric(curr)
                    return func(prev_norm, curr_norm)
                return False

            return False
        except Exception as e:
            _log_rule_error_limited(self.name, e)
            return False

    def format_message(self, prev: dict | None, curr: dict) -> str:
        """格式化消息"""
        try:
            fmt_args = {}
            for arg_name, field_name in self.fields.items():
                if arg_name.startswith("prev_"):
                    fmt_args[arg_name] = prev.get(field_name, 0) if prev else 0
                else:
                    fmt_args[arg_name] = curr.get(field_name, 0) or 0
            return self.message_template.format(**fmt_args)
        except Exception as e:
            logger.warning(f"消息格式化异常 {self.name}: {e}")
            return self.message_template
