---
title: "007-review-e3f5f645-signal-rule-id-readonly-history"
status: review
created: 2026-03-18
updated: 2026-03-18
owner: codex
priority: high
type: review
linear: TRA-31
---

# [Review-007] signal rule_id + readonly history

## Review 范围

- Commit: `e3f5f6456160465c7d7bd161f352fd3b1108c460`
- Title: `feat(signal-service): add stable rule ids and read-only signal history access`
- 检查重点：
  - `rule_id` 是否稳定且唯一，重名规则 resolve 是否正确
  - cooldown key 切换为 `rule_id` 后是否避免冲突
  - history 读取与展示对旧数据是否兼容

## Review 结果（回填）

### 执行记录

- 已执行 `codex review --commit e3f5f645`（两次，均在长时间源码扫描后超时退出；命令本身已执行）。
- 补充执行建议验证：
  - `cd services/signal-service && pytest -q tests/test_rules_registry.py tests/test_read_only_signals.py tests/test_history.py`
  - 结果：`12 passed in 0.50s`

### Findings

1. **[Medium] signal correlation 脚本对新 `rule_id` 行的元数据回填不完整**
   - 位置：`scripts/signal_correlation_analysis.py`
   - 细节：`_load_rule_meta()` 仅以 `rule.name` 建索引；`_load_history_events()` 用 `signal_history.signal_type` 直接查该索引。
   - 影响：该 commit 后 SQLite 新信号写入的 `signal_type` 为 `rule_id`，导致相关脚本读取历史时可能拿不到 `category/table/direction` 元数据，分析维度退化（不一定报错，但统计质量下降）。
   - 建议：在 `rule_meta` 中同时建立 `rule.name` 和 `rule.rule_id` 索引，或读取时先走 `resolve_rule_id/resolve_rule_name` 再映射。

### 通过项

- `rule_id` 稳定性与唯一性：
  - `SignalRule.__post_init__` 基于 `category/subcategory/table/name/direction` 生成稳定 `rule_id`。
  - `tests/test_rules_registry.py::test_rule_ids_are_unique` 验证当前规则集唯一。
  - 重名规则（例如 `主动买盘极端`）可通过 `category/subcategory` scope 正确 resolve，且展示 key 去冲突。
- cooldown key 冲突规避：
  - `services/signal-service/src/engines/sqlite_engine.py` 冷却键已改为 `rule_id + symbol + timeframe`，可避免同名规则在不同分类上的 key 冲突。
- history 向后兼容：
  - `storage/history.py` 写入时保留 `extra.rule_id/rule_name`；展示优先 `rule_name`，并对旧 `signal_type` 保持兼容 fallback。
  - `storage/read_only.py` 以只读方式读取 `signal_history`，不改 schema，不改写历史数据。

## 结论

- 该提交主目标（稳定 `rule_id`、冷却键去冲突、只读历史查询）已达成。
- 存在 1 个中等级别的下游脚本兼容风险（`signal_correlation_analysis.py` 元数据映射），建议在后续 commit 修复。

## 闭环更新（2026-03-18）

- 已修复：`scripts/signal_correlation_analysis.py`
  - `_load_rule_meta()` 现在同时建立 `rule_id` 与 `rule.name` 索引。
  - `_load_cooldown_events()` 与 `_load_history_events()` 对 `rule_id` 输入可正确回填 `rule_name/category/table/direction` 元数据。
- 验证：
  - `python3 -m py_compile scripts/signal_correlation_analysis.py` 通过。
