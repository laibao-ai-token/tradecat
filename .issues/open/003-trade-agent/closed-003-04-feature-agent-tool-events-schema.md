---
title: "003-04-feature-agent-tool-events-schema"
status: closed
created: 2026-03-09
updated: 2026-03-10
closed: 2026-03-10
owner: lixh6
priority: high
type: feature
---

# #003-04 工具事件统一 DTO 与字段语义（TradeCat 内部稳定合约）

## 背景 / 问题

在真正实现 `OpenClawAdapter` 之前，TradeCat 需要先统一一套内部稳定的事件 DTO（Data Transfer Object）与字段语义，用于承接：

- TUI 实时展示
- session jsonl 落盘与回放
- 结构化日志与问题排查

如果不先收敛内部合约，后续会出现三类问题：

- 上游 gateway/provider payload 一变，TUI 和日志解析一起失效
- 同一轮工具调用在 UI、session、日志中无法稳定关联
- transport timeout / fallback 这类“非回答文本但非常关键”的信息无法被可靠记录

## 参考与假设

- 父 issue：`.issues/open/003-trade-agent/003-feature-trade-agent-development.md`
- 已有支撑文档：
  - `.issues/open/003-trade-agent/closed-003-02-01-feature-agent-gateway-contract-mapping.md`
  - `.issues/open/003-trade-agent/closed-003-02-02-feature-agent-transport-state-fallback-mapping.md`
- 预期服务边界：
  - TradeCat：负责 TUI / 布局 / 交互
  - openclaw：负责 session / chat / model / tool backend
  - TradeCat 通过 `OpenClawAdapter` 对接 openclaw Gateway
- 即使已有 `#003-02` 支撑文档，本 spec 仍以上游 payload 为不透明 JSON 为前提，不绑定某个具体 key path。

> 结论：本规格将上游 payload 视为不透明 JSON。adapter 负责映射、补字段、脱敏与兜底；TUI 不直接吃上游结构。

## 目标

交付一份可直接指导 `#003-02` 与 `#003-05` 落地的“工具事件合约（v1）”：

1. 最小事件族与命名规则（`session / assistant / tool / system`）
2. 每类事件的字段表：必填 / 可选 / 类型 / 语义说明
3. `openclaw payload -> TradeCat DTO` 映射责任与兜底策略（必须保留 `raw`）
4. 至少 2~3 条事件链样例（JSONL）：
   - tool 成功
   - tool 失败（`tool.error`）
   - transport timeout / fallback 决策（模拟样例，注明假设）

## 非目标（本单不做）

- 不定义 openclaw 对外 API 合约
- 不要求当前仓库直接接入 `repository/openclaw`
- 不在 TUI 中实现完整 UI 卡片，只定义稳定 DTO 壳
- 不承载模型思维链（reasoning / chain-of-thought）

---

## 总体规范（v1）

### 事件命名规则

- `type` 使用点分层命名：`{family}.{action}`
- `family` 仅允许：`session` / `assistant` / `tool` / `system`
- `action` 使用 `snake_case`
- v1 固定允许的最小类型集合如下：

| family | type | 产生时机 | 主要用途 |
|---|---|---|---|
| `session` | `session.start` | 会话创建 | 记录会话配置快照 |
| `session` | `session.user_message` | 用户提交一轮输入 | 回放 / turn 关联 |
| `session` | `session.end` | 会话结束 | 标记退出原因 |
| `assistant` | `assistant.delta` | 流式增量到达 | TUI 流式渲染 |
| `assistant` | `assistant.message` | assistant 完整回答落定 | 回放 / 持久化 |
| `tool` | `tool.start` | 工具开始调用 | 工具卡片开始态 |
| `tool` | `tool.update` | 工具有中间状态 | 进度 / 中间结果 |
| `tool` | `tool.end` | 工具成功结束 | 工具结果展示 |
| `tool` | `tool.error` | 工具失败结束 | 错误卡片 / 兜底说明 |
| `system` | `system.transport` | 传输链路状态变化 | timeout / retry / error 可观测 |
| `system` | `system.fallback` | 发生 fallback 决策 | 为什么切模型 / 降级 |
| `system` | `system.log` | 未映射事件或诊断日志 | 保底观测与排障 |

### 基础字段（所有事件通用）

> DTO 是面向 TUI / 日志 / 回放的稳定结构。任何上游字段变化都应由 adapter 吸收。

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `v` | ✅ | `int` | schema version，v1 固定为 `1` |
| `type` | ✅ | `str` | 事件类型，例如 `tool.start` |
| `ts` | ✅ | `float` | 事件发生时间，UTC Unix seconds，可带小数 |
| `event_id` | ✅ | `str` | 事件唯一 ID，用于去重 / 排障 |
| `session_id` | ✅ | `str` | TradeCat 内部稳定 session ID |
| `turn_id` | ⭕ | `str` | 同一轮“用户输入 -> assistant 输出”的关联 ID |
| `source` | ⭕ | `str` | 事件来源，例如 `tradecat` / `adapter` / `openclaw` / `tool` |
| `raw` | ⭕ | `object` | 上游原始 payload 的 JSON-safe 脱敏副本 |
| `meta` | ⭕ | `object` | TradeCat 本地扩展元数据，不参与主语义判断 |

### `raw` 的强制语义

- 所有由上游事件驱动的 DTO，adapter 都必须保留 `raw`
- `raw` 必须满足：
  - JSON-safe
  - 已脱敏
  - 允许截断，但不能伪造或重排主语义
- TUI、日志展示、session 回放逻辑禁止依赖 `raw` 结构
- `raw` 仅用于：
  - 排障
  - 对账
  - 未来 adapter 重映射

### 脱敏底线

以下字段若出现在 `raw` / `args` / `error.detail` / `meta` 中，adapter 必须先脱敏再产出 DTO：

- `Authorization`
- `Cookie`
- `api_key`
- `token`
- `secret`
- 任意 provider 原始 headers 中的鉴权值

---

## 事件族定义

### 1) `session.*`

用于描述会话生命周期以及用户输入，属于 TradeCat 本地稳定事实。

#### `session.start`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `config` | ⭕ | `object` | 会话配置快照，仅保留非敏感字段，例如 `provider` / `model` / `base_url` / `tool_policy` |

#### `session.user_message`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `message_id` | ✅ | `str` | 用户消息 ID |
| `content` | ✅ | `str` | 用户输入文本 |

#### `session.end`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `reason` | ⭕ | `str` | 结束原因，例如 `user_exit` / `timeout` / `error` / `restart` |

### 2) `assistant.*`

用于描述模型输出。可以先流式增量，再有一个终态 message。

#### `assistant.delta`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `message_id` | ✅ | `str` | assistant 消息 ID |
| `delta` | ✅ | `str` | 本次增量文本 |

#### `assistant.message`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `message_id` | ✅ | `str` | assistant 消息 ID |
| `content` | ✅ | `str` | assistant 完整输出文本 |
| `finish_reason` | ⭕ | `str` | 终止原因，例如 `stop` / `length` / `tool_call` / `error` |
| `usage` | ⭕ | `object` | token 统计，例如 `{prompt_tokens, completion_tokens, total_tokens}` |

> 约束：DTO 不承载 reasoning / chain-of-thought。若上游提供 reasoning，adapter 只能丢弃或做不可逆摘要并放到 `meta`。

### 3) `tool.*`

用于描述工具调用生命周期。工具事件必须可被 TUI 卡片化展示，也必须可写入 session jsonl。

#### 关键关联字段

- `tool_call_id`：同一次调用的 `start / update / end / error` 必须一致
- `tool_name`：稳定、可展示、可过滤的工具名

#### `tool.start`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `tool_call_id` | ✅ | `str` | 工具调用 ID |
| `tool_name` | ✅ | `str` | 工具名 |
| `args` | ⭕ | `object` | JSON-safe 入参，需脱敏 |
| `timeout_s` | ⭕ | `float` | 本次工具调用超时阈值 |

#### `tool.update`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `tool_call_id` | ✅ | `str` | 工具调用 ID |
| `tool_name` | ✅ | `str` | 工具名 |
| `message` | ⭕ | `str` | 进度描述，适合 TUI 状态展示 |
| `progress` | ⭕ | `float` | 进度值，范围 `0.0 ~ 1.0` |
| `partial_output` | ⭕ | `object` | 中间结果，必须 JSON-safe |

#### `tool.end`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `tool_call_id` | ✅ | `str` | 工具调用 ID |
| `tool_name` | ✅ | `str` | 工具名 |
| `output` | ⭕ | `object` | 工具输出；若只有文本，建议包成 `{"text": "..."}` |
| `elapsed_s` | ⭕ | `float` | 调用耗时 |

#### `tool.error`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `tool_call_id` | ✅ | `str` | 工具调用 ID |
| `tool_name` | ✅ | `str` | 工具名 |
| `error` | ✅ | `object` | 统一错误结构 |
| `elapsed_s` | ⭕ | `float` | 调用耗时 |

##### 统一错误结构 `error`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `code` | ⭕ | `str` | 稳定错误码，例如 `timeout` / `invalid_args` / `http_500` |
| `message` | ✅ | `str` | 人类可读错误信息 |
| `type` | ⭕ | `str` | 异常类型或上游错误 type |
| `retryable` | ⭕ | `bool` | adapter 对是否可重试的判断 |
| `detail` | ⭕ | `object` | 结构化细节，例如 http status、provider、片段响应等 |

### 4) `system.*`

用于描述传输状态、fallback 决策和兜底日志。这些不是 assistant 正文的一部分，但对排障与 TUI 很关键。

#### `system.transport`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `component` | ✅ | `str` | 组件，例如 `gateway` / `provider` / `tool` / `storage` |
| `operation` | ✅ | `str` | 操作名，例如 `chat.stream` / `chat.request` / `tool.invoke` |
| `state` | ✅ | `str` | 状态，例如 `start` / `ok` / `timeout` / `error` / `retrying` |
| `attempt` | ⭕ | `int` | 第几次尝试，从 `1` 开始 |
| `timeout_s` | ⭕ | `float` | 本次超时阈值 |
| `elapsed_s` | ⭕ | `float` | 已耗时或总耗时 |
| `error` | ⭕ | `object` | 统一错误结构，同 `tool.error.error` |

#### `system.fallback`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `strategy` | ✅ | `str` | fallback 策略，例如 `retry` / `switch_model` / `switch_provider` / `use_cache` / `disable_tools` |
| `reason` | ✅ | `str` | 触发原因，要求简短可读 |
| `from` | ⭕ | `object` | 变更前配置快照，需脱敏 |
| `to` | ⭕ | `object` | 变更后配置快照，需脱敏 |
| `result` | ⭕ | `str` | 决策结果，例如 `applied` / `skipped` / `failed` |

> 代码实现可用 `from_` 存储字段名，序列化时必须输出 JSON key `from`。

#### `system.log`

| 字段 | 必填 | 类型 | 语义 |
|---|---:|---|---|
| `level` | ✅ | `str` | `debug` / `info` / `warning` / `error` |
| `message` | ✅ | `str` | 可读日志文本 |
| `data` | ⭕ | `object` | 附加结构化信息 |

---

## 映射责任：`openclaw payload -> TradeCat DTO`

### 责任边界

- Adapter 负责：
  1. 将上游 payload 映射为本 spec 的 DTO
  2. 为缺失字段补齐默认值
  3. 对 `raw` / `args` / `detail` 做脱敏
  4. 为未知事件提供保底事件，而不是把异常结构直接抛给 TUI
- TUI 负责：
  - 只消费 DTO
  - 不解析上游 payload
  - 不根据 `raw` 做业务分支
- Session writer / logger 负责：
  - 原样写入 DTO
  - 不在落盘时再次“猜测”字段语义

### 语义映射矩阵（adapter 视角）

| 上游语义 | 产出 DTO | 关键字段 | 兜底策略 |
|---|---|---|---|
| 会话创建 | `session.start` | `session_id`, `config` | `config` 缺失则省略，不阻塞事件产出 |
| 用户提交消息 | `session.user_message` | `message_id`, `content` | `message_id` 缺失则生成稳定本地 ID |
| assistant 流式文本块 | `assistant.delta` | `message_id`, `delta` | 无流式能力时可不产出 |
| assistant 最终文本 | `assistant.message` | `message_id`, `content`, `finish_reason` | `finish_reason` 未知则保留原值 |
| 工具调用开始 | `tool.start` | `tool_call_id`, `tool_name`, `args` | 缺 `tool_call_id` 时生成并写入 `meta.reason` |
| 工具中间进度 | `tool.update` | `tool_call_id`, `tool_name`, `message`/`partial_output` | 若上游无中间态，可不产出 |
| 工具成功返回 | `tool.end` | `tool_call_id`, `tool_name`, `output` | 纯文本输出统一包成 `{"text": ...}` |
| 工具失败 | `tool.error` | `tool_call_id`, `tool_name`, `error.message` | 缺结构化错误时至少填 `message` |
| 传输状态变化 | `system.transport` | `component`, `operation`, `state` | 未知 state 原样保留 |
| fallback 决策 | `system.fallback` | `strategy`, `reason`, `from`, `to`, `result` | 若仅有“重试中”语义，也应产出一条清晰 fallback 事件 |
| 未映射上游事件 | `system.log` | `level="warning"`, `message`, `raw` | `meta.reason="unmapped_upstream_event"` |

### 字段归一化规则

| DTO 字段 | 优先来源 | fallback |
|---|---|---|
| `ts` | 上游事件时间 | TradeCat 接收时间 |
| `event_id` | 上游稳定事件 ID | 本地生成 `uuid4` |
| `session_id` | TradeCat 会话上下文 | adapter 本地会话 ID，禁止留空 |
| `turn_id` | 当前用户输入上下文 | 若无法判断则省略，不得猜测串联不同 turn |
| `message_id` | 上游消息 ID | 本地生成稳定 ID |
| `tool_call_id` | 上游 tool call ID | 本地生成 `uuid4` 并写入 `meta.reason` |
| `tool_name` | 上游工具声明 | 若缺失，填 `unknown_tool` 并保留 `raw` |
| `error.code` | 上游稳定错误码 | 缺失可省略 |
| `error.message` | 上游 message / exception text | 不得留空 |
| `error.retryable` | 上游标记 | adapter 基于错误类型推断 |
| `usage` | provider usage / token stats | 缺失可省略 |

### 兜底策略（必须实现）

1. 未知上游事件：
   - 产出 `system.log`
   - `level="warning"`
   - `meta.reason="unmapped_upstream_event"`
   - 保留脱敏后的 `raw`
2. 关键字段缺失：
   - `ts` 缺失：使用接收时间
   - `event_id` 缺失：生成 `uuid4`
   - `tool_call_id` 缺失：生成 `uuid4` 并写入 `meta.reason`
3. 枚举漂移：
   - `finish_reason` / `state` / `error.code` 若不在已知集合中，允许原值透传
   - 下游只能展示和统计，不能据此硬编码业务逻辑
4. 结构过大：
   - `raw` 允许截断大字段
   - 截断时必须保留高层语义，并在 `meta` 标记 `raw_truncated=true`

---

## 消费边界（给 #003-05 / TUI）

- TUI 可以依赖：
  - `type`
  - 统一字段名
  - `tool_call_id` / `message_id` / `turn_id` 的关联关系
- TUI 不可以依赖：
  - `raw` 的 key path
  - 上游 provider 的错误枚举
  - openclaw 自带 payload 的层级结构
- 日志 / session jsonl 应直接落 DTO，不做“二次转换”

---

## 事件链样例（JSONL）

> 说明：以下样例展示的是 TradeCat DTO，而不是上游 payload。`event_id/session_id/turn_id/message_id/tool_call_id` 均为示例值。

### A) Tool 成功链

```jsonl
{"v":1,"type":"session.start","ts":1730000000.001,"event_id":"evt-001","session_id":"sess-01","source":"tradecat","config":{"provider":"openclaw","model":"gpt-4.1-mini","tool_policy":"auto"}}
{"v":1,"type":"session.user_message","ts":1730000001.100,"event_id":"evt-002","session_id":"sess-01","turn_id":"turn-01","source":"tradecat","message_id":"m-user-01","content":"查一下 BTCUSDT 当前价格"}
{"v":1,"type":"tool.start","ts":1730000001.300,"event_id":"evt-003","session_id":"sess-01","turn_id":"turn-01","source":"adapter","tool_call_id":"call-01","tool_name":"get_crypto_price","args":{"symbol":"BTCUSDT"},"raw":{"upstream_type":"tool_call","name":"get_crypto_price","arguments":{"symbol":"BTCUSDT"}}}
{"v":1,"type":"tool.end","ts":1730000001.820,"event_id":"evt-004","session_id":"sess-01","turn_id":"turn-01","source":"tool","tool_call_id":"call-01","tool_name":"get_crypto_price","output":{"symbol":"BTCUSDT","price":68234.12,"currency":"USDT"},"elapsed_s":0.52}
{"v":1,"type":"assistant.message","ts":1730000002.050,"event_id":"evt-005","session_id":"sess-01","turn_id":"turn-01","source":"adapter","message_id":"m-assistant-01","content":"BTCUSDT 现价约 68234.12 USDT（工具：get_crypto_price）。","finish_reason":"stop"}
```

### B) Tool 失败链

```jsonl
{"v":1,"type":"session.user_message","ts":1730000100.100,"event_id":"evt-101","session_id":"sess-01","turn_id":"turn-02","source":"tradecat","message_id":"m-user-02","content":"查一下 ETHUSDT 当前价格"}
{"v":1,"type":"tool.start","ts":1730000100.250,"event_id":"evt-102","session_id":"sess-01","turn_id":"turn-02","source":"adapter","tool_call_id":"call-02","tool_name":"get_crypto_price","args":{"symbol":"ETHUSDT"},"timeout_s":5.0,"raw":{"upstream_type":"tool_call","name":"get_crypto_price","arguments":{"symbol":"ETHUSDT"}}}
{"v":1,"type":"tool.error","ts":1730000105.260,"event_id":"evt-103","session_id":"sess-01","turn_id":"turn-02","source":"tool","tool_call_id":"call-02","tool_name":"get_crypto_price","elapsed_s":5.01,"error":{"code":"timeout","message":"tool get_crypto_price timeout after 5s","type":"TimeoutError","retryable":true}}
{"v":1,"type":"assistant.message","ts":1730000105.500,"event_id":"evt-104","session_id":"sess-01","turn_id":"turn-02","source":"adapter","message_id":"m-assistant-02","content":"工具调用超时（get_crypto_price）。你可以稍后重试，或让我改用最近一分钟 K 线收盘价近似。","finish_reason":"stop"}
```

### C) Transport timeout + fallback 决策（模拟）

假设：

- 上游 gateway 的流式连接超时
- adapter 判定该错误可重试
- TradeCat 改用非流式请求继续本轮回答

```jsonl
{"v":1,"type":"system.transport","ts":1730000200.010,"event_id":"evt-201","session_id":"sess-01","turn_id":"turn-03","source":"adapter","component":"gateway","operation":"chat.stream","state":"start","attempt":1,"timeout_s":20.0}
{"v":1,"type":"system.transport","ts":1730000220.050,"event_id":"evt-202","session_id":"sess-01","turn_id":"turn-03","source":"adapter","component":"gateway","operation":"chat.stream","state":"timeout","attempt":1,"elapsed_s":20.04,"error":{"code":"timeout","message":"stream idle timeout","retryable":true}}
{"v":1,"type":"system.fallback","ts":1730000220.060,"event_id":"evt-203","session_id":"sess-01","turn_id":"turn-03","source":"adapter","strategy":"retry","reason":"stream timeout, retry with non-streaming request","from":{"mode":"stream"},"to":{"mode":"non_stream"},"result":"applied"}
{"v":1,"type":"system.transport","ts":1730000220.070,"event_id":"evt-204","session_id":"sess-01","turn_id":"turn-03","source":"adapter","component":"gateway","operation":"chat.request","state":"ok","attempt":2,"elapsed_s":0.65}
{"v":1,"type":"assistant.message","ts":1730000220.800,"event_id":"evt-205","session_id":"sess-01","turn_id":"turn-03","source":"adapter","message_id":"m-assistant-03","content":"已切换为非流式请求并成功返回结果（原因：流式超时）。","finish_reason":"stop"}
```

---

## 建议落地位置（代码）

- DTO 壳：`services-preview/tui-service/src/agent_events.py`
- 单测：`services-preview/tui-service/tests/test_agent_events.py`
- `#003-02` adapter：负责“上游 payload -> DTO”
- `#003-05` TUI / session / 日志：只消费 DTO

---

## 验收对照

- [x] 事件类型命名统一，语义边界清晰
- [x] 每类事件有最小字段定义（必填 / 可选 / 类型 / 语义）
- [x] 明确 adapter 负责 payload 映射，TUI 不吃上游原始结构
- [x] 有样例事件链（tool 成功 / tool 失败 / transport + fallback）
- [x] 新增最小 DTO 代码壳与单测

## 进展记录

### 2026-03-10

- [x] 完成 v1 DTO 规格整理，覆盖 `session / assistant / tool / system`
- [x] 明确 `raw`、脱敏、字段补齐与未知事件兜底语义
- [x] 补充 JSONL 事件链样例，可直接给 `#003-02` / `#003-05` 参考
- [x] 新增 `services-preview/tui-service/src/agent_events.py`
- [x] 新增 `services-preview/tui-service/tests/test_agent_events.py`
- [x] 已在 `services-preview/tui-service/src/tui.py` 接入本地 Agent Shell 的最小 DTO / jsonl 事件落点
- [x] 关闭说明：本单关闭边界是“DTO、字段说明、最小样例和一处稳定输出具备即可关闭”，当前已满足；真实 openclaw adapter 接线继续由 `#003-02` / `#003-05` 推进
