---
title: "003-05-feature-agent-observability-tui-log-session"
status: closed
created: 2026-03-09
updated: 2026-03-11
closed: 2026-03-11
owner: lixh6
priority: high
type: feature
---

# [003-05] TUI / 日志 / session 三端可观测打通

## 背景

`#003-04` 解决“事件怎么定义”，本单解决“同一轮请求在哪里看、怎么对账”。

当前拆分关系已经明确：

- TradeCat：用户/开发者直接看的 TUI 观察面
- adapter：把上游 runtime 事件映射成 TradeCat 内部 DTO
- openclaw：runtime / session / history 的事实来源

本单需要给 `#003-02 / #003-04 / #003-06` 一个可以直接落地和验收的最小闭环，而不是只给抽象原则。

## 当前仓库证据

### 已有可复用能力

- `.issues/open/003-trade-agent/003-feature-trade-agent-development.md` 已把“三端可观测”列为 `#003` 的 Phase 4 主目标：
  - 结构化工具事件：`tool_start / tool_update / tool_end`
  - TUI 可见工具卡片或事件摘要
  - session `jsonl` 可回溯同一轮调用
- `.issues/open/003-trade-agent/closed-003-04-feature-agent-tool-events-schema.md` 已定义 TradeCat 内部稳定 DTO 草案，可作为本单锚点字段来源。
- `.issues/open/003-trade-agent/closed-003-02-01-feature-agent-gateway-contract-mapping.md` 与 `.issues/open/003-trade-agent/closed-003-02-02-feature-agent-transport-state-fallback-mapping.md` 已提供上游语义映射参考。
- `services-preview/tui-service/src/tui.py` 已有稳定的 TUI 顶栏和左右双栏布局，可直接承载最小 agent 观测信息：
  - 顶栏：`_build_header_line()` / `_draw_header()`
  - 右栏：`_draw_market_master()` 中已有“右侧细节面板”模式
- `services-preview/tui-service/src/tui.py` 已新增本地 Agent Shell 的 `session_id / turn_id / final_status / last_event_type` 展示，并按 DTO 结构写入 `services-preview/tui-service/logs/agent_events.jsonl`。
- `services-preview/tui-service/tests/test_tui_agent_shell.py` 已验证本地 Agent Shell 输入会稳定产出 `session.start -> session.user_message -> tool.start -> tool.end -> assistant.message` 事件链。
- `services/trading-service/src/observability/logger.py` 已有 JSON 日志格式与上下文字段注入模式，可直接复用到 agent 事件日志。
- `repository/openclaw/docs/gateway/security/index.md` 已明确 session transcript 默认落盘路径：`~/.openclaw/agents/<agentId>/sessions/*.jsonl`。
- `repository/openclaw/src/tui/gateway-chat.ts` 已确认上游 Gateway 侧存在 `chat.history`、`sessions.list`、`sessions.patch`、`sessions.reset` 等观测入口。
- `repository/openclaw/test/helpers/gateway-e2e-harness.ts` 已确认 run 终态可通过 `runId + sessionKey + state=final` 识别，为三端对账提供上游终态锚点。

### 当前剩余依赖（不阻断本单关闭）

- `#003-02` 仍需把真实 `OpenClawAdapter` 主链路接入 TradeCat，替换当前本地 demo shell 的占位事件源。
- `#003-06` 仍需沿“TradeCat 面板 -> openclaw backend”真实产品路径跑一次最小 smoke，补齐最终对账证据。
- 当前已能完成的是“字段契约 + TUI 落点 + 日志落点 + upstream session/history 路径与接口确认”；真实联调执行不再放在本单关闭边界内。

## 目标

交付一个可直接指导实现和验收的三端可观测闭环方案，明确：

1. 最小稳定锚点字段
2. 三端各自的落点
3. 最小交叉验证链路
4. 可复用的证据模板 / runbook
5. 当前 blocker 与后续依赖

## 非目标

- 不修改 `config/.env`
- 不修改数据库 schema
- 不修改 `libs/database/*`
- 默认不修改 `repository/openclaw`
- 不伪造真实 runtime / session 联调结果

## 假设

- 假设 `#003-04` 最终会落地 `tool_start / tool_update / tool_end / tool_error` 事件族。
- 假设 openclaw 的 session/history 仍然是 append-only 历史源（`jsonl` 或等价事件流存储）。
- 假设 adapter 可以透传上游锚点，而不是在 TUI / 日志 / session 三端分别各自生成一套 ID。

若以上任一假设不成立，本单结论需要回滚并重新对齐。

## 最小观测锚点字段

### 必须共享的字段

| 字段 | 是否必须 | 说明 | 备注 |
| --- | --- | --- | --- |
| `session_id` | 必须 | 同一会话稳定不变 | 三端对账的一级锚点 |
| `turn_id` | 必须 | 同一轮用户请求稳定不变 | 三端对账的核心锚点 |
| `event_seq` | 必须 | 同一 `turn_id` 内单调递增 | 用于排序和补漏 |
| `ts` | 必须 | 事件时间，UTC ISO8601 | 人工比对和日志 grep 都依赖它 |
| `event_type` | 必须 | 事件类型 | 至少支持 `turn_start / tool_* / assistant_final / turn_end` |
| `status` | 必须 | 当前事件状态 | 推荐 `running / success / error / cancelled / partial` |
| `model` | 必须 | 实际发起请求的模型名 | 必须是“真实请求模型”，不是 UI 幻象 |
| `final_status` | 必须 | 本轮最终状态 | 建议仅在 `assistant_final` 或 `turn_end` 上写入 |

### 工具事件必须补充的字段

| 字段 | 是否必须 | 说明 |
| --- | --- | --- |
| `tool_name` | 必须 | 如 `get_quote` / `search_news` |
| `tool_call_id` | 推荐 | 若上游提供则原样透传；若没有，可由 `(turn_id, event_seq)` 派生一次并全链路复用 |
| `tool_summary` | 推荐 | 面向人读的短摘要，120 字符内，不放原始大 payload |
| `latency_ms` | 推荐 | 工具完成或失败时写入，便于定位慢点 |

### 建议的最小事件包

```json
{
  "ts": "2026-03-10T23:55:12.123Z",
  "session_id": "sess_20260310_001",
  "turn_id": "turn_00017",
  "event_seq": 4,
  "event_type": "tool_end",
  "status": "success",
  "final_status": "",
  "model": "qwen3-max",
  "tool_name": "get_quote",
  "tool_call_id": "call_turn_00017_02",
  "tool_summary": "BTCUSDT 68234.12 from Binance REST",
  "latency_ms": 183
}
```

## 三端落点

### 1. TUI 侧

| 位置 | 必须显示的字段 | 用途 |
| --- | --- | --- |
| 顶栏状态区 | `session_id(short)` / `turn_id(short)` / `model` / `final_status or status` / `ts(last)` | 给用户一个“当前看的是哪一轮”的稳定锚点 |
| 事件摘要区或工具卡片区 | `event_seq` / `ts` / `event_type` / `tool_name` / `status` / `tool_summary` | 给开发者和用户看同一轮内发生了什么 |
| 最终回答来源标记 | `final_status` / `tool_summary` / `model` | 避免“回答看起来像查过，但找不到证据” |

落点建议：

- 顶栏直接复用 `services-preview/tui-service/src/tui.py` 现有 header 模式，扩展为“TradeCat TUI | 页 | session | turn | model | status”。
- 事件摘要区复用现有双栏右侧细节面板思路，不在主回答区里混排原始事件，避免阅读噪音。
- TUI 只展示短摘要，不展示完整 payload；完整证据去日志和 session/history 看。

推荐的最小 TUI 文案：

```text
Agent s=sess_001 t=turn_017 model=qwen3-max status=running updated=23:55:12Z
23:55:12 [1] turn_start
23:55:12 [2] tool_start get_quote
23:55:13 [3] tool_end get_quote success BTCUSDT 68234.12
23:55:13 [4] assistant_final success tool:get_quote
```

### 2. TradeCat 日志侧

| 位置 | 必须落的字段 | 用途 |
| --- | --- | --- |
| 结构化事件日志（推荐 jsonl） | 全量锚点字段 | 机器可 grep、可聚合、可离线复盘 |
| 回合结束摘要行 | `session_id` / `turn_id` / `model` / `final_status` / `tool_summary` / `latency_ms(total)` | 人工快速判定一轮是否成功 |

日志建议：

- 推荐新增服务级文件，例如 `services-preview/tui-service/logs/agent_events.jsonl`。
- 每个事件一行 JSON，禁止只写纯文本“工具执行成功”。
- 可以直接复用 `services/trading-service/src/observability/logger.py` 的 JSON formatter + `ctx` 注入模式，避免再造一套日志约定。

### 3. openclaw session/history 侧

| 位置 | 必须落的字段 | 用途 |
| --- | --- | --- |
| session 元信息 | `session_id` / `created_ts` / `model(default)` | 会话级定位 |
| history 事件流 | 与日志同一套锚点字段 | 作为跨端对账的事实源 |
| turn 收尾事件 | `turn_id` / `final_status` / `tool_summary` | 让“这一轮结束了吗”可被程序判断 |

说明：

- session/history 侧应当是“事实源”，adapter 和 TUI 只消费，不重写锚点。
- openclaw repo 已确认默认 transcript 路径为 `~/.openclaw/agents/<agentId>/sessions/*.jsonl`，运行时还可通过 `chat.history` 读取同一会话历史。
- 若 openclaw 当前没有 `turn_start / assistant_final / turn_end`，建议在 `#003-04` 中补齐，否则只能证明“工具调用发生过”，不能证明“同一轮已闭环结束”。

## 最小交叉验证链路

### 前置条件

- `#003-02` 已能从 TradeCat 发起真实请求到 runtime
- `#003-04` 已按本单定义落地统一锚点字段
- openclaw session/history 可读
- TradeCat 侧已有 agent 事件日志文件

若任一前置条件不满足，真实产品链路结论必须写 `BLOCKED`，该执行结果由 `#003-06` 负责承接。

### 推荐验证请求

使用一个高概率触发工具、且结果容易人工判断的请求：

```text
帮我查 BTCUSDT 最新价格，并说明是否调用了工具、工具名和时间。
```

### 对账步骤

1. 在 TUI 发起请求。
2. 记录 TUI 顶栏中的 `session_id`、`turn_id`、`model`、最终状态。
3. 在 TUI 事件区确认至少出现：
   - `turn_start`
   - `tool_start`
   - `tool_end` 或 `tool_error`
   - `assistant_final`
   - `turn_end`
4. 在 TradeCat 结构化日志里按 `turn_id` 检索。
5. 在 openclaw session/history 里按同一 `turn_id` 检索。
6. 核对三端是否满足：
   - `session_id` 一致
   - `turn_id` 一致
   - `model` 一致
   - `tool_name` 一致
   - `final_status` 一致
   - 时间顺序与 `event_seq` 不冲突

### 推荐命令

```bash
TURN_ID="turn_00017"
rg "\"turn_id\":\"${TURN_ID}\"" services-preview/tui-service/logs/agent_events.jsonl
rg "\"turn_id\":\"${TURN_ID}\"" "$OPENCLAW_SESSION_JSONL"
```

说明：

- `OPENCLAW_SESSION_JSONL` 默认可先按 `~/.openclaw/agents/<agentId>/sessions/*.jsonl` 实际路径定位，再结合 `chat.history` 做运行时对账。
- 如果只能搜到日志，搜不到 session/history，不算产品链路 pass，应在 `#003-06` 中记录为 `BLOCKED(session sample missing)`。

## 证据模板

```md
## Case

- Date:
- Prompt:
- Model:
- Session ID:
- Turn ID:

## TUI

- Header:
- Event trace:
- Final answer source tag:

## TradeCat Log

- File:
- Matched events:
- Missing fields:

## openclaw Session/History

- File:
- Matched events:
- Missing fields:

## Verdict

- PASS / FAIL / BLOCKED:
- Reason:
- Next action:
```

## 推荐落地顺序

### 对 `#003-04`

- 固化事件包，至少覆盖 `turn_start / tool_start / tool_update / tool_end / tool_error / assistant_final / turn_end`
- 明确 `session_id / turn_id / event_seq / model / final_status` 为全链路必填

### 对 `#003-02`

- 从 runtime 入口就生成或接收 `session_id / turn_id`
- adapter 只做透传和投影，不重复生成锚点
- 若上游缺 `tool_call_id`，只允许在 adapter 首次补一次，之后全链路沿用

### 对 `#003-06`

- TUI 先做“顶栏 + 事件摘要区”最小版，不要一开始做复杂卡片
- 日志先落 `jsonl`，后续再接 ELK/Loki
- 验收按本单 runbook 做，不以“页面上看起来有东西”替代真正对账

## 后续依赖（交由 `#003-02` / `#003-06`）

1. `#003-02`：接通真实 `TradeCat -> openclaw` adapter，让 TUI / 日志事件不再来自本地 demo shell。
2. `#003-06`：按本单 runbook 走完整产品路径，产出真实三端对账证据与 pass/fail 结论。
3. 若真实 runtime 的 session/history 字段与当前假设不一致，再回补到 DTO/adapter 层，而不是回滚本单的观测契约。

## 交付结论

- [x] 已定义最小共享锚点字段
- [x] 已明确 TUI / 日志 / session-history 三端逻辑落点
- [x] 已确认 openclaw upstream 的 session transcript 默认路径、Gateway history 入口与 run 终态锚点
- [x] 已完成 TradeCat 本地 Agent Shell 的最小 TUI 展示与 `agent_events.jsonl` 落点
- [x] 已给出一条最小对账 runbook
- [x] 已给出证据模板
- [x] 已把真实产品链路 smoke 明确下沉到 `#003-06`

关闭说明：本单的收口目标是“定义并落好可观测契约、投影位置与对账 runbook”，不是替代真实产品链路冒烟。真实联调 pass/fail 由 `#003-06` 负责承接。

## 进展记录

### 2026-03-10

- [x] 基于当前仓库结构，补齐三端可观测闭环方案
- [x] 明确最小锚点字段与三端落点
- [x] 补充最小对账 runbook 与证据模板

### 2026-03-11

- [x] 已确认 `repository/openclaw` 内可直接引用的 upstream 证据：session transcript 默认路径、`chat.history` / `sessions.list` 入口、run 终态识别方式
- [x] 已完成 TradeCat 本地 Agent Shell 的最小 TUI / jsonl 观测落点
- [x] 已将真实产品链路 smoke 明确转交 `#003-06`，本单关闭
