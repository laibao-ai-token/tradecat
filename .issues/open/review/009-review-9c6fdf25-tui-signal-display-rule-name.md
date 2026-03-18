---
title: "009-review-9c6fdf25-tui-signal-display-rule-name"
status: review
created: 2026-03-18
updated: 2026-03-18
owner: codex
priority: medium
type: review
linear: TRA-33
---

# [Review-009][9c6fdf25] codex review tui signal display rule name

## Review 输入

- Commit: `9c6fdf250c3612aa1a6daa275a3274f52ac2e250`
- Title: `feat(tui): display rule name from signal metadata`
- Command: `codex review --commit 9c6fdf25`

## Review 结果（回填）

- 已执行 `codex review --commit 9c6fdf25`，最终结论为：该提交在当前代码上下文中未发现明确回归或正确性问题。
- 检查点 1（`extra` JSON 解析失败回退）：`services-preview/tui-service/src/db.py` 中 `json.loads` 包裹在 `try/except`，解析失败时保留原 `signal_type`，行为平滑。
- 检查点 2（显示优先级）：显示字段采用 `payload.get("rule_name") or display_signal_type`，即 `extra.rule_name` 优先于原字段。
- 检查点 3（历史数据格式兼容）：当前代码 `SELECT ... extra FROM signal_history` 依赖 `extra` 列存在；若接入非常老的历史库（无 `extra` 列），查询会进入异常分支并返回空列表，建议后续补一版列存在性兼容处理。

## 验证记录

- 执行命令：`cd services-preview/tui-service && pytest -q tests/test_news_db.py`
- 结果：`10 passed in 0.15s`

## 闭环更新（2026-03-18）

- 已修复：`services-preview/tui-service/src/db.py`
  - 查询 `signal_history` 前会探测 `extra` 列是否存在；
  - 对旧库（无 `extra`）使用 `NULL AS extra` 回退，避免查询异常并保持历史兼容。
- 新增测试：`services-preview/tui-service/tests/test_db.py`
  - 覆盖有 `extra` 列与无 `extra` 列两种 schema。
- 验证：
  - `cd services-preview/tui-service && pytest -q tests/test_db.py tests/test_news_db.py` 通过（`12 passed`）。
