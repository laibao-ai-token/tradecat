---
title: "004-review-06b98d00-tui-agent-shell-placeholder-events"
status: review
created: 2026-03-17
updated: 2026-03-18
owner: codex
priority: high
type: review
linear: TRA-28
---

# Review-004: 06b98d00 tui agent shell placeholder events

## 基本信息

- Commit: `06b98d008bf9487bb4c891aa6c7672fb4c2deb7c`
- Title: `feat(tui): add agent shell placeholder events`
- 范围：
  - `services-preview/tui-service/src/agent_events.py`
  - `services-preview/tui-service/src/tui.py`
  - `services-preview/tui-service/tests/test_agent_events.py`
  - `services-preview/tui-service/tests/test_tui_agent_shell.py`

## 执行记录

1. 执行 `codex review --commit 06b98d00`：
   - 首次直接执行长期无输出；
   - 二次执行 `timeout 300 codex review --commit 06b98d00` 返回 `exit 124`（超时）。
2. 在自动 review 结果不可用的情况下，改为人工审查 commit diff，并结合目标测试验证。

## Review 结果（回填）

- 结论：`通过（无阻塞问题）`
- 重点检查：
  - `事件 DTO 字段前后兼容`：通过
    `agent_events.py` 中事件类型目录固定（`EVENT_TYPES`），统一携带版本字段 `v=1`，并通过 `_strip_none()` 去除可选空字段；`system.fallback` 的 `from_ -> from` 序列化映射明确，兼容 JSON key 约束。
  - `placeholder 与真实 agent 通道隔离`：通过
    `tui.py` 中占位壳层由 `TUI_ENABLE_AGENT_PLACEHOLDER` 显式开关控制（默认关闭），状态明确标注 `connection_state=shell-only`、`model=openclaw-shell`，且交互逻辑只在右侧占位 panel 生效，未混入真实通道。
  - `JSONL 写入稳健性`：通过
    `_record_agent_event()` 对日志写入异常进行了捕获降级（只更新 `status_text`，不向上抛错），避免日志写失败导致主界面崩溃。
- 备注：
  - 本地执行 `codex review` 在当前环境超时，未产出自动审查文本；本次结论基于 diff + 测试结果给出。

## 验证

- 指令（按建议）：
  - `cd services-preview/tui-service`
  - `pytest -q tests/test_agent_events.py tests/test_tui_agent_shell.py`
- 实际结果：
  - 直接运行因环境路径未注入报错：`ModuleNotFoundError: common`
  - 使用 `PYTHONPATH=.:../../libs pytest -q tests/test_agent_events.py tests/test_tui_agent_shell.py` 后通过：`12 passed`
