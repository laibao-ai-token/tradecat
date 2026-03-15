# Agent Observability: TUI / Log / Session

## Summary

本文件是 `TRA-11` 的实现分析补充，目标不是证明“已经联调通过”，而是把当前仓库里能确认的证据、建议的最小字段契约和后续实现顺序写清楚。

当前结论：

- 锚点字段、TUI 投影、日志投影已经收敛。
- TradeCat 本地 Agent Shell 已有最小 `jsonl` 事件落点。
- openclaw repo 内已能确认 session transcript 默认路径、Gateway history 入口与 run 终态锚点。
- 因此当前状态可视为 `contract-ready / local-landing-done`；真实产品链路 smoke 由 `#003-06` 继续承接。

## Repository Evidence

| 证据 | 位置 | 结论 |
| --- | --- | --- |
| parent issue 已把三端可观测列为主线目标 | `.issues/open/003-trade-agent/003-feature-trade-agent-development.md` | 这不是额外需求，而是 `#003` 既定范围 |
| TUI 已有可扩展的顶栏 | `services-preview/tui-service/src/tui.py` 中 `_build_header_line()` / `_draw_header()` | 可以放 `session/turn/model/status` |
| TUI 已有右侧细节面板模式 | `services-preview/tui-service/src/tui.py` 中 `_draw_market_master()` | 可以放事件摘要流，而不必先做复杂卡片 |
| 仓库已有 JSON 结构化日志方案 | `services/trading-service/src/observability/logger.py` | agent 日志不需要重新发明格式 |
| `#003-04` 已定义内部 DTO 草案 | `.issues/open/003-trade-agent/closed-003-04-feature-agent-tool-events-schema.md` | 本地锚点字段已可收敛 |
| `#003-02` 支撑文档已存在 | `.issues/open/003-trade-agent/closed-003-02-01-feature-agent-gateway-contract-mapping.md`、`.issues/open/003-trade-agent/closed-003-02-02-feature-agent-transport-state-fallback-mapping.md` | 可作为 adapter 映射参考 |
| openclaw session transcript 默认路径已明确 | `repository/openclaw/docs/gateway/security/index.md` | 默认落盘到 `~/.openclaw/agents/<agentId>/sessions/*.jsonl` |
| openclaw Gateway history/session 入口可读 | `repository/openclaw/src/tui/gateway-chat.ts` | `chat.history` / `sessions.list` / `sessions.patch` / `sessions.reset` 已可定位 |
| openclaw run 终态锚点可读 | `repository/openclaw/test/helpers/gateway-e2e-harness.ts` | 可按 `runId + sessionKey + state=final` 识别一轮结束 |

## Minimal Contract

### Why current `tool_*` events are not enough

仅有 `tool_start / tool_update / tool_end / tool_error`，只能证明“工具被调过”，不能稳定回答下面两个问题：

1. 这条工具事件属于哪一轮请求？
2. 这一轮最终是成功、失败还是中途取消？

因此最小闭环需要补齐非工具事件：

- `turn_start`
- `assistant_final`
- `turn_end`

## Required Fields

| Field | Required On | Purpose |
| --- | --- | --- |
| `session_id` | all events | conversation-level anchor |
| `turn_id` | all events | per-request anchor |
| `event_seq` | all events | deterministic ordering |
| `ts` | all events | time correlation |
| `event_type` | all events | lifecycle classification |
| `status` | all events | running/success/error/cancelled/partial |
| `model` | turn/tool/final events | actual serving model |
| `final_status` | `assistant_final` / `turn_end` | terminal verdict |
| `tool_name` | tool events | tool identity |
| `tool_call_id` | tool events preferred | tool call correlation |
| `tool_summary` | tool end/final preferred | human-readable evidence |
| `latency_ms` | tool end/final preferred | bottleneck diagnosis |

## Projection Rules

### TUI

TUI 不应该承担完整审计存储，只承担“快速定位本轮”的职责。

必须显示：

- header: `session_id(short)` / `turn_id(short)` / `model` / `status`
- trace list: `ts` / `event_seq` / `event_type` / `tool_name` / `status` / `tool_summary`
- answer badge: `final_status` + `tool_summary`

不建议在 TUI 中显示：

- 原始工具 payload
- 长 prompt
- 大段 history 原文

### Log

日志应当是“TradeCat 侧可 grep 的事实副本”，而不是一堆人工文本。

推荐：

- 文件：`services-preview/tui-service/logs/agent_events.jsonl`
- 一事件一行 JSON
- 一轮结束时额外写一条摘要，便于人工排查

### Session / History

openclaw session/history 应当是全链路事实源。

要求：

- 与日志复用同一套锚点字段
- 不允许 TUI 或 adapter 在落盘时重命名关键字段
- 至少存在一个 terminal event 表达本轮结束

## Recommended TUI Placement

### Header

现有 `tui.py` 顶栏已经承载时间、页面名、刷新状态、服务状态，天然适合再追加 agent 锚点。

建议格式：

```text
TradeCat TUI | Agent | s=sess_001 | t=turn_017 | model=qwen3-max | success | 23:55:13Z
```

### Right Pane

现有右侧面板用于显示符号信号明细，说明 UI 已经具备“左主视图 + 右细节流”的结构。agent 版可以直接沿用该模式：

```text
23:55:12 [1] turn_start
23:55:12 [2] tool_start get_quote
23:55:13 [3] tool_end get_quote success BTCUSDT 68234.12
23:55:13 [4] assistant_final success
```

## Verification Runbook

### Preconditions

- direct API path available
- event contract landed
- agent log file available
- openclaw session/history readable

### Procedure

1. 发起固定测试请求：`帮我查 BTCUSDT 最新价格，并说明是否调用了工具、工具名和时间。`
2. 从 TUI 记录 `session_id`、`turn_id`、`model`、最终状态。
3. 在日志中按 `turn_id` grep。
4. 在 openclaw session/history 中按同一 `turn_id` grep。
5. 对比 `session_id / model / tool_name / final_status / event_seq`。
6. 若任一端缺记录，则标记为 `BLOCKED` 或 `FAIL`，不能算通过。

### Grep Template

```bash
TURN_ID="turn_00017"
rg "\"turn_id\":\"${TURN_ID}\"" services-preview/tui-service/logs/agent_events.jsonl
rg "\"turn_id\":\"${TURN_ID}\"" "$OPENCLAW_SESSION_JSONL"
```

## Blockers

1. `#003-02` 仍需把真实 adapter 主链路接进来，替换本地 demo shell 事件源。
2. `#003-06` 仍需沿最终产品路径补一次真实 smoke，并写回三端对账证据。
3. 若真实 runtime 字段与当前 contract 不一致，需要回补 adapter 映射而不是回滚 observability 契约。

## Implementation Order

1. `#003-04` 先固化事件 envelope。
2. `#003-02` 保证 direct path / adapter 透传同一组锚点。
3. TradeCat 本地 TUI / `jsonl` 投影已经完成；剩余真实产品链路执行交给 `#003-06`。
4. 最后按本文件 runbook 做真实三端对账。

## Exit Criteria For Real Pass

只有同时满足下面四项，TRA-11 才能从“方案 ready”升级为“真实验收 pass”：

- TUI 可见同一轮 `session_id + turn_id`
- TradeCat 日志可 grep 到同一 `turn_id`
- openclaw session/history 可 grep 到同一 `turn_id`
- 三端的 `model / tool_name / final_status` 一致
