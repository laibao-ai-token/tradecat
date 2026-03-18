---
title: "003-02-01-feature-agent-gateway-contract-mapping"
status: closed
created: 2026-03-09
updated: 2026-03-12
closed: 2026-03-12
owner: lixh6
priority: medium
type: feature
---

# Trade Agent 003-02-01：Gateway 接口清单与 DTO 映射

## 描述

为 `#003-02` 的 `OpenClawAdapter` 实现准备一份可直接落地的 Gateway 合约说明，覆盖：

- `sessions.list`
- `chat.history`
- `chat.send`
- `sessions.patch`
- `sessions.reset`
- `models.list`

目标不是直接改造 TUI，也不是实现 adapter，而是先把 **Gateway 方法 → TradeCat DTO** 的映射、缺口和实现风险一次讲清楚。

## 取证范围与说明

### 本地现状

- 当前 workspace 中 `repository/openclaw` 子模块为空目录，无法直接从本地 checkout 读取 upstream 源码。
- 当前 workspace 中也没有现成的 `OpenClawAdapter`、agent DTO、session/chat/model 协议封装。
- TradeCat 现有 TUI 数据模型风格以显式 dataclass 为主，可参考：
  - `services-preview/tui-service/src/db.py`
  - `services-preview/tui-service/src/news_db.py`
  - `services-preview/tui-service/src/quote.py`
  - `services-preview/tui-service/src/tui.py`

### 外部依据

下述 Gateway 合约整理基于 openclaw 官方文档与官方源码 schema 页面（检索时间：`2026-03-09`）：

- 控制台 / Gateway 行为说明：`https://docs.openclaw.dev/server/control-ui/`
- Session 工具文档（用于辅助确认 session 行结构）：`https://docs.openclaw.dev/server/session-tools/`
- 官方源码 schema：
  - `https://github.com/openclaw/openclaw/blob/main/src/gateway/schema/sessions.ts`
  - `https://github.com/openclaw/openclaw/blob/main/src/gateway/schema/logs-chat.ts`
  - `https://github.com/openclaw/openclaw/blob/main/src/gateway/schema/agents-models-skills.ts`
- 官方源码 handler：
  - `https://github.com/openclaw/openclaw/blob/main/src/gateway/server-methods/chat.ts`
  - `https://github.com/openclaw/openclaw/blob/main/src/gateway/server-methods/models.ts`

> 说明：下面凡是标注“**推断**”的内容，表示官方 schema 没有直接给出 TradeCat 侧需要的语义，因此这里给出的是 adapter 设计建议，而不是 upstream 明文承诺。

## 结论摘要（TL;DR）

1. `sessions.list` / `sessions.patch` / `sessions.reset` 都围绕同一份 `SessionEntry` 工作，TradeCat 应同时保存 `session_key` 与 `session_id`，不要只保留其一。
2. `chat.send` 不是“一次请求一次完整响应”，而是 **立即 ACK + 后续事件流**；TradeCat 必须把“发送确认”和“流式事件”拆成两个 DTO 层。
3. `chat.history` 返回的是 **parts-based message**，不是简单 `role + text`；若只保留纯文本，会丢失 reasoning/tool/result 等结构信息。
4. `models.list` 更像“可选模型目录”，不能单独用来表达“当前会话正在使用哪个模型”；会话当前模型应优先从 session 侧读取。
5. `sessions.reset` 返回 `null`，不会顺手返回新 session snapshot；客户端调用后需要主动刷新 `chat.history` 和 `sessions.list`。

## 推荐的 TradeCat DTO 草案

以下 DTO 是为 `OpenClawAdapter` 设计的 **TradeCat 侧规范**，不是 upstream 原生 schema。

| DTO | 推荐字段 | 说明 |
|---|---|---|
| `SessionSummaryDTO` | `session_key`, `session_id`, `title`, `summary`, `created_at`, `updated_at`, `latest_at`, `cwd`, `model_id`, `message_count`, `turns`, `archived`, `pinned`, `status`, `kind`, `scope`, `managed_by`, `source`, `metadata_raw` | 列表页 / 侧栏使用的会话摘要。 |
| `SessionListDTO` | `items`, `next_cursor` | 对应 `sessions.list` 分页结果。 |
| `ChatMessageDTO` | `message_id`, `role`, `seq`, `version`, `created_at`, `updated_at`, `text`, `parts_raw`, `metadata_raw` | `text` 用于列表显示，`parts_raw` 用于无损保真。 |
| `ChatTranscriptDTO` | `session_key`, `session_id`, `thinking_level`, `verbose_level`, `messages` | 对应 `chat.history`。 |
| `ChatRunAckDTO` | `session_key`, `session_id`, `run_id`, `status`, `idempotency_key` | 对应 `chat.send` 的立即确认。 |
| `ChatEventDTO` | `run_id`, `session_key`, `seq`, `kind`, `delta`, `payload_raw` | 对应 `chat.send` 后续事件流；`payload_raw` 必须原样保留。 |
| `SessionPatchResultDTO` | `item`, `changed_fields` | 对应 `sessions.patch`。 |
| `ModelInfoDTO` | `model_id`, `label`, `description`, `source`, `mode`, `enabled_reason`, `tags`, `metadata_raw` | 对应 `models.list` 单项。 |
| `GatewayErrorDTO` | `code`, `message`, `data_raw` | 统一包装 Gateway 层错误。 |

### DTO 设计约束

- `session_key`：作为 Gateway 调用时的首选 transport identifier。
- `session_id`：作为后端返回的稳定标识补充保存；不可假定永远存在于所有接口结果中。
- `text`：仅作为 UI 快速显示字段；真实渲染仍应回退到 `parts_raw`。
- `metadata_raw` / `payload_raw`：保留 upstream 原始 JSON，避免协议演化时丢字段。

## Gateway 接口清单

### 1) `sessions.list`

#### 用途

- 拉取会话列表，驱动 TradeCat 左侧 session 列表 / 最近会话切换。
- 支持分页、搜索、是否包含归档会话。

#### 输入

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cursor` | `string` | 否 | 分页游标；**必须视为 opaque token**。 |
| `limit` | `integer` | 否 | 每页数量；schema 上限为 `200`。 |
| `q` | `string` | 否 | 模糊搜索标题 / 摘要等。 |
| `includeArchived` | `boolean` | 否 | 是否包含归档会话。 |

#### 输出

返回对象：`{ items: SessionEntry[], nextCursor: string | null }`

`SessionEntry` 核心字段：

- `id`
- `key`
- `createdAt`
- `updatedAt`
- `latestAt`
- `cwd`
- `title`
- `summary`
- `model`
- `messageCount`
- `turns`
- `archived`
- `pinned`
- `metadata`
- `source`
- `kind`
- `status`
- `scope`
- `managedBy`

#### 限制与注意事项

- `nextCursor` 为唯一可信分页续传标识，客户端不要自行推算分页位置。
- `key` 与 `id` 并存：**TradeCat 不应把两者混为一个字段**。
- `model` 是会话当前/最近关联模型的高价值信号，但不能替代 `models.list` 的完整目录。

#### 最小请求样例

```json
{
  "id": 1,
  "method": "sessions.list",
  "params": {
    "limit": 20,
    "includeArchived": false
  }
}
```

#### 最小响应样例

```json
{
  "id": 1,
  "result": {
    "items": [
      {
        "id": "session_01JQEXAMPLE",
        "key": "sess_btc_review",
        "createdAt": "2026-03-09T08:00:00.000Z",
        "updatedAt": "2026-03-09T08:05:10.000Z",
        "latestAt": "2026-03-09T08:05:10.000Z",
        "cwd": "/workspace/tradecat",
        "title": "BTC 信号复盘",
        "summary": "检查最近 1h 的信号与新闻",
        "model": "qwen3-max",
        "messageCount": 12,
        "turns": 6,
        "archived": false,
        "pinned": true,
        "metadata": {
          "provider": "openai-compatible"
        },
        "source": "gateway",
        "kind": "chat",
        "status": "ready",
        "scope": "workspace",
        "managedBy": "user"
      }
    ],
    "nextCursor": null
  }
}
```

#### `openclaw -> TradeCat DTO` 映射

| openclaw 字段 | TradeCat DTO 字段 | 说明 |
|---|---|---|
| `item.key` | `SessionSummaryDTO.session_key` | Gateway 调用优先使用这个字段。 |
| `item.id` | `SessionSummaryDTO.session_id` | 后端稳定 ID；用于诊断和去重。 |
| `item.title` | `SessionSummaryDTO.title` | 可为空字符串。 |
| `item.summary` | `SessionSummaryDTO.summary` | 直接透传。 |
| `item.createdAt` | `SessionSummaryDTO.created_at` | 解析为统一时间格式。 |
| `item.updatedAt` | `SessionSummaryDTO.updated_at` | 解析为统一时间格式。 |
| `item.latestAt` | `SessionSummaryDTO.latest_at` | 用于最近活动排序。 |
| `item.cwd` | `SessionSummaryDTO.cwd` | 可用于展示当前工作目录。 |
| `item.model` | `SessionSummaryDTO.model_id` | 只代表该会话关联模型，不代表目录项。 |
| `item.messageCount` | `SessionSummaryDTO.message_count` | 整数透传。 |
| `item.turns` | `SessionSummaryDTO.turns` | 整数透传。 |
| `item.archived` | `SessionSummaryDTO.archived` | 布尔透传。 |
| `item.pinned` | `SessionSummaryDTO.pinned` | 布尔透传。 |
| `item.status` | `SessionSummaryDTO.status` | 未知枚举值要原样保留。 |
| `item.kind` | `SessionSummaryDTO.kind` | 未知枚举值要原样保留。 |
| `item.scope` | `SessionSummaryDTO.scope` | 未知枚举值要原样保留。 |
| `item.managedBy` | `SessionSummaryDTO.managed_by` | 未知枚举值要原样保留。 |
| `item.source` | `SessionSummaryDTO.source` | 透传。 |
| `item.metadata` | `SessionSummaryDTO.metadata_raw` | 原样保存 JSON。 |
| `nextCursor` | `SessionListDTO.next_cursor` | 不要自行构造。 |

---

### 2) `chat.history`

#### 用途

- 拉取单个会话的历史消息。
- 作为进入会话详情页、恢复页面状态、重建消息列表的基础接口。

#### 输入

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionKey` | `string` | 二选一 | 会话 transport key。 |
| `sessionId` | `string` | 二选一 | 会话后端 ID。 |
| `limit` | `integer` | 否 | schema 范围 `1..500`，默认行为按 upstream 为准。 |

#### 输出

返回对象：

```json
{
  "sessionKey": "...",
  "sessionId": "...",
  "messages": [...],
  "thinkingLevel": "medium",
  "verboseLevel": "normal"
}
```

其中 `messages[*]` 核心字段：

- `id`
- `role`
- `createdAt`
- `updatedAt`
- `seq`
- `version`
- `parts`
- `metadata`

#### 限制与注意事项

- `parts` 是一组结构化消息片段，不应在 adapter 层被直接压平成单一字符串后丢弃原始结构。
- `thinkingLevel` / `verboseLevel` 更接近“会话展示偏好”，建议放在 transcript 级 DTO，而不是每条 message 上重复保存。
- `limit` 只控制返回条数，不等价于 cursor-based pagination。

#### 最小请求样例

```json
{
  "id": 2,
  "method": "chat.history",
  "params": {
    "sessionKey": "sess_btc_review",
    "limit": 100
  }
}
```

#### 最小响应样例

```json
{
  "id": 2,
  "result": {
    "sessionKey": "sess_btc_review",
    "sessionId": "session_01JQEXAMPLE",
    "thinkingLevel": "medium",
    "verboseLevel": "normal",
    "messages": [
      {
        "id": "msg_user_001",
        "role": "user",
        "createdAt": "2026-03-09T08:04:59.000Z",
        "updatedAt": "2026-03-09T08:04:59.000Z",
        "seq": 11,
        "version": 1,
        "parts": [
          {
            "type": "input_text",
            "text": "帮我总结 BTC 最近 1 小时的信号。"
          }
        ],
        "metadata": {}
      },
      {
        "id": "msg_assistant_001",
        "role": "assistant",
        "createdAt": "2026-03-09T08:05:10.000Z",
        "updatedAt": "2026-03-09T08:05:10.000Z",
        "seq": 12,
        "version": 1,
        "parts": [
          {
            "type": "output_text",
            "text": "最近 1 小时 BTC 以震荡为主。"
          }
        ],
        "metadata": {
          "runId": "run_01HQEXAMPLE"
        }
      }
    ]
  }
}
```

#### `openclaw -> TradeCat DTO` 映射

| openclaw 字段 | TradeCat DTO 字段 | 说明 |
|---|---|---|
| `sessionKey` | `ChatTranscriptDTO.session_key` | 透传。 |
| `sessionId` | `ChatTranscriptDTO.session_id` | 透传。 |
| `thinkingLevel` | `ChatTranscriptDTO.thinking_level` | 会话级展示偏好。 |
| `verboseLevel` | `ChatTranscriptDTO.verbose_level` | 会话级展示偏好。 |
| `message.id` | `ChatMessageDTO.message_id` | 主键。 |
| `message.role` | `ChatMessageDTO.role` | 透传。 |
| `message.seq` | `ChatMessageDTO.seq` | 保留原序号，便于增量更新。 |
| `message.version` | `ChatMessageDTO.version` | 用于幂等更新。 |
| `message.createdAt` | `ChatMessageDTO.created_at` | 时间标准化。 |
| `message.updatedAt` | `ChatMessageDTO.updated_at` | 时间标准化。 |
| `message.parts` | `ChatMessageDTO.parts_raw` | 原样保存，不裁剪。 |
| `message.parts[*].text` | `ChatMessageDTO.text` | **推断**：仅抽取 text-like part 拼接为展示文本。 |
| `message.metadata` | `ChatMessageDTO.metadata_raw` | 原样保存 JSON。 |

---

### 3) `chat.send`

#### 用途

- 发送一条用户消息并启动一次新的 agent run。
- 立即返回 ACK；真正的输出通过后续 `chat.event` 流送达。

#### 输入

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionKey` | `string` | 二选一 | 目标会话 transport key。 |
| `sessionId` | `string` | 二选一 | 目标会话后端 ID。 |
| `text` | `string` | 是 | 用户输入文本。 |
| `model` | `string` | 否 | 本次 run 覆盖模型。 |
| `cwd` | `string` | 否 | 本次 run 的工作目录覆盖。 |
| `approvalPolicy` | `string` | 否 | 工具/命令审批策略。 |
| `sandboxMode` | `string` | 否 | 沙箱模式。 |
| `idempotencyKey` | `string` | 否 | 用于防重复提交。 |
| `attachments` | `array` | 否 | 附件列表；MVP 可先不接。 |
| `metadata` | `object` | 否 | 自定义透传元数据。 |

#### 输出

`chat.send` 的立即返回值是 ACK，而不是完整 assistant reply。

最小 ACK 结构：

```json
{
  "runId": "run_01HQEXAMPLE",
  "status": "started"
}
```

后续事件流使用 `chat.event` 推送，核心字段：

- `runId`
- `sessionKey`
- `seq`
- `kind`
- `delta`
- `payload`

#### 限制与注意事项

- 根据 openclaw 官方 Control UI 文档，`chat.send` 采用 **ACK + 事件流** 模式，不能当作同步问答接口使用。
- `idempotencyKey` 建议由 TradeCat 在“用户一次点击发送”粒度生成，避免重复发起 run。
- **推断**：TradeCat 应将 ACK 与事件流拆成 `ChatRunAckDTO` 和 `ChatEventDTO` 两层，而不是把它们塞进同一个响应对象。
- `payload` 为开放结构，adapter 层不应硬编码只识别单一 tool schema；必须保留原始 JSON。

#### 最小请求样例

```json
{
  "id": 3,
  "method": "chat.send",
  "params": {
    "sessionKey": "sess_btc_review",
    "text": "帮我检查最近 1 小时 BTC 的信号、新闻和风险点。",
    "idempotencyKey": "tc-send-20260309-080600-001"
  }
}
```

#### 最小响应样例（ACK）

```json
{
  "id": 3,
  "result": {
    "runId": "run_01HQEXAMPLE",
    "status": "started"
  }
}
```

#### 最小事件样例（后续 `chat.event`）

```json
{
  "method": "chat.event",
  "params": {
    "runId": "run_01HQEXAMPLE",
    "sessionKey": "sess_btc_review",
    "seq": 7,
    "kind": "tool_update",
    "delta": "正在读取最近 1 小时信号",
    "payload": {
      "tool_name": "signals.query",
      "status": "running"
    }
  }
}
```

#### `openclaw -> TradeCat DTO` 映射

| openclaw 字段 | TradeCat DTO 字段 | 说明 |
|---|---|---|
| `result.runId` | `ChatRunAckDTO.run_id` | ACK 主键。 |
| `result.status` | `ChatRunAckDTO.status` | 立即状态，如 `started`。 |
| `params.sessionKey` | `ChatRunAckDTO.session_key` | 由请求上下文补齐。 |
| `params.sessionId` | `ChatRunAckDTO.session_id` | 由请求上下文补齐。 |
| `params.idempotencyKey` | `ChatRunAckDTO.idempotency_key` | 本地自持有，便于重试 / 去重。 |
| `event.runId` | `ChatEventDTO.run_id` | 事件关联 run。 |
| `event.sessionKey` | `ChatEventDTO.session_key` | 事件所属会话。 |
| `event.seq` | `ChatEventDTO.seq` | 严格递增序号。 |
| `event.kind` | `ChatEventDTO.kind` | `tool_start / tool_update / tool_end / ...` 等。 |
| `event.delta` | `ChatEventDTO.delta` | UI 可直接显示的增量文本。 |
| `event.payload` | `ChatEventDTO.payload_raw` | 原样保存 JSON。 |

---

### 4) `sessions.patch`

#### 用途

- 更新单个 session 的展示/管理属性。
- 可用于重命名、归档、置顶、补充摘要或写入 TradeCat 自有 metadata。

#### 输入

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionKey` | `string` | 二选一 | 目标会话 transport key。 |
| `sessionId` | `string` | 二选一 | 目标会话后端 ID。 |
| `patch.title` | `string` | 否 | 更新标题。 |
| `patch.summary` | `string` | 否 | 更新摘要。 |
| `patch.archived` | `boolean` | 否 | 归档开关。 |
| `patch.pinned` | `boolean` | 否 | 置顶开关。 |
| `patch.metadata` | `object` | 否 | 附加元数据。 |

#### 输出

返回对象：

```json
{
  "item": { "...": "SessionEntry" },
  "changed": ["title", "pinned"]
}
```

#### 限制与注意事项

- 当前 schema 只允许 patch 上述 5 类字段；不要把 `cwd` / `model` / `status` 误认为可 patch。
- `changed` 列表是非常实用的 UI 优化信号，TradeCat 可据此只刷新受影响字段。
- `metadata` 是扩展位，适合写入 TradeCat 自己的视图偏好，但要避免覆盖 upstream 自带键名。

#### 最小请求样例

```json
{
  "id": 4,
  "method": "sessions.patch",
  "params": {
    "sessionKey": "sess_btc_review",
    "patch": {
      "title": "BTC 风险复盘",
      "pinned": true
    }
  }
}
```

#### 最小响应样例

```json
{
  "id": 4,
  "result": {
    "item": {
      "id": "session_01JQEXAMPLE",
      "key": "sess_btc_review",
      "createdAt": "2026-03-09T08:00:00.000Z",
      "updatedAt": "2026-03-09T08:06:20.000Z",
      "latestAt": "2026-03-09T08:06:20.000Z",
      "cwd": "/workspace/tradecat",
      "title": "BTC 风险复盘",
      "summary": "检查最近 1h 的信号与新闻",
      "model": "qwen3-max",
      "messageCount": 12,
      "turns": 6,
      "archived": false,
      "pinned": true,
      "metadata": {},
      "source": "gateway",
      "kind": "chat",
      "status": "ready",
      "scope": "workspace",
      "managedBy": "user"
    },
    "changed": [
      "title",
      "pinned"
    ]
  }
}
```

#### `openclaw -> TradeCat DTO` 映射

| openclaw 字段 | TradeCat DTO 字段 | 说明 |
|---|---|---|
| `result.item` | `SessionPatchResultDTO.item` | 复用 `SessionSummaryDTO` 映射。 |
| `result.changed[*]` | `SessionPatchResultDTO.changed_fields` | 直接透传为字符串数组。 |

---

### 5) `sessions.reset`

#### 用途

- 重置当前会话上下文，用于 TradeCat `/new` 或“清空当前对话上下文”的动作。

#### 输入

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionKey` | `string` | 二选一 | 目标会话 transport key。 |
| `sessionId` | `string` | 二选一 | 目标会话后端 ID。 |

#### 输出

- 返回 `null`（无 `item`、无 `changed`、无新的 transcript snapshot）。

#### 限制与注意事项

- 这是破坏性操作，TradeCat 调用前应要求用户确认，或与 `/new` 明确绑定。
- 因为返回值为空，调用成功后必须主动刷新：
  1. `chat.history`
  2. `sessions.list`
- **推断**：若 UI 需要“立即看到空白会话”，本地状态应先 optimistic clear，再等待后台刷新校准。

#### 最小请求样例

```json
{
  "id": 5,
  "method": "sessions.reset",
  "params": {
    "sessionKey": "sess_btc_review"
  }
}
```

#### 最小响应样例

```json
{
  "id": 5,
  "result": null
}
```

#### `openclaw -> TradeCat DTO` 映射

| openclaw 字段 | TradeCat DTO 字段 | 说明 |
|---|---|---|
| `result = null` | `None` / `void` | 不单独建 payload DTO。 |
| `sessionKey` / `sessionId` | 本地 reset action context | 用于后续刷新和 optimistic clear。 |

---

### 6) `models.list`

#### 用途

- 拉取 Gateway 当前可用模型目录。
- 驱动 TradeCat `/model` 菜单、模型选择下拉列表、配置校验。

#### 输入

- 无参数；最小请求可直接传空对象或不传 `params`。

#### 输出

返回对象：`{ items: ModelChoice[] }`

`ModelChoice` 核心字段：

- `id`
- `label`
- `description`
- `source`
- `mode`
- `tags`
- `enabledReason`

#### 限制与注意事项

- `models.list` 表达的是“目录”，不是“当前 session 实际正在使用的模型”。
- `/model` 切换后，TradeCat 仍应从 session 侧状态确认“当前模型已生效”，不能只依赖本目录刷新成功。
- `tags` / `enabledReason` 应原样保留，用于后续 UI 能力提示和调试。

#### 最小请求样例

```json
{
  "id": 6,
  "method": "models.list",
  "params": {}
}
```

#### 最小响应样例

```json
{
  "id": 6,
  "result": {
    "items": [
      {
        "id": "qwen3-max",
        "label": "Qwen 3 Max",
        "description": "General purpose chat model",
        "source": "openai-compatible",
        "mode": "chat",
        "tags": ["default", "tools"],
        "enabledReason": "configured"
      }
    ]
  }
}
```

#### `openclaw -> TradeCat DTO` 映射

| openclaw 字段 | TradeCat DTO 字段 | 说明 |
|---|---|---|
| `item.id` | `ModelInfoDTO.model_id` | 主键。 |
| `item.label` | `ModelInfoDTO.label` | 展示名。 |
| `item.description` | `ModelInfoDTO.description` | 说明文案。 |
| `item.source` | `ModelInfoDTO.source` | 提供方 / catalog 来源。 |
| `item.mode` | `ModelInfoDTO.mode` | 如 `chat` / 其他模式。 |
| `item.tags` | `ModelInfoDTO.tags` | 能力标签。 |
| `item.enabledReason` | `ModelInfoDTO.enabled_reason` | 当前模型可用原因。 |
| `item` 整体 | `ModelInfoDTO.metadata_raw` | **推断**：建议保留原对象快照，便于未来扩展。 |

## Adapter 实现建议（供 `#003-02` 直接使用）

### 建议的方法边界

```python
list_sessions(...) -> SessionListDTO
load_history(session_ref, ...) -> ChatTranscriptDTO
send_message(...) -> ChatRunAckDTO
patch_session(...) -> SessionPatchResultDTO
reset_session(...) -> None
list_models() -> tuple[ModelInfoDTO, ...]
handle_chat_event(event) -> ChatEventDTO
```

### 推荐的本地状态拆分

1. **Session Store**：缓存 `SessionSummaryDTO` 列表。
2. **Transcript Store**：缓存 `ChatTranscriptDTO`，按 `session_key` 建索引。
3. **Run Store**：缓存 `ChatRunAckDTO` 与 run 生命周期。
4. **Event Store**：缓存 `ChatEventDTO`，并提供 TUI 卡片 / 日志 / session history 三路消费。

## 协议缺口、歧义点、实现风险

### P0：必须先处理

1. **`sessionKey` 与 `sessionId` 双标识并存**
   - 风险：如果 TradeCat 只保存一个字段，后续 patch/reset/history 调用会出现错配。
   - 建议：所有 DTO 都保留两个字段；调用时优先传 `sessionKey`，诊断与去重时保留 `sessionId`。

2. **`chat.send` 是 ACK，不是完整响应**
   - 风险：如果 adapter 把它当同步问答接口，会造成 UI 提前结束、tool 事件丢失、日志不闭环。
   - 建议：发送后立即建 run，再持续消费 `chat.event` 到 run 完成。

3. **`chat.history.messages[*].parts` 为结构化消息**
   - 风险：若只提取纯文本，工具输出、reasoning、富文本/附件等后续能力无法补回。
   - 建议：保留 `parts_raw`，`text` 只作为展示缓存。

4. **`sessions.reset` 返回空值**
   - 风险：调用成功后如果不主动刷新，UI 与真实会话状态会漂移。
   - 建议：reset 后强制刷新 `chat.history` 与 `sessions.list`。

### P1：实现期需要留钩子

5. **`models.list` 不是当前模型状态源**
   - 风险：`/model` 切换后 UI 显示变了，但实际请求模型未切换成功。
   - 建议：目录来自 `models.list`，当前会话实际模型以 session 数据 / 发包回执校准。

6. **`payload` / `metadata` 是开放 JSON**
   - 风险：未来新增字段或 tool 类型时，强 schema 解析会直接崩。
   - 建议：解析关键字段，其他内容完整保存为 raw JSON。

7. **Workspace 本地缺少 pinned upstream checkout**
   - 风险：本说明基于官方文档与 main 分支源码，真正实现前若子模块 commit 有差异，字段枚举可能漂移。
   - 建议：`#003-02` 开工前先把 `repository/openclaw` 初始化到目标 commit，并对照本清单做一次 schema diff。

8. **`attachments` 尚未进入当前 TUI MVP 范围**
   - 风险：如果一开始强做附件上传，会把 adapter 范围拉大。
   - 建议：第一版接口保留字段但默认禁用，先聚焦文本/事件闭环。

## 对 `#003-02` 的直接输入

- 先实现上述 6 个 Gateway wrapper，不要在 wrapper 层引入 TUI 细节。
- 先把 `ChatRunAckDTO` 与 `ChatEventDTO` 分开，不要合并。
- 先定义 `SessionSummaryDTO` / `ChatMessageDTO` / `ModelInfoDTO` 三个核心 dataclass，再补 transcript 与 patch result。
- `session_key` 作为 Gateway transport 主键，`session_id` 作为补充主键。
- 所有 `raw` 字段都保留，直到 UI 明确不再需要。

## 相关文件

- `.issues/open/003-trade-agent/003-feature-trade-agent-development.md`（主 issue）
- `.issues/open/003-trade-agent/closed-003-02-01-feature-agent-gateway-contract-mapping.md`（本文件）
- `services-preview/tui-service/src/db.py`
- `services-preview/tui-service/src/news_db.py`
- `services-preview/tui-service/src/quote.py`
- `services-preview/tui-service/src/tui.py`

## 进展记录

### 2026-03-09 00:00 UTC

- [x] 确认本地 `repository/openclaw` 子模块为空，无法直接依赖 workspace 内源码
- [x] 基于 openclaw 官方文档与 schema 梳理 6 个核心 Gateway 方法
- [x] 为每个方法补齐最小请求 / 响应样例
- [x] 输出 `openclaw -> TradeCat DTO` 字段映射建议
- [x] 输出协议缺口 / 歧义点 / 实现风险清单

## 验收标准

- [x] 6 个核心 Gateway 接口都有清单说明
- [x] 每个接口至少有一组最小请求 / 响应样例
- [x] 有一份 `openclaw -> TradeCat DTO` 字段映射表
- [x] 有缺口 / 风险列表，可直接指导 `#003-02` 实现

---

## 备注

### 下一步建议

1. 在 `#003-02` 中把 DTO 先落为 dataclass / typed dict。
2. 接着补一个最小 Gateway client，优先打通：
   - `models.list`
   - `sessions.list`
   - `chat.history`
   - `chat.send + chat.event`
3. 最后再接 `sessions.patch` 与 `sessions.reset` 到 TUI 命令动作。

### 2026-03-12

- [x] 已人工确认该参考规格单可以正式收口，保留在 `closed-*` 文件名下作为历史材料
